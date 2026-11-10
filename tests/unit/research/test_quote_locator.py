"""Exact source coordinates expose ambiguity without normalizing source wording."""

from hashlib import sha256

import pytest

from hrs.research.quote_locator import locate_quotes


def source(text):
    return {
        "id": "frozen-article",
        "text": text,
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
    }


def test_exact_unicode_keeps_codepoint_offsets_and_does_not_guess_normalization():
    original = source("𠮷甲\n甲 乙\r\n甲乙 e\u0301 é")
    result = locate_quotes(original, ["甲\n甲", "甲乙", "甲 乙", "é", "e\u0301", "甲\n乙"])
    assert result["article_id"] == original["id"]
    assert result["text_sha256"] == original["text_sha256"]
    assert result["quote_counts"] == [
        {"quote_index": index, "total_matches": count}
        for index, count in enumerate([1, 1, 1, 1, 1, 0])
    ]
    for match in result["matches"]:
        assert (
            original["text"][match["start"] : match["end"]]
            == ["甲\n甲", "甲乙", "甲 乙", "é", "e\u0301"][match["quote_index"]]
        )
    assert result["matches"][0] == {"quote_index": 0, "start": 1, "end": 4}
    assert "text" not in result and "quotes" not in result


def test_overlapping_and_duplicate_quotes_keep_separate_ambiguity_counts():
    result = locate_quotes(source("甲甲甲甲"), ["甲甲", "甲甲", "乙"], offset=2, limit=3)
    assert result["quote_counts"] == [
        {"quote_index": 0, "total_matches": 3},
        {"quote_index": 1, "total_matches": 3},
        {"quote_index": 2, "total_matches": 0},
    ]
    assert result["matches"] == [
        {"quote_index": 0, "start": 2, "end": 4},
        {"quote_index": 1, "start": 0, "end": 2},
        {"quote_index": 1, "start": 1, "end": 3},
    ]
    assert result["total"] == 6 and result["offset"] == 2 and result["limit"] == 3
    assert result["truncated"]
    final = locate_quotes(source("甲甲甲甲"), ["甲甲", "甲甲", "乙"], offset=5, limit=3)
    assert final["matches"] == [{"quote_index": 1, "start": 2, "end": 4}]
    assert not final["truncated"]


def test_pagination_is_bounded_even_when_the_original_contains_many_matches():
    result = locate_quotes(source("甲" * 50000), ["甲"], offset=49990, limit=20)
    assert result["total"] == 50000 and len(result["matches"]) == 10
    assert result["matches"][0]["start"] == 49990
    beyond = locate_quotes(source("甲"), ["甲"], offset=100)
    assert beyond["matches"] == [] and beyond["total"] == 1 and not beyond["truncated"]


@pytest.mark.parametrize(
    "quotes", [[], "甲", [""], [1], ["甲" * 4001], ["甲"] * 21, ["甲" * 4000] * 5]
)
def test_invalid_quote_inputs_are_rejected(quotes):
    with pytest.raises(ValueError):
        locate_quotes(source("甲"), quotes)


@pytest.mark.parametrize(
    ("offset", "limit"), [(-1, 1), (False, 1), (1.0, 1), (0, 0), (0, 21), (0, True), (0, 1.5)]
)
def test_pagination_requires_strict_integer_offsets_and_bounded_limits(offset, limit):
    with pytest.raises(ValueError):
        locate_quotes(source("甲"), ["甲"], offset=offset, limit=limit)


def footnoted_source():
    original = source("🙂甲页[1]。\n[1]: 甲说明。\n乙页[1]。\n[1]: 乙说明。\n未关联[9]。")
    text = original["text"]
    split = text.index("乙页")
    original["footnotes"] = [
        {
            "id": identity,
            "label": "1",
            "page": page,
            "note": {"start": note, "end": text.index("\n", note) + 1},
            "references": [{"start": ref, "end": ref + 3}],
        }
        for identity, page, ref, note in [
            ("page-three-one", 3, text.index("[1]"), text.index("[1]:")),
            ("page-nine-one", 9, text.index("[1]", split), text.index("[1]:", split)),
        ]
    ]
    return original


def test_same_printed_label_on_two_pages_returns_only_each_exact_match_owned_note_id():
    from copy import deepcopy

    original = footnoted_source()
    frozen = deepcopy(original)
    result = locate_quotes(original, ["[1]", "未关联[9]"])
    assert [match["footnote_ids"] for match in result["matches"]] == [
        ["page-three-one"],
        ["page-three-one"],
        ["page-nine-one"],
        ["page-nine-one"],
        [],
    ]
    assert result["quote_counts"] == [
        {"quote_index": 0, "total_matches": 4},
        {"quote_index": 1, "total_matches": 1},
    ]
    assert result["text_sha256"] == original["text_sha256"]
    assert result["matches"][0]["start"] == 3  # The emoji is one Unicode codepoint.
    assert original == frozen


def test_quote_in_note_definition_is_owned_but_touching_either_boundary_is_not():
    original = footnoted_source()
    result = locate_quotes(original, ["甲说明", "甲页", "。\n乙页", "未关联"])
    assert [match["footnote_ids"] for match in result["matches"]] == [
        ["page-three-one"],
        [],
        ["page-three-one"],
        [],
    ]
    # The note ends just before 乙页; a quote beginning there must not inherit it.
    second = locate_quotes(original, ["乙页"])
    assert second["matches"][0]["footnote_ids"] == []
    before = locate_quotes(original, ["🙂甲页"])
    assert before["matches"][0]["footnote_ids"] == []


def test_multiple_owned_notes_follow_source_order_and_unparsed_ranges_never_guess():
    original = footnoted_source()
    original["footnotes"] = [
        original["footnotes"][1],
        original["footnotes"][0],
        {"id": "unknown", "label": "1"},
        {"label": "1", "references": [{"start": 0, "end": 10}]},
        {"id": "invalid", "references": [{"start": -1, "end": 99999}]},
        {"id": "unparsed", "note": "[1]: undecoded"},
    ]
    result = locate_quotes(original, [original["text"]])
    assert result["matches"][0]["footnote_ids"] == ["page-nine-one", "page-three-one"]
    assert result["total"] == 1


def test_footnote_hints_do_not_change_pagination_ambiguity_or_legacy_shape():
    original = footnoted_source()
    result = locate_quotes(original, ["[1]", "[1]"], offset=3, limit=2)
    assert [(match["quote_index"], match["footnote_ids"]) for match in result["matches"]] == [
        (0, ["page-nine-one"]),
        (1, ["page-three-one"]),
    ]
    assert result["total"] == 8 and result["truncated"]
    assert locate_quotes(source("甲"), ["甲"])["matches"] == [
        {"quote_index": 0, "start": 0, "end": 1},
    ]


def test_only_new_v6_instructions_prefer_mechanical_note_ids_without_skipping_source_reading():
    from hrs.research.execution import role_configs
    from hrs.research.policy import POLICY
    from tests.unit.research.test_workflow import legacy_policy

    phrase = "Prefer each locate match's owned footnote_ids"
    assert all(phrase in role["instructions"] for role in role_configs(POLICY).values())
    assert all(
        phrase not in role["instructions"] for role in role_configs(legacy_policy(5)).values()
    )
