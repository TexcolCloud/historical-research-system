from hrs_codex.review_locations import locate_issues


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
