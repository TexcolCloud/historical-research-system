"""Real PostgreSQL/S3 ownership and recoverable deletion, using unique synthetic bytes."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from hrs.api.main import create_app
from hrs.library.deletion import require_run_active
from hrs.storage import models as db


def seed(engine, *, source=None, stage="verify_upload"):
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="删除技术夹具", state="queued"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, state="queued", stage=stage, source=source)
        )
        connection.execute(
            insert(db.outbox).values(
                run_id=run, kind="start_book", dedup_key=f"start:{run}", payload={}
            )
        )
    return book, run


def test_delete_is_immediate_idempotent_and_blocks_recovery(platform):
    settings, engine = platform
    book, run = seed(engine)
    with TestClient(create_app(settings, engine)) as api:
        first = api.delete(f"/api/v2/books/{book}")
        assert first.status_code == 202
        assert api.delete(f"/api/v2/books/{book}").json() == first.json()
        assert api.get(f"/api/v2/books/{book}").json()["state"] == "deleting"
        assert (
            api.post(f"/api/v2/runs/{run}/retry", json={"request_id": str(uuid4())}).status_code
            == 409
        )
        with engine.connect() as connection:
            assert connection.scalar(select(db.outbox.c.delivered)) is True
        with pytest.raises(Exception, match="正在删除"):
            with engine.begin() as connection:
                require_run_active(connection, run)
