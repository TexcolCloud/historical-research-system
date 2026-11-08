"""Native process faults and identity attestations use synthetic workflow inputs."""

import json
import subprocess
import threading

import pytest

from hrs.processing.errors import EvidenceMismatch, LeaseConflict
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research import execution
from hrs.research.diagnostics import failure_details
from tests.unit.research.test_workflow import draft, read_all, snapshot, submit_draft


def test_runtime_diagnostics_keep_retry_metadata_but_exclude_credentials_and_reasoning():
    details = failure_details(
        {
            "message": 'postgresql://user:db-secret@host api_key=key-secret {"password":"json-secret"} Bearer bearer-secret',
            "retry_after": 75,
            "scope": "account",
            "headers": {"Authorization": "raw-secret"},
            "chain_of_thought": "never retain",
            "evidence": {"file": "events.jsonl", "line": 2},
        }
    )
    assert all(
        secret not in str(details)
        for secret in (
            "db-secret",
            "key-secret",
            "json-secret",
            "bearer-secret",
            "raw-secret",
            "never retain",
        )
    )
    assert details["retry_after"] == 75 and details["evidence"]["line"] == 2
    assert failure_details(
        {"reason_code": "candidate_output_truncated", "details": "Tool output was truncated"}
    ) == {"reason": "candidate_output_truncated", "message": "Tool output was truncated"}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from hrs.processing import research_workflow as module

    monkeypatch.setattr(
        module, "POLICY", {**module.POLICY, "version": 4, "complete_research": False}
    )
    store = Store(tmp_path / "native")
    workflow = ResearchWorkflow(store)
    run_id = workflow.enqueue(snapshot())["run_id"]
    monkeypatch.setattr(execution.shutil, "which", lambda _: "synthetic-codex")
    monkeypatch.setattr(
        execution, "command", lambda *args, **kwargs: (["synthetic-codex"], "dispatch", tmp_path)
    )
    return store, workflow, run_id


def receipt(store, run_id):
    paths = list((store.root / "research-runtime" / run_id).glob("*/receipt.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text("utf-8"))


@pytest.mark.parametrize("phase", ["launch", "communicate"])
def test_process_fault_always_keeps_receipt_and_releases_its_reservation(
    runtime, monkeypatch, phase
):
    store, workflow, run_id = runtime

    class BrokenProcess:
        returncode = None

        def __init__(self, *args, **kwargs):
            if phase == "launch":
                raise OSError("Synthetic launch failure")

        def communicate(self, **kwargs):
            raise OSError("Synthetic pipe failure")

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution.subprocess, "Popen", BrokenProcess)
    monkeypatch.setattr(
        execution, "stop_owned_process", lambda process: setattr(process, "returncode", -15)
    )
    with pytest.raises(RuntimeError, match="Synthetic"):
        execution.run_research(store, run_id)
    saved = receipt(store, run_id)
    assert saved["failure"]["code"] == "tool_error"
    assert saved["returncode"] == (None if phase == "launch" else -15)
    assert workflow.status(run_id)["runtime_failures"] == 1
    assert store.checkpoints.ledger.capacity_used() == 0
    assert store.runtime_control.snapshot()["state"] == "normal"


@pytest.mark.parametrize("failed_turn", [False, True])
def test_clean_exit_without_work_or_failed_turn_never_reports_success(
    runtime, monkeypatch, failed_turn
):
    store, workflow, run_id = runtime

    class Process:
        returncode = None

        def __init__(self, *args, stdout, **kwargs):
            self.stdout = stdout

        def communicate(self, **kwargs):
            if failed_turn:
                self.stdout.write(
                    json.dumps(
                        {
                            "type": "turn.failed",
                            "error": {
                                "code": "rate_limit_exceeded",
                                "message": "Synthetic rate limit",
                                "scope": "account",
                                "retry_after": 75,
                            },
                        }
                    )
                    + "\n"
                )
                self.stdout.flush()
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution.subprocess, "Popen", Process)
    with pytest.raises(RuntimeError):
        execution.run_research(store, run_id)
    saved = receipt(store, run_id)
    assert saved["returncode"] == 0 and saved["failure"]
    assert workflow.status(run_id)["counts"]["completed"] == 0
    if failed_turn:
        assert saved["failure"]["scope"] == "account"
        assert saved["failure"]["retry_after"] == 75
        account = store.runtime_control.snapshot()["account"]
        assert account["state"] == "cooldown" and account["retry_at"] >= account["first_at"] + 75
    else:
        assert store.runtime_control.snapshot()["state"] == "normal"


