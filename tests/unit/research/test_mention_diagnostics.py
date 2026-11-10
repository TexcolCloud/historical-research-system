"""Mention diagnostics locate every rejected occurrence without relaxing source containment."""

from copy import deepcopy

import pytest

from hrs.processing.errors import EvidenceMismatch
from hrs.research.complete_contracts import validate_complete_cards
from tests.unit.research.test_complete_workflow import TEXT, draft, source


def mismatched_mentions():
    card = draft()
    article = source()["articles"][0]
    for identity, name in (("recipient-only", "李四"), ("sender-only", "张三")):
        start = TEXT.index(name)
        card["evidence"].append(
            {
                "id": identity,
                "article_id": article["id"],
                "start": start,
                "end": start + len(name),
                "quote": name,
                "footnote_ids": [],
            }
        )
    card["mentions"][0]["evidence_ids"] = ["recipient-only"]
    card["mentions"][0]["locations"].append(deepcopy(card["mentions"][0]["locations"][0]))
    card["mentions"][1]["evidence_ids"] = ["sender-only"]
    return card, article


def test_every_bad_occurrence_has_exact_path_ids_and_cited_evidence_bounds():
    card, article = mismatched_mentions()
    retained = deepcopy((card, article))
    with pytest.raises(EvidenceMismatch) as rejected:
        validate_complete_cards([card], article, stage="author")
    issues = getattr(rejected.value, "validation_issues", [])
    assert [issue["loc"] for issue in issues] == [
        ("cards", 0, "mentions", 0, "locations", 0),
        ("cards", 0, "mentions", 0, "locations", 1),
        ("cards", 0, "mentions", 1, "locations", 0),
    ]
    assert all(issue["type"] == "entity_occurrence_evidence_containment" for issue in issues)
    first = issues[0]["ctx"]
    assert first["card_id"] == "master" and first["mention_id"] == "sender"
    assert first["qualified_mention_id"] == "master/sender"
    assert first["location"] == {
        key: card["mentions"][0]["locations"][0][key] for key in ("article_id", "start", "end")
    }
    assert first["expected_article_id"] == article["id"] and first["article_length"] == len(TEXT)
    assert first["cited_evidence"] == [
        {
            "evidence_id": "recipient-only",
            "evidence_index": 1,
            "qualified_evidence_id": "master/recipient-only",
            "article_id": article["id"],
            "start": TEXT.index("李四"),
            "end": TEXT.index("李四") + 2,
        }
    ]
    assert "quote" not in first["location"] and "text" not in first
    assert "master/sender" in issues[0]["msg"]
    assert "evidence[1] master/recipient-only" in issues[0]["msg"]
    assert f"{article['id']}[{TEXT.index('李四')},{TEXT.index('李四') + 2})" in issues[0]["msg"]
    assert (card, article) == retained


def test_existing_native_rejection_reply_exposes_precise_positions_and_ids_without_new_transport():
    from hrs.codex.research_tools import rejection_diagnostics

    card, article = mismatched_mentions()
    with pytest.raises(EvidenceMismatch) as rejected:
        validate_complete_cards([card], article, stage="author")
    audit, reply = rejection_diagnostics(rejected.value)
    assert len(audit["issues"]) == reply["issue_count"] == 3
    assert reply["issues"][0]["indices"] == [[0, 0, 0], [0, 0, 1]]
    assert "master/sender" in reply["issues"][0]["message"]
    assert "evidence[1] master/recipient-only" in reply["issues"][0]["message"]
    assert "exclusive ends" in reply["issues"][0]["message"]


def test_wrong_original_location_and_uncontained_occurrence_are_reported_together():
    card, article = mismatched_mentions()
    card["mentions"][0]["locations"] = [{**card["mentions"][0]["locations"][0], "quote": "错误"}]
    with pytest.raises(EvidenceMismatch) as rejected:
        validate_complete_cards([card], article, stage="author")
    issues = getattr(rejected.value, "validation_issues", [])
    assert [issue["type"] for issue in issues] == [
        "entity_occurrence_original_range",
        "entity_occurrence_evidence_containment",
    ]
    assert [issue["loc"][3] for issue in issues] == [0, 1]


@pytest.mark.parametrize("side", ["start", "end"])
def test_overlap_or_uncited_wider_evidence_does_not_satisfy_containment(side):
    card, article = mismatched_mentions()
    card["mentions"] = [card["mentions"][0]]
    card["mentions"][0]["locations"] = [card["mentions"][0]["locations"][0]]
    card["mentions"][0]["evidence_ids"] = ["sender-only"]
    narrow = card["evidence"][2]
    narrow[side] += 1 if side == "start" else -1
    narrow["quote"] = TEXT[narrow["start"] : narrow["end"]]
    for unit in card["coverage"]:
        unit["mention_ids"] = ["sender"]
    with pytest.raises(EvidenceMismatch, match="contained in its cited evidence"):
        validate_complete_cards([card], article, stage="author")


def test_valid_full_source_containment_is_unchanged():
    card, article = mismatched_mentions()
    for mention in card["mentions"]:
        mention["evidence_ids"].append("e1")
    validate_complete_cards([card], article, stage="author")
