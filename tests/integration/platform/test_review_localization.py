import json
from uuid import uuid4

import pytest
from sqlalchemy import update

from hrs.api.schemas import ReviewDecision, ReviewDraft
from hrs.library.errors import ServiceError
from hrs.library.review import Review, digest
from hrs.storage import models as db

from .test_review import seed


def test_resolution_draft_is_durable_but_deferred_doubt_cannot_release_page(platform):
    settings, engine = platform
    run, ids = seed(engine, settings=settings)
    review = Review(settings, engine)
    body = {
        "text": "甲待辨乙",
        "pages": [1],
        "images": [],
        "kind": "page",
        "reasons": [{"candidate_quote": "待辨", "explanation": "合成协议疑点"}],
    }
    ref = review.objects.put_bytes(json.dumps(body).encode(), run_id=run)
    with engine.begin() as conn:
        conn.execute(
            update(db.review_issues)
            .where(db.review_issues.c.id == ids[0])
            .values(content=ref, text_sha256=digest(body["text"]))
        )
    issue = review.get(ids[0])
    doubt = issue["locations"][0]
    assert doubt["start"] == 1 and doubt["end"] == 3
    saved = review.save_draft(
        ids[0],
        ReviewDraft(
            expected_revision=1,
            text=body["text"],
            resolutions=[{"id": doubt["id"], "action": "defer"}],
        ),
    )
    assert review.get(ids[0])["resolutions"] == [{"id": doubt["id"], "action": "defer"}]
    request = dict(
        decision_id=uuid4(),
        expected_revision=saved["revision"],
        expected_text_sha256=digest(body["text"]),
        action="confirm",
    )
    with pytest.raises(ServiceError, match="逐项"):
        review.decide(
            ids[0], ReviewDecision(**request, resolutions=[{"id": doubt["id"], "action": "defer"}])
        )
    assert review.get(ids[0])["state"] == "pending"
    receipt = review.decide(
        ids[0], ReviewDecision(**request, resolutions=[{"id": doubt["id"], "action": "keep"}])
    )
    assert receipt["pending_count"] == 1