def test_shutdown_keeps_an_interruption_receipt_without_consuming_failure_budget(
    runtime, monkeypatch
):
    store, workflow, run_id = runtime
    stopped = threading.Event()

    class Process:
        returncode = None

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, **kwargs):
            stopped.set()
            raise subprocess.TimeoutExpired("synthetic-codex", 1)

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution.subprocess, "Popen", Process)
    monkeypatch.setattr(
        execution, "stop_owned_process", lambda process: setattr(process, "returncode", -15)
    )
    result = execution.run_research(store, run_id, stop_event=stopped)
    saved = receipt(store, run_id)
    assert saved["stopped"] == "executor_shutdown" and saved["failure"] is None
    assert result.get("runtime_failures", 0) == 0 and result["state"] == "running"
    assert workflow.status(run_id)["counts"]["completed"] == 0


def test_stalled_native_child_is_stopped_instead_of_renewed_forever(runtime, monkeypatch):
    store, workflow, run_id = runtime

    class Process:
        returncode = None

        def __init__(self, *args, stdout, **kwargs):
            worker = f"{run_id}-{workflow.status(run_id)['native_execution_id']}-author-0001"
            packet = workflow.claim(run_id, worker)
            with workflow.ledger.connect(write=True) as connection:
                task, _ = workflow._lease(connection, packet["task_token"])
                task["last_progress_at"] -= 601
                workflow._save_task(connection, task)
            stdout.write(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "collab_tool_call",
                            "tool": "spawn_agent",
                            "prompt": f"worker_id={worker} stage=author",
                            "receiver_thread_ids": ["native-stalled"],
                            "agents_states": {"native-stalled": {"status": "running"}},
                        },
                    }
                )
                + "\n"
            )
            stdout.flush()

        def communicate(self, **kwargs):
            raise subprocess.TimeoutExpired("synthetic-codex", 1)

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution.subprocess, "Popen", Process)
    monkeypatch.setattr(
        execution, "stop_owned_process", lambda process: setattr(process, "returncode", -15)
    )
    with pytest.raises(RuntimeError, match="600 seconds"):
        execution.run_research(store, run_id)
    saved = receipt(store, run_id)
    assert saved["returncode"] == -15 and saved["failure"]["code"] == "timeout"
    assert workflow.status(run_id)["counts"]["leased"] == 0
    assert store.runtime_control.snapshot()["state"] == "normal"


@pytest.mark.parametrize("stop", ["pause", "cancel", "absolute", "unobserved"])
def test_complete_native_generation_over_600_seconds_survives_until_explicit_stop(
    tmp_path, monkeypatch, stop
):
    from tests.unit.research.test_complete_workflow import source

    clock = [execution.time.time()]
    monkeypatch.setattr(execution.time, "time", lambda: clock[0])
    monkeypatch.setattr(execution.time, "monotonic", lambda: clock[0])
    store = Store(tmp_path / "native")
    workflow = ResearchWorkflow(store)
    run_id = workflow.enqueue(source())["run_id"]
    monkeypatch.setattr(execution.shutil, "which", lambda _: "synthetic-codex")
    monkeypatch.setattr(execution, "command", lambda *a, **k: (["synthetic"], "dispatch", tmp_path))

    class Process:
        returncode = None
        calls = 0

        def __init__(self, *args, stdout, **kwargs):
            worker = f"{run_id}-{workflow.status(run_id)['native_execution_id']}-author-0001"
            self.packet = workflow.claim(run_id, worker)
            if stop == "unobserved":
                return
            stdout.write(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "collab_tool_call",
                            "tool": "spawn_agent",
                            "prompt": f"worker_id={worker} stage=author",
                            "receiver_thread_ids": ["native-generating"],
                            "agents_states": {"native-generating": {"status": "running"}},
                        },
                    }
                )
                + "\n"
            )
            stdout.flush()

        def communicate(self, **kwargs):
            self.calls += 1
            if stop in {"absolute", "unobserved"} and any(
                task.get("technical_failures") for task in workflow.status(run_id)["tasks"]
            ):
                # The failed lease returns ready while this coordinator remains alive.
                assert workflow.status(run_id)["counts"]["leased"] == 0
                workflow.pause(run_id)
                raise subprocess.TimeoutExpired("synthetic", 1)
            if self.calls == 1:
                clock[0] += 700
            else:
                assert workflow.status(run_id)["counts"]["leased"] == 1
                if stop == "pause":
                    workflow.pause(run_id)
                elif stop == "absolute":
                    clock[0] += 300
                    with workflow.ledger.connect(write=True) as connection:
                        task, _ = workflow._lease(connection, self.packet["task_token"])
                        task["last_progress_at"] = clock[0]
                        workflow._save_task(connection, task)
                else:
                    workflow.cancel_book(source()["book_id"])
            raise subprocess.TimeoutExpired("synthetic", 1)

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution.subprocess, "Popen", Process)
    monkeypatch.setattr(execution, "stop_owned_process", lambda p: setattr(p, "returncode", -15))
    if stop in {"absolute", "unobserved"}:
        result = execution.run_research(store, run_id)
        saved = receipt(store, run_id)
        failed = next(task for task in result["tasks"] if task.get("technical_failures"))
        assert failed["last_failure"]["code"] == "timeout"
        assert ("absolute task deadline" if stop == "absolute" else "600 seconds") in failed[
            "last_failure"
        ]["details"]["message"]
        assert saved["failure"] is None and result.get("runtime_failures", 0) == 0
        assert saved["returncode"] == -15
        return
    result = execution.run_research(store, run_id)
    saved = receipt(store, run_id)
    assert saved["failure"] is None
    assert saved["stopped"] == ("run_paused" if stop == "pause" else "run_cancelled")
    assert result.get("runtime_failures", 0) == 0


