"""Observe native execution diagnostics without interpreting source or agent prose."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path


class RuntimeFailure(RuntimeError):
    """Stable execution failure transported from the subprocess to the durable runner."""

    def __init__(self, failure):
        self.failure = {"type": type(self).__name__, **failure}
        super().__init__(self.failure["message"])


_CODES = {
    "429": "rate_limit",
    "rate_limit": "rate_limit",
    "rate_limit_exceeded": "rate_limit",
    "model_at_capacity": "service_unavailable",
    "capacity_exceeded": "service_unavailable",
    "overloaded_error": "service_unavailable",
    "503": "service_unavailable",
    "service_unavailable": "service_unavailable",
    "server_error": "service_unavailable",
    "insufficient_quota": "quota_exhausted",
    "quota_exceeded": "quota_exhausted",
    "quota_exhausted": "quota_exhausted",
    "usage_limit_reached": "quota_exhausted",
    "credits_exhausted": "quota_exhausted",
    "401": "authentication",
    "authentication": "authentication",
    "authentication_error": "authentication",
    "invalid_api_key": "authentication",
    "unauthorized": "authentication",
    "token_expired": "authentication",
    "timeout": "timeout",
    "request_timeout": "timeout",
    "transport": "transport",
    "connection_error": "transport",
    "tool_error": "tool_error",
}

# These expressions apply only to runtime error fields, never ordinary agent text.
_MESSAGES = (
    (
        "quota_exhausted",
        r"\b(insufficient_quota|quota exhausted|quota exceeded|out of credits|usage limit (?:has been )?reached)\b",
    ),
    (
        "authentication",
        r"\b(invalid api key|authentication failed|not authenticated|login required|unauthorized|token expired)\b",
    ),
    (
        "rate_limit",
        r"\b(rate[_ -]limit(?:[_ -]exceeded|ed)?|too many requests|HTTP(?: status)? 429)\b",
    ),
    (
        "service_unavailable",
        r"\b(model (?:is )?at capacity|model capacity (?:exceeded|reached)|service unavailable|HTTP(?: status)? 503|server overloaded)\b",
    ),
    ("timeout", r"\b(timed out|request timeout|connection timeout)\b"),
    (
        "transport",
        r"\b(connection (?:reset|refused|closed)|network (?:error|unreachable)|stream disconnected|transport error)\b",
    ),
)


def _retry_seconds(value):
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        if not isinstance(value, str):
            return None
        try:
            moment = parsedate_to_datetime(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            seconds = (moment - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def diagnostic(value, *, model, event_id, evidence, fallback=None):
    """Normalize an explicitly identified engine error; an unknown warning stays a warning."""
    payload = value if isinstance(value, dict) else {"message": str(value or "")}
    nested = payload.get("error")
    if isinstance(nested, dict):
        payload = {**payload, **nested}
    message = str(payload.get("message") or payload.get("error") or "Native execution failed")
    code = next(
        (
            _CODES[str(payload[key]).lower()]
            for key in ("code", "error_code", "type", "status_code")
            if str(payload.get(key, "")).lower() in _CODES
        ),
        None,
    )
    if code is None:
        code = next(
            (kind for kind, pattern in _MESSAGES if re.search(pattern, message, re.I)), fallback
        )
    if code is None:
        return None
    scope = payload.get("scope")
    if not isinstance(scope, str) or scope not in {"account", "model"}:
        scope = "account" if code in {"quota_exhausted", "authentication"} else "model"
    headers = payload.get("headers")
    retry = payload.get("retry_after")
    if retry is None and isinstance(headers, dict):
        retry = next((v for k, v in headers.items() if str(k).lower() == "retry-after"), None)
    return {
        "code": code,
        "message": message[:4000],
        "model": model or (payload.get("model") if isinstance(payload.get("model"), str) else None),
        "scope": scope,
        "retry_after": _retry_seconds(retry),
        "event_id": event_id,
        "evidence": evidence,
    }


class RuntimeEvents:
    """Map exposed native worker failures back to their persistent, lease-bound sessions."""

    def __init__(
        self,
        *,
        run_id,
        model,
        fail_worker,
        session_model=lambda token: None,
        log_directory=None,
    ):
        self.run_id, self.model = run_id, model
        self.fail_worker, self.session_model = fail_worker, session_model
        self.agents = {}
        self.reported_sessions = set()
        self.last_error = None
        self.terminal_failure = None
        self.line_number = 0
        self.log_directory = Path(log_directory) if log_directory else None

    def _evidence(self, filename, line):
        return {
            "file": str(self.log_directory / filename) if self.log_directory else filename,
            "line": line,
        }

    def feed(self, line):
        self.line_number += 1
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if not isinstance(kind, str):
            return
        evidence = self._evidence("events.jsonl", self.line_number)
        identity = f"{self.run_id}:event:{self.line_number}"
        if kind == "turn.failed":
            failure = diagnostic(
                event.get("error", event),
                model=self.model,
                event_id=identity,
                evidence=evidence,
                fallback="tool_error",
            )
            self.terminal_failure = (
                {**self.last_error, "terminal_evidence": evidence}
                if failure["code"] == "tool_error" and self.last_error
                else failure
            )
            self.last_error = self.terminal_failure
        elif kind == "turn.completed":
            self.terminal_failure = self.last_error = None
        elif kind in {"error", "request.failed"}:
            self.last_error = (
                diagnostic(
                    event,
                    model=self.model,
                    event_id=identity,
                    evidence=evidence,
                )
                or self.last_error
            )
        elif kind == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                return
            if item.get("type") == "error":
                self.last_error = (
                    diagnostic(
                        item,
                        model=self.model,
                        event_id=identity,
                        evidence=evidence,
                    )
                    or self.last_error
                )
            elif item.get("type") == "collab_tool_call":
                self._collaboration(item, evidence)

    def _collaboration(self, item, evidence):
        assignment = None
        if item.get("tool") == "spawn_agent":
            prompt = item.get("prompt")
            if not isinstance(prompt, str):
                return
            token = re.search(r"\bsession_token[\"']?\s*[:=]\s*[\"']?([a-f0-9]{32})\b", prompt)
            worker = re.search(r"\bworker_id[\"']?\s*[:=]\s*[\"']?([^\s,;\"'}]+)", prompt)
            if token and worker:
                assignment = (token[1], worker[1])
                receivers = item.get("receiver_thread_ids")
                if isinstance(receivers, list):
                    for agent in receivers:
                        if isinstance(agent, str):
                            self.agents[agent] = assignment
                if item.get("status") in ("failed", "errored"):
                    self._failed(assignment, item.get("error") or item, evidence)
        states = item.get("agents_states")
        if not isinstance(states, dict):
            return
        for agent, state in states.items():
            if isinstance(state, dict) and state.get("status") in ("failed", "errored"):
                owned = self.agents.get(agent) or assignment
                if owned:
                    self._failed(owned, state.get("error") or state, evidence)

    def _failed(self, assignment, value, evidence):
        token, worker = assignment
        if token in self.reported_sessions:
            return
        failure = diagnostic(
            value,
            model=self.session_model(token),
            event_id=f"{self.run_id}:session:{token}",
            evidence=evidence,
            fallback="tool_error",
        )
        self.fail_worker(token, worker, failure["code"], details=failure)
        self.reported_sessions.add(token)

    def stderr(self, text):
        """Only explicit stderr diagnostics are eligible as an unsuccessful-exit fallback."""
        for number, line in enumerate(text.splitlines(), 1):
            stripped = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if not re.match(
                r"^(?:\d{4}-\d\d-\d\dT\S+\s+)?(?:ERROR\b|Error\b|error:|HTTP\b|\{)",
                stripped,
            ):
                continue
            try:
                value = json.loads(stripped)
            except ValueError:
                value = {"message": stripped}
            failure = diagnostic(
                value,
                model=self.model,
                event_id=f"{self.run_id}:stderr:{number}",
                evidence=self._evidence("stderr.log", number),
            )
            if failure:
                self.last_error = failure
