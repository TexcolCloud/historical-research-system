"""Synthetic regressions for cross-stage lifecycle and storage failures."""

import asyncio
import io
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from sqlalchemy import insert, select, update

from hrs_platform import models as db
from hrs_platform.jobs.conversion import Activities
from hrs_platform.services.deletion import request_deletion
from hrs_platform.services.review import Review
from test_book_deletion import seed


def test_initialize_cannot_resurrect_deleting_book(platform, monkeypatch):
    settings, engine = platform
    book, run = seed(engine, stage="conversion")
    with engine.begin() as c:
        c.execute(update(db.runs).where(db.runs.c.id == run).values(conversion={"fixture": "bundle"}))
    request_deletion(engine, book)
    review = Review(settings, engine)
    bundle = {
        "files": {"md": "md", "cards": "cards", "pages": "pages"},
        "manifest": {
            "candidate_markdown": "md",
            "review_cards": "cards",
            "page_boundaries": "pages",
            "pages": [{"page": 1, "status": "release-accepted"}],
        },
    }
    monkeypatch.setattr(
        review,
        "read_json",
        lambda ref: (
            bundle
            if isinstance(ref, dict)
            else {"cards": []}
            if ref == "cards"
            else {"pages": [{"page": 1, "start": 0, "end": 7}]}
        ),
    )
    monkeypatch.setattr(review.objects, "read_bytes", lambda _: b"fixture")
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="409"):
        review.initialize(run)
    with engine.connect() as c:
        assert c.scalar(select(db.books.c.state).where(db.books.c.id == book)) == "deleting"