def test_native_thread_identity_cannot_be_relabelled_to_self_verify(runtime, tmp_path):
    _, workflow, run_id = runtime
    workflow.start_execution(run_id, "execution-1")
    workflow.bind_native_worker(run_id, "author", "native-source-writer", "execution-1")
    submit_draft(workflow, run_id, "author", "author")
    workflow.bind_native_worker(run_id, "enricher", "native-enricher", "execution-1")
    submit_draft(workflow, run_id, "enrich", "enricher")
    workflow.bind_native_worker(run_id, "renamed-verifier", "native-source-writer", "execution-1")
    packet = workflow.claim(run_id, "renamed-verifier", stage="verify")
    read_all(workflow, packet)
    source = packet["source_index"][0]
    with pytest.raises(EvidenceMismatch, match="same native agent"):
        workflow.candidate(
            packet["task_token"],
            "renamed-verifier",
            {
                "article_id": source["id"],
                "text_sha256": source["text_sha256"],
                "summary": "不得自审",
            },
        )


def test_pause_racing_watchdog_failure_wins_without_recovery(runtime, monkeypatch):
    store, workflow, run_id = runtime

    class Process:
        returncode = None

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, **kwargs):
            raise subprocess.TimeoutExpired("synthetic", 1)

        def poll(self):
            return self.returncode

    def race(*args, **kwargs):
        workflow.pause(run_id)
        raise TimeoutError("Obsolete watchdog result after user pause")

    monkeypatch.setattr(execution.subprocess, "Popen", Process)
    monkeypatch.setattr(execution, "stop_owned_process", lambda p: setattr(p, "returncode", -15))
    monkeypatch.setattr(ResearchWorkflow, "supervision_timeouts", race)
    final = execution.run_research(store, run_id)
    assert final["state"] == "paused" and final.get("runtime_failures", 0) == 0
    assert receipt(store, run_id)["failure"] is None


def test_coordinator_worker_ids_are_allocated_in_the_host_execution_namespace(runtime, tmp_path):
    _, workflow, run_id = runtime
    first = execution.coordinator_prompt(run_id, 2, execution_id="host-execution-one")
    second = execution.coordinator_prompt(run_id, 2, execution_id="host-execution-two")
    one = f"{run_id}-host-execution-one-author-0001"
    two = f"{run_id}-host-execution-two-author-0001"
    assert f"worker_id='{one}'" in first and one not in second
    assert f"worker_id='{two}'" in second and two not in first
    workflow.start_execution(run_id, "host-execution-two")
    events = execution.ResearchEvents(
        workflow,
        run_id,
        "gpt-6.1-sol",
        tmp_path / "host-execution-two",
        worker_prefix=f"{run_id}-host-execution-two-",
    )

    def spawn(worker, native):
        return json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "prompt": f"worker_id='{worker}' stage=author",
                    "receiver_thread_ids": [native],
                    "agents_states": {native: {"status": "running"}},
                },
            }
        )

    with pytest.raises(LeaseConflict, match="execution namespace"):
        events.feed(spawn(one, "native-from-current-execution"))
    events.feed(spawn(two, "native-current"))
    assert events.active_workers() == {two}
    with pytest.raises(LeaseConflict, match="reassigned"):
        events.feed(spawn(two, "different-native"))


