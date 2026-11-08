"""Incremental proposals retain business meaning, source boundaries and atomicity."""

from copy import deepcopy
from math import inf, nan

import pytest

from hrs.processing.errors import EvidenceMismatch
from hrs.processing.hashing import digest
from hrs.research.card_updates import apply_card_changes
from hrs.research.models import CardDraft


def cards():
    return [
        CardDraft.model_validate(
            {
                "id": "local-card",
                "article_id": "primary",
                "title": "Letter",
                "summary": "Initial statement",
                "evidence": [
                    {"id": "e1", "article_id": "primary", "start": 0, "end": 1, "quote": "甲"}
                ],
                "tags": ["old", "retained"],
            }
        ).model_dump()
    ]


def apply(baseline, changes):
    return apply_card_changes(baseline, digest(baseline), changes)


def test_incremental_and_full_business_drafts_have_identical_materialized_meaning():
    baseline = cards()
    external = {
        "id": "e2",
        "article_id": "frozen-external",
        "start": 0,
        "end": 1,
        "quote": "乙",
        "footnote_ids": [],
    }
    patch = [
        {
            "op": "replace",
            "path": "/cards/0/summary",
            "value": "Both sources retain their own attribution",
        },
        {"op": "add", "path": "/cards/0/evidence/-", "value": external},
        {"op": "remove", "path": "/cards/0/tags/0"},
        {"op": "add", "path": "/cards/0/tags/0", "value": "new"},
    ]
    expected = deepcopy(baseline)
    expected[0]["summary"] = patch[0]["value"]
    expected[0]["evidence"].append(external)
    expected[0]["tags"][0] = "new"
    actual = apply(baseline, patch)
    assert actual == expected
    assert (
        CardDraft.model_validate(actual[0]).model_dump()
        == CardDraft.model_validate(expected[0]).model_dump()
    )
    assert baseline == cards()
    actual[0]["evidence"][1]["quote"] = "Changed after apply"
    assert external["quote"] == "乙" and patch[1]["value"]["quote"] == "乙"


def test_baseline_digest_is_checked_before_changes_and_never_trusts_client_baseline():
    with pytest.raises(EvidenceMismatch, match="baseline"):
        apply_card_changes(cards(), "0" * 64, [])


def test_failed_later_operation_does_not_change_any_original_object():
    baseline = cards()
    operations = [
        {"op": "replace", "path": "/cards/0/summary", "value": "Must not leak"},
        {"op": "replace", "path": "/cards/0/evidence/99/quote", "value": "Outside"},
    ]
    original_operations = deepcopy(operations)
    with pytest.raises(ValueError):
        apply(baseline, operations)
    assert baseline == cards() and operations == original_operations


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/cards",
        "/cards/0",
        "/cards/0/id",
        "/cards/0/article_id",
        "/cards/-/title",
        "/cards/1/title",
        "/cards/-1/title",
        "/cards/00/title",
        "/cards/01/title",
        "/cards/١/title",
        "/cards/0/state",
        "/cards/0/approval_type",
        "/cards/0/evidence/0/sources",
        "/cards/0/evidence/0/reading_target",
        "/cards/0/metadata/authors/publication_sha256",
        "/cards/0/metadata/~2bad",
        "/cards/0/metadata/unfinished~",
        "/cards/0/tags/-1",
        "/cards/0/tags/01",
        "/cards/0/tags/-",
        "/cards/0/tags/99",
        "cards/0/summary",
        "/cards/0/" + "x" * 512,
    ],
)
def test_invalid_or_readonly_paths_are_rejected(path):
    with pytest.raises(ValueError):
        apply(cards(), [{"op": "replace", "path": path, "value": "Rejected"}])


@pytest.mark.parametrize(
    "field",
    [
        "state",
        "version",
        "approval_type",
        "checks",
        "book_id",
        "run_id",
        "source_run_id",
        "publication_sha256",
        "article_sha256",
        "original_article_id",
        "document_id",
        "lineage_id",
        "replaces_card_id",
        "sources",
        "pages",
    ],
)
def test_nested_values_cannot_smuggle_readonly_projection_or_approval_fields(field):
    value = {
        "id": "e2",
        "article_id": "valid-new-source",
        "start": 0,
        "end": 1,
        "quote": "乙",
        field: "forged",
    }
    with pytest.raises(ValueError):
        apply(cards(), [{"op": "add", "path": "/cards/0/evidence/-", "value": value}])


@pytest.mark.parametrize(
    "value",
    [nan, inf, -inf, {"value": nan}, [inf], {1: "non-string key"}, ("tuple",), {"set"}, b"bytes"],
)
def test_changes_accept_only_finite_json_values(value):
    with pytest.raises(ValueError):
        apply(cards(), [{"op": "add", "path": "/cards/0/summary", "value": value}])


def test_json_pointer_decodes_escaped_components_exactly_once():
    baseline = cards()
    baseline[0]["metadata"] = {"a/b": {"~1": "before"}}
    updated = apply(
        baseline, [{"op": "replace", "path": "/cards/0/metadata/a~1b/~01", "value": "after"}]
    )
    assert updated[0]["metadata"] == {"a/b": {"~1": "after"}}
    assert baseline[0]["metadata"] == {"a/b": {"~1": "before"}}


def test_array_insertions_and_removals_follow_sequential_patch_indices():
    updated = apply(
        cards(),
        [
            {"op": "add", "path": "/cards/0/tags/2", "value": "end"},
            {"op": "add", "path": "/cards/0/tags/1", "value": "middle"},
            {"op": "remove", "path": "/cards/0/tags/0"},
        ],
    )
    assert updated[0]["tags"] == ["middle", "retained", "end"]


def test_json_null_and_boolean_values_are_not_lost_by_delta_transport():
    updated = apply(
        cards(),
        [
            {"op": "add", "path": "/cards/0/metadata", "value": None},
            {"op": "add", "path": "/cards/0/no_argument_reason", "value": False},
        ],
    )
    assert updated[0]["metadata"] is None and updated[0]["no_argument_reason"] is False


def test_circular_value_is_rejected_without_mutating_the_baseline():
    circular = []
    circular.append(circular)
    baseline = cards()
    with pytest.raises(ValueError, match="circular"):
        apply(baseline, [{"op": "add", "path": "/cards/0/tags/-", "value": circular}])
    assert baseline == cards()


@pytest.mark.parametrize(
    "changes",
    [
        [],
        [{"op": [], "path": "/cards/0/title", "value": "x"}],
        [{"op": "copy", "path": "/cards/0/title", "from": "/cards/0/summary"}],
        [{"op": "remove", "path": "/cards/0/summary", "value": "forbidden"}],
        [{"op": "replace", "path": "/cards/0/summary"}],
        [{"op": "add", "path": "/cards/0/summary"}],
        [{"op": "add", "path": "/cards/0/summary", "value": "x", "unexpected": True}],
        [{"op": "remove", "path": "/cards/0/summary"}] * 129,
    ],
)
def test_operation_shapes_are_explicit_and_bounded(changes):
    with pytest.raises(ValueError):
        apply(cards(), changes)
