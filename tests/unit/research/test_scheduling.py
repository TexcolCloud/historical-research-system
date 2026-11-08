"""Bounded research concurrency shares native capacity and preserves source priority."""

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hrs.processing.contracts import SOL
from hrs.processing.execution_policy import configure_execution
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research import execution
from tests.unit.research.test_workflow import completed, snapshot


def test_entity_detail_uses_indexed_cards_without_global_card_or_entity_scan(tmp_path, monkeypatch):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = completed(workflow)
    completed(workflow, "unrelated-book")
    target = workflow.list_entities(run_id=run["run_id"])[0]
    monkeypatch.setattr(
        workflow, "list_entities", lambda *args, **kwargs: pytest.fail("global entity scan")
    )
    monkeypatch.setattr(
        workflow, "list_cards", lambda *args, **kwargs: pytest.fail("global card scan")
    )
    detail = workflow.entity(target["id"])
    assert detail["card_id"] == target["card_id"] and len(detail["mentions"]) == 1


def test_source_metadata_reserves_processing_headroom_and_paused_sources_do_not(tmp_path):
    store = Store(tmp_path / "native")
    configure_execution(store.root, capacity=6)
    source = {
        "id": "b" * 24,
        "state": "processing",
        "tasks": [{"task_id": "ocr-ready", "stage": "ocr", "state": "ready"}],
        "run_request": {"state": "pending"},
    }
    store.checkpoints.ledger.write(source)
    assert store.checkpoints.ledger.source_work_ready()
    control = store.runtime_control
    assert control.admit(SOL, "research-first", occupied=3, total_occupied=3, priority="research")[
        "allowed"
    ]
    deferred = control.admit(
        SOL, "research-would-fill-source", occupied=4, total_occupied=4, priority="research"
    )
    assert not deferred["allowed"] and deferred["reason"] == "source_processing_priority"
    assert control.admit(SOL, "native-gets-headroom", occupied=4, total_occupied=4)["allowed"]
    source["run_request"]["pause_requested"] = True
    store.checkpoints.ledger.write(source)
    assert not store.checkpoints.ledger.source_work_ready()
    assert control.admit(
        SOL, "research-after-source-pause", occupied=4, total_occupied=4, priority="research"
    )["allowed"]


def test_recovery_has_only_one_control_session_before_any_probe_starts(tmp_path):
    store = Store(tmp_path / "native")
    failure = store.runtime_control.failure("capacity", "synthetic-outage", model=SOL)
    now = failure["retry_at"] + 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(
            pool.map(
                lambda owner: store.runtime_control.reserve_coordinator(SOL, owner, now=now),
                ["research-coordinator:first", "research-coordinator:second"],
            )
        )
    assert sum(decision["allowed"] for decision in decisions) == 1
    assert any(decision["reason"] == "recovery_coordinator_active" for decision in decisions)
    assert all(
        state["probe_owner"] is None for state in store.runtime_control.snapshot(now=now)["scopes"]
    )


def test_two_books_run_together_and_shutdown_retains_both_receipts_and_checkpoints(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "native")
    configure_execution(store.root, capacity=4)
    workflow = ResearchWorkflow(store)
    runs = [workflow.enqueue(snapshot(book)) for book in ("first-book", "second-book")]
    input_hashes = {run["run_id"]: run["input_hash"] for run in runs}
    started, lock = set(), threading.Lock()
    both_started = threading.Event()
    monkeypatch.setattr(execution.shutil, "which", lambda _: "synthetic-codex")
    monkeypatch.setattr(
        execution,
        "command",
        lambda executable, root, run_id, *args, **kwargs: (
            ["synthetic-" + run_id],
            "dispatch",
            tmp_path,
        ),
    )

    class Process:
        returncode = None

        def __init__(self, args, *, stdout, **kwargs):
            self.run_id = args[0].removeprefix("synthetic-")
            execution_id = workflow.status(self.run_id)["native_execution_id"]
            worker = f"{self.run_id}-{execution_id}-author-0001"
            assert workflow.claim(self.run_id, worker)
            stdout.write(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "collab_tool_call",
                            "tool": "spawn_agent",
                            "prompt": f"worker_id={worker} stage=author",
                            "receiver_thread_ids": ["native-" + self.run_id],
                            "agents_states": {"native-" + self.run_id: {"status": "running"}},
                        },
                    }
                )
                + "\n"
            )
            stdout.flush()
            with lock:
                started.add(self.run_id)
                if len(started) == 2:
                    both_started.set()

        def communicate(self, **kwargs):
            threading.Event().wait(0.01)
            raise subprocess.TimeoutExpired("synthetic-codex", 1)

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution.subprocess, "Popen", Process)
    monkeypatch.setattr(
        execution, "stop_owned_process", lambda process: setattr(process, "returncode", -15)
    )
    runner = execution.ResearchRunner(store)
    runner.start()
    try:
        assert both_started.wait(8), "second book must start while the first one remains in flight"
        assert store.checkpoints.ledger.capacity_used() == 4
    finally:
        runner.stop()
    assert not runner.thread.is_alive()
    assert store.checkpoints.ledger.capacity_used() == 0
    for run in runs:
        status = workflow.status(run["run_id"])
        assert status["input_hash"] == input_hashes[run["run_id"]]
        assert status["counts"]["completed"] == status["counts"]["leased"] == 0
        saved = list((store.root / "research-runtime" / run["run_id"]).glob("*/receipt.json"))
        assert len(saved) == 1
        assert json.loads(saved[0].read_text("utf-8"))["stopped"] == "executor_shutdown"
