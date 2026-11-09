"""Generated model schemas expose the same constraints as their semantic validators."""

import pytest
from pydantic import ValidationError

from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.models import DraftSubmission, SourceValue, submission_model
from tests.unit.research.test_complete_workflow import draft, read, source
from tests.unit.research.test_workflow import legacy_policy

POLICY = legacy_policy(5)


@pytest.fixture(autouse=True)
def frozen_v5_policy(monkeypatch):
    monkeypatch.setattr("hrs.processing.research_workflow.POLICY", POLICY)


def test_submission_schema_explains_unknown_values_without_relaxing_the_validator():
    schema = DraftSubmission.model_json_schema()["$defs"]["SourceValue"]
    assert "certainty=unknown" in schema.get("description", "")
    assert "value=''" in schema["properties"]["value"]["description"]
    assert "missing facts" in schema["properties"]["note"]["description"]
    assert set(schema["properties"]["certainty"]["enum"]) == {
        "stated",
        "inferred",
        "unknown",
        "disputed",
    }
    with pytest.raises(ValidationError, match="Unknown metadata"):
        SourceValue(value="此间", certainty="unknown", note="具体地点未明")


def test_v5_submit_reports_missing_locations_and_plan_in_one_structural_error(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    packet = workflow.claim(run["run_id"], "complete-author", stage="author")
    read(workflow, packet)
    incomplete = draft()
    incomplete["mentions"][0].pop("locations")
    incomplete.pop("research_plan")
    with pytest.raises(ValidationError) as error:
        workflow.submit(
            packet["task_token"],
            "complete-author",
            {"input_hash": packet["input_hash"], "cards": [incomplete]},
        )
    paths = {issue["loc"] for issue in error.value.errors()}
    assert ("cards", 0, "mentions", 0, "locations") in paths
    assert ("cards", 0, "research_plan") in paths


def test_v5_claim_schema_has_the_actual_complete_write_constraints(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    schema = workflow.claim(run["run_id"], "schema-author", stage="author")["result_schema"]
    definitions = schema["$defs"]
    cards = schema["properties"]["cards"]
    assert cards["minItems"] == cards["maxItems"] == 1
    card = definitions[cards["items"]["$ref"].split("/")[-1]]
    assert {"metadata", "coverage", "research_plan"} <= set(card["required"])
    assert card["properties"]["coverage"]["minItems"] == 1
    assert card["properties"]["research_plan"]["minItems"] == 1
    mention = definitions[card["properties"]["mentions"]["items"]["$ref"].split("/")[-1]]
    assert {"identity_key", "locations"} <= set(mention["required"])
    assert mention["properties"]["locations"]["minItems"] == 1
    assert mention["properties"]["identity_key"]["minLength"] == 1
    event = definitions[card["properties"]["events"]["items"]["$ref"].split("/")[-1]]
    assert {"identity_key", "action", "status_note", "event_status"} <= set(event["required"])
    assert set(definitions[card["properties"]["metadata"]["$ref"].split("/")[-1]]["required"]) == {
        "title_origin",
        "original_title",
        "authors",
        "recipients",
        "editors",
        "publication",
        "volume",
        "archive_id",
        "formation_place",
        "purpose",
        "testimony",
    }


def test_complete_enrichment_schema_and_submit_require_evaluated_plan_together(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source(), _policy={**POLICY, "incremental_updates": False})
    author = workflow.claim(run["run_id"], "author", stage="author")
    read(workflow, author)
    workflow.submit(
        author["task_token"], "author", {"input_hash": author["input_hash"], "cards": [draft()]}
    )
    packet = workflow.claim(run["run_id"], "enrich", stage="enrich")
    schema = packet["result_schema"]
    assert schema["title"] == packet["write_contract"] == "CompleteEnrichedDraftSubmission"
    question = schema["$defs"]["EvaluatedResearchQuestion"]
    assert "planned" not in question["properties"]["status"]["enum"]
    assert {"status", "evaluation", "stop_reason"} <= set(question["required"])
    read(workflow, packet)
    with pytest.raises(ValidationError) as error:
        workflow.submit(
            packet["task_token"], "enrich", {"input_hash": packet["input_hash"], "cards": [draft()]}
        )
    paths = {issue["loc"] for issue in error.value.errors()}
    assert ("cards", 0, "research_plan", 0, "status") in paths
    assert ("cards", 0, "research_plan", 0, "evaluation") in paths
    assert ("cards", 0, "research_plan", 0, "stop_reason") in paths


def test_retained_leased_packet_keeps_its_frozen_old_structural_contract():
    assert submission_model(POLICY, "author", frozen_name="DraftSubmission") is DraftSubmission
    assert submission_model(POLICY, "author").__name__ == "CompleteDraftSubmission"
    with pytest.raises(ValueError, match="does not match"):
        submission_model(POLICY, "author", frozen_name="CompleteEnrichedDraftSubmission")


def test_v4_keeps_its_sparse_write_contract(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source(), _policy={**POLICY, "version": 4, "complete_research": False})
    packet = workflow.claim(run["run_id"], "legacy-author", stage="author")
    assert packet["result_schema"]["title"] == "DraftSubmission"
    sparse = draft()
    for field in ("metadata", "coverage", "research_plan"):
        sparse.pop(field)
    for mention in sparse["mentions"]:
        mention.pop("locations")
        mention.pop("identity_key")
    read(workflow, packet)
    after = workflow.submit(
        packet["task_token"],
        "legacy-author",
        {"input_hash": packet["input_hash"], "cards": [sparse]},
    )
    assert after["counts"]["completed"] == 1
