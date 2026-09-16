"""Real PostgreSQL/S3 ownership and recoverable deletion, using unique synthetic bytes."""

from hrs_platform.services.lifecycle import RunLifecycle
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from hrs_platform import models as db
from hrs_platform.main import create_app
from hrs_platform.services.books import list_books
from hrs_platform.jobs.deletion import DeletionActivities
from hrs_platform.jobs.deletion import remove_cache
from hrs_platform.services.deletion import request_deletion
from hrs_platform.services.search import search_client


def seed(engine, *, source=None, stage="verify_upload"):
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="删除技术夹具", state="queued"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, state="queued", stage=stage, source=source)
        )
        connection.execute(
            insert(db.outbox).values(run_id=run, kind="start_book", dedup_key=f"start:{run}", payload={})
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
        assert api.post(f"/api/v2/runs/{run}/retry", json={"request_id": str(uuid4())}).status_code == 409
        with engine.connect() as connection:
            assert connection.scalar(select(db.outbox.c.delivered)) is True
        with pytest.raises(Exception, match="正在删除"):
            RunLifecycle(engine).transition(run, "processing", "conversion")


def test_unfinished_upload_without_run_can_be_deleted(platform, monkeypatch):
    settings, engine = platform
    with TestClient(create_app(settings, engine)) as api:
        upload = api.post("/api/v2/uploads", json={"filename": "abandoned.pdf", "byte_length": 128}).json()
        book_id = upload["book_id"]
        assert api.get(f"/api/v2/books/{book_id}").json()["run_id"] is None
        response = api.delete(f"/api/v2/books/{book_id}")
        assert response.status_code == 202, response.text
        assert api.get(f"/api/v2/books/{book_id}").json()["state"] == "deleting"
        headers = {"Authorization": "Bearer " + upload["token"]}
        info = {
            "ID": upload["session_id"] + "/" + str(uuid4()) + "+multipart-id",
            "Size": 128,
            "MetaData": {"session_id": upload["session_id"]},
        }
        stopped = []
        monkeypatch.setattr(
            "hrs_platform.services.upload_cleanup.terminate_upload",
            lambda settings, identity, session: stopped.append(identity),
        )
        assert api.post(
            "/internal/tus", json={"Type": "post-create", "Event": {"Upload": info}}, headers=headers
        ).json()["StopUpload"]
        assert stopped == [info["ID"]]
        assert api.post(
            "/internal/tus", json={"Type": "post-receive", "Event": {"Upload": info}}, headers=headers
        ).json()["StopUpload"]
        assert api.post(
            "/internal/tus", json={"Type": "pre-create", "Event": {"Upload": info}}, headers=headers
        ).json()["RejectUpload"]
        DeletionActivities(settings, engine).erase_book(book_id)
        assert api.get(f"/api/v2/books/{book_id}").status_code == 404


def test_nested_files_shared_retention_and_retry_after_partial_cleanup(platform, tmp_path, monkeypatch):
    settings, engine = platform
    settings = settings.model_copy(
        update={"cache_root": tmp_path, "opensearch_index": "test-delete-" + uuid4().hex}
    )
    cleanup = DeletionActivities(settings, engine)
    objects = cleanup.objects
    shared = objects.put_bytes(("shared-" + uuid4().hex).encode(), "text/plain")
    exclusive = objects.put_bytes(("exclusive-" + uuid4().hex).encode(), "text/plain")
    bundle = objects.put_bytes(json.dumps({"files": [shared, exclusive]}).encode())
    book, run = seed(engine, source=bundle)
    other, _ = seed(engine, source=shared)
    search = search_client(settings.opensearch_url)
    search.indices.create(
        index=settings.opensearch_index, body={"mappings": {"properties": {"book_id": {"type": "keyword"}}}}
    )
    for identity in (book, other):
        search.index(index=settings.opensearch_index, id=identity, body={"book_id": identity}, refresh=True)
    upload_id = str(uuid4())
    upload_key = settings.upload_prefix + upload_id + "/" + str(uuid4())
    objects.client.put_object(Bucket=objects.bucket, Key=upload_key, Body=b"original uploaded bytes")
    objects.client.create_multipart_upload(Bucket=objects.bucket, Key=upload_key + "-partial")
    with engine.begin() as connection:
        connection.execute(
            insert(db.uploads).values(
                id=upload_id,
                book_id=book,
                filename="fixture.pdf",
                byte_length=23,
                token_hash="0" * 64,
                state="received",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    cache = tmp_path / run
    cache.mkdir()
    (cache / "temporary.bin").write_bytes(b"owned")
    request_deletion(engine, book)
    original_delete = objects.client.delete_object
    once = []

    def fail_after_one(**kwargs):
        if once:
            raise OSError("injected transport failure")
        once.append(kwargs["Key"])
        return original_delete(**kwargs)

    monkeypatch.setattr(objects.client, "delete_object", fail_after_one)
    with pytest.raises(OSError, match="transport"):
        cleanup.erase_book(book)
    assert any(row["id"] == book for row in list_books(engine))
    cleanup.deletion_failed(book)
    assert request_deletion(engine, book)["state"] == "pending"
    monkeypatch.setattr(objects.client, "delete_object", original_delete)
    assert cleanup.erase_book(book)["state"] == "completed"
    assert [row["id"] for row in list_books(engine)] == [other]
    assert search.count(index=settings.opensearch_index)["count"] == 1
    assert search.get(index=settings.opensearch_index, id=other)["_source"]["book_id"] == other
    assert not cache.exists()
    assert not objects.client.list_objects_v2(
        Bucket=objects.bucket, Prefix=settings.upload_prefix + upload_id
    ).get("Contents")
    assert not objects.client.list_multipart_uploads(
        Bucket=objects.bucket, Prefix=settings.upload_prefix + upload_id
    ).get("Uploads")
    assert objects.read_bytes(shared).startswith(b"shared-")
    for ref in (exclusive, bundle):
        with pytest.raises(ClientError) as error:
            objects.client.head_object(Bucket=objects.bucket, Key=ref["key"])
        assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404
    assert request_deletion(engine, book)["state"] == "completed"
    request_deletion(engine, other)
    cleanup.erase_book(other)
    assert search.count(index=settings.opensearch_index)["count"] == 0
    search.indices.delete(index=settings.opensearch_index)
    with pytest.raises(ClientError):
        objects.client.head_object(Bucket=objects.bucket, Key=shared["key"])


def test_cache_cleanup_refuses_non_task_paths(tmp_path):
    protected = tmp_path / "keep"
    protected.mkdir()
    with pytest.raises(ValueError):
        remove_cache(tmp_path, ["../keep"])
    assert protected.exists()
