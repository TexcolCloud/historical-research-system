"""Dossier cards bind interpretation and citation fields without upgrading old records."""

import pytest
from pydantic import ValidationError

from hrs.processing.errors import EvidenceMismatch
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research import models
from hrs.research.complete_contracts import complete_checks
from hrs.research.policy import POLICY
from tests.unit.research.test_card_only_contracts import card_only
from tests.unit.research.test_complete_workflow import observation, read, source

DOSSIER_FIELDS = (
    "research_scope",
    "formation_background",
    "text_structure",
    "source_nature",
    "source_role",
    "research_value",
    "can_support",
    "cannot_support",
    "needs_corroboration",
)
V7 = {
    "version": 7,
    "card_only": True,
    "dossier_cards": True,
    "complete_research": True,
    "incremental_updates": True,
}


def dossier():
    return {
        **{
            field: {
                "value": "该函涉及经费申请，不能由申请直接推定已拨付。",
                "certainty": "inferred",
                "evidence_ids": ["e1"],
                "note": "限于现有函件内容的解释。",
            }
            for field in DOSSIER_FIELDS
        },
        "argument_units": [
            {
                "id": "funding-request",
                "title": "申请与执行的区别",
                "claim": "张三致函申请经费，未载拨付结果。",
                "evidence_ids": ["e1"],
                "applicability_limit": "仅说明本文所载申请，不证明获批或实施。",
                "corroboration_needed": "若讨论执行，仍需批复或账册。",
            }
        ],
        "argument_units_note": "",
        "citation": {
            "full": {},
            "short": {},
            "printed_pages": {},
            "publication_date": {},
            "completeness": "unknown",
            "missing_fields": ["publication", "printed_pages"],
        },
    }


def draft():
    return {**card_only(), "dossier": dossier()}


def author(workflow, *, policy=None, card=None):
    run = workflow.enqueue(source(), _policy=policy)
    packet = workflow.claim(run["run_id"], "author", stage="author")
    read(workflow, packet)
    workflow.submit(
        packet["task_token"],
        "author",
        {"input_hash": packet["input_hash"], "cards": [card or draft()]},
    )
    return run, packet


def verification(workflow, run, *, stage="verify", worker="reviewer", decision="approve"):
    packet = workflow.claim(run["run_id"], worker, stage=stage)
    read(workflow, packet)
    candidate = workflow.candidate(packet["task_token"], worker, observation())
    result = {
        "input_hash": packet["input_hash"],
        "decision": decision,
        **candidate["required_checks"],
        "coverage_assessments": [
            {
                "finding_id": identity,
                "status": "covered",
                "candidate_ids": ["master/" + identity],
                "reason": "合成原文与该条内容一致。",
            }
            for identity in ("sender", "recipient", "letter")
        ],
        "issues": ["需要收紧论证的适用范围。"] if decision == "pending" else [],
    }
    return packet, candidate, result


def test_v7_uses_an_explicit_dossier_schema_and_v6_remains_unchanged():
    assert models.card_model(V7).__name__ == "DossierCardDraft"
    assert models.submission_model(V7, "author").__name__ == "DossierDraftSubmission"
    assert models.submission_model(V7, "repair").__name__ == "DossierIncrementalSubmission"
    assert models.submission_model(V7, "verify").__name__ == "DossierVerificationSubmission"
    old = {**V7, "version": 6}
    assert models.card_model(old) is models.CardOnlyCardDraft
    assert models.submission_model(old, "verify") is models.CardOnlyVerificationSubmission
    models.card_model(old).model_validate(card_only())
    with pytest.raises(ValidationError, match="dossier"):
        models.card_model(V7).model_validate(card_only())


@pytest.mark.parametrize("field", [*DOSSIER_FIELDS, "citation", "argument_units"])
def test_v7_cannot_silently_omit_a_dossier_section(field):
    card = draft()
    card["dossier"].pop(field)
    with pytest.raises(ValidationError):
        models.card_model(V7).model_validate(card)


