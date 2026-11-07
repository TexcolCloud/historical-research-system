"""Uploads own short-lived download copies, never durable native intake PDFs."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from sqlalchemy import insert, select, update

from hrs.library import processing_bridge
from hrs.library.books import get_run
from hrs.library.errors import ServiceError
from hrs.library.processing_bridge import CodexBridge
from hrs.processing.workspace import identity
from hrs.storage import models as db


@pytest.fixture
def intake_run(platform):
    settings, engine = platform
    bridge = CodexBridge(settings, engine)
    writer, stream = PdfWriter(), BytesIO()
    writer.add_blank_page(width=200, height=300)
    writer.add_metadata({"/Title": "Intake fixture " + uuid4().hex})
    writer.write(stream)
    content = stream.getvalue()
    source = bridge.objects.put_bytes(content, "application/pdf")
    book_id, run_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book_id, title="Intake fixture", state="queued"))
        connection.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="queued",
                stage="verify_upload",
                source=source,
                result={
                    "processing_backend": "codex",
                    "codex_workspace_id": identity(bridge.store)["workspace_id"],
                },
            )
        )
        event = dict(
            connection.execute(
                insert(db.outbox)
                .values(run_id=run_id, kind="start_book", payload={}, dedup_key=run_id)
                .returning(db.outbox)
            )
            .mappings()
            .one()
        )
    try:
        yield bridge, event, content, source
    finally:
        bridge.objects.client.delete_object(Bucket=bridge.objects.bucket, Key=source["key"])


def assert_intake_released(bridge, paths):
    assert paths and all(not path.exists() for path in paths)
    assert all(path.is_relative_to(bridge.settings.cache_root) for path in paths)
    assert not (bridge.store.root / "platform-intake").exists()


def test_dispatch_keeps_only_native_original_after_success(intake_run, monkeypatch):
    bridge, event, content, source = intake_run
    prepare = processing_bridge.prepare
    downloaded = []

    def observe(store, path, title):
        downloaded.append(Path(path))
        assert Path(path).read_bytes() == content
        return prepare(store, path, title)

    monkeypatch.setattr(processing_bridge, "prepare", observe)
    bridge.dispatch(event)
    assert_intake_released(bridge, downloaded)
    run = get_run(bridge.engine, event["run_id"])
    document = bridge.store.status(run["result"]["codex_document_id"])
    bridge.store.verify_evidence(document)
    native_source = bridge.store.document_directory(document["id"]) / document["source"]["path"]
    assert native_source.read_bytes() == content
    assert bridge.objects.read_bytes(run["source"]) == bridge.objects.read_bytes(source) == content
    assert document["run_request"]["state"] == "pending"
    with bridge.engine.connect() as connection:
        assert connection.scalar(select(db.outbox.c.delivered)) is True


@pytest.mark.parametrize("failure", ["prepare", "object_upload"])
def test_failed_intake_releases_download_and_retries_original(intake_run, monkeypatch, failure):
    bridge, event, content, source = intake_run
    prepare, put_file = processing_bridge.prepare, bridge.objects.put_file
    downloaded = []

    def observe(store, path, title):
        downloaded.append(Path(path))
        if failure == "prepare":
            raise ValueError("Synthetic preparation failure")
        return prepare(store, path, title)

    def fail_upload(*args, **kwargs):
        raise ValueError("Synthetic object upload failure")

    monkeypatch.setattr(processing_bridge, "prepare", observe)
    if failure == "object_upload":
        monkeypatch.setattr(bridge.objects, "put_file", fail_upload)
    with pytest.raises(ValueError, match="Synthetic"):
        bridge.dispatch(event)
    assert_intake_released(bridge, downloaded)
    assert bridge.objects.read_bytes(source) == content
    with bridge.engine.connect() as connection:
        assert connection.scalar(select(db.outbox.c.delivered)) is False
    assert not get_run(bridge.engine, event["run_id"])["result"].get("codex_document_id")

    monkeypatch.setattr(processing_bridge, "prepare", prepare)
    monkeypatch.setattr(bridge.objects, "put_file", put_file)
    bridge.dispatch(event)
    assert len(bridge.store.list_documents()) == 1
    run = get_run(bridge.engine, event["run_id"])
    assert bridge.store.status(run["result"]["codex_document_id"])["run_request"]["state"] == "pending"
    assert bridge.objects.read_bytes(run["source"]) == content


@pytest.mark.parametrize("invalid_length", [False, True])
def test_streamed_upload_closes_body_validates_length_and_releases_copy(
    intake_run, monkeypatch, invalid_length
):
    bridge, event, content, _ = intake_run
    key = "hrs/v2/uploads/intake-fixture-" + uuid4().hex
    client = bridge.objects.client
    client.put_object(Bucket=bridge.objects.bucket, Key=key, Body=content)
    with bridge.engine.begin() as connection:
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == event["run_id"])
            .values(source={"upload_key": key, "byte_length": len(content) + invalid_length})
        )
    get_object, prepare = client.get_object, processing_bridge.prepare
    closed, downloaded = [], []

    def observe_body(**kwargs):
        response = get_object(**kwargs)
        body, close = response["Body"], response["Body"].close

        def record_close():
            closed.append(True)
            close()

        monkeypatch.setattr(body, "close", record_close)
        return response

    def observe_prepare(store, path, title):
        downloaded.append(Path(path))
        return prepare(store, path, title)

    monkeypatch.setattr(client, "get_object", observe_body)
    monkeypatch.setattr(processing_bridge, "prepare", observe_prepare)
    try:
        if invalid_length:
            with pytest.raises(ValueError, match="length differs"):
                bridge.dispatch(event)
            assert not downloaded and bridge.store.list_documents() == []
        else:
            bridge.dispatch(event)
            assert_intake_released(bridge, downloaded)
        assert closed and list((bridge.settings.cache_root / "intake").iterdir()) == []
    finally:
        client.delete_object(Bucket=bridge.objects.bucket, Key=key)


def test_book_deletion_during_intake_rejects_work_and_still_releases_copy(
    intake_run, monkeypatch
):
    bridge, event, content, source = intake_run
    put_file = bridge.objects.put_file

    def delete_before_upload(*args, **kwargs):
        run = get_run(bridge.engine, event["run_id"])
        with bridge.engine.begin() as connection:
            connection.execute(
                update(db.books).where(db.books.c.id == run["book_id"]).values(state="deleting")
            )
        return put_file(*args, **kwargs)

    monkeypatch.setattr(bridge.objects, "put_file", delete_before_upload)
    with pytest.raises(ServiceError):
        bridge.dispatch(event)
    assert list((bridge.settings.cache_root / "intake").iterdir()) == []
    assert bridge.objects.read_bytes(source) == content
    assert all(
        not bridge.store.status(doc["id"]).get("run_request")
        for doc in bridge.store.list_documents()
    )
    with bridge.engine.connect() as connection:
        assert connection.scalar(select(db.outbox.c.delivered)) is False
