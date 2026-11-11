"""Native SQLite supervision contracts; no model inference is replaced by these checks."""

import json
import time

import pytest

from hrs.processing.execution_policy import configure_execution
from hrs.processing.store import Store
from hrs.studies.analysis import MODEL, AnalysisService, AnalysisWorkflow
from hrs.studies.execution import StudyAnalysisRunner
from hrs.studies.store import StudyStore


@pytest.fixture
def analysis(tmp_path):
    native = Store(tmp_path / "native")
    configure_execution(native.root, capacity=2)
    store = StudyStore(native)
    topic = store.create("topic", {"title": "Synthetic personal topic"})
    frozen = store.create("analysis_input", {"sources": []}, topic_id=topic["id"])
    job = store.create(
        "analysis",
        {"mode": "summary", "state": "queued", "input_id": frozen["id"], "tasks": {}},
        topic_id=topic["id"],
    )
    flow = AnalysisWorkflow(store)
    flow.begin(job["id"], "execution")
    worker = f"{job['id']}-execution-author"
    flow.attest(job["id"], worker, "native-child")
    return native, store, flow, job["id"], worker


def test_native_processing_occupancy_blocks_personal_analysis(analysis):
    native, store, flow, job_id, worker = analysis
    for index in range(2):
        store.ledger.save_session(
            {
                "session_token": f"ocr-{index}",
                "document_id": "source",
                "stage": "ocr",
                "actual_model": "gpt-6.1-terra",
                "state": "busy",
                "expires_at": time.time() + 300,
            }
        )
    assert flow.claim(job_id, worker, "author")["wait"]
    assert store.ledger.capacity_used() == 2
    assert native.runtime_control.snapshot()["state"] == "normal"


def test_personal_analysis_occupies_native_pool_and_claim_retry_is_idempotent(analysis):
    native, store, flow, job_id, worker = analysis
    first = flow.claim(job_id, worker, "author")
    assert store.ledger.capacity_used() == store.ledger.model_capacity_used(MODEL) == 1
    coordinator = "research-coordinator:other-book"
    assert not native.runtime_control.reserve_coordinator(MODEL, coordinator)["allowed"]
    assert flow.claim(job_id, worker, "author")["token"] == first["token"]
    assert store.ledger.capacity_used() == 1
    store.ledger.save_session(
        {
            "session_token": "native-sol",
            "document_id": "source",
            "stage": "review",
            "actual_model": MODEL,
            "state": "busy",
            "expires_at": time.time() + 300,
        }
    )
    assert not native.runtime_control.admit(
        MODEL,
        "another-native-worker",
        occupied=store.ledger.model_capacity_used(MODEL),
        total_occupied=store.ledger.capacity_used(),
    )["allowed"]
    session = store.ledger.sessions()[0]
    store.ledger.save_session({**session, "state": "exited"})
    AnalysisService(type("Studies", (), {"store": store})()).cancel(job_id, 1)
    assert store.ledger.capacity_used() == 0
    assert native.runtime_control.reserve_coordinator(MODEL, coordinator)["allowed"]


def test_analysis_runner_preserves_one_worker_slot(analysis, monkeypatch):
    native, store, _, job_id, _ = analysis
    current = store.read("analysis", job_id)
    store.save("analysis", job_id, {"state": "queued"}, expected_version=current["version"])
    native.runtime_control.reserve_coordinator(MODEL, "research-coordinator:book")
    monkeypatch.setattr("hrs.studies.execution.shutil.which", lambda name: None)
    StudyAnalysisRunner(native).execute(job_id)
    assert store.read("analysis", job_id)["state"] == "queued"
    assert store.ledger.capacity_used() == 1


def test_failed_analysis_releases_worker_reservation(analysis):
    native, store, flow, job_id, worker = analysis
    flow.claim(job_id, worker, "author")
    assert store.ledger.capacity_used() == 1
    StudyAnalysisRunner(native)._fail(job_id, "Synthetic supervisor failure")
    assert store.ledger.capacity_used() == 0


def test_queue_selects_active_work_before_terminal_pagination(analysis, monkeypatch):
    native, store, _, job_id, _ = analysis
    current = store.read("analysis", job_id)
    store.save("analysis", job_id, {"state": "queued"}, expected_version=current["version"])
    with store.transaction():
        for _ in range(215):
            store.create("analysis", {"state": "ready"})
    runner = StudyAnalysisRunner(native)
    seen = []
    monkeypatch.setattr(runner, "execute", seen.append)
    runner.tick()
    assert seen == [job_id]


