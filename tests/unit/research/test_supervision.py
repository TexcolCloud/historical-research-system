"""Generation budgets use native identities, source sizes and immutable claim deadlines."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from hrs.processing.errors import EvidenceMismatch, LeaseConflict, WorkflowStopped
from hrs.processing.research_workflow import POLICY, ResearchWorkflow
from hrs.processing.store import Store
from hrs.research import execution
from hrs.research.supervision import task_limits
from tests.unit.research.test_complete_workflow import draft, source
from tests.unit.research.test_workflow import legacy_policy


@pytest.mark.parametrize("frozen_fallback", [False, True])
def test_watchdog_and_renewal_share_generation_and_absolute_limits(
    tmp_path, monkeypatch, frozen_fallback
):
    clock = [execution.time.time()]
    monkeypatch.setattr(execution.time, "time", lambda: clock[0])
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    policy = deepcopy(POLICY)
    if frozen_fallback:
        policy.pop("supervision")
    run = workflow.enqueue(source(), _policy=policy)
    workflow.start_execution(run["run_id"], "execution-new")
    # Queueing time is not generation time.
    with workflow.ledger.connect(write=True) as connection:
        task = workflow._tasks(connection, run["run_id"])[0]
        task["created_at"] -= 20000
        workflow._save_task(connection, task)
    packet = workflow.claim(run["run_id"], "worker")
    workflow.bind_native_worker(run["run_id"], "worker", "native-worker", "execution-new")
    active = {"worker"}
    clock[0] += 700
    assert workflow.supervision_timeouts(run["run_id"], active_workers=active) == []
    assert workflow.renew_run_leases(run["run_id"], active_workers=active) == 1
    assert "600 seconds" in workflow.supervision_timeouts(run["run_id"])[0]["reason"]
    assert workflow.renew_run_leases(run["run_id"], active_workers={"invented"}) == 0
    with workflow.ledger.connect(write=True) as connection:
        task, stored = workflow._lease(connection, packet["task_token"])
        limits = task_limits(stored["policy"], task, 100)
        clock[0] = task["claimed_at"] + limits["absolute_seconds"] + 1
        task["last_progress_at"] = clock[0]  # Tool traffic cannot defeat the absolute bound.
        task["expires_at"] = clock[0] + 900
        workflow._save_task(connection, task)
    assert (
        "absolute task deadline"
        in workflow.supervision_timeouts(run["run_id"], active_workers=active)[0]["reason"]
    )
    assert workflow.renew_run_leases(run["run_id"], active_workers=active) == 0


def test_generation_budget_scales_with_frozen_source_but_has_a_hard_cap():
    task = {"stage": "author"}
    assert (
        task_limits(POLICY, task, 5000)["generation_seconds"]
        > task_limits(POLICY, task, 100)["generation_seconds"]
    )
    assert task_limits(POLICY, task, 10_000_000)["generation_seconds"] == 3600
    assert task_limits({**POLICY, "version": 4}, task, 10_000_000)["generation_seconds"] == 600


def test_frozen_v5_claim_start_is_recovered_from_its_event_without_policy_mutation(
    tmp_path, monkeypatch
):
    clock = [execution.time.time()]
    monkeypatch.setattr(execution.time, "time", lambda: clock[0])
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    policy = deepcopy(POLICY)
    policy.pop("supervision")
    run = workflow.enqueue(source(), _policy=policy)
    workflow.start_execution(run["run_id"], "execution-v5-old")
    packet = workflow.claim(run["run_id"], "worker")
    workflow.bind_native_worker(run["run_id"], "worker", "native-worker", "execution-v5-old")
    with workflow.ledger.connect(write=True) as connection:
        task, _ = workflow._lease(connection, packet["task_token"])
        task.pop("claimed_at")
        task["created_at"] -= 20000
        workflow._save_task(connection, task)
    clock[0] += 700
    assert workflow.supervision_timeouts(run["run_id"], active_workers={"worker"}) == []
    assert workflow.renew_run_leases(run["run_id"], active_workers={"worker"}) == 1
    assert "supervision" not in workflow.status(run["run_id"])["policy"]


def test_observer_old_execution_and_pending_init_do_not_extend_new_generation(
    tmp_path, monkeypatch
):
    clock = [execution.time.time()]
    monkeypatch.setattr(execution.time, "time", lambda: clock[0])
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    workflow.start_execution(run["run_id"], "execution-new")
    workflow.claim(run["run_id"], "worker")
    with workflow.ledger.connect(write=True) as connection:
        connection.execute(
            "INSERT INTO research_workers VALUES (?,?,?,?)",
            (run["run_id"], "worker", "old-native", "execution-old"),
        )
    clock[0] += 700
    assert (
        "600 seconds"
        in workflow.supervision_timeouts(run["run_id"], active_workers={"worker"})[0]["reason"]
    )
    assert workflow.renew_run_leases(run["run_id"], active_workers={"worker"}) == 0
    observer = execution.ResearchEvents(
        workflow, run["run_id"], "gpt-6.1-sol", tmp_path / "execution-new"
    )
    observer.workers["native"] = "worker"
    observer.states["native"] = "pending_init"
    assert observer.active_workers() == set()


@pytest.mark.parametrize("terminal", ["completed", "errored"])
def test_pending_init_with_successful_reads_is_running_during_unfinished_parent_wait(
    tmp_path, monkeypatch, terminal
):
    clock = [execution.time.time()]
    monkeypatch.setattr(execution.time, "time", lambda: clock[0])
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    workflow.start_execution(run["run_id"], "execution-read")
    packet = workflow.claim(run["run_id"], "reader", paged=True)
    observer = execution.ResearchEvents(
        workflow, run["run_id"], "gpt-6.1-sol", tmp_path / "execution-read"
    )
    observer.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "prompt": "worker_id='reader'",
                    "receiver_thread_ids": ["native-reader"],
                    "agents_states": {"native-reader": {"status": "pending_init"}},
                },
            }
        )
    )
    assert observer.active_workers() == set()
    offset = 0
    while True:
        window = workflow.packet_text(packet["task_token"], "reader", offset, 2000)
        offset = window["end"]
        if offset == window["total"]:
            break
    workflow.read_source(packet["task_token"], 0, 1000)
    observer.feed(
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "wait",
                    "receiver_thread_ids": ["native-reader"],
                },
            }
        )
    )
    clock[0] += 700
    assert observer.states["native-reader"] == "pending_init"
    assert observer.active_workers() == {"reader"}
    assert (
        workflow.supervision_timeouts(run["run_id"], active_workers=observer.active_workers()) == []
    )
    assert workflow.renew_run_leases(run["run_id"], active_workers=observer.active_workers()) == 1
    with workflow.ledger.connect() as connection:
        task = workflow._tasks(connection, run["run_id"])[0]
    assert task["expires_at"] == clock[0] + 900
    older = execution.ResearchEvents(
        workflow, run["run_id"], "gpt-6.1-sol", tmp_path / "execution-older"
    )
    older.workers, older.states = dict(observer.workers), dict(observer.states)
    assert older.active_workers() == set()
    observer.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "wait",
                    "agents_states": {"native-reader": {"status": terminal}},
                },
            }
        )
    )
    assert observer.active_workers() == set()
    assert workflow.renew_run_leases(run["run_id"], active_workers=observer.active_workers()) == 0

    observer.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "wait",
                    "agents_states": {"native-reader": {"status": "pending_init"}},
                },
            }
        )
    )
    assert observer.states["native-reader"] == terminal
    assert observer.active_workers() == set()


def test_unstarted_pending_init_is_not_extended_by_host_binding_alone(tmp_path, monkeypatch):
    clock = [execution.time.time()]
    monkeypatch.setattr(execution.time, "time", lambda: clock[0])
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    workflow.start_execution(run["run_id"], "execution-unstarted")
    workflow.claim(run["run_id"], "unstarted")
    observer = execution.ResearchEvents(
        workflow, run["run_id"], "gpt-6.1-sol", tmp_path / "execution-unstarted"
    )
    observer.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "prompt": "worker_id='unstarted'",
                    "receiver_thread_ids": ["native-unstarted"],
                    "agents_states": {"native-unstarted": {"status": "pending_init"}},
                },
            }
        )
    )
    clock[0] += 700
    assert observer.active_workers() == set()
    assert (
        "600 seconds"
        in workflow.supervision_timeouts(run["run_id"], active_workers=observer.active_workers())[
            0
        ]["reason"]
    )
    assert workflow.renew_run_leases(run["run_id"], active_workers=observer.active_workers()) == 0


def test_changed_rejected_proposal_is_activity_without_approval_or_repeated_payload_heartbeat(
    tmp_path, monkeypatch
):
    clock = [execution.time.time()]
    monkeypatch.setattr(execution.time, "time", lambda: clock[0])
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source(), _policy=legacy_policy(5))
    workflow.start_execution(run["run_id"], "rejection-execution")
    packet = workflow.claim(run["run_id"], "writer", stage="author")
    workflow.bind_native_worker(run["run_id"], "writer", "native-writer", "rejection-execution")
    workflow.read_source(packet["task_token"], 0, 1000)
    clock[0] += 700
    assert workflow.renew_run_leases(run["run_id"], active_workers={"writer"}) == 1
    clock[0] += 650
    assert workflow.supervision_timeouts(run["run_id"], active_workers={"writer"})
    broken = draft()
    broken.pop("research_plan")
    proposal = {"input_hash": packet["input_hash"], "cards": [broken]}
    with pytest.raises(ValidationError) as error:
        workflow.submit(packet["task_token"], "writer", proposal)
    receipt = workflow.record_submission_rejection(
        packet["task_token"],
        "writer",
        proposal,
        {
            "type": "ValidationError",
            "message": str(error.value),
            "chain_of_thought": "do not retain",
        },
    )
    assert receipt["recorded"] and receipt["activity_renewed"]
    assert workflow.supervision_timeouts(run["run_id"], active_workers={"writer"}) == []
    with workflow.ledger.connect() as connection:
        task = workflow._tasks(connection, run["run_id"])[0]
        retained = workflow._load_source(connection, receipt["rejection_ref"])
    original_claim = task["claimed_at"]
    assert retained["proposal"] == proposal and retained["accepted"] is False
    assert "chain_of_thought" not in retained["error"]
    progress = task["last_progress_at"]
    clock[0] += 100
    repeated = workflow.record_submission_rejection(
        packet["task_token"], "writer", deepcopy(proposal), {"message": "same payload"}
    )
    assert not repeated["recorded"] and not repeated["activity_renewed"]
    with workflow.ledger.connect() as connection:
        task = workflow._tasks(connection, run["run_id"])[0]
    assert task["last_progress_at"] == progress and task["claimed_at"] == original_claim
    changed = deepcopy(proposal)
    changed["cards"][0]["summary"] += "仍缺后续结果。"
    with pytest.raises(ValidationError):
        workflow.submit(packet["task_token"], "writer", changed)
    updated = workflow.record_submission_rejection(
        packet["task_token"], "writer", changed, {"message": "still missing plan"}
    )
    assert updated["recorded"] and updated["activity_renewed"]
    assert updated["rejection_ref"] != receipt["rejection_ref"]
    clock[0] += 100
    assert not workflow.record_submission_rejection(
        packet["task_token"], "writer", proposal, {"message": "old payload again"}
    )["activity_renewed"]
    assert workflow.status(run["run_id"])["counts"]["completed"] == 0
    assert workflow.list_cards(run_id=run["run_id"]) == []


def test_rejected_proposal_activity_requires_current_identity_hash_and_read_sources(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    workflow.start_execution(run["run_id"], "rejection-execution")
    packet = workflow.claim(run["run_id"], "writer", stage="author")
    result = {"input_hash": packet["input_hash"], "cards": [draft()]}
    with pytest.raises(LeaseConflict, match="attestation"):
        workflow.record_submission_rejection(
            packet["task_token"], "writer", result, {"message": "rejected"}
        )
    workflow.bind_native_worker(run["run_id"], "writer", "native-writer", "rejection-execution")
    with pytest.raises(EvidenceMismatch, match="Read"):
        workflow.record_submission_rejection(
            packet["task_token"], "writer", result, {"message": "rejected"}
        )
    workflow.read_source(packet["task_token"], 0, 1000)
    with pytest.raises(LeaseConflict):
        workflow.record_submission_rejection(
            packet["task_token"], "impostor", result, {"message": "rejected"}
        )
    with pytest.raises(EvidenceMismatch, match="input hash"):
        workflow.record_submission_rejection(
            packet["task_token"],
            "writer",
            {**result, "input_hash": "wrong"},
            {"message": "rejected"},
        )
    foreign = deepcopy(result)
    foreign["cards"][0]["evidence"][0]["article_id"] = "outside-task"
    with pytest.raises(EvidenceMismatch, match="outside"):
        workflow.record_submission_rejection(
            packet["task_token"], "writer", foreign, {"message": "rejected"}
        )
    workflow.pause(run["run_id"])
    with pytest.raises(LeaseConflict):
        workflow.record_submission_rejection(
            packet["task_token"], "writer", result, {"message": "rejected"}
        )


def test_rejection_audits_cannot_reset_the_absolute_claim_deadline(tmp_path, monkeypatch):
    clock = [execution.time.time()]
    monkeypatch.setattr(execution.time, "time", lambda: clock[0])
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    workflow.start_execution(run["run_id"], "rejection-deadline")
    packet = workflow.claim(run["run_id"], "writer", stage="author")
    workflow.bind_native_worker(run["run_id"], "writer", "native-writer", "rejection-deadline")
    workflow.read_source(packet["task_token"], 0, 1000)
    claimed = clock[0]
    for version in range(7):
        clock[0] += 700
        proposal = {
            "input_hash": packet["input_hash"],
            "cards": [draft()],
            "unsupported_version": version,
        }
        assert workflow.record_submission_rejection(
            packet["task_token"], "writer", proposal, {"message": "unknown field"}
        )["recorded"]
    clock[0] = claimed + 5401
    with pytest.raises(WorkflowStopped, match="absolute task deadline"):
        workflow.record_submission_rejection(
            packet["task_token"],
            "writer",
            {**proposal, "unsupported_version": 99},
            {"message": "unknown field"},
        )
