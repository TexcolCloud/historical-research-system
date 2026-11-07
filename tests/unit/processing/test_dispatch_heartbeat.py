"""A live coordinator renews declared work, without resurrecting stale workers or pressure probes."""

import json
from copy import deepcopy

import pytest

from hrs.processing.execution_policy import configure_execution
from hrs.processing.runtime_control import TERRA
from tests.unit.processing.test_dispatch import second_document
from tests.unit.processing.test_store import document


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    return now


def busy_worker(tmp_path, clock, *, probe=False):
    store, identity = document(tmp_path, count=1)
    configure_execution(store.root, capacity=2, overlap_reading=False)
    if probe:
        failed = store.runtime_control.failure("429", "initial-account-outage", scope="account")
        clock[0] = failed["retry_at"] + 1
    store.request_run(identity)
    run = store.claim_run("coordinator", 900, document_id=identity)
    assignments = store.dispatch.reserve(identity)["assignments"]
    assignment = next(row for row in assignments if row["stage"] == "ocr")
    token = assignment["session_token"]
    task, _, _ = store.dispatch.open(token, "native-worker")
    return store, identity, run, token, task


def read_task(store, identity, task_id):
    return next(t for t in store.status(identity)["tasks"] if t["task_id"] == task_id)


def control_payloads(store):
    with store.checkpoints.ledger.connect() as connection:
        return dict(connection.execute("SELECT scope,payload FROM runtime_control"))


def test_declared_busy_worker_survives_beyond_original_expiry_with_same_claim(tmp_path, clock):
    store, identity, run, token, packet = busy_worker(tmp_path, clock)
    original = deepcopy(read_task(store, identity, packet["task_id"]))
    for elapsed in (200, 400, 600, 800, 950, 1200):
        clock[0] = 1000.0 + elapsed
        store.renew_run(identity, run["token"], 900)
        result = store.dispatch.reserve(identity, active_session_tokens=[token])
        assert result["assignments"] == []
        current = read_task(store, identity, packet["task_id"])
        session = store.dispatch.session(token)
        assert current["state"] == "leased" and session["state"] == "busy"
        assert current["claim"]["expires_at"] > clock[0]
        assert session["expires_at"] == current["claim"]["expires_at"]
        assert current["claim"]["lease_token"] == original["claim"]["lease_token"]
        assert current["claim"]["worker_id"] == original["claim"]["worker_id"]
        assert current["claim"]["input_sha256"] == original["claim"]["input_sha256"]
        assert current["inputs"] == original["inputs"]
        assert not current.get("technical_failures")
    assert store.status(identity)["run_request"]["attempts"] == 1


def test_untracked_worker_expires_even_while_the_run_heartbeat_continues(tmp_path, clock):
    store, identity, run, token, task = busy_worker(tmp_path, clock)
    clock[0] = task["claim"]["expires_at"] - 10
    store.renew_run(identity, run["token"], 900)
    store.dispatch.reserve(identity)  # The coordinator no longer declares this native worker.
    clock[0] += 11
    store.dispatch.reserve(identity)
    assert store.dispatch.session(token, allow_closed=True)["state"] == "exited"
    assert read_task(store, identity, task["task_id"])["state"] == "ready"


@pytest.mark.parametrize(
    "guard",
    [
        "expired_run",
        "expired_task",
        "pause",
        "cancel",
        "wrong_worker",
        "wrong_claim",
        "changed_inputs",
        "replaced_coordinator",
    ],
)
def test_heartbeat_cannot_extend_a_stopped_or_mismatched_claim(tmp_path, clock, guard):
    store, identity, run, token, packet = busy_worker(tmp_path, clock)
    clock[0] += 100
    with store.lock:
        doc = store.checkpoints.read(identity)
        task = next(t for t in doc["tasks"] if t["task_id"] == packet["task_id"])
        if guard == "expired_run":
            doc["run_request"]["lease_expires_at"] = clock[0] - 1
        elif guard == "expired_task":
            task["claim"]["expires_at"] = clock[0] - 1
        elif guard in {"pause", "cancel"}:
            doc["run_request"][guard + "_requested"] = True
        elif guard == "wrong_worker":
            task["claim"]["worker_id"] = "another-worker"
        elif guard == "wrong_claim":
            task["claim"]["lease_token"] = "another-claim"
        elif guard == "changed_inputs":
            task["inputs"]["changed"] = True
        else:
            doc["run_request"]["token"] = "different-coordinator-generation"
        store.checkpoints.write(doc)
    before = read_task(store, identity, packet["task_id"])["claim"]["expires_at"]
    before_token = read_task(store, identity, packet["task_id"])["claim"]["lease_token"]
    store.dispatch.reserve(identity, active_session_tokens=[token])
    current = read_task(store, identity, packet["task_id"])["claim"]
    assert current["expires_at"] == before or current["lease_token"] != before_token


