"""Execution failures retain machine-readable diagnostics, without model requests."""

import json
import sqlite3
import threading

import pytest

from hrs_codex import execution
from hrs_codex.store import Store


def prepared_store(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"retained original evidence")
    store = Store(tmp_path / "workspace")
    return store, store.register(source, [{"page": 1, "image": str(source)}])["id"]


class FailedProcess:
    def __init__(self, arguments, **options):
        self.options = options
        self.returncode = 1
        self.pid = 12345

    def communicate(self, *, input, **options):
        self.options["stdout"].write(
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Model at capacity",
                        "retry_after": 73,
                    },
                }
            )
            + "\n"
        )

    def poll(self):
        return self.returncode


def test_coordinator_capacity_failure_keeps_classification_and_actual_model(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(execution.subprocess, "Popen", FailedProcess)
    with pytest.raises(RuntimeError) as caught:
        execution.run_codex(store, document_id)
    failure = getattr(caught.value, "failure", {})
    assert failure.get("code") == "rate_limit"
    assert failure["model"] == "gpt-6.1-sol"
    assert failure["scope"] == "model"
    assert failure["retry_after"] == 73
    assert failure["event_id"]
    logs = next((store.root / "runs" / document_id).iterdir())
    assert json.loads((logs / "request.json").read_text("utf-8"))["actual_model"] == "gpt-6.1-sol"
    assert json.loads((logs / "receipt.json").read_text("utf-8"))["error"]["code"] == "rate_limit"


def test_native_failure_is_reported_while_coordinator_is_still_running(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    reported = threading.Event()
    token = "b" * 32
    failures = []

    def report(session_token, worker_id, code, *, details):
        failures.append((session_token, worker_id, code, details))
        reported.set()

    class LiveProcess(FailedProcess):
        def communicate(self, *, input, **options):
            self.returncode = None
            for item in (
                {
                    "id": "spawn-1",
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "prompt": f"session_token={token} worker_id=worker-1; call open_task_packet",
                    "receiver_thread_ids": ["native-1"],
                    "agents_states": {},
                    "status": "completed",
                },
                {
                    "id": "wait-1",
                    "type": "collab_tool_call",
                    "tool": "wait",
                    "agents_states": {
                        "native-1": {"status": "errored", "message": "Model at capacity"}
                    },
                    "status": "completed",
                },
            ):
                self.options["stdout"].write(
                    json.dumps({"type": "item.completed", "item": item}) + "\n"
                )
            self.options["stdout"].flush()
            assert reported.wait(3), "Worker recovery must not wait for coordinator exit"
            self.options["stdout"].write('{"type":"turn.completed"}\n')
            self.returncode = 0

    monkeypatch.setattr(store.dispatch, "fail_runtime", report, raising=False)
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(execution.subprocess, "Popen", LiveProcess)
    receipt = execution.run_codex(store, document_id)
    assert failures[0][:3] == (token, "worker-1", "service_unavailable")
    assert receipt["returncode"] == 0 and receipt["error"] is None
    assert receipt["observation_errors"] == []


def test_persisted_runtime_block_stops_its_owned_coordinator(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    stopped = threading.Event()
    calls = []

    class WaitingProcess(FailedProcess):
        def communicate(self, *, input, **options):
            self.returncode = None
            assert stopped.wait(4), "Runtime block must stop an otherwise idle coordinator"
            self.returncode = -15

    def stop(process):
        calls.append(process.pid)
        stopped.set()

    monkeypatch.setattr(
        store.dispatch,
        "runtime_stop_reason",
        lambda identity: {
            "code": "quota_exhausted",
            "model": "gpt-6.1-sol",
            "scope": "account",
            "message": "Quota exhausted; results retained",
            "retry_after": None,
            "event_id": "persisted:quota",
            "evidence": {"state": "blocked"},
        },
        raising=False,
    )
    monkeypatch.setattr(execution, "stop_owned_process", stop)
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(execution.subprocess, "Popen", WaitingProcess)
    with pytest.raises(RuntimeError) as caught:
        execution.run_codex(store, document_id)
    assert caught.value.failure["code"] == "quota_exhausted"
    assert calls == [12345]


@pytest.mark.parametrize("callback", ["worker", "watchdog"])
def test_unexpected_observer_exception_is_a_durable_execution_failure(
    tmp_path, monkeypatch, callback
):
    store, document_id = prepared_store(tmp_path)
    stopped = threading.Event()
    stopped_pids = []
    token = "c" * 32

    def database_failure(*args, **kwargs):
        raise sqlite3.OperationalError("Synthetic observer database is locked")

    class ObservedProcess(FailedProcess):
        def communicate(self, *, input, **options):
            self.returncode = None
            events = [{"type": "turn.started"}]
            if callback == "worker":
                events.extend(
                    [
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "spawn",
                                "type": "collab_tool_call",
                                "tool": "spawn_agent",
                                "prompt": f"session_token={token} worker_id=worker; call open_task_packet",
                                "receiver_thread_ids": ["native"],
                                "agents_states": {},
                                "status": "completed",
                            },
                        },
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "wait",
                                "type": "collab_tool_call",
                                "tool": "wait",
                                "agents_states": {
                                    "native": {"status": "errored", "message": "Model at capacity"}
                                },
                                "status": "completed",
                            },
                        },
                    ]
                )
            for event in events:
                self.options["stdout"].write(json.dumps(event) + "\n")
            self.options["stdout"].flush()
            was_stopped = stopped.wait(1.5)
            self.returncode = -15 if was_stopped else 0

    def stop(process):
        stopped_pids.append(process.pid)
        stopped.set()

    if callback == "worker":
        monkeypatch.setattr(store.dispatch, "fail_runtime", database_failure)
    else:
        monkeypatch.setattr(store.dispatch, "runtime_stop_reason", database_failure)
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(execution.subprocess, "Popen", ObservedProcess)
    monkeypatch.setattr(execution, "stop_owned_process", stop)
    with pytest.raises(execution.RuntimeFailure) as caught:
        execution.run_codex(store, document_id)
    assert caught.value.failure["code"] == "tool_error"
    assert caught.value.failure["reason"] == "observer_failed"
    assert caught.value.failure["evidence"]["exception_type"] == "OperationalError"
    assert stopped_pids == [12345]
    logs = next((store.root / "runs" / document_id).iterdir())
    receipt = json.loads((logs / "receipt.json").read_text("utf-8"))
    assert receipt["error"]["code"] == "tool_error"
    assert receipt["observation_errors"][0]["type"] == "OperationalError"
    assert (logs / "events.jsonl").read_text("utf-8").startswith('{"type":')
    assert store.human_queue() == []
