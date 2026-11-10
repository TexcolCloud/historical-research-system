"""Recovery dispatch keeps one free controller and budgets a second real coordinator."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hrs.processing.contracts import SOL
from hrs.processing.execution_policy import configure_execution
from hrs.processing.store import Store


def recovering(store, scopes=("model",), limit=3, *, now=100):
    control = store.runtime_control
    failures = [
        control.failure("capacity", f"outage-{scope}", model=SOL, scope=scope, now=now)
        for scope in scopes
    ]
    now = max(failure["retry_at"] for failure in failures) + 1
    assert control.admit(SOL, "probe", now=now)["probe"]
    control.success(SOL, "probe", "probe-committed", now=now + 1)
    for step in range(limit - 1):
        now += 31
        for item in range(2):
            owner = f"recovery-{step}-{item}"
            assert control.admit(SOL, owner, now=now)["allowed"]
            control.success(SOL, owner, owner + "-committed", now=now)
    return now


def setup(tmp_path, scopes=("model",), limit=3):
    store = Store(tmp_path / "native")
    configure_execution(store.root, capacity=6)
    now = recovering(store, scopes, limit)
    return store, store.runtime_control, now


def admission(store, owner):
    with store.checkpoints.ledger.connect() as connection:
        row = connection.execute(
            "SELECT payload FROM runtime_admissions WHERE owner=?", (owner,)
        ).fetchone()
        return json.loads(row[0]) if row else None


@pytest.mark.parametrize("scopes", [("account",), ("model",), ("account", "model")])
def test_second_recovery_coordinator_is_counted_and_stays_counted_on_reserve_and_renew(
    tmp_path, scopes
):
    store, control, now = setup(tmp_path, scopes)
    first, second = "research-coordinator:first", "research-coordinator:second"
    assert control.reserve_coordinator(SOL, first, now=now)["control_only"] is True
    result = control.reserve_coordinator(SOL, second, now=now)
    assert result["allowed"] and result["control_only"] is False
    assert store.checkpoints.ledger.capacity_used(now=now) == 1 + int("account" not in scopes)
    assert store.checkpoints.ledger.model_capacity_used(SOL, now=now) == 1 + int(
        "model" not in scopes
    )
    assert control.reserve_coordinator(SOL, second, now=now + 1)["control_only"] is False
    assert control.renew(SOL, second, expires_at=now + 1000, now=now + 2)
    assert admission(store, second)["control_only"] is False
    assert store.checkpoints.ledger.capacity_used(now=now + 2) == 1 + int("account" not in scopes)
    assert not control.reserve_coordinator(SOL, "research-coordinator:third", now=now)["allowed"]


@pytest.mark.parametrize("source_ready", [False, True])
def test_second_coordinator_requires_worker_headroom_and_no_source_priority(
    tmp_path, monkeypatch, source_ready
):
    store, control, now = setup(tmp_path, limit=2)
    assert control.reserve_coordinator(SOL, "research-coordinator:first", now=now)["allowed"]
    if source_ready:
        monkeypatch.setattr(store.checkpoints.ledger, "source_work_ready", lambda **kw: True)
    else:
        store.checkpoints.ledger.save_session(
            {
                "session_token": "existing-worker",
                "document_id": "a" * 24,
                "stage": "review",
                "state": "busy",
                "expires_at": now + 1000,
            }
        )
    result = control.reserve_coordinator(SOL, "research-coordinator:second", now=now)
    assert not result["allowed"]
    assert result["reason"] == (
        "source_processing_priority" if source_ready else "capacity_occupied"
    )


@pytest.mark.parametrize("scope", ["account", "model"])
def test_new_provider_generation_yields_counted_coordinator_without_converting_it_to_free(
    tmp_path, scope
):
    store, control, now = setup(tmp_path, ("account", "model"))
    first, second = "research-coordinator:first", "research-coordinator:second"
    assert control.reserve_coordinator(SOL, first, now=now)["allowed"]
    assert control.reserve_coordinator(SOL, second, now=now)["allowed"]
    control.failure("capacity", "new-outage", scope=scope, model=SOL, now=now + 1)
    assert not control.renew(SOL, second, expires_at=now + 1000, now=now + 2)
    assert admission(store, second)["control_only"] is False
    assert control.renew(SOL, first, expires_at=now + 1000, now=now + 2)
    assert store.checkpoints.ledger.capacity_used(now=now + 2) == 1
    control.release(second, now=now + 2)
    assert store.checkpoints.ledger.capacity_used(now=now + 2) == 0


def test_normal_recovery_counts_every_coordinator_and_does_not_mistake_success_generation_for_failure(
    tmp_path,
):
    store, control, now = setup(tmp_path, limit=5)
    first, second = "research-coordinator:first", "research-coordinator:second"
    assert control.reserve_coordinator(SOL, first, now=now)["allowed"]
    assert control.reserve_coordinator(SOL, second, now=now)["allowed"]
    now += 31
    for index in range(2):
        owner = f"normalizing-{index}"
        assert control.admit(SOL, owner, now=now)["allowed"]
        control.success(SOL, owner, owner + "-committed", now=now)
    assert control.gate(SOL, now=now)["state"] == "normal"
    assert store.checkpoints.ledger.capacity_used(now=now) == 2
    assert control.renew(SOL, first, expires_at=now + 1000, now=now)
    assert control.renew(SOL, second, expires_at=now + 1000, now=now)
    assert admission(store, first)["control_only"] is False
    assert admission(store, second)["control_only"] is False


def test_concurrent_recovery_reservations_have_one_free_and_one_counted_winner(tmp_path):
    store, control, now = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda index: control.reserve_coordinator(
                    SOL, f"research-coordinator:{index}", now=now
                ),
                range(3),
            )
        )
    winners = [result for result in results if result["allowed"]]
    assert len(winners) == 2 and sum(result["control_only"] for result in winners) == 1
    assert store.checkpoints.ledger.capacity_used(now=now) == 2
    assert store.checkpoints.ledger.model_capacity_used(SOL, now=now) == 1


@pytest.mark.parametrize("expired", [False, True])
def test_expired_or_reused_pid_reservation_does_not_block_replacement_or_count_capacity(
    tmp_path, expired
):
    store, control, now = setup(tmp_path, ("account", "model"))
    first, second = "research-coordinator:first", "research-coordinator:second"
    assert control.reserve_coordinator(SOL, first, now=now)["allowed"]
    assert control.reserve_coordinator(SOL, second, now=now)["allowed"]
    value = admission(store, second)
    value["expires_at"] = now - 1 if expired else value["expires_at"]
    value["process_create_time"] += 1
    with store.checkpoints.ledger.connect(write=True) as connection:
        connection.execute(
            "UPDATE runtime_admissions SET payload=? WHERE owner=?", (json.dumps(value), second)
        )
    assert store.checkpoints.ledger.capacity_used(now=now) == 0
    assert not control.renew(SOL, second, expires_at=now + 1000, now=now)
    assert control.reserve_coordinator(SOL, "research-coordinator:replacement", now=now)["allowed"]
    assert admission(store, second) is None
    assert store.checkpoints.ledger.capacity_used(now=now) == 1


def test_legacy_reservation_without_process_identity_retains_expiry_semantics(tmp_path):
    store, control, now = setup(tmp_path, ("account", "model"))
    owner = "research-coordinator:legacy"
    assert control.reserve_coordinator(SOL, owner, now=now)["allowed"]
    value = admission(store, owner)
    for key in ("pid", "process_create_time", "control_scopes"):
        value.pop(key)
    with store.checkpoints.ledger.connect(write=True) as connection:
        connection.execute(
            "UPDATE runtime_admissions SET payload=? WHERE owner=?", (json.dumps(value), owner)
        )
    assert control.renew(SOL, owner, expires_at=now + 1000, now=now)
    assert store.checkpoints.ledger.capacity_used(now=now) == 0
    assert not control.renew(SOL, owner, expires_at=now + 2000, now=now + 1001)


def test_free_controller_cannot_take_the_unaffected_normal_scopes_last_worker_slot(tmp_path):
    store, control, now = setup(tmp_path)
    for index in range(5):
        store.checkpoints.ledger.save_session(
            {
                "session_token": f"source-worker-{index}",
                "document_id": "a" * 24,
                "stage": "ocr",
                "state": "busy",
                "expires_at": now + 1000,
            }
        )
    result = control.reserve_coordinator(SOL, "research-coordinator:first", now=now)
    assert not result["allowed"] and result["reason"] == "capacity_occupied"


def test_a_scope_that_was_restored_to_normal_cannot_become_free_on_a_new_incident(tmp_path):
    from hrs.processing.contracts import TERRA

    store, control, now = setup(tmp_path, ("account", "model"))
    owner = "research-coordinator:first"
    assert control.reserve_coordinator(SOL, owner, now=now)["allowed"]
    for step in range(3):
        now += 31
        for index in range(2):
            worker = f"other-model-{step}-{index}"
            assert control.admit(TERRA, worker, now=now)["allowed"]
            control.success(TERRA, worker, worker + "-committed", now=now)
    assert control.snapshot(now=now)["account"]["state"] == "normal"
    assert control.renew(SOL, owner, expires_at=now + 1000, now=now)
    assert admission(store, owner)["control_only"] is True
    assert admission(store, owner)["control_scopes"] == ["model:" + SOL]
    control.failure("capacity", "account-relapse", scope="account", now=now + 1)
    assert not control.renew(SOL, owner, expires_at=now + 1000, now=now + 2)
    assert store.checkpoints.ledger.capacity_used(now=now + 2) == 1


def test_unobserved_normal_recovery_then_relapse_yields_former_free_controller(tmp_path):
    store, control, now = setup(tmp_path, limit=5)
    owner = "research-coordinator:first"
    assert control.reserve_coordinator(SOL, owner, now=now)["allowed"]
    now += 31
    for index in range(2):
        worker = f"restore-{index}"
        assert control.admit(SOL, worker, now=now)["allowed"]
        control.success(SOL, worker, worker, now=now)
    assert control.gate(SOL, now=now)["state"] == "normal"
    control.failure("capacity", "unobserved-relapse", model=SOL, now=now + 1)
    assert not control.renew(SOL, owner, expires_at=now + 1000, now=now + 2)


def test_normal_coordinators_are_bounded_and_both_count(tmp_path):
    store = Store(tmp_path / "native")
    configure_execution(store.root, capacity=6)
    control = store.runtime_control
    for owner in ("first", "second"):
        result = control.reserve_coordinator(SOL, "research-coordinator:" + owner, now=100)
        assert result["allowed"] and not result["control_only"]
    assert not control.reserve_coordinator(SOL, "research-coordinator:third", now=100)["allowed"]
    assert store.checkpoints.ledger.capacity_used(now=100) == 2


def test_missing_new_reservation_generation_does_not_bypass_the_recovery_fence(tmp_path):
    store, control, now = setup(tmp_path, ("account", "model"))
    owner = "research-coordinator:first"
    assert control.reserve_coordinator(SOL, owner, now=now)["allowed"]
    value = admission(store, owner)
    value["generations"].pop("model:" + SOL)
    with store.checkpoints.ledger.connect(write=True) as connection:
        connection.execute(
            "UPDATE runtime_admissions SET payload=? WHERE owner=?", (json.dumps(value), owner)
        )
    assert not control.renew(SOL, owner, expires_at=now + 1000, now=now)


def test_source_priority_yields_only_the_counted_recovery_coordinator(tmp_path, monkeypatch):
    store, control, now = setup(tmp_path, ("account", "model"))
    first, second = "research-coordinator:first", "research-coordinator:second"
    assert control.reserve_coordinator(SOL, first, now=now)["allowed"]
    assert control.reserve_coordinator(SOL, second, now=now)["allowed"]
    monkeypatch.setattr(store.checkpoints.ledger, "source_work_ready", lambda **kw: True)
    assert control.renew(SOL, first, expires_at=now + 1000, now=now)
    assert not control.renew(SOL, second, expires_at=now + 1000, now=now)
    assert admission(store, second)["control_only"] is False
    assert store.checkpoints.ledger.capacity_used(now=now) == 1
