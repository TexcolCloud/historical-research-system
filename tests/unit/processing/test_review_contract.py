"""Submission contracts and worker instructions use the same issue definition."""

import pytest

from hrs.codex.runtime_policy import worker_instructions
from hrs.processing.workflow_rules import review_verdict


@pytest.mark.parametrize("issue", [None, {}, "", "   ", 42])
def test_empty_or_unusable_issue_is_rejected(issue):
    with pytest.raises(ValueError, match="issue|Issue"):
        review_verdict({"verdict": "pending", "issues": [issue]})


def test_legacy_text_and_quote_issues_remain_accepted():
    for issue in ["原图模糊，须人工核对。", {"candidate_quote": "候选", "source_quote": "原文"}]:
        assert review_verdict({"verdict": "pending", "issues": [issue]}) == "pending"


@pytest.mark.parametrize("focus", ["Personal name", None, 12])
def test_new_contract_rejects_nonliteral_focus_before_commit(focus):
    result = {
        "verdict": "pending",
        "issue_contract_version": 1,
        "issues": [
            {
                "anchor": {"exact": "候选文字"},
                "focus": focus,
                "explanation": "原图字形待核。",
            }
        ],
    }
    with pytest.raises(ValueError, match="focus"):
        review_verdict(result)


def test_explicit_unlocated_issue_is_valid_without_an_invented_anchor():
    assert (
        review_verdict(
            {
                "verdict": "pending",
                "issue_contract_version": 1,
                "issues": [
                    {
                        "explanation": "原图损坏，无法对应候选文字。",
                        "unlocated_reason": "原件缺损。",
                    }
                ],
            }
        )
        == "pending"
    )


def test_review_and_verify_receive_identical_field_semantics():
    from hrs.processing.review_contract import ISSUE_INSTRUCTIONS

    for stage in ("review", "verify"):
        assert ISSUE_INSTRUCTIONS in worker_instructions(stage)
        assert "literal substring" in worker_instructions(stage)


def test_strict_contract_keeps_legacy_strings_but_validates_each_explicit_occurrence():
    from hrs.processing.review_contract import validate_issues

    validate_issues(["原件待辨。"], strict=True)
    issue = {
        "explanation": "两处待核。",
        "occurrences": [
            {"anchor": {"exact": "第一处"}, "focus": "一"},
            {"anchor": {"exact": "第二处"}, "insertion_offset": 1},
        ],
    }
    validate_issues([issue], strict=True)
    issue["occurrences"][1]["insertion_offset"] = True
    with pytest.raises(ValueError, match="insertion_offset"):
        validate_issues([issue], strict=True)


def test_strict_contract_rejects_bad_box_and_pending_disposition():
    from hrs.processing.review_contract import validate_issues

    with pytest.raises(ValueError, match="boxes"):
        validate_issues(
            [
                {
                    "explanation": "待核。",
                    "anchor": {"exact": "文字"},
                    "focus": "文",
                    "boxes": [[0.1, 0.2, float("nan"), 0.4]],
                }
            ],
            strict=True,
        )
    with pytest.raises(ValueError, match="disposition"):
        review_verdict({"verdict": "pending", "disposition": "unknown", "issues": ["待核。"]})


def test_shared_location_dataclasses_can_describe_normalized_native_output():
    from dataclasses import asdict

    from hrs.processing.review_contract import ReviewAnchor, ReviewLocation, ReviewTextSpan
    from hrs.processing.review_locations import locate_issues

    location = locate_issues(
        "😀字号", [{"candidate_quote": "字号", "focus": "字"}], page=1, image_sha256="source"
    )[0]
    value = {
        **location,
        "anchor": ReviewAnchor(**location["anchor"]),
        "text_spans": [
            ReviewTextSpan(**{**span, "anchor": ReviewAnchor(**span["anchor"])})
            for span in location["text_spans"]
        ],
    }
    restored = asdict(ReviewLocation(**value))
    assert restored["start"] == 1 and restored["end"] == 2
    assert restored["offset_unit"] == "unicode_codepoint"
    assert restored["text_spans"][0]["text_precision"] == "character"
