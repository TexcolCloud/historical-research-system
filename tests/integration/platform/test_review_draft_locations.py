"""Draft positions survive a real API roundtrip without approving content."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from hrs.api.main import create_app
from hrs.library.review import Review, digest
from hrs.storage import models as db

from .test_review import seed


def test_two_location_draft_roundtrip_retains_identity_and_pending(platform):
    from hrs.processing.review_locations import locate_issues

    settings, engine = platform
    run, ids = seed(engine)
    review = Review(settings, engine)
    original = "😀甲错乙，丙错丁。"
    final = "😀甲对乙，丙正丁。"
    reasons = [
        {"anchor": {"exact": "甲错乙"}, "focus": "错", "explanation": "首处待核"},
        {"anchor": {"exact": "丙错丁"}, "focus": "错", "explanation": "次处待核"},
    ]
    base = locate_issues(original, reasons, page=1, image_sha256="a" * 64)
    body = {
        "text": original,
        "reasons": reasons,
        "locations": base,
        "pages": [1],
        "images": ["pages/00001.png"],
        "kind": "page",
    }
    import json

    ref = review.objects.put_bytes(json.dumps(body, ensure_ascii=False).encode(), run_id=run)
    with engine.begin() as connection:
        connection.execute(
            update(db.review_issues)
            .where(db.review_issues.c.id == ids[0])
            .values(content=ref, text_sha256=digest(original))
        )
    locations = []
    for location, exact, focus in zip(base, ("甲对乙", "丙正丁"), ("对", "正"), strict=True):
        locations.append(
            {
                **location,
                "anchor": {"exact": exact, "prefix": "", "suffix": ""},
                "focus": focus,
                "start": None,
                "end": None,
            }
        )
    request = {"expected_revision": 1, "text": final, "locations": locations, "resolutions": []}
    with TestClient(create_app(settings, engine)) as client:
        url = f"/api/v2/reviews/{ids[0]}/draft"
        response = client.put(url, json=request)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert {l["id"] for l in saved["locations"]} == {l["id"] for l in base}
        assert saved["locations_text_sha256"] == digest(final)
        restored = client.get(f"/api/v2/reviews/{ids[0]}").raise_for_status().json()
        assert restored["draft_text"] == final
        assert restored["draft_locations"] == saved["locations"]
        assert restored["draft_locations_text_sha256"] == digest(final)
        assert restored["text"] == original
        assert client.put(url, json=request).json() == saved
        forged = {
            **request,
            "expected_revision": saved["revision"],
            "locations": [{**locations[0], "id": str(uuid4())}],
        }
        assert client.put(url, json=forged).status_code == 422
    with engine.connect() as connection:
        row = (
            connection.execute(select(db.review_issues).where(db.review_issues.c.id == ids[0]))
            .mappings()
            .one()
        )
        assert row["state"] == "pending"
        assert connection.scalar(select(db.runs.c.pending_count).where(db.runs.c.id == run)) == 2
