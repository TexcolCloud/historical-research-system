from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from hrs_platform import schema as db
from hrs_platform.api import create_app
from hrs_platform.review import Review, digest


def seed(engine):
    book, upload, run = [str(uuid4()) for _ in range(3)]
    ids = [str(uuid4()) for _ in range(2)]
    with engine.begin() as conn:
        conn.execute(insert(db.books).values(id=book, title="技术核对测试", state="awaiting_review"))
        conn.execute(
            insert(db.uploads).values(
                id=upload,
                book_id=book,
                filename="test.pdf",
                byte_length=1,
                token_hash="x" * 64,
                state="received",
                expires_at=datetime.now(UTC),
            )
        )
        conn.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                upload_id=upload,
                state="awaiting_review",
                stage="review",
                pending_count=2,
                result={"review_initialized": True},
            )
        )
        conn.execute(
            insert(db.review_issues),
            [
                {
                    "id": identity,
                    "run_id": run,
                    "scope_key": str(n),
                    "page": n,
                    "content": {"technical_fixture": True},
                    "text_sha256": digest("unchanged"),
                }
                for n, identity in enumerate(ids, 1)
            ],
        )
    return run, ids


def test_confirmation_only_updates_one_issue_and_replays_receipt(platform, monkeypatch):
    settings, engine = platform
    run, ids = seed(engine)
    review = Review(settings, engine)

    def forbidden(*args, **kwargs):
        raise AssertionError("Confirming unchanged text must not fetch or rewrite book objects.")

    monkeypatch.setattr(review.objects, "put_bytes", forbidden)
    monkeypatch.setattr(review.objects, "read_bytes", forbidden)
    from hrs_platform.contracts import ReviewDecision

    request = ReviewDecision(
        decision_id=uuid4(), expected_revision=1, expected_text_sha256=digest("unchanged"), action="confirm"
    )
    first = review.decide(ids[0], request)
    assert first["pending_count"] == 1 and first["next_issue_id"] == ids[1]
    repeated = review.decide(ids[0], request)
    assert repeated["revision"] == first["revision"]
    with engine.connect() as conn:
        assert (
            conn.scalar(select(db.review_issues.c.state).where(db.review_issues.c.id == ids[1])) == "pending"
        )
        assert conn.scalar(select(db.runs.c.pending_count).where(db.runs.c.id == run)) == 1


def test_stale_or_changed_confirmation_is_rejected_and_last_issue_releases_gate(platform):
    settings, engine = platform
    run, ids = seed(engine)
    with TestClient(create_app(settings, engine)) as client:
        for index, identity in enumerate(ids):
            request = {
                "decision_id": str(uuid4()),
                "expected_revision": 1,
                "expected_text_sha256": digest("unchanged"),
                "action": "confirm",
            }
            url = f"/api/v2/reviews/{identity}/decisions"
            assert client.post(url, json={**request, "expected_revision": 2}).status_code == 409
            receipt = client.post(url, json=request).json()
            assert receipt["pending_count"] == 1 - index
            assert client.post(url, json={**request, "decision_id": str(uuid4())}).status_code == 409
        assert receipt["state"] == "ready_for_ingestion" and receipt["next_issue_id"] is None
        assert client.get("/api/v2/reviews", params={"run_id": run}).json() == []


def test_saved_draft_is_durable_without_releasing_any_review_scope(platform):
    settings, engine = platform
    run, ids = seed(engine)
    with TestClient(create_app(settings, engine)) as client:
        url = f"/api/v2/reviews/{ids[0]}/draft"
        request = {"expected_revision": 1, "text": "用户尚未确认的草稿"}
        saved = client.put(url, json=request)
        assert saved.status_code == 200
        assert client.put(url, json=request).json() == saved.json()
        assert client.put(url, json={**request, "text": "另一个过期的编辑"}).status_code == 409
    with engine.connect() as connection:
        assert connection.scalar(select(db.runs.c.pending_count).where(db.runs.c.id == run)) == 2
        ref = connection.scalar(select(db.review_issues.c.draft).where(db.review_issues.c.id == ids[0]))
    assert Review(settings, engine).objects.read_bytes(ref).decode() == "用户尚未确认的草稿"
