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
