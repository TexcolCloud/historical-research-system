import copy
import json
from pathlib import Path

import pytest

from hrs.processing.review_locations import locate_issues


@pytest.mark.parametrize(
    "snapshot",
    json.loads(
        (Path(__file__).parent / "fixtures/review_focus_20261106.json").read_text(encoding="utf-8")
    ),
    ids=lambda snapshot: str(snapshot["page"]),
)
def test_frozen_actual_review_focus_normalizes_without_changing_raw_or_human_ids(snapshot):
    issues = copy.deepcopy(snapshot["issues"])
    locations = locate_issues(
        snapshot["text"], issues, page=snapshot["page"], image_sha256="source"
    )
    assert [location["id"] for location in locations] == snapshot["expected_ids"]
    assert issues == snapshot["issues"]
    for location in locations:
        assert location["text_status"] == "located"
        assert snapshot["text"][location["start"] : location["end"]] == location["focus"]
        expected_precision = {75: "character", 85: "insertion", 90: "character", 93: "character"}
        assert location["text_precision"] == expected_precision.get(snapshot["page"], "phrase")
        assert "invalid_focus" in location["location_diagnostics"]
    if snapshot["page"] == 141:
        assert len(locations[0]["boxes"]) == 3
        assert len(locations[0]["text_spans"]) == 1
        assert "additional_text_locations_unspecified" in locations[0]["location_diagnostics"]


