"""Provider outages exercise the real Store/dispatch/lease recovery seam."""

from copy import deepcopy

import pytest
from test_store import complete, document

from hrs_codex.execution_policy import configure_execution
from hrs_codex.runtime_control import SOL, TERRA


@pytest.fixture
def clock(monkeypatch):
    value = [100000.0]
    monkeypatch.setattr("hrs_codex.runtime_control.time.time", lambda: value[0])
    return value


def test_account_probe_dispatches_concrete_ocr_before_source_only_warm_review(tmp_path, clock):
    store, identity = document(tmp_path, 4)
    configure_execution(store.root, capacity=14, overlap_reading=True)
    failure = store.runtime_control.failure("429", "account-outage", scope="account")
    assert not store.dispatch.reserve(identity)["assignments"]
    clock[0] = failure["retry_at"] + 1
    assignments = store.dispatch.reserve(identity)["assignments"]
    assert [assignment["stage"] for assignment in assignments] == ["ocr"]
    token = assignments[0]["session_token"]
    session = store.dispatch.session(token)
    assert session["task_id"] and not session.get("prewarmed")
    assert store.runtime_control.snapshot()["account"]["probe_owner"] == token
    task, _, _ = store.dispatch.open(token, "probe-transcriber")
    assert task["stage"] == "ocr"
    assert store.checkpoints.ledger.capacity_used() == 1


def test_sol_capacity_failure_keeps_terra_ocr_admission_available(tmp_path, clock):
    store, identity = document(tmp_path, 12)
    configure_execution(store.root, capacity=14, overlap_reading=True)
    store.runtime_control.failure("capacity", "sol-outage", model=SOL)
    assignments = store.dispatch.reserve(identity)["assignments"]
    assert assignments and all(assignment["stage"] == "ocr" for assignment in assignments)
    assert store.runtime_control.snapshot()["account"]["state"] == "normal"
    task, _, _ = store.dispatch.open(assignments[0]["session_token"], "terra-still-working")
    assert task["claim"]["actual_model"] == TERRA
    assert store.status(identity)["pages"][task["page"] - 1]["state"] == "awaiting_ocr"


@pytest.mark.parametrize("first", ["mcp", "observer"])
def test_mcp_and_observer_failure_replays_share_one_lease_failure(tmp_path, clock, first):
    store, identity = document(tmp_path, 4)
    configure_execution(store.root, capacity=4, overlap_reading=False)
    assignment = next(
        assignment
        for assignment in store.dispatch.reserve(identity)["assignments"]
        if assignment["stage"] == "ocr"
    )
    token = assignment["session_token"]
    task, _, _ = store.dispatch.open(token, "failed-transcriber")

    def mcp():
        return store.report_task_failure(identity, task["task_id"], task["lease_token"], "429")

    def observer():
        return store.dispatch.fail_runtime(
            token,
            "failed-transcriber",
            "rate_limit",
            details={"scope": "model", "model": TERRA, "event_id": "observed-outage"},
        )

    calls = (mcp, observer) if first == "mcp" else (observer, mcp)
    calls[0]()
    first_snapshot = store.runtime_control.snapshot()
    calls[1]()
    observer()
    mcp()
    snapshot = store.runtime_control.snapshot()
    assert snapshot == first_snapshot
    pressured = [scope for scope in snapshot["scopes"] if scope["state"] != "normal"]
    assert len(pressured) == 1 and pressured[0]["failures"] == 1
    assert pressured[0]["limit"] == (1 if first == "observer" else 2)
    retained = next(t for t in store.status(identity)["tasks"] if t["task_id"] == task["task_id"])
    assert len(retained["technical_failures"]) == 1 and retained["state"] == "ready"
    assert all(page["state"] == "awaiting_ocr" for page in store.status(identity)["pages"])
    assert store.dispatch.session(token, allow_closed=True)["state"] == "exited"


def test_three_task_failures_stop_until_manual_recovery_and_preserve_completed_pages(
    tmp_path, clock
):
    store, identity = document(tmp_path, 2)
    complete(
        store, identity, "ocr", "retained-ocr", {"markdown": "Retained[^1]\n\n[^1]: Source note."}
    )
    complete(store, identity, "review", "retained-review", {"verdict": "approved", "issues": []})
    before = store.status(identity)
    retained_page = deepcopy(before["pages"][0])
    retained_tasks = {
        task["task_id"]: deepcopy(task) for task in before["tasks"] if task["state"] == "completed"
    }
    leases = []
    for attempt in range(3):
        task = store.claim_task(identity, "ocr", f"failed-attempt-{attempt}")
        assert task and task["page"] == 2
        leases.append(task["lease_token"])
        failure = store.report_task_failure(
            identity, task["task_id"], task["lease_token"], "timeout"
        )
        assert failure["technical_blocked"] is (attempt == 2)
        clock[0] = failure["retry_after"] + 1
    assert store.claim_task(identity, "ocr", "fourth-without-recovery") is None
    blocked = store.status(identity)
    assert blocked["pages"][0] == retained_page
    assert blocked["pages"][1]["state"] == "awaiting_ocr"
    assert not any(task["stage"] == "human_review" for task in blocked["tasks"])
    assert store.dispatch.runtime_stop_reason(identity)
    receipt = store.recover_run(identity, "manual-after-task-budget")
    assert receipt["evidence_checked"] and receipt["retry_limit"] == 3
    recovered = store.status(identity)
    assert recovered["pages"][0] == retained_page
    assert all(
        task == retained_tasks[task["task_id"]]
        for task in recovered["tasks"]
        if task["task_id"] in retained_tasks
    )
    task = store.claim_task(identity, "ocr", "manual-recovered-worker")
    assert task and task["lease_token"] not in leases
    assert task["technical_retry_since"] == 3
    failure = store.report_task_failure(identity, task["task_id"], task["lease_token"], "timeout")
    assert not failure["technical_blocked"]
