"""Independent evidence scopes and failed dispatch batches stay safe under contention."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest

from hrs.processing.execution_policy import configure_execution
from hrs.processing.store import Store
from tests.unit.processing.test_store import document


def test_different_ready_pages_advance_without_exhausting_one_evidence_conflict_budget(
    tmp_path, monkeypatch
):
    store, identity = document(tmp_path, 16)
    configure_execution(store.root, capacity=64)
    competitor = Store(store.root)
    original = store.context.evidence.verify
    pages_seen = []

    def advancing(snapshot, pages=None):
        original(snapshot, pages)
        pages_seen.append(pages)
        if len(pages_seen) <= 12:
            assert competitor.claim_task(identity, "ocr", f"other-{len(pages_seen)}")

    monkeypatch.setattr(store.context.evidence, "verify", advancing)
    claim = store.claim_task(identity, "ocr", "final-reader")
    assert claim["page"] == 13
    assert pages_seen == [[page] for page in range(1, 14)]
    store.verify_evidence(store.status(identity))


def test_one_page_whose_actual_evidence_keeps_changing_still_has_eight_attempts(
    tmp_path, monkeypatch
):
    store, identity = document(tmp_path)
    competitor = Store(store.root)
    original = store.context.evidence.verify
    checked = []

    def changing(snapshot, pages=None):
        original(snapshot, pages)
        checked.append(pages)
        with competitor.lock:
            current = competitor.checkpoints.read(identity)
            current["pages"][0]["content_revision"] = len(checked)
            competitor.checkpoints.write(current)

    monkeypatch.setattr(store.context.evidence, "verify", changing)
    with pytest.raises(ValueError, match="Document changed during evidence validation"):
        store.claim_task(identity, "ocr", "reader")
    assert checked == [[1]] * 8
    assert store.checkpoints.ledger.capacity_used() == 0


def test_heartbeat_expiry_changes_do_not_repeat_an_already_checked_page(tmp_path, monkeypatch):
    store, identity = document(tmp_path)
    task = store.claim_task(identity, "ocr", "worker")
    competitor = Store(store.root)
    original = store.context.evidence.verify
    checked = []

    def heartbeating(snapshot, pages=None):
        original(snapshot, pages)
        checked.append(pages)
        with competitor.lock:
            current = competitor.checkpoints.read(identity)
            current["tasks"][0]["claim"]["expires_at"] += 1
            competitor.checkpoints.write(current)

    monkeypatch.setattr(store.context.evidence, "verify", heartbeating)
    with store.context.verified_task(identity, task_id=task["task_id"]) as snapshot:
        assert snapshot["tasks"][0]["claim"]["expires_at"] > task["expires_at"]
    assert checked == [[1]]


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_pause_during_slow_original_hashing_is_responsive_and_prevents_a_claim(
    tmp_path, monkeypatch, action
):
    store, identity = document(tmp_path)
    competitor = Store(store.root)
    entered, release = Event(), Event()
    original = store.context.evidence.verify

    def slow(snapshot, pages=None):
        entered.set()
        assert release.wait(5)
        original(snapshot, pages)

    monkeypatch.setattr(store.context.evidence, "verify", slow)
    with ThreadPoolExecutor(2) as pool:
        claim = pool.submit(store.claim_task, identity, "ocr", "reader")
        try:
            assert entered.wait(3)
            stop = competitor.pause_document if action == "pause" else competitor.cancel_document
            paused = pool.submit(stop, identity)
            assert paused.result(timeout=1)["state"] == (
                "paused" if action == "pause" else "cancelled"
            )
        finally:
            release.set()
        with pytest.raises(ValueError, match="paused" if action == "pause" else "cancellation"):
            claim.result(timeout=3)
    assert not any(task["state"] == "leased" for task in store.status(identity)["tasks"])


@pytest.mark.parametrize("failure_after_commit", [False, True])
def test_failed_reserve_returns_its_previously_committed_claims_and_keeps_other_sessions(
    tmp_path, monkeypatch, failure_after_commit
):
    store, identity = document(tmp_path, 12)
    configure_execution(store.root, capacity=4, overlap_reading=False)
    existing = store.dispatch.reserve(identity)["assignments"]
    keep = existing[0]["session_token"]
    for item in existing[1:]:
        store.dispatch.end_session(store.dispatch.session(item["session_token"]), "fixture-room")
    before = store.dispatch.session(keep).copy()
    original = store.tasks.claim_task
    calls = []

    def failing(*args, **kwargs):
        calls.append(kwargs["session"]["session_token"])
        if len(calls) == 2:
            if failure_after_commit:
                assert original(*args, **kwargs)
            raise ValueError("Synthetic evidence validation failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store.tasks, "claim_task", failing)
    with pytest.raises(ValueError, match="Synthetic evidence validation failure"):
        store.dispatch.reserve(identity)
    assert len(calls) == 2
    assert store.dispatch.session(keep) == before
    assert store.checkpoints.ledger.capacity_used() == 1
    assert all(
        session["state"] == "exited"
        for session in store.dispatch.sessions()
        if session["session_token"] in calls
    )
    assert sum(task["state"] == "leased" for task in store.status(identity)["tasks"]) == 1


def test_returning_to_a_previous_scope_does_not_reset_its_conflict_budget(tmp_path):
    from hrs.processing.checkpoints import Checkpoints

    repository = Checkpoints(tmp_path / "scopes")
    identity = "a" * 24
    repository.write({"id": identity, "scope": 0, "content": 0})
    seen = []

    def validate(snapshot):
        seen.append(snapshot["scope"])
        with repository.lock:
            current = repository.read(identity)
            current.update(scope=1 - current["scope"], content=current["content"] + 1)
            repository.write(current)

    with pytest.raises(ValueError, match="Document changed during evidence validation"):
        with repository.validated(
            identity,
            validate,
            lambda doc: (doc["scope"], doc["content"]),
            retry_scope=lambda selected: selected[0],
        ):
            pytest.fail("A perpetually changing scope cannot be committed")
    assert seen.count(0) == 8 and seen.count(1) == 7


def test_natural_expiry_cannot_switch_to_an_unchecked_original_page(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    store, identity = document(tmp_path, 2)
    first = store.claim_task(identity, "ocr", "initial", lease_seconds=1)
    snapshot = store.status(identity)
    store.artifact_path(snapshot, snapshot["pages"][0]["image"]).write_bytes(b"tampered")
    original = store.context.evidence.verify
    seen = []

    def expiring(snapshot, pages=None):
        seen.append(pages)
        original(snapshot, pages)
        now[0] = first["expires_at"] + 0.1

    monkeypatch.setattr(store.context.evidence, "verify", expiring)
    with pytest.raises(ValueError, match="Original page 1 image hash mismatch"):
        store.claim_task(identity, "ocr", "later")
    assert seen == [[2], [1]]


def test_claim_stays_on_verified_page_if_expiry_occurs_after_final_selection(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    store, identity = document(tmp_path, 2)
    first = store.claim_task(identity, "ocr", "initial", lease_seconds=1)
    snapshot = store.status(identity)
    store.artifact_path(snapshot, snapshot["pages"][0]["image"]).write_bytes(b"tampered")
    original_connect = store.checkpoints.ledger.connect

    @contextmanager
    def expiry_before_commit(*args, **kwargs):
        if kwargs.get("write"):
            now[0] = first["expires_at"] + 0.1
        with original_connect(*args, **kwargs) as connection:
            yield connection

    monkeypatch.setattr(store.checkpoints.ledger, "connect", expiry_before_commit)
    claim = store.claim_task(identity, "ocr", "later")
    assert claim["page"] == 2 and claim["task_id"] != first["task_id"]


def test_real_coordinator_and_worker_heartbeat_finish_during_slow_validation(tmp_path, monkeypatch):
    from tests.unit.processing.test_dispatch_heartbeat import busy_worker

    now = [1000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    store, identity, run, token, task = busy_worker(tmp_path, now)
    competitor = Store(store.root)
    entered, release = Event(), Event()
    original = store.context.evidence.verify
    checked = []

    def slow(snapshot, pages=None):
        checked.append(pages)
        entered.set()
        assert release.wait(5)
        original(snapshot, pages)

    def validating():
        with store.context.verified_task(identity, task_id=task["task_id"]) as snapshot:
            return snapshot["tasks"][0]["claim"]["expires_at"]

    def heartbeat():
        competitor.renew_run(identity, run["token"], 900)
        with competitor.lock:
            competitor.dispatch.heartbeat(identity, [competitor.dispatch.session(token)])
        return competitor.dispatch.session(token)["expires_at"]

    monkeypatch.setattr(store.context.evidence, "verify", slow)
    with ThreadPoolExecutor(2) as pool:
        validation = pool.submit(validating)
        try:
            assert entered.wait(3)
            now[0] += 100
            renewed = pool.submit(heartbeat).result(timeout=1)
            assert renewed > task["expires_at"]
        finally:
            release.set()
        assert validation.result(timeout=3) == renewed
    assert checked == [[1]]


def test_failed_batch_does_not_reset_an_expired_claim_that_another_worker_reclaimed(
    tmp_path, monkeypatch
):
    now = [1000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    store, identity = document(tmp_path, 6)
    configure_execution(store.root, capacity=4)
    competitor = Store(store.root)
    original = store.tasks.claim_task
    first, replacement, tokens = [], [], []

    def reclaim_then_fail(*args, **kwargs):
        tokens.append(kwargs["session"]["session_token"])
        if len(tokens) == 1:
            result = original(*args, **kwargs)
            first.append(result)
            return result
        now[0] = first[0]["expires_at"] + 1
        replacement.append(competitor.claim_task(identity, "ocr", "independent-new-lease"))
        raise ValueError("Synthetic failure after an independent reclaim")

    monkeypatch.setattr(store.tasks, "claim_task", reclaim_then_fail)
    with pytest.raises(ValueError, match="Synthetic failure"):
        store.dispatch.reserve(identity)
    current = next(
        task for task in store.status(identity)["tasks"] if task["task_id"] == first[0]["task_id"]
    )
    assert current["state"] == "leased"
    assert (
        current["claim"]["lease_token"] == replacement[0]["lease_token"] != first[0]["lease_token"]
    )
    assert current["claim"]["worker_id"] == "independent-new-lease"
    assert all(
        store.dispatch.session(token, allow_closed=True)["state"] == "exited" for token in tokens
    )
    assert store.checkpoints.ledger.capacity_used() == 1
