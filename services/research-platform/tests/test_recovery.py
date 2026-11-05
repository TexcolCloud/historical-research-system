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


def test_native_recovery_preflight_failure_is_durable_and_can_be_retried(platform, tmp_path, monkeypatch):
    from hrs_codex.server import prepare
    from PIL import Image

    from hrs_platform.services.books import get_run
    from hrs_platform.services.codex_bridge import CodexBridge

    settings, engine = platform
    settings = settings.model_copy(update={"codex_root": tmp_path / "native"})
    bridge = CodexBridge(settings, engine)
    source = tmp_path / "fixture.png"
    Image.new("RGB", (40, 50), "white").save(source)
    identity = prepare(bridge.store, str(source))["id"]
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="Recovery fixture", state="failed"))
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="failed",
                stage="ocr",
                result={"processing_backend": "codex", "codex_document_id": identity},
            )
        )

    def offline(*args):
        raise ValueError("Codex 登录已失效，请重新登录后恢复。")

    monkeypatch.setattr("hrs_platform.services.runs.recovery.preflight", offline)
    with TestClient(create_app(settings, engine)) as client:
        url = f"/api/v2/runs/{run}/retry"
        request = {"request_id": str(uuid4())}
        assert client.post(url, json=request).json()["recovery"]["state"] == "checking"
        with engine.connect() as connection:
            event = dict(connection.execute(select(db.outbox)).mappings().one())
        bridge.dispatch(event)
        assert get_run(engine, run)["state"] == "failed"
        report = client.get(f"/api/v2/runs/{run}/recovery").json()
        assert report["state"] == "blocked" and "登录" in report["reason"]
        assert not bridge.store.status(identity).get("run_request")
        assert client.post(url, json=request).json()["recovery"]["state"] == "blocked"
        monkeypatch.setattr("hrs_platform.services.runs.recovery.preflight", lambda *_: None)
        assert client.post(url, json={"request_id": str(uuid4())}).status_code == 200
        with engine.connect() as connection:
            event = dict(
                connection.execute(select(db.outbox).where(db.outbox.c.delivered.is_(False))).mappings().one()
            )
        bridge.dispatch(event)
        assert bridge.store.status(identity)["run_request"]["state"] == "pending"
        assert client.get(f"/api/v2/runs/{run}/recovery").json()["state"] == "resumed"

    # A dispatcher restart after native commit must replay its receipt, even if login is now offline.
    monkeypatch.setattr("hrs_platform.services.runs.recovery.preflight", offline)
    bridge.dispatch(event)
    assert get_run(engine, run)["state"] == "queued"
    assert len(bridge.store.status(identity)["recoveries"]) == 1
