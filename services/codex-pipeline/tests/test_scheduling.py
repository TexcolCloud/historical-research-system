import pytest
from test_store import document

from hrs_codex.scheduling import scheduling_plan


def test_review_backlog_receives_capacity_without_stopping_for_human_pages():
    doc = {
        "pages": [
            {"page": i, "state": "pending" if i == 1 else "awaiting_review"} for i in range(1, 21)
        ],
        "tasks": [{"stage": "review", "page": i, "state": "ready"} for i in range(2, 21)]
        + [{"stage": "ocr", "page": i, "state": "ready"} for i in range(21, 101)],
    }
    plan = scheduling_plan(doc, now=1000)
    assert plan["workers"]["review"] > plan["workers"]["ocr"] >= 1
    assert sum(plan["workers"].values()) <= plan["capacity"]
    assert plan["human_pending"] == 1 and plan["automatic_work_remaining"]
    assert plan["batch_size"] == 2


def test_pause_and_technical_cooldown_retain_tasks(tmp_path):
    store, doc_id = document(tmp_path, 3)
    task = store.claim_task(doc_id, "ocr", "worker", scheduled=True)
    store.report_task_failure(doc_id, task["task_id"], task["lease_token"], "rate_limit")
    assert store.scheduling_plan(doc_id)["cooldown_until"] > 0
    assert store.claim_task(doc_id, "ocr", "retry", scheduled=True) is None
    assert store.pause_document(doc_id)["state"] == "paused"
    with pytest.raises(ValueError, match="paused"):
        store.claim_task(doc_id, "ocr", "new")
    store.request_run(doc_id, resume=True)
    assert not store.status(doc_id)["run_request"].get("pause_requested")


def test_ocr_original_image_accepts_its_own_active_lease(tmp_path):
    store, doc_id = document(tmp_path)
    task = store.claim_task(doc_id, "ocr", "ocr-worker")
    metadata, data = store.original_page(
        doc_id, 1, task_id=task["task_id"], lease_token=task["lease_token"]
    )
    assert data and metadata["image_receipt"]
    with pytest.raises(ValueError, match="lease"):
        store.original_page(doc_id, 1, task_id=task["task_id"], lease_token="wrong")


def test_measured_durations_change_next_stage_allocation():
    doc = {
        "pages": [],
        "tasks": [{"stage": s, "state": "ready"} for s in ("ocr", "review") for _ in range(20)],
    }
    initial = scheduling_plan(doc, now=1000)
    doc["tasks"] += [
        {
            "stage": s,
            "state": "completed",
            "receipt": {"duration_seconds": seconds, "created_at": 900},
        }
        for s, seconds in (("ocr", 180), ("review", 30))
        for _ in range(3)
    ]
    measured = scheduling_plan(doc, now=1000)
    assert measured["workers"]["ocr"] > initial["workers"]["ocr"]
    assert measured["metrics"]["review"]["median_seconds"] == 30
    doc["scheduler"] = {"allocation": initial["workers"], "rebalanced_at": 990}
    assert scheduling_plan(doc, now=1000)["workers"] == initial["workers"]
    assert scheduling_plan(doc, now=1021)["workers"] == measured["workers"]


def test_worker_batch_limit_does_not_block_fresh_worker(tmp_path):
    store, doc_id = document(tmp_path, 3)
    for _ in range(2):
        task = store.claim_task(doc_id, "ocr", "batch-worker", scheduled=True)
        store.submit_task(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {"input_sha256": task["input_sha256"], "markdown": "正文"},
        )
    assert store.claim_task(doc_id, "ocr", "batch-worker", scheduled=True) is None
    assert store.claim_task(doc_id, "ocr", "fresh-worker", scheduled=True)
