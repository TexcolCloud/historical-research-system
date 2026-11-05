"""Synthetic execution checks: durable dispatch must never invent source approval."""

import threading
import time

from PIL import Image

from hrs_codex.runner import Runner
from hrs_codex.server import prepare
from hrs_codex.store import Store


def fixture(tmp_path):
    image = tmp_path / "fixture.png"
    Image.new("RGB", (50, 60), "white").save(image)
    store = Store(tmp_path / "workspace")
    return store, prepare(store, str(image), "Synthetic runner fixture")["id"]


def submit(store, doc_id, stage, result):
    task = store.claim_task(doc_id, stage, "synthetic-" + stage)
    store.submit_task(
        doc_id,
        task["task_id"],
        task["lease_token"],
        {"input_sha256": task["input_sha256"], **result},
    )


def test_background_dispatch_resumes_after_explicit_synthetic_human_decision(tmp_path):
    store, doc_id = fixture(tmp_path)
    store.request_run(doc_id)
    calls = []

    def execute(s, document_id, **options):
        calls.append(document_id)
        if s.status(document_id)["pages"][0]["state"] == "awaiting_ocr":
            submit(s, document_id, "ocr", {"markdown": "Synthetic unclear character"})
            submit(
                s, document_id, "review", {"verdict": "pending", "issues": ["Synthetic ambiguity"]}
            )
        else:
            submit(
                s,
                document_id,
                "organization",
                {"articles": [{"title": "Synthetic article", "pages": [1]}]},
            )
            s.publish(document_id)

    with Runner(store, execute, poll_seconds=0.02):
        deadline = time.monotonic() + 5
        while store.status(doc_id).get("run_request", {}).get("state") != "waiting_human":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert len(calls) == 1
        packet = store.human_packet(doc_id, 1)
        store.record_human_review(
            doc_id,
            1,
            input_sha256=packet["input_sha256"],
            reviewer="Synthetic fixture reviewer",
            markdown="Synthetic resolved text",
            note="Synthetic fixture only",
            verdict="approved",
        )
        while store.status(doc_id)["run_request"]["state"] != "succeeded":
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert len(calls) == 2
    assert Store(store.root).search("resolved")[0]["document_id"] == doc_id
    assert store.status(doc_id)["publication"]["approval_type"] == "mixed"


def test_two_runners_do_not_duplicate_active_document_and_renew_lease(tmp_path):
    store, doc_id = fixture(tmp_path)
    store.request_run(doc_id)
    entered, release = threading.Event(), threading.Event()

    def blocking(s, document_id, **options):
        entered.set()
        assert release.wait(5)

    first = Runner(store, blocking, lease_seconds=1)
    worker = threading.Thread(target=first.tick)
    worker.start()
    assert entered.wait(3)
    second = Runner(
        Store(store.root),
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate run")),
    )
    assert second.tick() is False
    time.sleep(1.2)
    assert store.status(doc_id)["run_request"]["lease_expires_at"] > time.time()
    assert second.tick() is False
    release.set()
    worker.join(timeout=5)
    request = store.status(doc_id)["run_request"]
    assert request["attempts"] == 1
    assert request["state"] == "retryable_error"
    assert not store.human_queue()


def test_executor_failure_is_recoverable_runtime_error_not_human_uncertainty(tmp_path):
    store, doc_id = fixture(tmp_path)
    store.request_run(doc_id)

    def failing(*args, **kwargs):
        raise OSError("Synthetic disconnected service")

    runner = Runner(store, failing)
    assert runner.tick(document_id=doc_id)
    request = store.status(doc_id)["run_request"]
    assert request["state"] == "retryable_error"
    assert request["technical_failures"] == 1
    assert request["error"]["type"] == "OSError"
    assert runner.tick(document_id=doc_id) is False
    assert store.human_queue() == []
    assert store.status(doc_id)["pages"][0]["state"] == "awaiting_ocr"
    assert store.request_run(doc_id, resume=True)["state"] == "pending"


def test_lost_document_lease_cancels_only_its_executor(tmp_path, monkeypatch):
    import pytest

    store, doc_id = fixture(tmp_path)
    store.request_run(doc_id)

    def lost(*args, **kwargs):
        raise ValueError("Synthetic lease lost")

    monkeypatch.setattr(store, "renew_run", lost)

    def execute(s, identity, stop_event):
        assert stop_event.wait(2), "Lost ownership must stop this coordinator"

    runner = Runner(store, execute, lease_seconds=1)
    with pytest.raises(RuntimeError, match="lease lost"):
        runner.tick(doc_id)
    assert not runner.stop_event.is_set(), "One lost lease must not stop the workspace dispatcher"


def test_waiting_for_valid_workers_is_not_a_technical_failure(tmp_path):
    store, doc_id = fixture(tmp_path)
    store.request_run(doc_id)
    request = store.claim_run("coordinator")
    task = store.claim_task(doc_id, "ocr", "still-running-worker")
    finished = store.finish_run(doc_id, request["token"])
    assert finished["state"] == "waiting_workers"
    assert finished["error"] is None and finished.get("technical_failures", 0) == 0
    store.submit_task(
        doc_id,
        task["task_id"],
        task["lease_token"],
        {"input_sha256": task["input_sha256"], "markdown": "正文"},
    )
    assert store.claim_run("next-coordinator")["state"] == "running"


def test_a_book_waiting_on_its_coordinator_does_not_serialize_the_next_book(tmp_path):
    store, first_id = fixture(tmp_path)
    second = tmp_path / "second.png"
    Image.new("RGB", (50, 60), "black").save(second)
    second_id = prepare(store, str(second))["id"]
    for identity in (first_id, second_id):
        store.request_run(identity)
    started, release = threading.Event(), threading.Event()
    active, lock = set(), threading.Lock()

    def execute(s, identity, **options):
        with lock:
            active.add(identity)
            if len(active) == 2:
                started.set()
        assert release.wait(5)

    with Runner(store, execute, poll_seconds=0.02):
        try:
            assert started.wait(2), "The second book must start while the first is still active"
        finally:
            release.set()


def test_explicit_resume_clears_the_current_error_but_preserves_failure_history(tmp_path):
    store, doc_id = fixture(tmp_path)
    store.request_run(doc_id)
    run = store.claim_run("failed-coordinator")
    store.finish_run(doc_id, run["token"], error={"type": "transport"})
    resumed = store.request_run(doc_id, resume=True)
    assert resumed["error"] is None
    assert any(entry.get("error") == {"type": "transport"} for entry in resumed["history"])