def test_restarting_coordinator_for_same_run_gets_new_host_worker_allocations(
    runtime, monkeypatch, tmp_path
):
    store, workflow, run_id = runtime
    launches = []

    def command(*args, **kwargs):
        launches.append(kwargs["instructions"])
        return ["synthetic"], "dispatch", tmp_path

    class Process:
        returncode = None

        def __init__(self, *args, stdout, **kwargs):
            execution_id = workflow.status(run_id)["native_execution_id"]
            worker = f"{run_id}-{execution_id}-author-0001"
            workflow.claim(run_id, worker)
            stdout.write(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "collab_tool_call",
                            "tool": "spawn_agent",
                            "prompt": f"worker_id='{worker}'",
                            "receiver_thread_ids": ["native-" + execution_id],
                            "agents_states": {"native-" + execution_id: {"status": "running"}},
                        },
                    }
                )
                + "\n"
            )
            stdout.flush()

        def communicate(self, **kwargs):
            workflow.pause(run_id)
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution, "command", command)
    monkeypatch.setattr(execution.subprocess, "Popen", Process)
    assert execution.run_research(store, run_id)["state"] == "paused"
    first_id = workflow.status(run_id)["native_execution_id"]
    workflow.resume(run_id)
    assert execution.run_research(store, run_id)["state"] == "paused"
    second_id = workflow.status(run_id)["native_execution_id"]
    assert first_id != second_id
    assert f"worker_id='{run_id}-{first_id}-author-0001'" in launches[0]
    assert f"worker_id='{run_id}-{second_id}-author-0001'" in launches[1]
    with workflow.ledger.connect() as connection:
        workers = connection.execute(
            "SELECT worker_id,native_agent_id FROM research_workers WHERE run_id=?", (run_id,)
        ).fetchall()
    assert len(workers) == 2 and workers[0][0] != workers[1][0] and workers[0][1] != workers[1][1]


def test_real_admission_deferral_yields_without_a_false_no_progress_failure(runtime, monkeypatch):
    from hrs.processing.execution_policy import configure_execution

    store, workflow, run_id = runtime
    configure_execution(store.root, capacity=2)

    class Process:
        returncode = None

        def __init__(self, *args, **kwargs):
            store.checkpoints.ledger.save_session(
                {
                    "session_token": "source-entered-after-coordinator",
                    "document_id": "b" * 24,
                    "stage": "ocr",
                    "state": "busy",
                    "expires_at": execution.time.time() + 900,
                }
            )

        def communicate(self, **kwargs):
            assert workflow.claim(run_id, "legitimately-deferred") is None
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution.subprocess, "Popen", Process)
    result = execution.run_research(store, run_id)
    saved = receipt(store, run_id)
    assert saved["stopped"] == "admission_yield" and saved["failure"] is None
    assert result.get("runtime_failures", 0) == 0


def test_only_exposed_active_native_agents_receive_bounded_lease_renewal(runtime, tmp_path):
    store, workflow, run_id = runtime
    workflow.start_execution(run_id, "execution-1")
    packet = workflow.claim(run_id, "worker-1")
    read_all(workflow, packet)
    with pytest.raises(LeaseConflict, match="attestation"):
        workflow.submit(
            packet["task_token"],
            "worker-1",
            {"input_hash": packet["input_hash"], "cards": [draft()]},
        )
    events = execution.ResearchEvents(workflow, run_id, "gpt-6.1-sol", tmp_path / "execution-1")
    events.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "prompt": "worker_id='worker-1' stage=author",
                    "receiver_thread_ids": ["native-1"],
                    "agents_states": {"native-1": {"status": "running"}},
                },
            }
        )
    )
    assert workflow.renew_run_leases(run_id, active_workers=events.active_workers()) == 1
    assert workflow.renew_run_leases(run_id, active_workers={"made-up-worker"}) == 0
    with workflow.ledger.connect(write=True) as connection:
        task, _ = workflow._lease(connection, packet["task_token"])
        task["last_progress_at"] -= 601
        workflow._save_task(connection, task)
    assert workflow.renew_run_leases(run_id, active_workers=events.active_workers()) == 0
    events.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "wait",
                    "agents_states": {
                        "native-1": {
                            "status": "errored",
                            "error": {
                                "code": "rate_limit_exceeded",
                                "scope": "account",
                                "retry_after": 80,
                                "message": "Synthetic account rate limit",
                            },
                        }
                    },
                },
            }
        )
    )
    assert not events.active_workers()
    account = store.runtime_control.snapshot()["account"]
    assert account["retry_at"] >= account["first_at"] + 80
