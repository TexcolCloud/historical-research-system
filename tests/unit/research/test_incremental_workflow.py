"""Incremental native writes retain the same complete candidate and verification gates."""

import pytest
from pydantic import ValidationError

from hrs.processing.errors import EvidenceMismatch
from hrs.processing.hashing import digest
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from tests.unit.research.test_complete_workflow import draft, read, source, submit_author, verify
from tests.unit.research.test_workflow import legacy_policy

POLICY = legacy_policy(5)


@pytest.fixture(autouse=True)
def frozen_v5_policy(monkeypatch):
    monkeypatch.setattr("hrs.processing.research_workflow.POLICY", POLICY)


def prepared(workflow, *, incremental=True, missing_event=False, version=5):
    policy = {**POLICY, "incremental_updates": incremental}
    policy["version"] = version
    if version < 5:
        policy["complete_research"] = False
    if incremental is None:
        policy.pop("incremental_updates")
    run = workflow.enqueue(source(), _policy=policy)
    workflow.start_execution(run["run_id"], "native-fixture")
    submit_author(workflow, run, draft(event=not missing_event))
    packet = workflow.claim(run["run_id"], "enricher", stage="enrich")
    read(workflow, packet)
    card = draft(event=not missing_event)
    workflow.record_plan(packet["task_token"], "enricher", card["research_plan"])
    receipt = workflow.record_search(
        packet["task_token"],
        {
            "query": "张三 李四 经费",
            "backend": "hybrid",
            "available": True,
            "hits": [],
            "metrics": {},
        },
    )
    card["research_plan"][0].update(
        status="answered",
        search_receipt_ids=[receipt["receipt_id"]],
        evidence_ids=["e1"],
        evaluation="原文仅能证明申请。",
        stop_reason="现有证据不足以推出实际拨付。",
    )
    workflow.record_plan(packet["task_token"], "enricher", card["research_plan"])
    return run, packet, card


def delta(packet, card):
    return {
        "mode": "delta",
        "input_hash": packet["input_hash"],
        "base_sha256": packet["base_sha256"],
        "changes": [
            {"op": "replace", "path": "/cards/0/research_plan", "value": card["research_plan"]}
        ],
    }


def test_new_incremental_claim_and_full_delta_materializations_are_identical(tmp_path):
    candidates = []
    checks = []
    for mode in ("full", "delta"):
        workflow = ResearchWorkflow(Store(tmp_path / mode))
        run, packet, card = prepared(workflow)
        assert packet["update_mode"] == "delta_preferred"
        assert packet["base_sha256"] == digest(packet["cards"])
        result = (
            {"input_hash": packet["input_hash"], "cards": [card]}
            if mode == "full"
            else delta(packet, card)
        )
        if mode == "delta":
            result["changes"].extend(
                [
                    {"op": "replace", "path": "/cards/0/no_argument_reason", "value": None},
                    {"op": "add", "path": "/cards/0/tags/-", "value": "temporary"},
                    {"op": "remove", "path": "/cards/0/tags/0"},
                ]
            )
        workflow.submit(packet["task_token"], "enricher", result)
        with workflow.ledger.connect() as connection:
            task = next(
                t for t in workflow._tasks(connection, run["run_id"]) if t["stage"] == "verify"
            )
        candidates.append(workflow._task_cards(task))
        checks.append(workflow._verification_checks(candidates[-1], complete=True))
        final = verify(workflow, run)
        assert final["state"] == "completed"
        if mode == "delta":
            with workflow.ledger.connect() as connection:
                enriched = next(
                    t for t in workflow._tasks(connection, run["run_id"]) if t["stage"] == "enrich"
                )
                audit = enriched["materializations"][0]
                assert (
                    workflow._load_source(connection, audit["raw_delta_ref"])["submission"]
                    == result
                )
                assert (
                    workflow._load_source(connection, audit["canonical_ref"])["cards"]
                    == candidates[-1]
                )
            assert audit["base_sha256"] == packet["base_sha256"]
            assert audit["canonical_sha256"] == digest(candidates[-1])
            assert audit["native_agent_id"] == "native-enricher"
    assert candidates[0] == candidates[1]
    assert checks[0] == checks[1]


def test_failed_delta_keeps_frozen_baseline_and_all_writes_atomic(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, packet, card = prepared(workflow)
    broken = delta(packet, card)
    broken["changes"].append({"op": "replace", "path": "/cards/0/id", "value": "other-card"})
    with pytest.raises(ValueError):
        workflow.submit(packet["task_token"], "enricher", broken)
    with workflow.ledger.connect() as connection:
        task = next(t for t in workflow._tasks(connection, run["run_id"]) if t["stage"] == "enrich")
        assert workflow._task_cards(task) == packet["cards"]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM research_sources WHERE kind='native_card_delta'"
            ).fetchone()[0]
            == 0
        )
    assert task["state"] == "leased" and not task.get("materializations")
    stale = delta(packet, card)
    stale["base_sha256"] = "0" * 64
    with pytest.raises(EvidenceMismatch):
        workflow.submit(packet["task_token"], "enricher", stale)
    workflow.submit(packet["task_token"], "enricher", delta(packet, card))
    assert verify(workflow, run)["state"] == "completed"


