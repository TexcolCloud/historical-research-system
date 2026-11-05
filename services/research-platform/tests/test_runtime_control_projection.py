"""Runtime pressure remains visible without rewriting source acceptance state."""

from types import SimpleNamespace

from hrs_platform.schemas import SchedulingReport
from hrs_platform.services.native.facts import (
    projection_input,
    projection_state,
    runtime_control_for_document,
)
from hrs_platform.services.runs.recovery import native_report


def control(state="cooldown", code="capacity"):
    return {
        "state": state,
        "scopes": [
            {
                "scope": "model",
                "model": "gpt-6.1-sol",
                "state": state,
                "code": code,
                "limit": 3,
                "configured_limit": 7,
                "retry_at": 100,
            }
        ],
        "next_retry_at": 100,
    }


def document(state="processing"):
    return {
        "id": "a" * 24,
        "state": state,
        "pages": [{"page": 1, "state": "awaiting_ocr"}],
        "tasks": [{"task_id": "task", "stage": "ocr", "state": "ready", "page": 1}],
    }


def test_schema_keeps_model_pressure_and_zero_runtime_capacity():
    report = SchedulingReport.model_validate(
        {
            "capacity": 0,
            "workers": {},
            "active": {},
            "metrics": {},
            "batch_size": 8,
            "runtime_control": control("blocked", "authentication"),
        }
    )
    assert report.runtime_control.scopes[0].code == "authentication"
    assert report.capacity == 0


def test_shared_pressure_changes_projection_only_for_unfinished_documents():
    doc = document()
    initial = projection_input(doc)
    assert projection_input(doc, control()) != initial
    assert projection_input(doc, control("probing")) != projection_input(doc, control())
    doc["state"] = "published"
    assert projection_input(doc, control()) == projection_input(doc)


def test_only_relevant_blocking_pressure_requires_manual_recovery():
    doc = document()
    doc["tasks"][0]["stage"] = "review"
    pressure = control("blocked", "quota_exhausted")
    pressure["scopes"][0]["model"] = "gpt-6.1-terra"
    scheduling = {"automatic_work_remaining": True, "runtime_control": pressure}
    assert runtime_control_for_document(doc, pressure)["state"] == "normal"
    assert projection_state({"result": {}}, doc, scheduling, [], None, None)[0] == "running"
    pressure["scopes"][0]["model"] = "gpt-6.1-sol"
    assert projection_state({"result": {}}, doc, scheduling, [], None, None)[0] == "failed"
    scheduling["technical_blocked"] = 1
    scheduling["runtime_control"] = None
    assert projection_state({"result": {}}, doc, scheduling, [], None, None)[0] == "failed"


def test_recovery_report_uses_current_blocking_scope_instead_of_generic_retry(monkeypatch, tmp_path):
    doc = document()
    doc["run_request"] = {"state": "retryable_error", "technical_failures": 1, "retry_limit": 3}
    runtime = control("blocked", "authentication")
    store = SimpleNamespace(status=lambda _: doc, runtime_control=SimpleNamespace(snapshot=lambda: runtime))
    monkeypatch.setattr("hrs_codex.store.Store", lambda _: store)
    monkeypatch.setattr("hrs_codex.workspace.resolve_workspace", lambda _: tmp_path)
    monkeypatch.setattr("hrs_codex.recovery.plan", lambda _: {"unfinished_tasks": 1})
    monkeypatch.setattr(
        "hrs_platform.services.books.get_run",
        lambda *_: {
            "result": {"codex_document_id": doc["id"]},
        },
    )
    report = native_report(SimpleNamespace(codex_root=tmp_path), None, "run")
    assert report["state"] == "blocked"
    assert report["retry_at"] is None
    assert "重新登录" in report["reason"]
    assert report["runtime_control"] == runtime
