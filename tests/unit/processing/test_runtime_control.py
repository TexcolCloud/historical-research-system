"""Durable provider recovery is separate from source-content acceptance."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from hrs.processing.execution_policy import configure_execution
from hrs.processing.ledger import Ledger
from hrs.processing.runtime_control import RuntimeControl

TERRA = "gpt-6.1-terra"
SOL = "gpt-6.1-sol"


def control(tmp_path):
    configure_execution(tmp_path, capacity=14)
    return RuntimeControl(tmp_path, Ledger(tmp_path / "ledger.sqlite3"))


def test_model_pressure_does_not_block_other_model_and_merges_concurrent_failures(tmp_path):
    runtime = control(tmp_path)
    first = runtime.failure("429", "attempt-1", model=SOL, observed_capacity=7, now=100)
    assert first["limit"] == 3
    second = runtime.failure("service_unavailable", "attempt-2", model=SOL, now=101)
    assert second["limit"] == 3 and second["failures"] == 1
    assert runtime.failure("429", "attempt-1", model=SOL, now=102)["duplicate"]
    assert not runtime.admit(SOL, "review", now=102)["allowed"]
    assert runtime.admit(TERRA, "ocr", now=102)["allowed"]


def test_expired_cooldown_has_one_probe_across_documents_and_restarts(tmp_path):
    runtime = control(tmp_path)
    failure = runtime.failure("rate_limit", "outage", scope="account", now=100)
    retry = failure["retry_at"]
    assert runtime.gate(SOL, now=retry)["allowed"]
    assert runtime.admit(SOL, "document-a", now=retry)["probe"]
    restarted = RuntimeControl(tmp_path, Ledger(tmp_path / "ledger.sqlite3"))
    assert restarted.admit(SOL, "document-a", now=retry)["allowed"]
    assert not restarted.admit(TERRA, "document-b", now=retry)["allowed"]


def test_probe_race_only_one_winner(tmp_path):
    runtime = control(tmp_path)
    retry = runtime.failure("rate_limit", "outage", scope="account", now=100)["retry_at"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: runtime.admit(SOL, f"owner-{i}", now=retry), range(8)))
    assert sum(result["allowed"] for result in results) == 1


def test_success_requires_current_admission_and_recovers_in_steps(tmp_path):
    runtime = control(tmp_path)
    runtime.admit(SOL, "old-worker", now=90)
    retry = runtime.failure("rate_limit", "outage", model=SOL, now=100)["retry_at"]
    runtime.success(SOL, "old-worker", "old-result", now=retry)
    assert runtime.snapshot(now=retry)["state"] == "probing"
    runtime.admit(SOL, "probe", now=retry)
    runtime.success(SOL, "probe", "probe-result", now=retry + 1)
    state = runtime.snapshot(now=retry + 1)["models"][SOL]
    assert state["state"] == "recovering" and state["limit"] == 1
    for index in range(2):
        runtime.admit(SOL, f"next-{index}", now=retry + 40)
        runtime.success(SOL, f"next-{index}", f"result-{index}", now=retry + 40)
    assert runtime.snapshot(now=retry + 40)["models"][SOL]["limit"] == 2
    runtime.success(SOL, "next-1", "result-1", now=retry + 40)
    assert runtime.snapshot(now=retry + 40)["models"][SOL]["limit"] == 2


@pytest.mark.parametrize("code", ["quota_exhausted", "authentication"])
def test_permanent_failure_requires_explicit_reset(tmp_path, code):
    runtime = control(tmp_path)
    assert runtime.failure(code, "permanent", scope="account", now=100)["state"] == "blocked"
    assert not runtime.admit(TERRA, "work", now=100000)["allowed"]
    runtime.reset(now=100001)
    assert runtime.admit(TERRA, "work", now=100002)["allowed"]


def test_service_retry_time_is_honored_and_retry_budget_is_hard(tmp_path):
    runtime = control(tmp_path)
    failure = runtime.failure("rate_limit", "first", model=SOL, retry_after_seconds=120, now=100)
    assert failure["retry_at"] >= 220
    now = failure["retry_at"]
    for index in range(1, 6):
        runtime.admit(SOL, f"probe-{index}", now=now)
        failure = runtime.failure("rate_limit", f"failure-{index}", model=SOL, now=now)
        now = failure["retry_at"]
    assert failure["state"] == "blocked"
    assert failure["reason"] == "retry_budget_exhausted"


def test_wait_budget_survives_restarts_and_does_not_expire_to_normal(tmp_path):
    runtime = control(tmp_path)
    runtime.failure("timeout", "first", model=SOL, now=100)
    restarted = RuntimeControl(tmp_path, Ledger(tmp_path / "ledger.sqlite3"))
    assert restarted.snapshot(now=1900)["state"] == "blocked"
    assert restarted.admit(SOL, "late", now=1900)["reason"] == "wait_budget_exhausted"


def test_account_and_model_occupancy_are_separate(tmp_path):
    runtime = control(tmp_path)
    assert not runtime.admit(SOL, "full-account", occupied=1, total_occupied=14, now=100)["allowed"]
    assert runtime.admit(SOL, "free-account", occupied=1, total_occupied=13, now=100)["allowed"]


def test_probe_waits_for_capacity_without_interrupting_existing_work(tmp_path):
    runtime = control(tmp_path)
    retry = runtime.failure("rate_limit", "first", model=SOL, observed_capacity=7, now=100)[
        "retry_at"
    ]
    assert not runtime.admit(SOL, "probe", occupied=3, now=retry)["allowed"]
    assert runtime.admit(SOL, "probe", occupied=2, now=retry)["allowed"]


def test_model_count_includes_actual_model_reservations_and_legacy_leases(tmp_path):
    from tests.unit.processing.test_store import document

    store, identity = document(tmp_path, 4)
    configure_execution(store.root, capacity=4, overlap_reading=False)
    assignments = store.dispatch.reserve(identity)["assignments"]
    ledger = store.checkpoints.ledger
    sessions = ledger.sessions()
    assert (
        ledger.model_capacity_used(TERRA) + ledger.model_capacity_used(SOL)
        == ledger.capacity_used()
    )
    ocr = next(session for session in sessions if session["stage"] == "ocr")
    ocr["actual_model"] = SOL
    ledger.save_session(ocr)
    assert ledger.model_capacity_used(SOL) == sum(
        session.get("actual_model", TERRA if session["stage"] == "ocr" else SOL) == SOL
        for session in sessions
    )
    assert (
        ledger.model_capacity_used(SOL, ocr["session_token"]) == ledger.model_capacity_used(SOL) - 1
    )
    assert assignments


def test_pressure_and_task_mutations_share_rollback(tmp_path):
    runtime = control(tmp_path)
    with pytest.raises(RuntimeError), runtime.ledger.connect(write=True):
        runtime.failure("rate_limit", "rolled-back", model=SOL, now=100)
        raise RuntimeError("task commit failed")
    assert runtime.snapshot(now=101)["state"] == "normal"
    assert not runtime.failure("rate_limit", "rolled-back", model=SOL, now=101)["duplicate"]


def test_release_unused_probe_allows_replacement_and_expiry_is_a_failure(tmp_path):
    runtime = control(tmp_path)
    retry = runtime.failure("rate_limit", "first", model=SOL, now=100)["retry_at"]
    runtime.admit(SOL, "unused", now=retry)
    runtime.release("unused", now=retry)
    assert runtime.admit(SOL, "replacement", lease_seconds=10, now=retry)["allowed"]
    result = runtime.admit(SOL, "after-timeout", now=retry + 11)
    assert not result["allowed"]
    assert runtime.snapshot(now=retry + 11)["models"][SOL]["failures"] == 2


def test_legacy_snapshot_is_read_only_and_control_writes_require_migration(tmp_path):
    runtime = RuntimeControl(tmp_path)
    assert runtime.snapshot(now=100)["state"] == "normal"
    assert runtime.gate(SOL, now=100)["allowed"]
    assert not (tmp_path / "ledger.sqlite3").exists()
    with pytest.raises(ValueError, match="writable ledger"):
        runtime.admit(SOL, "owner", now=100)
    with pytest.raises(ValueError, match="writable ledger"):
        runtime.failure("rate_limit", "failure", model=SOL, now=100)
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_repeated_probe_admission_does_not_extend_its_deadline(tmp_path):
    runtime = control(tmp_path)
    retry = runtime.failure("rate_limit", "first", model=SOL, now=100)["retry_at"]
    runtime.admit(SOL, "probe", lease_seconds=10, now=retry)
    assert runtime.admit(SOL, "probe", lease_seconds=10, now=retry + 9)["allowed"]
    assert not runtime.admit(SOL, "probe", now=retry + 11)["allowed"]


def test_scoped_reset_preserves_other_model_probe_and_shared_account_probe(tmp_path):
    runtime = control(tmp_path)
    retry = runtime.failure("rate_limit", "account", scope="account", now=100)["retry_at"]
    runtime.failure("rate_limit", "sol", model=SOL, now=100)
    now = retry + 10
    assert runtime.admit(SOL, "shared-probe", now=now)["allowed"]
    runtime.reset("model", model=SOL, now=now)
    runtime.success(SOL, "shared-probe", "shared-result", now=now + 1)
    snapshot = runtime.snapshot(now=now + 1)
    assert snapshot["account"]["state"] == "recovering"
    assert snapshot["models"][SOL]["state"] == "normal"
    runtime.reset(now=200)
    retry = runtime.failure("rate_limit", "terra", model=TERRA, now=200)["retry_at"]
    runtime.admit(TERRA, "terra-probe", now=retry)
    runtime.reset("model", model=SOL, now=retry)
    runtime.success(TERRA, "terra-probe", "terra-result", now=retry + 1)
    assert runtime.snapshot(now=retry + 1)["models"][TERRA]["state"] == "recovering"


def test_late_probe_success_cannot_erase_elapsed_wait_budget(tmp_path):
    runtime = control(tmp_path)
    configure_execution(tmp_path, capacity=2)
    runtime.failure("rate_limit", "outage", model=SOL, now=100)
    runtime.admit(SOL, "late", now=1790)
    runtime.success(SOL, "late", "late-result", now=1900)
    snapshot = runtime.snapshot(now=1900)
    assert snapshot["state"] == "blocked"
    assert snapshot["models"][SOL]["reason"] == "wait_budget_exhausted"


def test_healthy_recovery_to_capacity_64_can_exceed_outage_wait_budget(tmp_path):
    runtime = control(tmp_path)
    configure_execution(tmp_path, capacity=64)
    now = runtime.failure("rate_limit", "outage", model=SOL, now=100)["retry_at"]
    runtime.admit(SOL, "probe", now=now)
    runtime.success(SOL, "probe", "probe-result", now=now + 1)
    now += 1
    for capacity in range(2, 65):
        now += 31
        for sample in range(2):
            owner = f"recovery-{capacity}-{sample}"
            assert runtime.admit(SOL, owner, now=now)["allowed"]
            runtime.success(SOL, owner, owner, now=now)
        state = runtime.snapshot(now=now)["models"][SOL]
        assert state["limit"] == capacity
        assert state["state"] == ("normal" if capacity == 64 else "recovering")
    assert now > 1900


def test_recovery_relapse_restarts_wait_period_but_retains_failure_budget(tmp_path):
    runtime = control(tmp_path)
    now = 100
    for failure_number in range(1, 7):
        failure = runtime.failure("rate_limit", f"outage-{failure_number}", model=SOL, now=now)
        assert failure["failures"] == failure_number
        if failure_number == 6:
            assert failure["state"] == "blocked"
            assert failure["reason"] == "retry_budget_exhausted"
            break
        assert failure["state"] == "cooldown" and failure["first_at"] == now
        now = failure["retry_at"]
        owner = f"probe-{failure_number}"
        runtime.admit(SOL, owner, now=now)
        runtime.success(SOL, owner, owner, now=now + 1)
        now += 2000
        assert runtime.snapshot(now=now)["models"][SOL]["state"] == "recovering"