@pytest.mark.parametrize("where", ["value", "citation", "date", "argument"])
def test_all_dossier_evidence_must_identify_real_local_spans(where):
    card = draft()
    if where == "value":
        card["dossier"]["research_value"]["evidence_ids"] = ["not-in-this-card"]
    elif where == "citation":
        card["dossier"]["citation"]["full"] = {
            "value": "伪造的书目信息",
            "certainty": "stated",
            "evidence_ids": ["absent-title-page"],
        }
    elif where == "date":
        card["dossier"]["citation"]["publication_date"] = {
            "text": "1991年",
            "normalized": "1991",
            "certainty": "stated",
            "evidence_ids": ["missing"],
        }
    else:
        card["dossier"]["argument_units"][0]["evidence_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="evidence"):
        models.card_model(V7).model_validate(card)


def test_unknown_citation_is_legal_and_cannot_claim_bibliographic_completeness():
    card = models.card_model(V7).model_validate(draft()).model_dump()
    assert card["dossier"]["citation"]["printed_pages"]["value"] == ""
    assert card["dossier"]["citation"]["publication_date"]["certainty"] == "unknown"
    card["dossier"]["citation"]["completeness"] = "complete"
    with pytest.raises(ValidationError, match="complete"):
        models.card_model(V7).model_validate(card)


def test_no_argument_units_requires_a_source_limitation_without_inventing_an_argument():
    card = draft()
    card["dossier"]["argument_units"] = []
    with pytest.raises(ValidationError, match="argument_units_note"):
        models.card_model(V7).model_validate(card)
    card["dossier"]["argument_units_note"] = "节选不足以形成独立论证单元。"
    validated = models.card_model(V7).model_validate(card).model_dump()
    checks = complete_checks([validated], card_only=True, dossier=True)["checked_dossier_fields"]
    assert "master/dossier/argument_units_note" in checks
    assert not any("funding-request" in item for item in checks)


def test_new_source_cards_freeze_v7_and_cannot_approve_an_unchecked_dossier_field(tmp_path):
    assert POLICY["version"] == 7 and POLICY["dossier_cards"]
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, author_packet = author(workflow)
    assert run["dossier_cards"] and run["policy"]["version"] == 7
    assert author_packet["write_contract"] == "DossierDraftSubmission"
    assert "dossier" in author_packet["instructions"]
    packet, candidate, result = verification(workflow, run)
    expected = candidate["required_checks"]["checked_dossier_fields"]
    assert set(f"master/dossier/{field}" for field in DOSSIER_FIELDS) <= set(expected)
    assert "master/dossier/citation/printed_pages" in expected
    assert "master/dossier/argument_units/funding-request/applicability_limit" in expected
    result["checked_dossier_fields"] = [
        field for field in expected if field != "master/dossier/citation/printed_pages"
    ]
    with pytest.raises(EvidenceMismatch, match="checked_dossier_fields"):
        workflow.submit(packet["task_token"], packet["worker_id"], result)
    assert workflow.status(run["run_id"])["card_ids"] == []
    result["checked_dossier_fields"] = expected
    final = workflow.submit(packet["task_token"], packet["worker_id"], result)
    retained = workflow.card(final["card_ids"][0])
    assert retained["state"] == "verified" and retained["dossier_cards"]
    assert retained["dossier"]["citation"]["completeness"] == "unknown"
    assert retained["checks"]["checked_dossier_fields"] == expected


def test_v7_repair_delta_retains_complete_dossier_and_requires_fresh_review(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, _ = author(workflow)
    packet, _, result = verification(workflow, run, decision="pending")
    assert workflow.submit(packet["task_token"], packet["worker_id"], result)["state"] == "running"
    repair = workflow.claim(run["run_id"], "repairer", stage="repair")
    assert repair["write_contract"] == "DossierIncrementalSubmission"
    read(workflow, repair)
    changed = "仅限于本文的经费申请表述，不代表实际拨付。"
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
                    "path": "/cards/0/dossier/argument_units/0/applicability_limit",
                    "value": changed,
                }
            ],
        },
    )
    assert repair["cards"][0]["dossier"]["argument_units"][0]["applicability_limit"] != changed
    packet, candidate, result = verification(
        workflow, run, stage="reverify", worker="fresh-reviewer"
    )
    assert candidate["cards"][0]["dossier"]["argument_units"][0]["applicability_limit"] == changed
    assert (
        candidate["cards"][0]["dossier"]["citation"]
        == models.DossierCitation.model_validate(dossier()["citation"]).model_dump()
    )
    final = workflow.submit(packet["task_token"], packet["worker_id"], result)
    assert final["state"] == "completed"
    assert {task["stage"] for task in final["tasks"]} == {"author", "verify", "repair", "reverify"}


