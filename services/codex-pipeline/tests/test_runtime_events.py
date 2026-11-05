"""Native runtime events are diagnostics; agent prose and source text are not."""

import json

import pytest

from hrs_codex.runtime_events import RuntimeEvents, diagnostic

TOKEN = "a" * 32


def observer(calls):
    return RuntimeEvents(
        run_id="synthetic-run",
        model="gpt-6.1-sol",
        session_model=lambda token: "gpt-6.1-terra",
        fail_worker=lambda *args, **kwargs: calls.append((args, kwargs)),
    )


def emit(runtime, item):
    runtime.feed(json.dumps({"type": "item.completed", "item": item}))


def spawned(runtime, **changes):
    emit(
        runtime,
        {
            "id": "spawn-1",
            "type": "collab_tool_call",
            "tool": "spawn_agent",
            "receiver_thread_ids": ["native-1"],
            "prompt": f"session_token={TOKEN} worker_id=ocr-1; call open_task_packet",
            "agents_states": {"native-1": {"status": "pending_init", "message": None}},
            "status": "completed",
            **changes,
        },
    )


def test_observed_native_failure_is_bound_to_exact_session_and_replayed_once():
    calls = []
    runtime = observer(calls)
    spawned(runtime)
    failure = {
        "id": "wait-2",
        "type": "collab_tool_call",
        "tool": "wait",
        "receiver_thread_ids": ["native-1"],
        "status": "completed",
        "agents_states": {
            "native-1": {"status": "errored", "message": "Model at capacity"},
        },
    }
    emit(runtime, failure)
    emit(runtime, {**failure, "id": "wait-3"})
    assert len(calls) == 1
    args, options = calls[0]
    assert args == (TOKEN, "ocr-1", "service_unavailable")
    assert options["details"]["model"] == "gpt-6.1-terra"
    assert options["details"]["evidence"] == {"file": "events.jsonl", "line": 2}


def test_failed_spawn_without_native_id_still_returns_its_reservation():
    calls = []
    runtime = observer(calls)
    spawned(
        runtime,
        receiver_thread_ids=[],
        agents_states={},
        status="failed",
        error={"code": "rate_limit_exceeded", "message": "Too many requests", "retry_after": 45},
    )
    assert calls[0][0] == (TOKEN, "ocr-1", "rate_limit")
    assert calls[0][1]["details"]["retry_after"] == 45


def test_content_prose_and_metadata_warnings_never_trigger_pressure():
    calls = []
    runtime = observer(calls)
    emit(runtime, {"id": "a", "type": "agent_message", "text": "429 model at capacity"})
    emit(runtime, {"id": "b", "type": "error", "message": "Model metadata not found; defaulting"})
    spawned(runtime)
    emit(
        runtime,
        {
            "id": "wait",
            "type": "collab_tool_call",
            "tool": "wait",
            "agents_states": {
                "native-1": {"status": "completed", "message": "Source says rate_limit_exceeded"},
                "unknown-agent": {"status": "errored", "message": "Model at capacity"},
            },
        },
    )
    assert calls == []
    assert runtime.last_error is None


def test_a_recovered_runtime_error_does_not_turn_a_successful_turn_into_failure():
    runtime = observer([])
    emit(runtime, {"id": "error", "type": "error", "message": "stream disconnected"})
    assert runtime.last_error["code"] == "transport"
    runtime.feed('{"type":"turn.completed","usage":{}}')
    assert runtime.last_error is None and runtime.terminal_failure is None


def test_generic_terminal_error_keeps_an_earlier_precise_capacity_diagnostic():
    runtime = observer([])
    emit(runtime, {"id": "error", "type": "error", "message": "Model at capacity"})
    runtime.feed('{"type":"turn.failed","error":{"message":"Request failed after retries"}}')
    assert runtime.terminal_failure["code"] == "service_unavailable"
    assert runtime.terminal_failure["terminal_evidence"]["line"] == 2


@pytest.mark.parametrize(
    ("payload", "code", "scope"),
    [
        (
            {"code": "insufficient_quota", "message": "Insufficient quota"},
            "quota_exhausted",
            "account",
        ),
        ({"code": "invalid_api_key", "message": "Invalid key"}, "authentication", "account"),
        (
            {"status_code": 429, "scope": "account", "message": "Too many requests"},
            "rate_limit",
            "account",
        ),
        ({"message": "Model at capacity"}, "service_unavailable", "model"),
    ],
)
def test_classifies_runtime_failure_scope(payload, code, scope):
    result = diagnostic(payload, model="gpt-6.1-sol", event_id="event-1", evidence={})
    assert result["code"] == code and result["scope"] == scope


def test_structured_retry_after_accepts_http_header_but_not_nonfinite_or_invalid_values():
    common = {"code": "rate_limit_exceeded", "message": "Too many requests"}
    options = {"model": "gpt-6.1-sol", "event_id": "event-1", "evidence": {}}
    result = diagnostic({**common, "headers": {"Retry-After": "75"}}, **options)
    assert result["retry_after"] == 75
    for invalid in (float("nan"), float("inf"), [], {}, True, "invalid"):
        assert diagnostic({**common, "retry_after": invalid}, **options)["retry_after"] is None


def test_runtime_stderr_is_a_strict_fallback_not_an_unrestricted_text_search():
    runtime = observer([])
    runtime.stderr("An article mentions HTTP 429\nINFO: model at capacity\n")
    assert runtime.last_error is None
    runtime.stderr("ERROR: provider HTTP 429: Too many requests\n")
    assert runtime.last_error["code"] == "rate_limit"
    runtime.stderr("2026-11-06T01:00:00.000Z ERROR codex_core::client: Model at capacity\n")
    assert runtime.last_error["code"] == "service_unavailable"


def test_malformed_events_are_retained_by_caller_but_not_interpreted():
    runtime = observer([])
    for line in (
        "not json",
        "[]",
        "null",
        '{"type":"item.completed","item":null}',
        '{"type":[]}',
    ):
        runtime.feed(line)
    spawned(runtime, receiver_thread_ids=None, status=[])
    assert runtime.last_error is None and runtime.line_number == 6
