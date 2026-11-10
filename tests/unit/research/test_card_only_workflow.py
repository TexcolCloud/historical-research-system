"""Post-publication v6 cards use direct independent review, not compulsory research."""

from copy import deepcopy

import pytest

from hrs.processing.errors import EvidenceMismatch
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.policy import POLICY
from tests.unit.research.test_complete_workflow import (
    TEXT,
    draft,
    observation,
    read,
    source,
)

V6_POLICY = {**deepcopy(POLICY), "version": 6, "dossier_cards": False}


@pytest.fixture(autouse=True)
def retain_v6_policy(monkeypatch):
    monkeypatch.setattr("hrs.research.policy.POLICY", V6_POLICY)


def card_draft(book="book-a"):
    card = draft(book)
    card.pop("research_plan")
    card.pop("question_findings", None)
    for unit in card["coverage"]:
        unit["no_mentions_reason"] = unit["no_events_reason"] = unit["no_relations_reason"] = ""
    return card


def author(workflow, book="book-a", card=None):
    run = workflow.enqueue(source(book), _policy=V6_POLICY)
    packet = workflow.claim(run["run_id"], "author-" + book, stage="author")
    read(workflow, packet)
    workflow.submit(
        packet["task_token"],
        packet["worker_id"],
        {
            "input_hash": packet["input_hash"],
            "cards": [card or card_draft(book)],
        },
    )
    return run


def review(workflow, run, *, book="book-a", stage="verify", decision="approve", worker="verifier"):
    packet = workflow.claim(run["run_id"], worker, stage=stage)
    read(workflow, packet)
    candidate = workflow.candidate(packet["task_token"], worker, observation(book))
    assessments = [
        {
            "finding_id": identity,
            "status": "covered",
            "candidate_ids": ["master/" + identity],
            "reason": "The synthetic source and candidate agree.",
        }
        for identity in ("sender", "recipient", "letter")
    ]
    return workflow.submit(
        packet["task_token"],
        worker,
        {
            "input_hash": packet["input_hash"],
            "decision": decision,
            **candidate["required_checks"],
            "coverage_assessments": assessments,
            "issues": ["A source interpretation remains disputed."]
            if decision == "pending"
            else [],
        },
    )


def test_default_cards_go_directly_from_author_to_source_first_verification(tmp_path):
    assert V6_POLICY["version"] == 6 and V6_POLICY["card_only"]
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = author(workflow)
    status = workflow.status(run["run_id"])
    assert {task["stage"] for task in status["tasks"]} == {"author", "verify"}
    assert workflow.claim(run["run_id"], "no-enricher", stage="enrich") is None
    packet = workflow.claim(run["run_id"], "verifier", stage="verify")
    assert "research_plan" not in packet["instructions"]["complete_research"]
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.candidate(packet["task_token"], "verifier", observation())
    read(workflow, packet)
    candidate = workflow.candidate(packet["task_token"], "verifier", observation())
    assert "checked_research_question_ids" not in candidate["required_checks"]
    assert "checked_question_finding_ids" not in candidate["required_checks"]
    final = workflow.submit(
        packet["task_token"],
        "verifier",
        {
            "input_hash": packet["input_hash"],
            "decision": "approve",
            **candidate["required_checks"],
            "coverage_assessments": [
                {
                    "finding_id": identity,
                    "status": "covered",
                    "candidate_ids": ["master/" + identity],
                    "reason": "The synthetic source and candidate agree.",
                }
                for identity in ("sender", "recipient", "letter")
            ],
        },
    )
    assert final["state"] == "completed"
    card = workflow.card(final["card_ids"][0])
    assert card["card_only"] and card["processing_mode"] == "source_cards"
    assert card["research_plan"] == card["question_findings"] == []
    assert card["state"] == "verified" and card["approval_type"] == "native"


