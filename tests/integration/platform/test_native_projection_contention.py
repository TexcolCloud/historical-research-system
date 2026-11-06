"""A busy native processing lock must not turn healthy work into a failed book."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from filelock import Timeout
from PIL import Image
from sqlalchemy import insert, select

from hrs.library.books import get_run
from hrs.library.processing_bridge import CodexBridge
from hrs.processing.application import prepare
from hrs.processing.store import Store
from hrs.storage import models as db


@pytest.fixture
def linked_run(platform, tmp_path):
    settings, engine = platform
    bridge = CodexBridge(settings.model_copy(update={"codex_root": tmp_path / "native"}), engine)
    source = tmp_path / "source.png"
    Image.new("RGB", (30, 40), "white").save(source)
    identity = prepare(bridge.store, str(source))["id"]
    book_id, run_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book_id, title="Contention", state="running"))
        connection.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="running",
                stage="ocr",
                source=bridge.objects.put_file(source),
                result={"codex_document_id": identity, "processing_backend": "codex"},
            )
        )
    return bridge, run_id, identity


def test_progress_projection_does_not_acquire_the_processing_lock(linked_run):
    bridge, run_id, identity = linked_run
    holder = Store(bridge.store.root)
    bridge.store.lock.timeout = 0.1
    revision = holder.checkpoints.revision(identity)
    with ThreadPoolExecutor(max_workers=1) as pool, holder.lock:
        pool.submit(bridge.sync, get_run(bridge.engine, run_id)).result(timeout=5)
    assert get_run(bridge.engine, run_id)["state"] == "running"
    assert holder.checkpoints.revision(identity) == revision


def test_repeated_projection_lock_timeouts_retry_without_failing_or_resuming_work(
    linked_run, monkeypatch
):
    bridge, run_id, identity = linked_run
    revision = bridge.store.checkpoints.revision(identity)
    sync = bridge.sync

    def busy(_run):
        raise Timeout(str(bridge.store.root / ".store.lock"))

    monkeypatch.setattr(bridge, "sync", busy)
    for _ in range(4):
        bridge.changes.reconcile_at = 0
        bridge.tick()
        assert bridge.changes.retry_at[identity] > 0
        run = get_run(bridge.engine, run_id)
        assert run["state"] == "running" and not run["error"]
        assert run["result"].get("bridge_failures", 0) == 0
        with bridge.engine.connect() as connection:
            assert (
                connection.scalar(select(db.books.c.state).where(db.books.c.id == run["book_id"]))
                == "running"
            )
    monkeypatch.setattr(bridge, "sync", sync)
    bridge.changes.reconcile_at = 0
    bridge.tick()
    assert identity not in bridge.changes.pending
    assert get_run(bridge.engine, run_id)["result"]["codex_page_progress"]["total"] == 1
    assert bridge.store.checkpoints.revision(identity) == revision