@pytest.mark.parametrize(
    "line",
    [
        "null",
        "[]",
        '{"item":null}',
        '{"type":"item.completed","item":{"type":"collab_tool_call","tool":"spawn_agent","status":[],"prompt":null,"receiver_thread_ids":null}}',
    ],
)
def test_malformed_runtime_events_cannot_attest_a_native_worker(analysis, line):
    native, store, _, job_id, _ = analysis
    before = store.read("analysis", job_id)
    StudyAnalysisRunner(native)._attest(job_id, line)
    assert store.read("analysis", job_id) == before


def test_only_host_completed_spawn_binds_distinct_native_thread(analysis):
    native, store, _, job_id, _ = analysis
    runner = StudyAnalysisRunner(native)
    current = store.read("analysis", job_id)
    store.save("analysis", job_id, {"tasks": {}}, expected_version=current["version"])
    event = {
        "type": "item.completed",
        "item": {
            "type": "collab_tool_call",
            "tool": "spawn_agent",
            "prompt": f"worker_id='{job_id}-execution-author'",
            "receiver_thread_ids": ["genuine-native-thread"],
        },
    }
    runner._attest(job_id, json.dumps({**event, "type": "item.started"}))
    assert not store.read("analysis", job_id)["tasks"]
    runner._attest(job_id, json.dumps(event))
    assert store.read("analysis", job_id)["tasks"]["author"]["thread_id"] == "genuine-native-thread"


@pytest.mark.parametrize("provider_error", [True, False])
def test_engine_capacity_failure_defers_but_agent_prose_never_restricts_pool(
    analysis, monkeypatch, provider_error
):
    native, store, _, job_id, _ = analysis
    current = store.read("analysis", job_id)
    store.save("analysis", job_id, {"state": "queued"}, expected_version=current["version"])
    event = (
        {
            "type": "turn.failed",
            "error": {"code": "model_at_capacity", "message": "Model at capacity"},
        }
        if provider_error
        else {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "This source mentions rate limits and model at capacity.",
            },
        }
    )

    class NativeProcess:
        returncode = None

        def __init__(self, arguments, **options):
            self.output = options["stdout"]

        def communicate(self, **options):
            self.output.write(json.dumps(event) + "\n")
            self.output.flush()
            self.returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr("hrs.studies.execution.shutil.which", lambda name: "codex")
    monkeypatch.setattr(
        "hrs.studies.execution.command", lambda *args, **kwargs: (["codex"], "prompt", native.root)
    )
    monkeypatch.setattr("hrs.studies.execution.subprocess.Popen", NativeProcess)
    StudyAnalysisRunner(native).execute(job_id)
    job = store.read("analysis", job_id)
    pressure = native.runtime_control.snapshot()["models"].get(MODEL, {"state": "normal"})
    assert job["state"] == ("queued" if provider_error else "failed")
    assert pressure["state"] == ("cooldown" if provider_error else "normal")
    assert store.ledger.capacity_used() == 0
    if provider_error:
        assert job["not_before"] > time.time()
        runner = StudyAnalysisRunner(native)
        monkeypatch.setattr(runner, "execute", lambda job: pytest.fail("cooldown dispatched early"))
        runner.tick()


def test_capacity_defer_stops_after_bounded_execution_budget(analysis):
    native, store, _, job_id, _ = analysis
    current = store.read("analysis", job_id)
    store.save("analysis", job_id, {"execution_attempt": 3}, expected_version=current["version"])
    StudyAnalysisRunner(native)._defer(job_id, "Synthetic capacity pressure", time.time() + 30)
    assert store.read("analysis", job_id)["state"] == "failed"


def test_analysis_runner_stays_inactive_on_legacy_read_only_workspace(tmp_path):
    legacy = tmp_path / "documents" / ("b" * 24) / "document.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"id": "b" * 24, "pages": [], "tasks": []}), encoding="utf8")
    native = Store(tmp_path)
    runner = StudyAnalysisRunner(native)
    runner.start()
    runner.tick()
    runner.stop()
    assert runner.thread is None
    assert not (tmp_path / "ledger.sqlite3").exists()
    assert not (tmp_path / ".study-analysis-runner.lock").exists()
