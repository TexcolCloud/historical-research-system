"""Coordinator leases, explicit continuation and terminal technical outcomes."""

from __future__ import annotations

import shutil
import time
from uuid import uuid4

from filelock import FileLock, Timeout

from hrs.processing.errors import LeaseConflict
from hrs.processing.workflow_rules import (
    metadata_work_remaining,
    request_continuation,
)


class RunWorkflow:
    def __init__(self, context, navigation):
        self.context = context
        self.navigation = navigation

    def pause_document(self, document_id: str) -> dict:
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            request = doc.setdefault("run_request", {"state": "paused", "history": []})
            request["pause_requested"] = True
            if request.get("state") != "running":
                request["state"] = "paused"
                doc["state"] = "paused"
                for task in doc["tasks"]:
                    if task["state"] == "leased":
                        task["state"] = "ready"
            request.setdefault("history", []).append({"event": "user_paused", "at": time.time()})
            (self.context.repository.directory(document_id) / ".pause-requested").touch()
            self.context.repository.write(doc)
            return request

    def request_run(self, document_id: str, *, resume: bool = False) -> dict:
        with (
            self.context.verified(document_id) as doc,
            self.context.repository.ledger.connect(write=True),
        ):
            if doc.get("run_request", {}).get("cancel_requested"):
                raise ValueError("This document is stopping for explicit deletion")
            existing = doc.get("run_request", {})
            if doc["state"] == "published" and not metadata_work_remaining(doc):
                return existing or request_continuation(doc)
            if (
                resume
                and existing.get("state") == "running"
                and existing.get("lease_expires_at", 0) > time.time()
            ):
                return existing
            live_workers = any(
                t["state"] == "leased" and t["claim"]["expires_at"] > time.time()
                for t in doc["tasks"]
            )
            request = request_continuation(doc, resume=resume)
            if resume:
                (self.context.repository.directory(document_id) / ".pause-requested").unlink(
                    missing_ok=True
                )
                self.navigation.refresh_document(doc)
                if not live_workers:
                    self.reset_runtime(doc)
            self.context.repository.write(doc)
            return request

    def reset_runtime(self, doc, *, automatic=False):
        """An explicit recovery grants fresh technical budget without changing content."""
        if not automatic:
            from hrs.processing.runtime_control import RuntimeControl

            RuntimeControl(self.context.repository.root, self.context.repository.ledger).reset()
        for task in doc["tasks"]:
            if task.get("technical_blocked"):
                task.update(
                    technical_blocked=False,
                    technical_retry_since=len(task.get("technical_failures", [])),
                    technical_retry_limit=1 if automatic else 3,
                )
                task["retry_after"] = 0
        doc.setdefault("scheduler", {}).pop("cooldown_until", None)

    def claim_run(
        self, owner: str, lease_seconds: int = 900, *, document_id: str | None = None
    ) -> dict | None:
        if not isinstance(owner, str) or not owner.strip() or not 1 <= lease_seconds <= 86400:
            raise ValueError("Run owner and lease between 1 and 86400 seconds required")
        with self.context.repository.lock:
            now = time.time()
            identities = [document_id] if document_id is not None else self.context.repository.ids()
            for identity in identities:
                if not self.context.repository.exists(identity):
                    continue
                doc = self.context.repository.read(identity)
                request = doc.get("run_request")
                if request and request.get("pause_requested"):
                    continue
                if not request or request["state"] not in {
                    "pending",
                    "retryable_error",
                    "running",
                    "waiting_workers",
                }:
                    continue
                if request["state"] == "waiting_workers" and not any(
                    task["state"] == "leased" and task["claim"]["expires_at"] > now
                    for task in doc["tasks"]
                ):
                    request["not_before"] = 0
                if (
                    request.get("technical_failures", 0) >= request.get("retry_limit", 3)
                    or request.get("not_before", 0) > now
                ):
                    continue
                if request["state"] == "running" and request["lease_expires_at"] > now:
                    continue
                from hrs.processing.runtime_control import RuntimeControl

                control = RuntimeControl(
                    self.context.repository.root, self.context.repository.ledger
                )
                actual_model = request.get("actual_model", "gpt-6.1-sol")
                if not control.gate(actual_model)["allowed"]:
                    continue
                if control.gate(actual_model)["probe"] and any(
                    (other := self.context.repository.read(other_id))
                    .get("run_request", {})
                    .get("state")
                    == "running"
                    and other["run_request"].get("lease_expires_at", 0) > now
                    for other_id in self.context.repository.ids()
                    if other_id != identity
                ):
                    # A coordinator launches the one real worker probe; it must not consume it.
                    continue
                # Model task leases outlive a crashed coordinator. Do not race its workers.
                if any(
                    t["state"] == "leased" and t["claim"]["expires_at"] > now for t in doc["tasks"]
                ):
                    continue
                try:
                    with FileLock(
                        self.context.repository.directory(identity) / ".executor.lock", timeout=0
                    ):
                        pass
                except Timeout:
                    continue
                if request["state"] == "running":
                    request["history"].append(
                        {"event": "lease_expired", "at": now, "owner": request["owner"]}
                    )
                    request["technical_failures"] = request.get("technical_failures", 0) + 1
                    request.update(
                        state="retryable_error",
                        error="Run lease expired",
                        not_before=now + 30 * (2 ** (request["technical_failures"] - 1)),
                    )
                    request["ready_after"] = request["not_before"]
                    self.context.repository.write(doc)
                    continue
                request.update(
                    state="running",
                    owner=owner,
                    token=uuid4().hex,
                    lease_expires_at=now + lease_seconds,
                    attempts=request["attempts"] + 1,
                    actual_model=actual_model,
                )
                request["ready_after"] = request["not_before"]
                request["history"].append(
                    {"event": "claimed", "at": now, "owner": owner, "attempt": request["attempts"]}
                )
                self.context.repository.write(doc)
                return request
            return None

    def renew_run(self, document_id: str, token: str, lease_seconds: int = 900) -> dict:
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("Run lease must be between 1 and 86400 seconds")
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            request = doc.get("run_request", {})
            if (
                request.get("state") != "running"
                or request.get("token") != token
                or request["lease_expires_at"] <= time.time()
            ):
                raise LeaseConflict("Run does not have this active lease")
            request["lease_expires_at"] = time.time() + lease_seconds
            self.context.repository.write(doc)
            return request

    def finish_run(self, document_id: str, token: str, error: str | dict | None = None) -> dict:
        def validate(doc):
            request = doc.get("run_request", {})
            if (
                doc["state"] == "published"
                and error is None
                and not request.get("cancel_requested")
                and not request.get("pause_requested")
            ):
                self.context.evidence.verify(doc)

        with (
            self.context.verified(document_id, validate) as doc,
            self.context.repository.ledger.connect(write=True),
        ):
            request = doc.get("run_request", {})
            if request.get("state") != "running" or request.get("token") != token:
                raise LeaseConflict("Run does not have this active lease")
            now = time.time()
            if request.get("cancel_requested"):
                request.update(state="cancelled", error=None, not_before=0)
                doc["state"] = "cancelled"
                self.context.repository.write(doc)
                return request
            if request.get("pause_requested"):
                for task in doc["tasks"]:
                    if task["state"] == "leased":
                        task["state"] = "ready"
                request.update(state="paused", error=None, not_before=0)
                doc["state"] = "paused"
                self.context.repository.write(doc)
                return request
            if request["lease_expires_at"] <= now:
                raise LeaseConflict("Run lease expired; reclaim before finishing")
            pressure = None
            if (
                isinstance(error, dict)
                and error.get("code")
                and error.get("reason") != "runtime_blocked"
            ):
                from hrs.processing.runtime_control import RuntimeControl

                with self.context.repository.ledger.connect(write=True):
                    pressure = RuntimeControl(
                        self.context.repository.root, self.context.repository.ledger
                    ).failure(
                        error["code"],
                        "run:" + token,
                        model=error.get("model") or request.get("actual_model", "gpt-6.1-sol"),
                        scope=error.get("scope", "model"),
                        retry_after_seconds=error.get("retry_after"),
                    )
            active = [
                t["claim"]["expires_at"]
                for t in doc["tasks"]
                if t["state"] == "leased" and t["claim"]["expires_at"] > now
            ]
            continuation = request.pop("continuation_requested", False)
            if active:
                request.update(
                    state="retryable_error" if error is not None else "waiting_workers",
                    not_before=max(active),
                    error=error,
                )
                if error is not None:
                    request["technical_failures"] = request.get("technical_failures", 0) + 1
                    request["not_before"] = max(
                        request["not_before"], now + 30 * (2 ** (request["technical_failures"] - 1))
                    )
            elif error is not None:
                request["technical_failures"] = request.get("technical_failures", 0) + 1
                request.update(
                    state="retryable_error",
                    error=error,
                    not_before=now + 30 * (2 ** (request["technical_failures"] - 1)),
                )
            elif metadata_work_remaining(doc):
                request.update(state="pending", error=None, not_before=0)
            elif doc["state"] == "published":
                request.update(state="succeeded", error=None, not_before=0)
            elif continuation and all(p["state"] == "approved" for p in doc["pages"]):
                request.update(state="pending", error=None, not_before=0)
            elif any(t["state"] == "ready" for t in doc["tasks"]) and any(
                p["state"] == "pending" for p in doc["pages"]
            ):
                request.update(state="pending", error=None, not_before=0)
            elif any(t["state"] == "pending" for t in doc["tasks"]):
                request.update(state="waiting_human", error=None, not_before=0)
            else:
                request["technical_failures"] = request.get("technical_failures", 0) + 1
                request.update(
                    state="retryable_error",
                    error="Coordinator exited before publication or content review decision",
                    not_before=now + 30 * (2 ** (request["technical_failures"] - 1)),
                )
            if pressure:
                request["not_before"] = max(request["not_before"], pressure.get("retry_at", 0))
            request["ready_after"] = request["not_before"]
            request["history"].append(
                {
                    "event": "finished",
                    "at": now,
                    "state": request["state"],
                    "error": request.get("error"),
                }
            )
            self.context.repository.write(doc)
            return request

    def cancel_document(self, document_id: str) -> dict:
        """Stop this document's owned coordinator before an explicit local deletion."""
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            (self.context.repository.directory(document_id) / ".cancel-requested").touch()
            request = doc.get("run_request") or request_continuation(doc)
            request["cancel_requested"] = True
            if request["state"] != "running":
                request["state"] = "cancelled"
            self.context.repository.write(doc)
            return {"document_id": document_id, "state": request["state"]}

    def forget_document(self, document_id: str) -> None:
        """Erase only this stopped document's verified workspace directories."""
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            if doc.get("run_request", {}).get("state") == "running":
                raise ValueError("Wait for the owned coordinator to stop before deletion")
            targets = [
                self.context.repository.directory(document_id),
                self.context.repository.root / "runs" / document_id,
            ]
            for target in targets:
                resolved = target.resolve()
                if resolved != target.absolute() or not resolved.is_relative_to(
                    self.context.repository.root
                ):
                    raise ValueError("Document deletion escapes its workspace")
            for target in targets:
                if target.exists():
                    shutil.rmtree(target)
            if self.context.repository.ledger is not None:
                self.context.repository.ledger.delete(document_id)

    def record_recovery_failure(self, document_id, expected_request, detail):
        with self.context.repository.lock:
            current = self.context.repository.read(document_id)
            if current.get("run_request") != expected_request:
                return False
            current["run_request"].update(
                automatic_checkpoint_used=True,
                recovery_error={"code": "checkpoint_invalid", "detail": detail},
            )
            self.context.repository.write(current)
            return True
