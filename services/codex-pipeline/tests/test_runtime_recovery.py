"""Runtime outages preserve content and enter the same durable admission gate."""

from test_store import document

from hrs_codex.runner import Runner


class CapacityFailure(RuntimeError):
    failure = {
        "type": "RuntimeFailure",
        "message": "model at capacity",
        "code": "service_unavailable",
        "model": "gpt-6.1-sol",
        "scope": "model",
        "event_id": "coordinator-start-1",
        "retry_after": 60,
    }


def test_coordinator_failure_is_classified_and_gates_subsequent_runs(tmp_path):
    store, identity = document(tmp_path)
    store.request_run(identity)

    def fail(*args, **kwargs):
        raise CapacityFailure("model at capacity")

    runner = Runner(store, fail)
    assert runner.tick(identity)
    request = store.status(identity)["run_request"]
    assert request["error"]["code"] == "service_unavailable"
    assert request["actual_model"] == "gpt-6.1-sol"
    assert store.scheduling_plan(identity)["runtime_control"]["state"] == "cooldown"
    assert not runner.tick(identity)
    assert store.status(identity)["pages"][0]["state"] == "awaiting_ocr"


def test_duplicate_failure_and_late_old_worker_do_not_reclaim_new_lease(tmp_path):
    store, identity = document(tmp_path, 4)
    item = next(a for a in store.dispatch.reserve(identity)["assignments"] if a["stage"] == "ocr")
    token = item["session_token"]
    details = {"event_id": "same-spawn", "scope": "model", "model": "gpt-6.1-terra"}
    store.dispatch.fail_runtime(token, "failed-worker", "service_unavailable", details=details)
    original = store.runtime_control.snapshot()
    store.dispatch.fail_runtime(token, "failed-worker", "service_unavailable", details=details)
    assert store.runtime_control.snapshot() == original
    assert store.dispatch.session(token, allow_closed=True)["state"] == "exited"
    assert not any(t["state"] == "pending" for t in store.status(identity)["tasks"])


def test_stale_starting_reservation_cannot_block_account_or_return_new_claim(tmp_path):
    store, identity = document(tmp_path, 4)
    item = next(a for a in store.dispatch.reserve(identity)["assignments"] if a["stage"] == "ocr")
    session = store.dispatch.session(item["session_token"])
    with store.lock:
        doc = store.checkpoints.read(identity)
        task = next(t for t in doc["tasks"] if t["task_id"] == session["task_id"])
        task["claim"]["expires_at"] = 0
        store.checkpoints.write(doc)
    replacement = store.claim_task(identity, "ocr", "replacement")
    assert replacement["task_id"] == session["task_id"]
    store.dispatch.fail_runtime(
        session["session_token"], "late-old-worker", "authentication", details={"scope": "account"}
    )
    assert store.runtime_control.snapshot()["state"] == "normal"
    current = next(
        t for t in store.status(identity)["tasks"] if t["task_id"] == replacement["task_id"]
    )
    assert current["state"] == "leased"
    assert current["claim"]["lease_token"] == replacement["lease_token"]


def test_resume_during_live_run_does_not_clear_shared_pressure(tmp_path):
    store, identity = document(tmp_path)
    store.request_run(identity)
    run = store.claim_run("owner")
    store.runtime_control.failure("authentication", "login-expired", scope="account")
    before = store.runtime_control.snapshot()
    assert store.request_run(identity, resume=True)["token"] == run["token"]
    assert store.runtime_control.snapshot() == before


def test_unfinished_original_reading_is_inflight_until_timeout(tmp_path):
    import time

    store, identity = document(tmp_path, 2)
    from hrs_codex.execution_policy import configure_execution

    configure_execution(store.root, capacity=7, overlap_reading=True)
    assignments = store.dispatch.reserve(identity)["assignments"]
    reader = next(a["session_token"] for a in assignments if a["stage"] == "review")
    store.dispatch.open(reader, "blind-reader")
    for assignment in assignments:
        if assignment["session_token"] != reader:
            store.dispatch.end_session(
                store.dispatch.session(assignment["session_token"]), "fixture_finished"
            )
    store.runtime_control.failure("quota_exhausted", "account-quota", scope="account")
    assert store.dispatch.runtime_stop_reason(identity) is None
    session = store.dispatch.session(reader)
    session["expires_at"] = time.time() - 1
    store.checkpoints.ledger.save_session(session)
    store.dispatch.expire()
    assert store.dispatch.session(reader, allow_closed=True)["exit_reason"] == "timeout"
    assert store.dispatch.runtime_stop_reason(identity)["reason"] == "runtime_blocked"


def test_only_one_coordinator_restarts_during_half_open_probe(tmp_path, monkeypatch):
    import time

    store, first = document(tmp_path, 2)
    source = tmp_path / "second-source.pdf"
    source.write_bytes(b"different-retained-source")
    page = store.status(first)["pages"][0]
    second = store.register(
        source,
        [
            {
                "page": 1,
                "image": str(store.document_directory(first) / page["image"]),
                "image_sha256": page["image_sha256"],
            }
        ],
    )["id"]
    store.request_run(first)
    store.request_run(second)
    retry = store.runtime_control.failure("rate_limit", "restart", model="gpt-6.1-sol")["retry_at"]
    monkeypatch.setattr(time, "time", lambda: retry + 1)
    assert store.claim_run("one", document_id=first)
    assert store.claim_run("two", document_id=second) is None


def test_one_slot_recovery_plan_prefers_ready_review_over_future_reading(tmp_path):
    from test_store import complete

    from hrs_codex.scheduling import scheduling_plan

    store, identity = document(tmp_path, 2)
    complete(store, identity, "ocr", "ocr", {"markdown": "Already recognized page"})
    plan = scheduling_plan(store.status(identity), capacity=1, overlap_reading=True)
    assert plan["capacity"] == 1
    assert plan["workers"]["review"] == 1
    assert sum(plan["workers"].values()) == 1
