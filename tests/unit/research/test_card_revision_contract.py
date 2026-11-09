"""Synthetic UI payload contracts; these checks make no historical accuracy claim."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from hrs.processing.errors import EvidenceMismatch
from hrs.research.complete_contracts import validate_complete_cards
from hrs.research.models import CardOnlyCardDraft

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "web/src/features/research/card-revision-contract.fixture.json"
)


def revision_payload(case):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    card = deepcopy(fixture["initial"])
    if case in {"document", "missing-coverage"}:
        card["mentions"].append(fixture["document"])
        if case == "document":
            card["coverage"] = fixture["expected_coverage"]
    elif case == "event":
        card["events"].append(fixture["new_event"])
        card["coverage"][0]["event_ids"].append(fixture["new_event"]["id"])
    elif case in {"delete", "unknown-empty"}:
        for collection in ("mentions", "events", "relations"):
            card[collection] = []
        for unit in card["coverage"]:
            for field in ("mention_ids", "event_ids", "relation_ids"):
                unit[field] = []
        if case == "unknown-empty":
            for value in card["metadata"].values():
                if isinstance(value, dict) and value["certainty"] == "unknown":
                    value["note"] = ""
    return CardOnlyCardDraft.model_validate(card).model_dump(), fixture["article"]


@pytest.mark.parametrize("case", ["initial", "document", "event", "delete", "unknown-empty"])
def test_complete_v6_ui_revision_payload_matches_backend_contract(case):
    card, article = revision_payload(case)
    validate_complete_cards([card], article, stage="verify", card_only=True)


def test_backend_still_rejects_the_pre_fix_ui_payload_missing_object_coverage():
    card, article = revision_payload("missing-coverage")
    with pytest.raises(EvidenceMismatch, match="requires a source coverage unit"):
        validate_complete_cards([card], article, stage="verify", card_only=True)