def test_descriptive_focus_falls_back_to_unique_literal_anchor_without_changing_identity():
    import hashlib
    import json

    text = "😀前文提到赵某甲，随后说明经过。"
    issue = {
        "anchor": {"exact": "赵某甲", "prefix": "前文提到", "suffix": "，随后"},
        "focus": "Personal name, middle character",
        "explanation": "姓名中间字待辨。",
        "source_quote": "赵〔待辨〕甲",
        "boxes": [[0.2, 0.3, 0.4, 0.5]],
        "image_precision": "line",
    }
    original = json.dumps(issue, ensure_ascii=False, sort_keys=True)
    location = locate_issues(text, [issue], page=4, image_sha256="source")[0]
    assert location["text_status"] == "located"
    assert text[location["start"] : location["end"]] == "赵某甲"
    assert location["focus"] == "赵某甲" and location["text_precision"] == "phrase"
    assert location["offset_unit"] == "unicode_codepoint"
    assert "invalid_focus" in location["location_diagnostics"]
    expected_id = hashlib.sha256(
        json.dumps([4, 0, issue], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:20]
    assert location["id"] == expected_id
    assert json.dumps(issue, ensure_ascii=False, sort_keys=True) == original


def test_invalid_focus_can_locate_a_single_difference_or_insertion():
    changed, inserted = locate_issues(
        "前往绥区。天亮太早了。",
        [
            {"candidate_quote": "绥区", "source_quote": "警区", "focus": "Wrong character"},
            {
                "candidate_quote": "天亮太早了",
                "source_quote": "天亮得太早了",
                "focus": "Omitted character",
            },
        ],
        page=1,
        image_sha256="source",
    )
    assert changed["focus"] == "绥" and changed["text_precision"] == "character"
    assert inserted["start"] == inserted["end"] == 7
    assert inserted["insertion_offset"] == 2 and inserted["text_precision"] == "insertion"


def test_invalid_box_retains_issue_and_records_why_image_location_degraded():
    location = locate_issues(
        "待核文字",
        [
            {
                "anchor": {"exact": "待核文字"},
                "focus": "文字",
                "boxes": [[0.1, 0.2, 1.2, 0.4]],
                "image_precision": "glyph",
                "explanation": "核对文字。",
            }
        ],
        page=2,
        image_sha256="source",
    )[0]
    assert location["text_status"] == "located"
    assert location["boxes"] == [] and location["image_precision"] == "page"
    assert {"invalid_boxes", "invalid_image_precision"} <= set(location["location_diagnostics"])


def test_explicit_occurrences_keep_one_issue_identity_with_distinct_text_spans():
    location = locate_issues(
        "甲处待辨。乙处待辨。",
        [
            {
                "explanation": "两处待辨字。",
                "occurrences": [
                    {"anchor": {"exact": "待辨", "prefix": "甲处"}, "focus": "待辨"},
                    {"anchor": {"exact": "待辨", "prefix": "乙处"}, "focus": "待辨"},
                ],
            }
        ],
        page=3,
        image_sha256="source",
    )[0]
    assert len(location["text_spans"]) == 2
    assert [(s["start"], s["end"]) for s in location["text_spans"]] == [(2, 4), (7, 9)]
    assert all(s["text_status"] == "located" for s in location["text_spans"])


def test_repeated_quote_requires_context_and_keeps_unicode_offsets():
    text = "😀甲石[unreadable]渡口。乙石[unreadable]渡口。"
    issues = [
        {"candidate_quote": "[unreadable]", "explanation": "缺字"},
        {
            "anchor": {"exact": "石[unreadable]渡口", "prefix": "乙"},
            "focus": "[unreadable]",
            "suggestion": "岔",
            "box": [0.2, 0.3, 0.4, 0.5],
        },
    ]
    first, second = locate_issues(text, issues, page=7, image_sha256="image")
    assert first["text_status"] == "ambiguous"
    assert second["start"] == text.rindex("[unreadable]")
    assert text[second["start"] : second["end"]] == "[unreadable]"
    assert second["image_precision"] == "region"
    assert second["page"] == 7


def test_legacy_quotes_focus_only_difference_and_missing_character_uses_insertion():
    replaced, missing = locate_issues(
        "😀到绥区。石渡口",
        [
            {"candidate_quote": "到绥区", "source_quote": "到警区"},
            {"candidate_quote": "石渡口", "source_quote": "石岔渡口"},
        ],
        page=1,
        image_sha256="source",
    )
    assert replaced["focus"] == "绥"
    assert replaced["start"] == 2 and replaced["end"] == 3
    assert missing["start"] == missing["end"] == 6
    assert missing["insertion_offset"] == 1


def test_unknown_ambiguous_missing_and_explicitly_unlocated_anchors_never_invent_offsets():
    ambiguous, mismatched, unlocated = locate_issues(
        "甲渡口，乙渡口。",
        [
            {"anchor": {"exact": "渡口"}, "focus": "渡"},
            {"anchor": {"exact": "渡口", "prefix": "丙"}, "focus": "渡口"},
            {"explanation": "纸页破损。", "unlocated_reason": "候选中无可靠对应文字。"},
        ],
        page=9,
        image_sha256="source",
    )
    assert ambiguous["text_status"] == "ambiguous"
    assert "ambiguous_text_anchor" in ambiguous["location_diagnostics"]
    assert mismatched["text_status"] == "missing"
    assert "anchor_context_mismatch" in mismatched["location_diagnostics"]
    assert unlocated["unlocated_reason"] == "候选中无可靠对应文字。"
    assert all(
        location["start"] is None and location["end"] is None
        for location in (ambiguous, mismatched, unlocated)
    )


def test_unknown_occurrences_retain_valid_boxes_and_do_not_claim_extra_text_locations():
    location = locate_issues(
        "😀第一处异文，第二处讹字。",
        [
            {
                "explanation": "两处应核对。",
                "locations": [
                    {
                        "anchor": {"exact": "异文"},
                        "focus": "异",
                        "boxes": [[0.1, 0.1, 0.2, 0.2], [False, 0.1, 0.2, 0.2]],
                    },
                    {"anchor": {"exact": "讹字"}, "focus": "讹", "boxes": [[0.3, 0.3, 0.4, 0.4]]},
                ],
            }
        ],
        page=1,
        image_sha256="source",
    )[0]
    assert [span["start"] for span in location["text_spans"]] == [4, 10]
    assert len(location["boxes"]) == 2
    assert "invalid_boxes" in location["location_diagnostics"]
    assert "image_unlocated" not in location["location_diagnostics"]
    assert "additional_text_locations_unspecified" not in location["location_diagnostics"]
