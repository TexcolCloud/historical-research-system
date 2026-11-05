"""Independent review of draft evidence identity and text binding."""

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from hrs_codex.review_locations import locate_issues
from sqlalchemy import select, update
from test_review import seed

from hrs_platform import models as db
from hrs_platform.domain.errors import ServiceError
from hrs_platform.schemas import ReviewDraft
from hrs_platform.services.documents.draft_locations import normalize_draft_locations
from hrs_platform.services.documents.review import Review, digest


def original_locations():
    return locate_issues(
        "😀甲错乙。",
        [
            {
                "anchor": {"exact": "甲错乙"},
                "focus": "错",
                "explanation": "文字待核。",
                "source_quote": "甲对乙",
                "boxes": [[0.1, 0.2, 0.3, 0.4]],
                "image_precision": "line",
            }
        ],
        page=3,
        image_sha256="a" * 64,
    )


def test_changed_top_anchor_synchronizes_the_equivalent_single_text_span():
    originals = original_locations()
    original_snapshot = copy.deepcopy(originals)
    text = "😀甲对乙。"
    incoming = {**originals[0], "anchor": {"exact": "甲对乙"}, "focus": "对"}
    request = ReviewDraft(expected_revision=1, text=text, locations=[incoming])
    location = normalize_draft_locations(text, request.locations, originals)[0]
    assert location["text_status"] == "located"
    assert text[location["start"] : location["end"]] == "对"
    assert location["text_spans"][0]["anchor"] == location["anchor"]
    assert location["text_spans"][0]["focus"] == "对"
    assert originals == original_snapshot


def test_draft_binding_is_recomputed_and_original_image_evidence_cannot_be_replaced():
    originals = original_locations()
    incoming = {
        **originals[0],
        "text_sha256": "b" * 64,
        "image_sha256": "c" * 64,
        "start": 999,
        "end": 1000,
        "boxes": [[0.7, 0.7, 0.8, 0.8]],
        "image_precision": "region",
        "message": "改写机器理由",
        "source_reading": "伪造读法",
    }
    request = ReviewDraft(expected_revision=1, text="😀甲错乙。", locations=[incoming])
    location = normalize_draft_locations(request.text, request.locations, originals)[0]
    assert location["id"] == originals[0]["id"]
    assert location["text_sha256"] == digest(request.text)
    assert location["start"] == 2 and location["end"] == 3
    for key in ("image_sha256", "boxes", "image_precision", "message", "source_reading"):
        assert location[key] == originals[0][key]


@pytest.mark.parametrize("change", ["missing", "duplicate", "page"])
def test_draft_location_identity_cannot_change(change):
    originals = original_locations()
    incoming = copy.deepcopy(originals)
    if change == "missing":
        incoming = []
    elif change == "duplicate":
        incoming *= 2
    else:
        incoming[0]["page"] += 1
    request = ReviewDraft(expected_revision=1, text="😀甲错乙。", locations=incoming)
    with pytest.raises(ServiceError) as error:
        normalize_draft_locations(request.text, request.locations, originals)
    assert error.value.status_code == 422


@pytest.mark.parametrize("hash_state", ["missing", "none", "stale", "valid"])
def test_reading_legacy_draft_retains_text_but_exposes_only_hash_bound_positions(
    platform, monkeypatch, hash_state
):
    settings, engine = platform
    _, ids = seed(engine)
    review = Review(settings, engine)
    original, draft = "😀甲错乙。", "😀甲对乙。"
    locations = original_locations()
    body = {
        "text": original,
        "reasons": [{"anchor": {"exact": "甲错乙"}, "focus": "错"}],
        "locations": locations,
        "pages": [1],
        "images": ["pages/00001.png"],
        "kind": "page",
    }
    reference = {
        "key": "isolated-test-draft",
        "review_locations": locations,
        "review_resolutions": [{"id": locations[0]["id"], "action": "edit"}],
    }
    if hash_state != "missing":
        reference["review_locations_text_sha256"] = (
            digest(draft) if hash_state == "valid" else digest(original) if hash_state == "stale" else None
        )
    with engine.begin() as connection:
        connection.execute(
            update(db.review_issues).where(db.review_issues.c.id == ids[0]).values(draft=reference)
        )
    monkeypatch.setattr(review, "read_json", lambda _: body)
    monkeypatch.setattr(review.objects, "read_bytes", lambda _: draft.encode("utf-8"))
    restored = review.get(ids[0])
    assert restored["draft_text"] == draft
    assert restored["resolutions"] == reference["review_resolutions"]
    if hash_state == "valid":
        assert restored["draft_locations"] == locations
        assert restored["draft_locations_text_sha256"] == digest(draft)
    else:
        assert restored["draft_locations"] is None
        assert restored["draft_locations_text_sha256"] is None


@pytest.mark.parametrize("same_request", [True, False])
def test_simultaneous_draft_saves_are_idempotent_or_reject_the_stale_edit(
    platform, monkeypatch, same_request
):
    settings, engine = platform
    run, ids = seed(engine)
    review = Review(settings, engine)
    originals = original_locations()
    monkeypatch.setattr(review, "get", lambda _: {"locations": originals})
    monkeypatch.setattr(
        review.objects,
        "put_bytes",
        lambda value, *_, **__: {
            "key": digest(value.decode("utf-8")),
            "sha256": digest(value.decode("utf-8")),
            "byte_length": len(value),
        },
    )
    barrier = Barrier(2)

    def save(replacement):
        text = f"😀甲{replacement}乙。"
        request = ReviewDraft(
            expected_revision=1,
            text=text,
            locations=[
                {
                    **originals[0],
                    "anchor": {"exact": f"甲{replacement}乙"},
                    "focus": replacement,
                }
            ],
        )
        barrier.wait(timeout=5)
        try:
            return review.save_draft(ids[0], request)
        except ServiceError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(save, ("对", "对" if same_request else "正")))
    saved = [receipt for receipt in receipts if isinstance(receipt, dict)]
    if same_request:
        assert len(saved) == 2 and saved[0] == saved[1]
    else:
        assert len(saved) == 1 and 409 in receipts
    assert all(receipt["revision"] == 2 for receipt in saved)
    assert all(receipt["locations"][0]["text_status"] == "located" for receipt in saved)
    with engine.connect() as connection:
        assert (
            connection.scalar(select(db.review_issues.c.revision).where(db.review_issues.c.id == ids[0])) == 2
        )
        assert (
            connection.scalar(select(db.review_issues.c.state).where(db.review_issues.c.id == ids[0]))
            == "pending"
        )
        assert connection.scalar(select(db.runs.c.pending_count).where(db.runs.c.id == run)) == 2
