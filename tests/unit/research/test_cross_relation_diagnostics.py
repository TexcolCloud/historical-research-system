"""Cross-card evidence IDs use the output card namespace, never counterpart-local IDs."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from hrs.codex.research_tools import rejection_diagnostics
from hrs.processing.errors import EvidenceMismatch
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.execution import role_configs
from hrs.research.models import CrossCardRelation
from hrs.research.policy import POLICY
from tests.unit.research.test_source_variants import (
    TEXT,
    complete_target,
    prepare,
    publication,
    target_text,
)


def counterpart(workflow):
    target = complete_target(workflow)
    run, packet, card = prepare(workflow, publication("primary"))
    manifest = workflow.target_card(packet["task_token"], packet["worker_id"], target["id"], 1)
    workflow.read_source(packet["task_token"], article_id=manifest["source_ids"][0])
    target_text(workflow, packet, manifest)
    card["evidence"].extend(
        [
            {
                "id": "copied-target",
                "article_id": manifest["source_ids"][0],
                "start": 0,
                "end": len(TEXT),
                "quote": TEXT,
            },
            {
                "id": "local-extra",
                "article_id": "article-primary",
                "start": 0,
                "end": len(TEXT),
                "quote": TEXT,
            },
        ]
    )
    card["cross_relations"] = [
        {
            "id": "cross",
            "target_card_id": target["id"],
            "target_card_version": 1,
            "target_publication_sha256": target["publication_sha256"],
            "kind": "background",
            "reason": "Synthetic contextual source",
            "source_evidence_ids": ["e1"],
            "target_evidence_ids": ["e1", "local-extra"],
        }
    ]
    return run, packet, card, manifest


def test_counterpart_local_id_collision_reports_every_wrong_output_source_without_remapping(
    tmp_path,
):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, manifest = counterpart(workflow)
    result = {"input_hash": packet["input_hash"], "cards": [card]}
    original = deepcopy(result)
    with pytest.raises(EvidenceMismatch) as failure:
        workflow.submit(packet["task_token"], packet["worker_id"], result)
    issues = getattr(failure.value, "validation_issues", [])
    assert [issue["loc"] for issue in issues] == [
        ("cards", 0, "cross_relations", 0, "target_evidence_ids", index) for index in (0, 1)
    ]
    for issue in issues:
        assert issue["ctx"]["relation_id"] == "cross"
        assert issue["ctx"]["target_card_id"] == manifest["card_id"]
        assert issue["ctx"]["target_card_version"] == 1
        assert issue["ctx"]["actual_source_id"] == "article-primary"
        assert issue["ctx"]["expected_source_ids"] == manifest["source_ids"]
        assert "this output card.evidence" in issue["msg"]
        assert TEXT not in issue["msg"]
    audit, reply = rejection_diagnostics(failure.value)
    assert audit["issue_count"] == reply["issue_count"] == 2
    assert manifest["source_ids"][0] in reply["issues"][0]["message"]
    assert result == original
    card["cross_relations"][0]["target_evidence_ids"] = ["copied-target"]
    card["cross_relations"][0]["source_evidence_ids"] = ["e1", "copied-target"]
    assert workflow.submit(packet["task_token"], packet["worker_id"], result)["state"] == "running"


@pytest.mark.parametrize("field", ["source_evidence_ids", "target_evidence_ids"])
def test_missing_output_evidence_id_names_relation_and_field_in_existing_model_rejection(
    tmp_path, field
):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, manifest = counterpart(workflow)
    card["cross_relations"][0][field] = ["counterpart-only"]
    result = {"input_hash": packet["input_hash"], "cards": [card]}
    with pytest.raises(ValidationError) as failure:
        workflow.submit(packet["task_token"], packet["worker_id"], result)
    message = str(failure.value)
    assert f"{field}[0]" in message
    assert "memo/cross" in message
    assert manifest["card_id"] in message
    assert "counterpart-only" in message and "this output card.evidence" in message


def test_long_native_identifiers_do_not_expand_diagnostic_or_include_source_text(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, _ = counterpart(workflow)
    long_id = "relation-" + "x" * 6000
    card["cross_relations"][0]["id"] = long_id
    with pytest.raises(EvidenceMismatch) as failure:
        workflow.submit(
            packet["task_token"],
            packet["worker_id"],
            {"input_hash": packet["input_hash"], "cards": [card]},
        )
    assert len(str(failure.value)) < 1800
    assert long_id not in str(failure.value)
    assert TEXT not in str(failure.value)


def test_foreign_only_source_side_reports_same_exact_output_namespace(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, manifest = counterpart(workflow)
    card["cross_relations"][0].update(
        source_evidence_ids=["copied-target"], target_evidence_ids=["copied-target"]
    )
    with pytest.raises(EvidenceMismatch) as failure:
        workflow.submit(
            packet["task_token"],
            packet["worker_id"],
            {"input_hash": packet["input_hash"], "cards": [card]},
        )
    issue = failure.value.validation_issues[0]
    assert issue["loc"] == ("cards", 0, "cross_relations", 0, "source_evidence_ids", 0)
    assert issue["ctx"]["actual_source_id"] == manifest["source_ids"][0]
    assert issue["ctx"]["expected_source_ids"] == ["article-primary"]


def test_cross_card_schema_describes_one_output_evidence_namespace():
    fields = CrossCardRelation.model_json_schema()["properties"]
    assert "this output card.evidence" in fields["source_evidence_ids"]["description"]
    assert "this output card.evidence" in fields["target_evidence_ids"]["description"]
    assert "counterpart" in fields["target_evidence_ids"]["description"]


def test_complete_producer_explains_local_ids_without_changing_legacy_instructions():
    phrase = (
        "Both source_evidence_ids and target_evidence_ids name IDs in this output card.evidence"
    )
    assert all(phrase in role["instructions"] for role in role_configs(POLICY).values())
    old_policy = {**POLICY, "version": 4, "complete_research": False}
    assert all(phrase not in role["instructions"] for role in role_configs(old_policy).values())


def test_repeated_invalid_references_keep_one_bounded_diagnostic_and_explicit_omissions(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, _ = counterpart(workflow)
    card["cross_relations"][0]["target_evidence_ids"] = ["e1"] * 4096
    with pytest.raises(EvidenceMismatch) as failure:
        workflow.submit(
            packet["task_token"],
            packet["worker_id"],
            {"input_hash": packet["input_hash"], "cards": [card]},
        )
    issues = failure.value.validation_issues
    assert len(issues) == 1
    assert issues[0]["ctx"]["occurrences"] == 4096
    assert issues[0]["ctx"]["reference_indices"] == list(range(8))
    assert issues[0]["ctx"]["omitted_indices"] == 4088
    assert "4096 invalid references" in issues[0]["msg"]
    assert "4088 omitted indices" in issues[0]["msg"]
    audit, reply = rejection_diagnostics(failure.value)
    assert len(str(audit)) < 5000 and len(str(reply)) < 5000


def test_invalid_reference_after_large_valid_prefix_is_still_checked(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, _ = counterpart(workflow)
    card["cross_relations"][0]["target_evidence_ids"] = ["copied-target"] * 4096 + ["e1"]
    with pytest.raises(EvidenceMismatch) as failure:
        workflow.submit(
            packet["task_token"],
            packet["worker_id"],
            {"input_hash": packet["input_hash"], "cards": [card]},
        )
    issue = failure.value.validation_issues[0]
    assert issue["loc"][-1] == 4096
    assert issue["ctx"]["bad_reference_count"] == issue["ctx"]["occurrences"] == 1
    assert issue["ctx"]["omitted_indices"] == 0


def test_source_side_still_accepts_any_primary_match_after_many_foreign_references(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, _ = counterpart(workflow)
    card["cross_relations"][0].update(
        source_evidence_ids=["copied-target"] * 4096 + ["e1"],
        target_evidence_ids=["copied-target"] * 4096,
    )
    assert (
        workflow.submit(
            packet["task_token"],
            packet["worker_id"],
            {"input_hash": packet["input_hash"], "cards": [card]},
        )["state"]
        == "running"
    )


def test_distinct_bad_references_beyond_diagnostic_cap_are_counted_not_silently_dropped(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, _ = counterpart(workflow)
    bad_ids = [f"bad-{index}" for index in range(12)]
    card["evidence"].extend({**card["evidence"][0], "id": identity} for identity in bad_ids)
    card["cross_relations"][0]["target_evidence_ids"] = bad_ids
    with pytest.raises(EvidenceMismatch) as failure:
        workflow.submit(
            packet["task_token"],
            packet["worker_id"],
            {"input_hash": packet["input_hash"], "cards": [card]},
        )
    issues = failure.value.validation_issues
    assert len(issues) == 4
    assert all(issue["ctx"]["bad_reference_count"] == 12 for issue in issues)
    assert all(issue["ctx"]["omitted_group_references"] == 8 for issue in issues)
    assert "8 further invalid references omitted" in issues[0]["msg"]


def test_long_ids_keep_counts_and_actionable_path_in_truncated_native_reply(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    _, packet, card, manifest = counterpart(workflow)
    bad_id = "evidence-" + "e" * 119
    card["evidence"].append({**card["evidence"][0], "id": bad_id})
    card["cross_relations"][0].update(
        id="relation-" + "r" * 119, target_evidence_ids=[bad_id] * 4096
    )
    assert len(manifest["source_ids"][0]) == 104
    with pytest.raises(EvidenceMismatch) as failure:
        workflow.submit(
            packet["task_token"],
            packet["worker_id"],
            {"input_hash": packet["input_hash"], "cards": [card]},
        )
    audit, reply = rejection_diagnostics(failure.value)
    message = reply["issues"][0]["message"]
    assert len(message) <= 400
    assert "4096 invalid references" in message and "4096 occurrences" in message
    assert "4088 omitted indices" in message
    assert "cards[0].cross_relations[0].target_evidence_ids[0]" in message
    assert failure.value.validation_issues[0]["ctx"]["occurrences"] == 4096
    assert manifest["source_ids"][0] in audit["issues"][0]["message"]
