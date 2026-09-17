from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from hrs_platform import models as db
from hrs_platform.main import create_app
from hrs_platform.services.runs.recovery import workflow_id


def test_repeated_retry_only_starts_one_new_attempt_and_preserves_published_book(platform):
    settings, engine = platform
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="恢复协议测试", state="ready"))
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="failed",
                stage="indexing",
                result={"published": True},
                source={"sha256": "a" * 64},
            )
        )
    with TestClient(create_app(settings, engine)) as client:
        request = {"request_id": str(uuid4())}
        url = f"/api/v2/runs/{run}/retry"
        assert client.post(url, json=request).status_code == 200
        assert client.post(url, json=request).status_code == 200
        assert client.post(url, json={"request_id": str(uuid4())}).status_code == 409
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.outbox)) == 1
        assert connection.scalar(select(db.runs.c.recovery_attempt)) == 1
        assert connection.scalar(select(db.books.c.state)) == "ready"
        assert connection.scalar(select(db.runs.c.source)) == {"sha256": "a" * 64}
    assert workflow_id("book", run, 1) == f"book/{run}/retry/1"


def test_parent_recovery_reuses_child_and_only_grants_one_request_epoch(platform):
    from sqlalchemy import update

    from hrs_platform.services.cards import Cards

    settings, engine = platform
    book, parent = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="子任务恢复", state="ready"))
        connection.execute(
            insert(db.runs).values(
                id=parent, book_id=book, state="failed", stage="cards", result={"published": True}
            )
        )
    cards = Cards(settings, engine)
    child = cards.create_run(parent)["run_id"]
    with engine.begin() as connection:
        connection.execute(update(db.runs).where(db.runs.c.id == child).values(state="failed"))
        connection.execute(update(db.runs).where(db.runs.c.id == parent).values(recovery_attempt=1))
    first = cards.create_run(parent)
    assert first == cards.create_run(parent)
    assert first["run_id"] == child
    assert first["workflow_id"] == f"cards/{child}/retry/1"
    with engine.connect() as connection:
        assert connection.scalar(select(db.runs.c.recovery_attempt).where(db.runs.c.id == child)) == 1
