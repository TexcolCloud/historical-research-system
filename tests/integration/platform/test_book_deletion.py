"""Real PostgreSQL/S3 ownership and recoverable deletion, using unique synthetic bytes."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from hrs.api.main import create_app
from hrs.library.deletion import request_deletion, require_run_active
from hrs.library.deletion_cleanup import BookCleanup
from hrs.storage import models as db
from hrs.storage.service import objects_for


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


def test_deletion_erases_original_and_derived_search_namespaces(platform):
    settings, engine = platform
    book, _ = seed(engine)
    erased = []
    search = SimpleNamespace(
        indices=SimpleNamespace(exists=lambda **kwargs: True),
        delete_by_query=lambda **kwargs: erased.append(kwargs) or {},
    )
    cleanup = BookCleanup(settings, engine, search_factory=lambda _: search)
    request_deletion(settings, engine, book)
    cleanup.erase_book(book)
    assert {request["index"] for request in erased} == {
        settings.opensearch_index,
        settings.opensearch_index + "-research",
    }
    assert all(request["body"] == {"query": {"term": {"book_id": book}}} for request in erased)


def test_other_book_research_evidence_keeps_original_object_until_last_owner_is_deleted(platform):
    """Storage ownership only; these rows do not represent native content approval."""
    settings, engine = platform
    objects = objects_for(settings)
    original = objects.put_bytes(uuid4().bytes, "application/octet-stream")
    book, run = seed(engine, source=original)
    other, other_run = seed(engine)
    research_id = uuid4().hex
    reference = objects.put_bytes(json.dumps({"retained_original": original}).encode())
    with engine.begin() as connection:
        connection.execute(
            insert(db.research_runs).values(
                id=research_id,
                book_id=other,
                source_run_id=other_run,
                workspace_id="synthetic-ownership",
                state="storage_reference_test",
                version=1,
                input_hash="a" * 64,
                summary={"purpose": "synthetic reference ownership"},
                content=reference,
            )
        )
    cleanup = BookCleanup(
        settings,
        engine,
        search_factory=lambda _: SimpleNamespace(
            indices=SimpleNamespace(exists=lambda **kwargs: False)
        ),
    )
    request_deletion(settings, engine, book)
    cleanup.erase_book(book)
    assert len(objects.read_bytes(original)) == 16
    with engine.connect() as connection:
        assert connection.scalar(select(db.books.c.state).where(db.books.c.id == book)) == "deleted"
        assert connection.scalar(select(db.research_runs.c.id)) == research_id
    request_deletion(settings, engine, other)
    cleanup.erase_book(other)
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError):
        objects.read_bytes(original)
    with engine.connect() as connection:
        assert connection.scalar(select(db.research_runs.c.id)) is None