def test_verified_upload_releases_temporary_pdf(tmp_path, monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    data = stream.getvalue()

    class Body(io.BytesIO):
        def iter_chunks(self, size):
            yield self.read()

    activity = Activities.__new__(Activities)
    activity.engine = None
    activity.settings = SimpleNamespace(cache_root=tmp_path)
    activity.objects = SimpleNamespace(
        bucket="fake",
        client=SimpleNamespace(get_object=lambda **_: {"ContentLength": len(data), "Body": Body(data)}),
        put_file=lambda *a, **k: {"sha256": "saved"},
    )
    monkeypatch.setattr(
        "hrs_platform.jobs.conversion.get_run",
        lambda *a: {"source": {"upload_key": "fake", "byte_length": len(data)}},
    )
    monkeypatch.setattr("hrs_platform.jobs.conversion.RunLifecycle",
                        lambda *_: SimpleNamespace(transition=lambda *a, **k: None))
    for _ in range(2):
        activity.verify_upload(str(uuid4()))
    assert not list(tmp_path.rglob("*.pdf"))
    assert not list(tmp_path.rglob("*.downloading"))


def test_delivery_cursor_keeps_late_commits(platform):
    from hrs_platform.services.events import read_events

    _, engine = platform
    book, _ = seed(engine)
    with engine.connect() as first:
        transaction = first.begin()
        early = first.scalar(
            insert(db.events).values(book_id=book, kind="early", payload={}).returning(db.events.c.sequence)
        )
        with engine.begin() as second:
            second.execute(insert(db.events).values(book_id=book, kind="late", payload={}))
        batch = read_events(engine, 0)
        assert [e["kind"] for e in batch] == ["late"]
        transaction.commit()
    followup = read_events(engine, batch[-1]["sequence"])
    assert [e["kind"] for e in followup] == ["early"]
    assert followup[0]["sequence"] > batch[-1]["sequence"]
    assert read_events(engine, followup[-1]["sequence"]) == []


def test_isolated_stage_does_not_block_heartbeat():
    from hrs_platform.jobs.pipeline import isolated

    async def scenario():
        ticks = []

        async def heartbeat():
            for _ in range(8):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        async def slow_storage():
            time.sleep(0.2)
            return "saved"

        result, _ = await asyncio.gather(isolated(slow_storage()), heartbeat())
        assert result == "saved"
        assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.15

    asyncio.run(scenario())


def test_unpublished_shared_object_survives_other_book_deletion(platform, tmp_path, monkeypatch):
    from hrs_platform.services.storage import objects_for
    from hrs_platform.jobs.deletion import DeletionActivities
    from hrs_runtime.object_storage import S3Objects
    import hashlib

    settings, engine = platform
    settings = settings.model_copy(update={"cache_root": tmp_path})
    a, ar = seed(engine)
    b, br = seed(engine)
    objects, present = objects_for(settings, engine), {}

    def put_bytes(self, content, media_type):
        sha = hashlib.sha256(content).hexdigest()
        ref = {
            "key": f"hrs/v2/objects/{sha[:2]}/{sha}",
            "sha256": sha,
            "byte_length": len(content),
            "media_type": media_type,
        }
        present[ref["key"]] = content
        return ref

    monkeypatch.setattr(S3Objects, "put_bytes", put_bytes)
    ref = objects.put_bytes(b"synthetic shared object", "text/plain", run_id=ar)
    # B has finished S3 upload but has not yet published its business reference.
    assert objects.put_bytes(b"synthetic shared object", "text/plain", run_id=br) == ref
    request_deletion(engine, a)
    cleanup = DeletionActivities(settings, engine)
    cleanup.objects = SimpleNamespace(
        bucket="fake", client=SimpleNamespace(delete_object=lambda **kw: present.pop(kw["Key"], None))
    )
    monkeypatch.setattr(
        "hrs_platform.services.deletion.search_client",
        lambda _: SimpleNamespace(indices=SimpleNamespace(exists=lambda **_: False)),
    )
    assert cleanup.erase_book(a)["state"] == "completed"
    assert ref["key"] in present
    with engine.begin() as c:
        c.execute(update(db.runs).where(db.runs.c.id == br).values(source=ref))
    request_deletion(engine, b)
    assert cleanup.erase_book(b)["state"] == "completed"
    assert ref["key"] not in present


def test_index_uses_owned_requests_and_durable_recovery(platform, monkeypatch):
    from hrs_platform.jobs.pipeline import PipelineActivities
    from hrs_platform.services.search import remote_compute
    from hrs_platform.domain.errors import Problem
    from hrs_platform.services.outputs import Outputs
    from temporalio.exceptions import ApplicationError

    settings, engine = platform
    _, run = seed(engine, stage="indexing")
    pipeline = PipelineActivities(settings, engine)
    attempts, requests = [], []

    def index(run):
        attempts.append(run)
        return remote_compute("http://fixture.invalid/retrieval", "embed", ["fixture"])

    search = SimpleNamespace(index=index, outputs=Outputs(settings, engine))
    monkeypatch.setattr("hrs_platform.jobs.pipeline.Search", lambda *_: search)

    async def observe(run, operation, **kwargs):
        return await operation

    monkeypatch.setattr(pipeline, "observe", observe)

    def post(endpoint, **kw):
        requests.append(kw["json"])
        raise Problem("retrieval_unavailable", "synthetic offline", retryable=True)

    monkeypatch.setattr("httpx.post", post)
    for _ in range(2):
        with pytest.raises(ApplicationError) as error:
            asyncio.run(pipeline.index_book(run))
        assert error.value.type == "retrieval_wait"
    assert attempts == [run]  # Cooldown survives activity re-entry.
    assert requests[0]["task_id"] == run
    assert requests[0]["request_id"]


def test_isolated_cancellation_drains_real_operation():
    from threading import Event
    from hrs_platform.jobs.pipeline import isolated

    started, drained = Event(), Event()

    async def scenario():
        async def operation():
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                await asyncio.sleep(0.08)
                drained.set()

        task = asyncio.create_task(isolated(operation()))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set()

    asyncio.run(scenario())


def test_slow_upload_allows_deletion_request_and_other_book_writes(platform, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import hashlib
    from hrs_runtime.object_storage import S3Objects
    from hrs_platform.services.storage import objects_for
    from hrs_platform.jobs.deletion import DeletionActivities

    settings, engine = platform
    settings = settings.model_copy(update={"cache_root": tmp_path})
    book, run = seed(engine)
    _, other = seed(engine)
    started, release = Event(), Event()
    present = {}

    def slow_write(self, content, media_type):
        digest = hashlib.sha256(content).hexdigest()
        ref = {
            "key": f"hrs/v2/objects/{digest[:2]}/{digest}",
            "sha256": digest,
            "byte_length": len(content),
            "media_type": media_type,
        }
        if content == b"slow":
            started.set()
            assert release.wait(5)
        present[ref["key"]] = content
        return ref

    monkeypatch.setattr(S3Objects, "put_bytes", slow_write)
    objects = objects_for(settings, engine)
    cleanup = DeletionActivities(settings, engine)
    cleanup.objects = SimpleNamespace(
        bucket="fake", client=SimpleNamespace(delete_object=lambda **kw: present.pop(kw["Key"], None))
    )
    monkeypatch.setattr(
        "hrs_platform.services.deletion.search_client",
        lambda _: SimpleNamespace(indices=SimpleNamespace(exists=lambda **_: False)),
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        uploading = pool.submit(objects.put_bytes, b"slow", "text/plain", run_id=run)
        assert started.wait(2)
        try:
            assert pool.submit(request_deletion, engine, book).result(timeout=2)["state"] == "pending"
            other_ref = pool.submit(objects.put_bytes, b"fast", "text/plain", run_id=other).result(timeout=2)
            erasing = pool.submit(cleanup.erase_book, book)
            time.sleep(0.15)
            assert not erasing.done()  # In-flight object cannot outlive deletion.
        finally:
            release.set()
        ref = uploading.result(timeout=3)
        assert erasing.result(timeout=3)["state"] == "completed"
    assert ref["key"] not in present
    assert other_ref["key"] in present


def test_reentered_upload_removes_old_source_cache(tmp_path, monkeypatch):
    run = str(uuid4())
    path = tmp_path / run / "source.pdf"
    path.parent.mkdir()
    path.write_bytes(b"old temporary copy")
    activity = Activities.__new__(Activities)
    activity.settings, activity.engine = SimpleNamespace(cache_root=tmp_path), None
    activity.objects = SimpleNamespace(verify=lambda _: None)
    monkeypatch.setattr("hrs_platform.jobs.conversion.get_run", lambda *_: {"source": {"sha256": "saved"}})
    activity.verify_upload(run)
    assert not path.exists()


def test_publish_cannot_resurrect_deleting_book(platform, monkeypatch):
    from hrs_platform.services.library import Library
    from fastapi import HTTPException

    settings, engine = platform
    book, run = seed(engine)
    library = Library(settings, engine)
    monkeypatch.setattr(library.review, "status", lambda _: {"pending_count": 0})
    monkeypatch.setattr(library.outputs, "get", lambda *a: {"groups": []})
    monkeypatch.setattr(library, "structure", lambda _: {})
    request_deletion(engine, book)
    with pytest.raises(HTTPException):
        library.publish(run)
    with engine.connect() as connection:
        assert connection.scalar(select(db.books.c.state).where(db.books.c.id == book)) == "deleting"


def test_delivery_migration_preserves_issued_cursors(platform):
    from sqlalchemy import text
    from hrs_platform.core.db import migrate
    from hrs_platform.services.events import read_events

    _, engine = platform
    book, _ = seed(engine)
    # Roll this disposable test schema back to the preceding physical layout;
    # production downgrades remain deliberately disabled.
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE object_owners"))
        connection.execute(text("ALTER TABLE events DROP COLUMN delivery_sequence"))
        connection.execute(text("DROP SEQUENCE event_delivery_sequence"))
        connection.execute(text("UPDATE alembic_version SET version_num='0007_book_deletions'"))
        connection.execute(
            text("INSERT INTO events(sequence,book_id,kind,payload) VALUES (50,:book,'old','{}')"),
            {"book": book},
        )
    migrate(engine)
    assert read_events(engine, 50) == []
    with engine.begin() as connection:
        connection.execute(insert(db.events).values(book_id=book, kind="new", payload={}))
    assert read_events(engine, 50)[0]["sequence"] > 50


def test_async_activity_lock_wait_keeps_heartbeats_on_worker_loop(platform):
    from dataclasses import replace
    from threading import get_ident
    from sqlalchemy import text
    from temporalio.testing import ActivityEnvironment
    from hrs_platform.jobs.pipeline import PipelineActivities
    from hrs_platform.services.storage import object_lock

    settings, engine = platform
    _, run = seed(engine)
    environment = ActivityEnvironment()
    environment.info = replace(environment.info, activity_type="index_book")
    main_thread, heartbeats = get_ident(), []
    def heartbeat(*_):
        assert get_ident() == main_thread
        heartbeats.append(True)
    environment.on_heartbeat = heartbeat
    key = "synthetic-lock-contention"
    async def scenario():
        with engine.begin() as holder:
            holder.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
            def write():
                with object_lock(engine, key):
                    return "saved"
            async def stage():
                return await asyncio.to_thread(write)
            task = asyncio.create_task(environment.run(PipelineActivities(settings, engine).observe, run, stage()))
            await asyncio.sleep(.35)
            assert not task.done()
        assert await asyncio.wait_for(task, 3) == "saved"
    asyncio.run(scenario())
    assert heartbeats