def test_user_dossier_revision_roundtrips_every_field_and_preserves_old_card(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, _ = author(workflow)
    packet, _, result = verification(workflow, run)
    final = workflow.submit(packet["task_token"], packet["worker_id"], result)
    original = workflow.card(final["card_ids"][0])
    edited = workflow.card_draft(original["id"])
    assert edited["dossier"] == original["dossier"]
    edited["dossier"]["cannot_support"]["note"] = "人工保留的出处使用限制。"
    revision = workflow.card_revision(original["id"], original["version"], edited)
    assert revision["policy"]["version"] == 7 and revision["dossier_cards"]
    assert revision["policy"]["max_content_repairs"] == 0
    assert workflow.card(original["id"]) == original
    packet, _, result = verification(workflow, revision, worker="user-draft-reviewer")
    updated = workflow.submit(packet["task_token"], packet["worker_id"], result)
    retained = workflow.card(updated["card_ids"][0])
    assert retained["dossier"] == edited["dossier"]
    assert retained["replaces_card_id"] == original["id"]


def test_retained_v6_revision_and_delta_never_silently_upgrade_to_dossiers(tmp_path):
    from hrs.processing.hashing import digest
    from hrs.research.card_updates import apply_card_changes

    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    policy = {**POLICY, "version": 6, "dossier_cards": False}
    run, packet = author(workflow, policy=policy, card=card_only())
    assert packet["write_contract"] == "CardOnlyDraftSubmission"
    packet, candidate, result = verification(workflow, run)
    assert "checked_dossier_fields" not in candidate["required_checks"]
    final = workflow.submit(packet["task_token"], packet["worker_id"], result)
    original = workflow.card(final["card_ids"][0])
    edited = workflow.card_draft(original["id"])
    assert "dossier" not in edited
    edited["summary"] = "保留v6冻结合同的人工修订。"
    revision = workflow.card_revision(original["id"], original["version"], edited)
    assert revision["policy"]["version"] == 6 and not revision["dossier_cards"]
    with pytest.raises(ValueError, match="business fields"):
        apply_card_changes(
            [edited],
            digest([edited]),
            [{"op": "add", "path": "/cards/0/dossier", "value": dossier()}],
        )
    with pytest.raises(ValueError, match="pre-v7"):
        workflow._revision_draft({**edited, "dossier": dossier()}, original, policy)


def test_unsupported_bibliography_cannot_bypass_independent_pending_and_one_repair(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    card = draft()
    card["dossier"]["citation"]["full"] = {
        "value": "A publisher claim unsupported by the synthetic letter.",
        "certainty": "stated",
        "evidence_ids": ["e1"],
        "note": "",
    }
    run, _ = author(workflow, card=card)
    packet, _, result = verification(workflow, run, decision="pending")
    result["issues"] = [
        "The source does not identify this publisher; the full citation must remain unknown or partial."
    ]
    workflow.submit(packet["task_token"], packet["worker_id"], result)
    repair = workflow.claim(run["run_id"], "repairer", stage="repair")
    read(workflow, repair)
    workflow.submit(
        repair["task_token"],
        "repairer",
        {
            "input_hash": repair["input_hash"],
            "cards": repair["cards"],
        },
    )
    packet, _, result = verification(
        workflow, run, stage="reverify", worker="fresh-reviewer", decision="pending"
    )
    result["issues"] = ["The unsupported bibliographic claim remains after the bounded repair."]
    final = workflow.submit(packet["task_token"], packet["worker_id"], result)
    assert final["state"] == "pending"
    retained = workflow.card(final["card_ids"][0])
    assert retained["state"] == "pending" and retained["approval_type"] is None
    assert workflow.claim(run["run_id"], "second-repair", stage="repair") is None


def test_v7_native_instructions_allow_source_use_units_without_restoring_paper_argument_work():
    from hrs.research.execution import role_configs

    instructions = [role["instructions"] for role in role_configs(POLICY).values()]
    assert all("Policy v7 literature dossier" in message for message in instructions)
    assert all(
        "source-specific use judgments" in message and "not answer research questions" in message
        for message in instructions
    )
    assert all("never convert ordinals into printed pages" in message for message in instructions)
    old = {**POLICY, "version": 6, "dossier_cards": False}
    assert all(
        "Policy v7 literature dossier" not in role["instructions"]
        for role in role_configs(old).values()
    )
