"""A native worker failure must not discard another article's live work."""

import json
import time
from copy import deepcopy

import pytest

from hrs.codex.research_tools import rejection_diagnostics
from hrs.processing.errors import EvidenceMismatch
from hrs.processing.hashing import digest
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.complete_contracts import validate_assessments
from hrs.research.execution import ResearchEvents
from tests.unit.research.test_complete_workflow import (
    draft,
    enrich,
    observation,
    read,
    source,
    submit_author,
)


def workers(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    snapshot = source()
    second = deepcopy(snapshot["articles"][0])
    second["id"] = "article-second"
    snapshot["articles"].append(second)
    run = workflow.enqueue(snapshot)
    workflow.start_execution(run["run_id"], "execution")
    observer = ResearchEvents(workflow, run["run_id"], "gpt-6.1-sol", tmp_path / "execution")
    packets = []
    for index in (1, 2):
        worker = f"worker-{index}"
        observer._collaboration(
            {
                "tool": "spawn_agent",
                "prompt": f"worker_id='{worker}'",
                "receiver_thread_ids": [f"native-{index}"],
                "agents_states": {f"native-{index}": {"status": "running"}},
            },
            {},
        )
        packets.append(workflow.claim(run["run_id"], worker, stage="author"))
    return workflow, run["run_id"], observer, packets


def test_terminal_child_without_submission_retries_only_its_lease(tmp_path):
    workflow, run, observer, packets = workers(tmp_path)
    observer._collaboration(
        {"tool": "wait", "agents_states": {"native-1": {"status": "completed"}}}, {}
    )
    status = workflow.status(run)
    failed = next(task for task in status["tasks"] if task["task_id"] == packets[0]["task_id"])
    healthy = next(task for task in status["tasks"] if task["task_id"] == packets[1]["task_id"])
    assert failed["state"] == "ready" and failed["technical_failures"] == 1
    assert failed["last_failure"]["code"] == "tool_error"
    assert healthy["state"] == "leased"
    with workflow.ledger.connect() as connection:
        live, _ = workflow._lease(connection, packets[1]["task_token"], "worker-2")
        assert live["task_id"] == packets[1]["task_id"]
    assert observer.active_workers() == {"worker-2"}
    assert status.get("runtime_failures", 0) == 0 and workflow.list_cards() == []
    observer._collaboration(
        {"tool": "wait", "agents_states": {"native-1": {"status": "pending_init"}}}, {}
    )
    assert observer.active_workers() == {"worker-2"}


def test_host_expires_one_exact_lease_even_after_deadline_without_touching_replacement(tmp_path):
    workflow, run, observer, packets = workers(tmp_path)
    with workflow.ledger.connect(write=True) as connection:
        task, _ = workflow._lease(connection, packets[0]["task_token"])
        task.update(claimed_at=time.time() - 6000, expires_at=time.time() - 1)
        workflow._save_task(connection, task)
    expired = workflow.supervision_timeouts(run, active_workers=observer.active_workers())
    assert len(expired) == 1
    before = workflow.status(run)["tasks"]
    observer.retire_timeouts(expired, before)
    after = workflow.status(run)
    assert after["counts"]["leased"] == 1 and after["counts"]["ready"] == 1
    assert after.get("runtime_failures", 0) == 0
    replacement = workflow.claim(run, "fresh-worker", stage="author")
    assert replacement["task_token"] != packets[0]["task_token"]
    observer.retire_timeouts(expired, before)
    assert workflow.status(run)["counts"]["leased"] == 2
    assert digest(replacement["task_token"]) != expired[0]["lease_fingerprint"]
    assert observer.states["native-1"] == "timed_out"


def test_successful_send_input_transient_interruption_keeps_only_started_generation_budget(
    tmp_path, monkeypatch
):
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    workflow, run, observer, packets = workers(tmp_path)
    workflow.read_source(packets[0]["task_token"], 0, 32000)
    before = next(t for t in workflow.status(run)["tasks"] if t["worker_id"] == "worker-1")
    clock[0] += 700
    observer._collaboration(
        {
            "tool": "send_input",
            "status": "completed",
            "receiver_thread_ids": ["native-1"],
            "agents_states": {"native-1": {"status": "interrupted"}},
        },
        {},
    )
    assert observer.active_workers() == {"worker-1", "worker-2"}
    task = next(t for t in workflow.status(run)["tasks"] if t["worker_id"] == "worker-1")
    assert task["last_progress_at"] == before["last_progress_at"]
    assert task["claimed_at"] == before["claimed_at"]
    assert workflow.supervision_timeouts(run, active_workers=observer.active_workers()) == []
    assert workflow.renew_run_leases(run, active_workers=observer.active_workers()) == 2
    with workflow.ledger.connect(write=True) as connection:
        task, _ = workflow._lease(connection, packets[0]["task_token"])
        task["claimed_at"] = clock[0] - 6000
        workflow._save_task(connection, task)
    expired = workflow.supervision_timeouts(run, active_workers=observer.active_workers())
    assert len(expired) == 1 and "absolute task deadline" in expired[0]["reason"]
    workflow.pause(run)
    assert workflow.renew_run_leases(run, active_workers=observer.active_workers()) == 0


@pytest.mark.parametrize("tool,call_state", [("wait", "completed"), ("send_input", "failed")])
def test_unrelated_interruption_is_not_a_continuing_native_turn(tmp_path, tool, call_state):
    workflow, run, observer, packets = workers(tmp_path)
    workflow.read_source(packets[0]["task_token"], 0, 32000)
    observer._collaboration(
        {
            "tool": tool,
            "status": call_state,
            "receiver_thread_ids": ["native-1"],
            "agents_states": {"native-1": {"status": "interrupted"}},
        },
        {},
    )
    assert observer.active_workers() == {"worker-2"}


def test_send_input_pending_snapshot_requires_actual_reads_from_this_execution(tmp_path):
    workflow, run, observer, packets = workers(tmp_path)
    observer.states["native-1"] = "pending_init"
    sent = {
        "tool": "send_input",
        "status": "completed",
        "receiver_thread_ids": ["native-1"],
        "agents_states": {"native-1": {"status": "interrupted"}},
    }
    observer._collaboration(sent, {})
    assert observer.states["native-1"] == "pending_init"
    assert observer.active_workers() == {"worker-2"}
    workflow.read_source(packets[0]["task_token"], 0, 32000)
    assert observer.active_workers() == {"worker-1", "worker-2"}
    workflow.pause(run)
    assert observer.active_workers() == {"worker-2"}
    assert workflow.renew_run_leases(run, active_workers=observer.active_workers()) == 0


@pytest.mark.parametrize("terminal", ["completed", "timed_out", "shutdown"])
def test_send_input_cannot_revive_a_retired_native_worker(tmp_path, terminal):
    workflow, run, observer, packets = workers(tmp_path)
    workflow.read_source(packets[0]["task_token"], 0, 32000)
    observer.states["native-1"] = terminal
    observer._collaboration(
        {
            "tool": "send_input",
            "status": "completed",
            "receiver_thread_ids": ["native-1"],
            "agents_states": {"native-1": {"status": "interrupted"}},
        },
        {},
    )
    assert observer.states["native-1"] == terminal
    assert observer.active_workers() == {"worker-2"}


def test_successful_close_retires_preclose_running_snapshot_without_global_failure(tmp_path):
    workflow, run, observer, packets = workers(tmp_path)
    observer._collaboration(
        {
            "tool": "close_agent",
            "status": "completed",
            "receiver_thread_ids": ["native-1"],
            "agents_states": {"native-1": {"status": "running"}},
        },
        {},
    )
    assert observer.states["native-1"] == "shutdown"
    assert observer.active_workers() == {"worker-2"}
    status = workflow.status(run)
    assert status["counts"]["ready"] == 1 and status["counts"]["leased"] == 1
    assert status.get("runtime_failures", 0) == 0


def test_source_finding_mapping_errors_name_all_wrong_positions_without_weakening_gate():
    inventory = observation()["inventory"]
    assessments = [
        {
            "finding_id": item["id"],
            "status": "covered",
            "candidate_ids": [identity],
            "reason": "check",
        }
        for item, identity in zip(
            inventory["findings"], ["master/letter", "recipient", "master/i-results"]
        )
    ]
    with pytest.raises(EvidenceMismatch) as caught:
        validate_assessments(assessments, inventory, [draft()], decision="approve")
    audit, reply = rejection_diagnostics(caught.value)
    assert audit["issue_count"] == 3
    assert [issue["path"] for issue in audit["issues"]] == [
        ["coverage_assessments", index, "candidate_ids", 0] for index in range(3)
    ]
    assert "finding sender" in audit["issues"][0]["message"]
    assert "master/letter" in audit["issues"][0]["message"]
    assert "mark the finding missing" in audit["issues"][0]["message"]
    assert len(json.dumps(reply)) < 5000


def test_rejected_verification_keeps_paths_and_never_renews_without_full_candidate(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    card = draft()
    submit_author(workflow, run, card)
    enrich(workflow, run, card)
    workflow.start_execution(run["run_id"], "execution")
    packet = workflow.claim(run["run_id"], "verifier", stage="verify")
    read(workflow, packet)
    proposal = {"input_hash": packet["input_hash"], "decision": "approve"}
    with pytest.raises(EvidenceMismatch, match="candidate"):
        workflow.record_submission_rejection(packet["task_token"], "verifier", proposal, {})
    candidate = workflow.candidate(packet["task_token"], "verifier", observation())
    proposal.update(candidate["required_checks"])
    proposal.update(
        coverage_assessments=[
            {
                "finding_id": item["id"],
                "status": "covered",
                "candidate_ids": ["master/letter"],
                "reason": "check",
            }
            for item in observation()["inventory"]["findings"]
        ],
        issues=[],
    )
    with pytest.raises(EvidenceMismatch) as caught:
        workflow.submit(packet["task_token"], "verifier", proposal)
    audit, _ = rejection_diagnostics(caught.value)
    receipt = workflow.record_submission_rejection(
        packet["task_token"], "verifier", proposal, audit
    )
    assert receipt["recorded"] and receipt["activity_renewed"]
    with workflow.ledger.connect() as connection:
        retained = workflow._load_source(connection, receipt["rejection_ref"])
    assert retained["error"]["issues"] == audit["issues"]
    assert retained["error"]["message"] == audit["issues"][0]["message"]
    assert retained["proposal"] == proposal and retained["accepted"] is False
    duplicate = workflow.record_submission_rejection(
        packet["task_token"], "verifier", proposal, audit
    )
    assert duplicate["activity_renewed"] is False
    assert workflow.list_cards() == [] and workflow.status(run["run_id"])["counts"]["leased"] == 1
