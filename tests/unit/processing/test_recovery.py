import pytest

from hrs.processing.store import Store
from tests.unit.processing.test_store import complete, document


def test_checkpoint_recovery_fences_expired_work_and_preserves_committed_content(tmp_path):
    store, identity = document(tmp_path, 2)
    complete(store, identity, "ocr", "first", {"markdown": "Retained[^1]\n\n[^1]: Source note."})
    complete(store, identity, "review", "independent", {"verdict": "approved"})
    stale = store.claim_task(identity, "ocr", "interrupted")
    with store.lock:
        doc = store.checkpoints.read(identity)
        next(t for t in doc["tasks"] if t["task_id"] == stale["task_id"])["claim"]["expires_at"] = 0
        doc["run_request"] = {
            "state": "retryable_error",
            "technical_failures": 3,
            "attempts": 3,
            "not_before": 0,
            "history": [],
        }
        store.checkpoints.write(doc)
    before = store.status(identity)["pages"][0]
    receipt = store.recover_run(identity, "manual-1")
    assert receipt["state"] == "pending" and receipt["reset_tasks"] == [stale["task_id"]]
    assert store.status(identity)["pages"][0] == before
    assert Store(store.root).recover_run(identity, "manual-1") == receipt
    with pytest.raises(ValueError, match="active lease"):
        store.submit_task(
            identity,
            stale["task_id"],
            stale["lease_token"],
            {"input_sha256": stale["input_sha256"], "markdown": "late overwrite"},
        )
    replacement = store.claim_task(identity, "ocr", "new-worker")
    assert replacement["lease_token"] != stale["lease_token"]


def test_automatic_checkpoint_fallback_has_one_attempt_then_requires_manual_recovery(
    tmp_path, monkeypatch
):
    from hrs.processing.runner import Runner

    store, identity = document(tmp_path)
    store.request_run(identity)
    calls = []
    clock = [1000.0]
    monkeypatch.setattr("hrs.processing.runner.time.time", lambda: clock[0])

    def disconnected(*args, **kwargs):
        calls.append(1)
        raise OSError("Synthetic service disconnected")

    runner = Runner(store, disconnected)
    for _ in range(12):
        runner.tick(identity)
        clock[0] += 1000
    request = store.status(identity)["run_request"]
    assert len(calls) == 4, (
        "Three normal attempts and one checkpoint fallback, never an infinite retry loop"
    )
    assert request["automatic_checkpoint_used"] and request["technical_failures"] == 1
    assert request["retry_limit"] == 1
    assert store.status(identity)["pages"][0]["state"] == "awaiting_ocr"
    assert store.recover_run(identity, "explicit-new-batch")["retry_limit"] == 3


def test_manual_recovery_preserves_human_drafts_waits_for_leases_and_rejects_damaged_evidence(
    tmp_path,
):
    from hrs.processing.recovery import RecoveryWait
    from hrs.processing.store import digest

    store, identity = document(tmp_path, 2)
    complete(store, identity, "ocr", "first", {"markdown": "Source text"})
    complete(
        store, identity, "review", "independent", {"verdict": "pending", "issues": ["unclear"]}
    )
    packet = store.human_packet(identity, 1)
    store.record_human_review(
        identity,
        1,
        input_sha256=packet["input_sha256"],
        markdown="Human draft[^1]\n\n[^1]: Retained note.",
        verdict="pending",
    )
    task = store.claim_task(identity, "ocr", "still-running")
    snapshot = store.status(identity)
    with pytest.raises(RecoveryWait):
        store.recover_run(identity, "wait-for-owner")
    assert digest(store.status(identity)["pages"]) == digest(snapshot["pages"])
    with store.lock:
        doc = store.checkpoints.read(identity)
        next(t for t in doc["tasks"] if t["task_id"] == task["task_id"])["claim"]["expires_at"] = 0
        store.checkpoints.write(doc)
    store.recover_run(identity, "ready-owner")
    assert store.status(identity)["pages"][0] == snapshot["pages"][0]
    before = store.status(identity)
    store.artifact_path(before, before["source"]["path"]).write_bytes(b"damaged original")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.recover_run(identity, "damaged-checkpoint")
    assert store.status(identity) == before


def test_checkpoint_recovery_does_not_override_a_later_pause(tmp_path):
    store, identity = document(tmp_path)
    store.request_run(identity)
    store.pause_document(identity)
    with pytest.raises(ValueError, match="paused"):
        store.recover_run(identity, "pause-must-win")
    assert store.status(identity)["run_request"]["pause_requested"]