def test_v5_frozen_policy_still_creates_enrichment_and_requires_its_plan(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    old = deepcopy(POLICY)
    old.update(version=5, card_only=False, enrichment_search_required=True)
    old["stages"] = {
        stage: {"model": old["model"], "reasoning_effort": old["reasoning_effort"]}
        for stage in ("author", "enrich", "verify", "repair", "reverify")
    }
    run = workflow.enqueue(source(), _policy=old)
    packet = workflow.claim(run["run_id"], "legacy-author", stage="author")
    read(workflow, packet)
    with pytest.raises(ValueError, match="research_plan"):
        workflow.submit(
            packet["task_token"],
            "legacy-author",
            {
                "input_hash": packet["input_hash"],
                "cards": [card_draft()],
            },
        )
    workflow.submit(
        packet["task_token"],
        "legacy-author",
        {
            "input_hash": packet["input_hash"],
            "cards": [draft()],
        },
    )
    assert workflow.claim(run["run_id"], "legacy-enricher", stage="enrich")


def test_one_delta_repair_then_fresh_reverification_retains_final_human_pending(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = author(workflow)
    first = review(workflow, run, decision="pending", worker="first-review")
    assert first["state"] == "running"
    repair = workflow.claim(run["run_id"], "repairer", stage="repair")
    assert repair["update_mode"] == "delta_preferred"
    assert repair["write_contract"] == "CardOnlyIncrementalSubmission"
    read(workflow, repair)
    workflow.submit(
        repair["task_token"],
        "repairer",
        {
            "mode": "delta",
            "input_hash": repair["input_hash"],
            "base_sha256": repair["base_sha256"],
            "changes": [
                {
                    "op": "replace",
                    "path": "/cards/0/summary",
                    "value": "The synthetic letter reports a request, not proof of payment.",
                }
            ],
        },
    )
    assert workflow.claim(run["run_id"], "first-review", stage="reverify") is None
    final = review(workflow, run, stage="reverify", decision="pending", worker="fresh-review")
    assert final["state"] == "pending"
    assert {task["stage"] for task in final["tasks"]} == {"author", "verify", "repair", "reverify"}
    assert workflow.claim(run["run_id"], "another-repairer", stage="repair") is None
    card = workflow.card(final["card_ids"][0])
    assert card["state"] == "pending" and card["approval_type"] is None
    assert card["summary"].startswith("The synthetic letter reports a request")


def test_optional_author_retrieval_needs_no_plan_but_citations_require_both_source_reads(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    packet = workflow.claim(run["run_id"], "author", stage="author")
    read(workflow, packet)
    workflow.prepare_search(packet["task_token"], "clarify the source")
    receipt = workflow.record_search(
        packet["task_token"],
        {
            "query": "clarify the source",
            "backend": "hybrid",
            "available": True,
            "hits": [],
            "metrics": {},
        },
    )
    index = workflow.add_source(packet["task_token"], source("book-b"))[0]
    card = card_draft()
    card["evidence"].append(
        {"id": "external", "article_id": index["id"], "start": 0, "end": len(TEXT), "quote": TEXT}
    )
    result = {"input_hash": packet["input_hash"], "cards": [card]}
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.submit(packet["task_token"], "author", result)
    workflow.read_source(packet["task_token"], article_id=index["id"])
    workflow.submit(packet["task_token"], "author", result)
    verify = workflow.claim(run["run_id"], "verifier", stage="verify")
    with pytest.raises(ValueError, match="Only enrichment"):
        workflow.prepare_search(verify["task_token"], "not permitted")
    workflow.read_source(verify["task_token"])
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.candidate(verify["task_token"], "verifier", observation())
    workflow.read_source(verify["task_token"], article_id=index["id"])
    candidate = workflow.candidate(verify["task_token"], "verifier", observation())
    assert candidate["required_checks"]["checked_search_receipt_ids"] == [receipt["receipt_id"]]
    assert len(candidate["research_audit"]["receipts"]) == 1
    assert all(
        not key.startswith("checked_research_question") for key in candidate["required_checks"]
    )


def test_document_entity_survives_publication_and_structured_revision(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    card = card_draft()
    start = TEXT.index("函")
    card["mentions"].append(
        {
            "id": "doc",
            "kind": "document",
            "name": "函",
            "identity_key": "document:letter",
            "identity_basis": "The synthetic source explicitly identifies a letter.",
            "evidence_ids": ["e1"],
            "locations": [
                {"article_id": "article-book-a", "start": start, "end": start + 1, "quote": "函"}
            ],
        }
    )
    card["coverage"][1]["mention_ids"].append("doc")
    run = author(workflow, card=card)
    final = review(workflow, run)
    original = workflow.card(final["card_ids"][0])
    documents = [
        entry for entry in workflow.catalogue_entities()["items"] if entry["kind"] == "document"
    ]
    assert len(documents) == 1 and documents[0]["name"] == "函"
    edited = workflow.card_draft(original["id"])
    assert edited["mentions"][-1]["kind"] == "document"
    edited["summary"] = "A human-retained revision of this synthetic source."
    revision = workflow.card_revision(original["id"], original["version"], edited)
    assert revision["card_only"] and revision["policy"]["max_content_repairs"] == 0
    assert workflow.card(original["id"]) == original
    new = review(workflow, revision, worker="revision-review")
    assert new["state"] == "completed"
    updated = workflow.card(new["card_ids"][0])
    assert updated["summary"] == edited["summary"] and updated["replaces_card_id"] == original["id"]
    assert updated["card_only"] and updated["processing_mode"] == "source_cards"


def test_card_only_identity_proposals_cannot_perform_a_global_merge(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    packet = workflow.claim(run["run_id"], "author", stage="author")
    read(workflow, packet)
    card = card_draft()
    card["identity_links"] = [
        {
            "local_mention_id": "sender",
            "entity_id": "entity-not-an-approval",
            "relation": "same_as",
            "evidence_ids": ["e1"],
            "reason": "This candidate cannot make a global human decision.",
        }
    ]
    with pytest.raises(EvidenceMismatch, match="explicit local human decision"):
        workflow.submit(
            packet["task_token"],
            "author",
            {
                "input_hash": packet["input_hash"],
                "cards": [card],
            },
        )


def test_possible_identity_lead_keeps_two_canonical_entities_until_explicit_human_merge(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    first_run = author(workflow)
    first = review(workflow, first_run, worker="first-review")
    first_card = workflow.card(first["card_ids"][0])
    target = next(item for item in workflow.catalogue_entities()["items"] if item["name"] == "张三")
    run = workflow.enqueue(source("book-b"))
    packet = workflow.claim(run["run_id"], "second-author", stage="author")
    read(workflow, packet)
    index = workflow.add_source(packet["task_token"], source())[0]
    workflow.read_source(packet["task_token"], article_id=index["id"])
    card = card_draft("book-b")
    card["evidence"].append(
        {"id": "external", "article_id": index["id"], "start": 0, "end": len(TEXT), "quote": TEXT}
    )
    card["identity_links"] = [
        {
            "local_mention_id": "sender",
            "entity_id": target["id"],
            "relation": "possible_same_as",
            "evidence_ids": ["e1", "external"],
            "reason": "Source-backed lead, not a global identity decision.",
        }
    ]
    workflow.submit(
        packet["task_token"],
        packet["worker_id"],
        {
            "input_hash": packet["input_hash"],
            "cards": [card],
        },
    )
    second = review(workflow, run, book="book-b", worker="second-review")
    second_card = workflow.card(second["card_ids"][0])
    people = [item for item in workflow.catalogue_entities()["items"] if item["name"] == "张三"]
    assert len(people) == 2 and people[0]["id"] != people[1]["id"]
    merged = workflow.identity_decision(
        people[0]["id"],
        "merge",
        people[0]["version"],
        target_id=people[1]["id"],
        target_version=people[1]["version"],
        evidence_refs=[
            {
                "card_id": first_card["id"],
                "card_version": first_card["version"],
                "evidence_id": "e1",
            },
            {
                "card_id": second_card["id"],
                "card_version": second_card["version"],
                "evidence_id": "e1",
            },
        ],
    )
    assert len(merged["source_mentions"]) == 2
