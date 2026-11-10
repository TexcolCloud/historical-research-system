"""One synthetic v7 fixture crosses the controlled editor and native card validation gate."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from hrs.research.complete_contracts import complete_checks, validate_complete_cards
from hrs.research.models import DossierCardDraft
from hrs.research.workspace import CardRevision

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "web/src/features/research/dossier-revision-contract.fixture.json"
)


@pytest.mark.parametrize("case", ["initial", "edited", "added-unit", "unknown"])
def test_v7_editor_retains_the_complete_dossier_and_citation_under_the_actual_card_gate(case):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    submitted = deepcopy(fixture["initial"])
    if case == "edited":
        submitted["dossier"] = fixture["edited_dossier"]
    elif case == "added-unit":
        submitted["dossier"]["argument_units"].append(fixture["new_unit"])
    elif case == "unknown":
        unknown = {"value": "", "certainty": "unknown", "evidence_ids": [], "note": ""}
        for field in submitted["dossier"].keys() - {
            "argument_units",
            "argument_units_note",
            "citation",
        }:
            submitted["dossier"][field] = deepcopy(unknown)
        submitted["dossier"].update(
            argument_units=[], argument_units_note="现有节选不足以划分独立论证单元。"
        )
        submitted["dossier"]["citation"] = {
            **{field: deepcopy(unknown) for field in ("full", "short", "printed_pages")},
            "publication_date": {
                "text": "",
                "normalized": None,
                "certainty": "unknown",
                "evidence_ids": [],
            },
            "completeness": "unknown",
            "missing_fields": [],
        }
    card = DossierCardDraft.model_validate(submitted).model_dump(mode="json")
    assert card == submitted
    request = CardRevision.model_validate({"expected_version": 2, "card": submitted})
    assert isinstance(request.card, DossierCardDraft)
    assert request.card.model_dump(mode="json") == submitted
    validate_complete_cards(
        [card], fixture["article"], stage="verify", card_only=True, dossier=True
    )
    for span in card["evidence"]:
        assert fixture["article"]["text"][span["start"] : span["end"]] == span["quote"]
    checks = complete_checks([card], card_only=True, dossier=True)
    prefix = card["id"] + "/dossier/"
    expected = {
        prefix + field
        for field in submitted["dossier"].keys()
        - {"argument_units", "argument_units_note", "citation"}
    }
    expected |= {prefix + "citation/" + field for field in submitted["dossier"]["citation"]}
    expected |= {prefix + "argument_units", prefix + "argument_units_note"}
    expected |= {
        prefix + "argument_units/" + unit["id"] + "/" + field
        for unit in card["dossier"]["argument_units"]
        for field in unit
    }
    assert set(checks["checked_dossier_fields"]) == expected
    assert "checked_research_question_ids" not in checks
    assert "checked_question_finding_ids" not in checks


@pytest.mark.parametrize("broken", ["lost-field", "foreign-evidence", "unexplained-unit-loss"])
def test_v7_card_gate_rejects_editor_payloads_that_lose_dossier_content_or_source_ownership(broken):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    card = deepcopy(fixture["initial"])
    if broken == "lost-field":
        del card["dossier"]["cannot_support"]
    elif broken == "foreign-evidence":
        card["dossier"]["argument_units"][0]["evidence_ids"] = ["not-in-this-card"]
    else:
        card["dossier"]["argument_units"] = []
    with pytest.raises(ValidationError):
        validate_complete_cards(
            [card], fixture["article"], stage="verify", card_only=True, dossier=True
        )