@pytest.mark.parametrize("incremental,version", [(None, 5), (False, 5), (None, 4)])
def test_retained_policies_without_flag_keep_full_wire_compatibility(
    tmp_path, incremental, version
):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, packet, card = prepared(workflow, incremental=incremental, version=version)
    assert "base_sha256" not in packet and "update_mode" not in packet
    assert packet["result_schema"]["title"] == (
        "CompleteEnrichedDraftSubmission" if version == 5 else "DraftSubmission"
    )
    with pytest.raises(ValidationError):
        workflow.submit(
            packet["task_token"],
            "enricher",
            {
                "mode": "delta",
                "input_hash": packet["input_hash"],
                "base_sha256": digest(packet["cards"]),
                "changes": [
                    {
                        "op": "replace",
                        "path": "/cards/0/research_plan",
                        "value": card["research_plan"],
                    }
                ],
            },
        )
    workflow.submit(
        packet["task_token"], "enricher", {"input_hash": packet["input_hash"], "cards": [card]}
    )
    assert verify(workflow, run)["state"] == "completed"


def test_delta_still_requires_full_source_reads_and_valid_decoded_citations(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    workflow.start_execution(run["run_id"], "native-fixture")
    submit_author(workflow, run, draft())
    packet = workflow.claim(run["run_id"], "enricher", stage="enrich")
    workflow.bind_native_worker(run["run_id"], "enricher", "native-enricher", "native-fixture")
    with pytest.raises(EvidenceMismatch, match="Read"):
        workflow.submit(packet["task_token"], "enricher", delta(packet, draft()))
    read(workflow, packet)
    outside = delta(packet, draft())
    outside["changes"] = [
        {"op": "replace", "path": "/cards/0/evidence/0/article_id", "value": "outside-task"}
    ]
    with pytest.raises(EvidenceMismatch, match="outside"):
        workflow.record_submission_rejection(
            packet["task_token"], "enricher", outside, {"message": "invalid source"}
        )
    workflow2 = ResearchWorkflow(Store(tmp_path / "quotes"))
    run2, packet2, card2 = prepared(workflow2)
    changed = delta(packet2, card2)
    changed["changes"].append(
        {"op": "replace", "path": "/cards/0/evidence/0/quote", "value": "fabricated"}
    )
    with pytest.raises(EvidenceMismatch, match="quot"):
        workflow2.submit(packet2["task_token"], "enricher", changed)
    with workflow2.ledger.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM research_sources WHERE kind='native_card_delta'"
            ).fetchone()[0]
            == 0
        )


def test_delta_failure_audit_is_not_the_next_baseline(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, packet, card = prepared(workflow)
    invalid = delta(packet, card)
    invalid["changes"].append({"op": "remove", "path": "/cards/0/metadata"})
    with pytest.raises(ValidationError):
        workflow.submit(packet["task_token"], "enricher", invalid)
    receipt = workflow.record_submission_rejection(
        packet["task_token"], "enricher", invalid, {"message": "missing metadata"}
    )
    assert receipt["recorded"] and receipt["activity_renewed"]
    with workflow.ledger.connect() as connection:
        task = next(t for t in workflow._tasks(connection, run["run_id"]) if t["stage"] == "enrich")
    assert workflow._task_cards(task) == packet["cards"]
    workflow.submit(packet["task_token"], "enricher", delta(packet, card))
    assert verify(workflow, run)["state"] == "completed"


def test_delta_wrapper_omission_retains_disposition_and_explicit_null_clears_it(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, packet, card = prepared(workflow)
    disposition = {
        "mode": "not_needed",
        "category": "no_named_entities_or_external_claims",
        "reason": "Explicit synthetic wrapper used solely to test retained delta semantics.",
        "evidence_ids": ["master/e1"],
    }
    with workflow.ledger.connect(write=True) as connection:
        task, stored = workflow._lease(connection, packet["task_token"], "enricher")
        task["research_disposition"] = disposition
        workflow._save_task(connection, task)
    preserved, _ = workflow._materialize_submission(task, stored, delta(packet, card))
    cleared, _ = workflow._materialize_submission(
        task, stored, {**delta(packet, card), "research_disposition": None}
    )
    assert (
        preserved["research_disposition"] == disposition and cleared["research_disposition"] is None
    )


def test_repair_delta_still_materializes_every_event_and_requires_fresh_reverification(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, packet, incomplete = prepared(workflow, missing_event=True)
    workflow.submit(packet["task_token"], "enricher", delta(packet, incomplete))
    assert verify(workflow, run, decision="pending")["state"] == "running"
    repair = workflow.claim(run["run_id"], "repairer", stage="repair")
    read(workflow, repair)
    fixed = draft()
    fixed["research_plan"] = incomplete["research_plan"]
    workflow.record_plan(repair["task_token"], "repairer", fixed["research_plan"])
    patch = {
        "mode": "delta",
        "input_hash": repair["input_hash"],
        "base_sha256": repair["base_sha256"],
        "changes": [
            {"op": "replace", "path": "/cards/0/events", "value": fixed["events"]},
            {"op": "replace", "path": "/cards/0/coverage", "value": fixed["coverage"]},
        ],
    }
    workflow.submit(repair["task_token"], "repairer", patch)
    assert workflow.claim(run["run_id"], "verifier", stage="reverify") is None
    final = verify(workflow, run, stage="reverify", worker="fresh-verifier")
    assert final["state"] == "completed"
    card = workflow.card(final["card_ids"][0])
    assert len(card["events"]) == 1 and card["events"][0]["id"] == "letter"


def test_author_remains_full_only_with_incremental_policy(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    packet = workflow.claim(run["run_id"], "author", stage="author")
    read(workflow, packet)
    assert packet["result_schema"]["title"] == "CompleteDraftSubmission"
    assert "base_sha256" not in packet
    with pytest.raises(ValidationError):
        workflow.submit(
            packet["task_token"],
            "author",
            {
                "mode": "delta",
                "input_hash": packet["input_hash"],
                "base_sha256": "0" * 64,
                "changes": [{"op": "add", "path": "/cards/0/summary", "value": "should not work"}],
            },
        )
