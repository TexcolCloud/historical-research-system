"""Card-only extraction keeps source coverage without requiring a research project."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from hrs.processing.errors import EvidenceMismatch
from hrs.research import models
from hrs.research.complete_contracts import (
    complete_checks,
    validate_assessments,
    validate_complete_cards,
    validate_inventory,
)
from tests.unit.research.test_complete_workflow import draft, source

POLICY = {"version": 6, "card_only": True, "complete_research": True, "incremental_updates": True}


def card_only():
    card = draft()
    card["research_plan"] = []
    for value in card["metadata"].values():
        if isinstance(value, dict) and value["certainty"] == "unknown":
            value["note"] = ""
    for unit in card["coverage"]:
        unit["no_relations_reason"] = ""
    return card


def test_card_only_submission_preserves_metadata_and_coverage_without_research_requirements():
    model = models.submission_model(POLICY, "author")
    assert model.__name__ == "CardOnlyDraftSubmission"
    submission = model.model_validate({"input_hash": "a" * 64, "cards": [card_only()]})
    card = submission.model_dump()["cards"][0]
    validate_complete_cards([card], source()["articles"][0], stage="author", card_only=True)
    assert card["metadata"]["publication"]["certainty"] == "unknown"
    assert card["research_plan"] == card["question_findings"] == []
    assert models.submission_model(POLICY, "repair").__name__ == "CardOnlyIncrementalSubmission"


def test_v6_observation_supports_important_facts_and_documents_without_changing_old_enums():
    article = source()["articles"][0]
    findings = [
        {
            "id": kind,
            "kind": kind,
            "article_id": article["id"],
            "start": 0,
            "end": len(article["text"]),
            "quote": article["text"],
            "description": "原文的重要事实或文献。",
        }
        for kind in ("fact", "document")
    ]
    raw = {
        "article_id": article["id"],
        "text_sha256": article["text_sha256"],
        "summary": "源文所述申请函。",
        "inventory": {
            "coverage": [
                {"start": part["start"], "end": part["end"], "summary": "逐段阅读。"}
                for part in article["parts"]
            ],
            "findings": findings,
        },
    }
    observation = models.observation_model(POLICY).model_validate(raw).model_dump()
    inventory = validate_inventory(observation, article, card_only=True)
    assert [item["kind"] for item in inventory["findings"]] == ["fact", "document"]
    assert models.submission_model(POLICY, "verify").__name__ == "CardOnlyVerificationSubmission"
    for version in range(1, 6):
        assert (
            models.observation_model({**POLICY, "version": version})
            is models.IndependentObservation
        )
        with pytest.raises(ValidationError):
            models.observation_model({**POLICY, "version": version}).model_validate(raw)


def finding(kind, quote, *, identity="finding", event_status="unknown"):
    article = source()["articles"][0]
    start = article["text"].index(quote)
    return {
        "id": identity,
        "kind": kind,
        "article_id": article["id"],
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "description": quote,
        "required": True,
        "event_status": event_status,
    }


def assessment(finding_id, candidate_id):
    return {
        "finding_id": finding_id,
        "status": "covered",
        "candidate_ids": [candidate_id],
        "reason": "重要事实保留在具有同篇精确引文的卡片字段。",
    }


def test_v6_important_facts_can_be_retained_in_items_metadata_and_dates():
    card = card_only()
    card["items"] = [
        {"id": "request-fact", "kind": "evidence", "text": "申请经费", "evidence_ids": ["e1"]}
    ]
    card["formation_date"] = {
        "text": "1939年2月",
        "certainty": "stated",
        "evidence_ids": ["e1"],
    }
    findings = [
        finding("fact", "申请经费", identity="request"),
        finding("fact", "张三", identity="author"),
        finding("fact", "1939年2月", identity="date"),
    ]
    assessments = [
        assessment("request", "master/request-fact"),
        assessment("author", "master/metadata/authors"),
        assessment("date", "master/formation_date"),
    ]
    validate_assessments(
        assessments, {"findings": findings}, [card], decision="approve", card_only=True
    )
    with pytest.raises(EvidenceMismatch, match="matching source-backed"):
        validate_assessments(assessments, {"findings": findings}, [card], decision="approve")
    broken = deepcopy(assessments)
    broken[1]["candidate_ids"] = ["master/metadata/publication"]
    with pytest.raises(EvidenceMismatch, match="matching source-backed"):
        validate_assessments(
            broken, {"findings": findings}, [card], decision="approve", card_only=True
        )


def alias_card():
    text = "张三（字仲明）致函李四，讨论经费。"
    alias_text = "张三（字仲明）"
    article = {"id": "alias-source", "text": text, "parts": [{"start": 0, "end": len(text)}]}
    card = models.CardOnlyCardDraft.model_validate(
        {
            "id": "master",
            "article_id": article["id"],
            "title": "讨论经费函",
            "summary": "张三致函李四讨论经费。",
            "metadata": models.DocumentMetadata().model_dump(),
            "evidence": [
                {
                    "id": "alias-span",
                    "article_id": article["id"],
                    "start": 0,
                    "end": len(alias_text),
                    "quote": alias_text,
                },
                {
                    "id": "other-span",
                    "article_id": article["id"],
                    "start": len(alias_text),
                    "end": len(text),
                    "quote": text[len(alias_text) :],
                },
            ],
            "mentions": [
                {
                    "id": "person",
                    "kind": "person",
                    "name": "张三",
                    "identity_key": "alias-source:张三",
                    "identity_basis": "原文署名及字。",
                    "evidence_ids": ["alias-span"],
                    "aliases": [{"name": "仲明", "evidence_ids": ["alias-span"]}],
                    "locations": [
                        {"article_id": article["id"], "start": 0, "end": 2, "quote": "张三"}
                    ],
                }
            ],
            "coverage": [
                {
                    "id": "whole",
                    "article_id": article["id"],
                    "start": 0,
                    "end": len(text),
                    "summary": "姓名与经费事项。",
                    "mention_ids": ["person"],
                }
            ],
        }
    ).model_dump()
    independent = {
        "id": "alias-finding",
        "kind": "relation",
        "article_id": article["id"],
        "start": 0,
        "end": len(alias_text),
        "quote": alias_text,
        "description": "张三字仲明。",
        "event_status": "unknown",
        "required": True,
        "relation_kind": "alias",
    }
    return article, card, independent


def test_v6_evidenced_alias_covers_its_source_relationship_without_a_duplicate_edge():
    article, card, independent = alias_card()
    validate_complete_cards([card], article, stage="author", card_only=True)
    assessments = [assessment("alias-finding", "master/person")]
    validate_assessments(
        assessments, {"findings": [independent]}, [card], decision="approve", card_only=True
    )
    assert card["relations"] == []
    with pytest.raises(EvidenceMismatch, match="matching source-backed"):
        validate_assessments(assessments, {"findings": [independent]}, [card], decision="approve")
    for mutation in ("missing", "unrelated", "wrong_name", "other_article", "wrong_quote"):
        broken = deepcopy(card)
        if mutation == "missing":
            broken["mentions"][0]["aliases"] = []
        elif mutation == "unrelated":
            broken["mentions"][0]["aliases"][0]["evidence_ids"] = ["other-span"]
        elif mutation == "wrong_name":
            broken["mentions"][0]["aliases"][0]["name"] = "李四"
        elif mutation == "other_article":
            broken["evidence"][0]["article_id"] = "another-source"
        else:
            broken["evidence"][0]["quote"] = "张三（字别名）"
        with pytest.raises(EvidenceMismatch, match="matching source-backed"):
            validate_assessments(
                assessments,
                {"findings": [independent]},
                [broken],
                decision="approve",
                card_only=True,
            )


def test_a_different_relation_in_the_same_quotation_cannot_be_covered_by_an_alias():
    _, card, independent = alias_card()
    independent["relation_kind"] = "other"
    with pytest.raises(EvidenceMismatch, match="matching source-backed"):
        validate_assessments(
            [assessment("alias-finding", "master/person")],
            {"findings": [independent]},
            [card],
            decision="approve",
            card_only=True,
        )
    del independent["relation_kind"]
    with pytest.raises(EvidenceMismatch, match="matching source-backed"):
        validate_assessments(
            [assessment("alias-finding", "master/person")],
            {"findings": [independent]},
            [card],
            decision="approve",
            card_only=True,
        )


def test_v6_retains_exact_source_quotes_event_modality_and_required_findings():
    article = source()["articles"][0]
    observed = {
        "inventory": {
            "coverage": [
                {"start": part["start"], "end": part["end"], "summary": "逐段源文。"}
                for part in article["parts"]
            ],
            "findings": [finding("event", "申请经费", event_status="planned")],
        }
    }
    inventory = validate_inventory(observed, article, card_only=True)
    with pytest.raises(EvidenceMismatch, match="modality"):
        validate_assessments(
            [assessment("finding", "master/letter")],
            inventory,
            [card_only()],
            decision="approve",
            card_only=True,
        )
    with pytest.raises(EvidenceMismatch, match="matching source-backed"):
        validate_assessments(
            [assessment("finding", "master/metadata/authors")],
            inventory,
            [card_only()],
            decision="approve",
            card_only=True,
        )
    with pytest.raises(EvidenceMismatch, match="silently omitted"):
        validate_assessments(
            [
                {
                    "finding_id": "finding",
                    "status": "not_applicable",
                    "candidate_ids": [],
                    "reason": "卡片未保留。",
                }
            ],
            inventory,
            [card_only()],
            decision="approve",
            card_only=True,
        )
    observed["inventory"]["findings"][0]["quote"] = "已拨经费"
    with pytest.raises(EvidenceMismatch, match="exactly match"):
        validate_inventory(observed, article, card_only=True)
    incomplete = card_only()
    incomplete["coverage"].pop()
    with pytest.raises(EvidenceMismatch, match="every complete source part"):
        validate_complete_cards([incomplete], article, stage="author", card_only=True)
    incomplete = card_only()
    incomplete["events"][0]["participant_roles"].pop()
    with pytest.raises(EvidenceMismatch, match="exactly one source-backed role"):
        validate_complete_cards([incomplete], article, stage="author", card_only=True)


def test_v6_document_mentions_are_source_located_and_the_old_card_enum_is_frozen():
    article, card, _ = alias_card()
    card["mentions"][0]["kind"] = "document"
    validated = models.card_model(POLICY).model_validate(card).model_dump()
    validate_complete_cards([validated], article, stage="author", card_only=True)
    for version in range(1, 6):
        assert models.card_model({**POLICY, "version": version}) is models.CardDraft
        with pytest.raises(ValidationError):
            models.card_model({**POLICY, "version": version}).model_validate(card)
    validated["mentions"][0]["locations"][0]["quote"] = "错引"
    with pytest.raises(EvidenceMismatch, match="exactly locate"):
        validate_complete_cards([validated], article, stage="author", card_only=True)


def test_v5_research_contract_cannot_be_relaxed_by_a_card_only_flag():
    policy = {**POLICY, "version": 5}
    assert not models.is_card_only(policy)
    assert models.submission_model(policy, "author") is models.CompleteDraftSubmission
    with pytest.raises(ValidationError):
        models.submission_model(policy, "author").model_validate(
            {"input_hash": "a" * 64, "cards": [card_only()]}
        )
    with pytest.raises(EvidenceMismatch, match="Unknown metadata"):
        validate_complete_cards([card_only()], source()["articles"][0], stage="author")
    legacy = draft()
    legacy["research_plan"] = []
    with pytest.raises(EvidenceMismatch, match="research_plan"):
        validate_complete_cards([legacy], source()["articles"][0], stage="author")
    legacy = draft()
    legacy["coverage"][0]["no_relations_reason"] = ""
    with pytest.raises(EvidenceMismatch, match="empty extraction category"):
        validate_complete_cards([legacy], source()["articles"][0], stage="author")
    legacy_checks = complete_checks([draft()])
    card_checks = complete_checks([card_only()], card_only=True)
    assert legacy_checks["checked_research_question_ids"] == ["master/payment"]
    assert "checked_research_question_ids" not in card_checks
    assert "checked_question_finding_ids" not in card_checks
    assert card_checks["checked_metadata_fields"] == legacy_checks["checked_metadata_fields"]
    assert card_checks["checked_event_ids"] == ["master/letter"]


def test_only_old_policies_can_use_their_frozen_legacy_submission_schemas():
    for stage, name in (
        ("author", "DraftSubmission"),
        ("repair", "CompleteEnrichedDraftSubmission"),
    ):
        old = models.submission_model({**POLICY, "version": 5}, stage, frozen_name=name)
        assert old.__name__ == name
        with pytest.raises(ValueError, match="does not match"):
            models.submission_model(POLICY, stage, frozen_name=name)
