"""Bound repeated I/O and keep slow evidence checks outside interactive locks."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from PIL import Image
from test_original_reading import reading, submit
from test_store import complete, document

from hrs_codex.details import original_detail
from hrs_codex.server import prepare
from hrs_codex.store import Store


def test_each_evidence_file_is_hashed_once_per_pass_and_tampering_still_fails(
    tmp_path, monkeypatch
):
    from hrs_codex import evidence as module

    source = tmp_path / "source.png"
    Image.new("RGB", (80, 120), "white").save(source)
    store = Store(tmp_path / "library")
    doc_id = prepare(store, str(source))["id"]
    complete(store, doc_id, "ocr", "ocr", {"markdown": "Retained source"})
    task = store.claim_task(doc_id, "review", "review", source_first=True)
    evidence = reading(store, doc_id, task)
    details = [original_detail(store, doc_id, 1, [0, i / 8, 1, (i + 1) / 8])[0] for i in range(8)]
    compact = [{k: d[k] for k in ("image_sha256", "metadata_sha256")} for d in details]
    store.review_candidate(
        doc_id, task["task_id"], task["lease_token"], {**evidence, "original_details": compact}
    )
    submit(store, doc_id, task, {"verdict": "approved", "original_details": compact})
    calls = Counter()
    original = module.file_digest

    def counted(path):
        calls[str(path)] += 1
        return original(path)

    monkeypatch.setattr(module, "file_digest", counted)
    doc = store.status(doc_id)
    store.verify_evidence(doc)
    assert max(calls.values()) == 1, (
        "Repeated compact receipts must not reread the metadata directory"
    )
    store.artifact_path(doc, details[0]["image"]).write_bytes(b"changed original detail")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.verify_evidence(doc)


def test_background_validation_does_not_block_status_or_overwrite_a_newer_snapshot(
    tmp_path, monkeypatch
):
    store, doc_id = document(tmp_path)
    entered, release = Event(), Event()
    verify = store.context.evidence.verify
    passes = []

    def delayed(doc, pages=None):
        passes.append(doc["title"])
        entered.set()
        assert release.wait(3)
        verify(doc, pages)

    monkeypatch.setattr(store.context.evidence, "verify", delayed)
    other = Store(store.root)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(store.claim_task, doc_id, "ocr", "worker")
        try:
            assert entered.wait(2)
            status = pool.submit(other.status, doc_id).result(timeout=0.3)
            with other.lock:
                status.pop("directory", None)
                status.pop("counts", None)
                status["title"] = "Concurrent edit retained"
                status["pages"][0]["page_kind"] = "text"
                other.checkpoints.write(status)
        finally:
            release.set()
        assert claim.result(timeout=3)["stage"] == "ocr"
    assert other.status(doc_id)["title"] == "Concurrent edit retained"
    assert len(passes) == 2


@pytest.mark.parametrize("stage", ["ocr", "organization"])
def test_progress_and_lease_heartbeats_do_not_invalidate_evidence(tmp_path, monkeypatch, stage):
    store, identity = document(tmp_path)
    if stage == "organization":
        complete(store, identity, "ocr", "transcriber", {"markdown": "Retained source"})
        complete(store, identity, "review", "reviewer", {"verdict": "approved", "issues": []})
    store.request_run(identity)
    other = Store(store.root)
    verify = store.context.evidence.verify
    passes = []

    def concurrent_heartbeat(snapshot, pages=None):
        verify(snapshot, pages)
        passes.append(pages)
        with other.lock:
            current = other.checkpoints.read(identity)
            current["run_request"]["last_progress_at"] = len(passes)
            current["run_request"]["lease_expires_at"] = 100000 + len(passes)
            current.setdefault("scheduler", {})["rebalanced_at"] = len(passes)
            other.checkpoints.write(current)

    monkeypatch.setattr(store.context.evidence, "verify", concurrent_heartbeat)
    task = store.claim_task(identity, stage, "worker")
    assert task["stage"] == stage
    assert len(passes) == 1, "Telemetry does not change retained source or decision evidence"
    assert other.status(identity)["run_request"]["last_progress_at"] == 1


def test_status_reads_last_atomic_commit_without_waiting_for_a_writer(tmp_path):
    store, doc_id = document(tmp_path)
    other = Store(store.root)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.lock:
            read = pool.submit(other.status, doc_id)
            assert read.result(timeout=0.3)["id"] == doc_id


def test_a_new_status_reader_does_not_wait_for_the_command_mutex(tmp_path):
    store, doc_id = document(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.lock:
            read = pool.submit(lambda: Store(store.root).status(doc_id))
            assert read.result(timeout=0.3)["id"] == doc_id


def test_dispatch_evidence_validation_does_not_block_pause(tmp_path, monkeypatch):
    store, doc_id = document(tmp_path, count=2)
    other = Store(store.root)
    entered, release = Event(), Event()
    verify = store.context.evidence.verify

    def delayed(doc, pages=None):
        entered.set()
        assert release.wait(3)
        verify(doc, pages)

    monkeypatch.setattr(store.context.evidence, "verify", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatch = pool.submit(store.dispatch.reserve, doc_id)
        try:
            assert entered.wait(2)
            assert (
                pool.submit(other.pause_document, doc_id).result(timeout=0.3)["state"] == "paused"
            )
        finally:
            release.set()
        with pytest.raises(ValueError, match="paused"):
            dispatch.result(timeout=3)


def test_opening_original_pixels_does_not_block_mutation(tmp_path, monkeypatch):
    import hrs_codex.task_inspection as module

    store, doc_id = document(tmp_path)
    other = Store(store.root)
    entered, release = Event(), Event()
    original = module.file_digest

    def delayed(path):
        entered.set()
        assert release.wait(3)
        return original(path)

    monkeypatch.setattr(module, "file_digest", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        image = pool.submit(store.original_page, doc_id, 1)
        try:
            assert entered.wait(2)
            pause = pool.submit(other.pause_document, doc_id)
            assert pause.result(timeout=0.3)["state"] == "paused"
        finally:
            release.set()
        assert image.result(timeout=3)[1]
    assert other.status(doc_id)["run_request"]["pause_requested"]