def test_stopping_all_heartbeats_still_expires_a_previously_renewed_worker(tmp_path, clock):
    store, identity, run, token, task = busy_worker(tmp_path, clock)
    clock[0] += 400
    store.renew_run(identity, run["token"], 900)
    store.dispatch.reserve(identity, active_session_tokens=[token])
    deadline = read_task(store, identity, task["task_id"])["claim"]["expires_at"]
    clock[0] = deadline + 1
    store.dispatch.reserve(identity, active_session_tokens=[token])
    assert store.dispatch.session(token, allow_closed=True)["state"] == "exited"
    assert len(read_task(store, identity, task["task_id"])["technical_failures"]) == 1


def test_matching_probe_lease_renews_without_resetting_failures_or_generation(tmp_path, clock):
    store, identity, run, token, task = busy_worker(tmp_path, clock, probe=True)
    before = store.runtime_control.snapshot()["account"]
    clock[0] += 100
    store.renew_run(identity, run["token"], 900)
    store.dispatch.reserve(identity, active_session_tokens=[token])
    after = store.runtime_control.snapshot()["account"]
    assert after["probe_owner"] == token and after["probe_until"] > before["probe_until"]
    assert after["generation"] == before["generation"]
    assert after["failures"] == before["failures"] and after["limit"] == before["limit"]
    assert after["state"] == before["state"] == "probing"
    with store.checkpoints.ledger.connect() as connection:
        admission = json.loads(
            connection.execute(
                "SELECT payload FROM runtime_admissions WHERE owner=?", (token,)
            ).fetchone()[0]
        )
    assert (
        admission["expires_at"]
        == read_task(store, identity, task["task_id"])["claim"]["expires_at"]
    )


def test_foreign_and_closed_sessions_never_gain_a_renewed_lease(tmp_path, clock):
    store, identity, _, token, task = busy_worker(tmp_path, clock)
    other = second_document(store, tmp_path, count=1)
    original_expiry = task["claim"]["expires_at"]
    clock[0] += 100
    with pytest.raises(ValueError, match="belong"):
        store.dispatch.reserve(other, active_session_tokens=[token])
    assert read_task(store, identity, task["task_id"])["claim"]["expires_at"] == original_expiry
    store.dispatch.submit(
        token, "native-worker", {"input_sha256": task["input_sha256"], "markdown": "正文"}
    )
    store.dispatch.close(token, "native-worker")
    closed = deepcopy(store.dispatch.session(token, allow_closed=True))
    store.dispatch.reserve(identity, active_session_tokens=[token])
    assert store.dispatch.session(token, allow_closed=True) == closed


def test_old_probe_generation_cannot_extend_a_new_probe_or_replenish_its_budget(tmp_path, clock):
    store, _ = document(tmp_path, count=1)
    control = store.runtime_control
    first = control.failure("429", "first-outage", model=TERRA)
    clock[0] = first["retry_at"] + 1
    assert control.admit(TERRA, "old-probe")["allowed"]
    second = control.failure("429", "new-outage", model=TERRA)
    clock[0] = second["retry_at"] + 1
    assert control.admit(TERRA, "new-probe")["allowed"]
    before = control_payloads(store)
    with store.checkpoints.ledger.connect() as connection:
        old = json.loads(
            connection.execute(
                "SELECT payload FROM runtime_admissions WHERE owner='old-probe'"
            ).fetchone()[0]
        )
    assert control.renew(TERRA, "old-probe", expires_at=clock[0] + 900)
    assert control_payloads(store) == before
    with store.checkpoints.ledger.connect() as connection:
        renewed = json.loads(
            connection.execute(
                "SELECT payload FROM runtime_admissions WHERE owner='old-probe'"
            ).fetchone()[0]
        )
    assert renewed["generations"] == old["generations"]
    assert control.snapshot()["models"][TERRA]["probe_owner"] == "new-probe"


def test_expired_admission_cannot_be_revived(tmp_path, clock):
    store, _ = document(tmp_path, count=1)
    assert store.runtime_control.admit(TERRA, "expired-owner", lease_seconds=10)["allowed"]
    clock[0] += 11
    before = control_payloads(store)
    assert not store.runtime_control.renew(TERRA, "expired-owner", expires_at=clock[0] + 900)
    assert control_payloads(store) == before
