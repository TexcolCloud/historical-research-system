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
    assert retained["proposal"] == proposal and retained["accepted"] is False
    duplicate = workflow.record_submission_rejection(
        packet["task_token"], "verifier", proposal, audit
    )
    assert duplicate["activity_renewed"] is False
    assert workflow.list_cards() == [] and workflow.status(run["run_id"])["counts"]["leased"] == 1
