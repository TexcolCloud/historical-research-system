from test_original_reading import reading, submit
from test_store import complete, document


def test_policy_upgrade_is_explicit_paused_and_preserves_protected_pages(tmp_path):
    store, doc_id = document(tmp_path, count=2)
    for i in range(2):
        complete(store, doc_id, "ocr", f"ocr-{i}", {"markdown": f"文{i}"})
        complete(store, doc_id, "review", f"review-{i}", {"verdict": "pending", "issues": ["待核"]})
    store.pause_document(doc_id)
    receipt = store.upgrade_review_policy(doc_id, protected_pages=[2])
    assert receipt["reopened_pages"] == [1]
    doc = store.status(doc_id)
    assert doc["run_request"]["state"] == "paused"
    assert [p["state"] for p in doc["pages"]] == ["awaiting_review", "pending"]
    assert store.upgrade_review_policy(doc_id, protected_pages=[2])["reopened_pages"] == []


def test_disputed_review_gets_one_independent_unchanged_verification(tmp_path):
    store, doc_id = document(tmp_path)
    doc = store.checkpoints.read(doc_id)
    doc["processing_policy"] = {"review_failure": "bounded"}
    store.checkpoints.write(doc)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "甲名"})
    first = store.claim_task(doc_id, "review", "review", source_first=True)
    store.review_candidate(
        doc_id, first["task_id"], first["lease_token"], reading(store, doc_id, first)
    )
    submit(
        store,
        doc_id,
        first,
        {"verdict": "pending", "disposition": "disputed", "issues": ["读法冲突"]},
    )
    assert store.status(doc_id)["pages"][0]["state"] == "awaiting_verification"
    second = store.claim_task(doc_id, "verify", "fresh-review", source_first=True)
    assert second["inputs"]["markdown"] == "甲名"
    store.review_candidate(
        doc_id, second["task_id"], second["lease_token"], reading(store, doc_id, second)
    )
    submit(
        store,
        doc_id,
        second,
        {"verdict": "pending", "disposition": "disputed", "issues": ["仍不清"]},
    )
    assert store.status(doc_id)["pages"][0]["state"] == "pending"
    assert store.claim_task(doc_id, "verify", "third") is None


def test_located_correction_preserves_notes_and_requires_distinct_original_first_verifier(tmp_path):
    import pytest

    store, doc_id = document(tmp_path)
    with store.lock:
        doc = store.checkpoints.read(doc_id)
        doc["processing_policy"] = {"review_failure": "bounded"}
        store.checkpoints.write(doc)
    original = "到绥区[^1]\n\n[^1]: 原注。"
    complete(store, doc_id, "ocr", "ocr", {"markdown": original})
    task = store.claim_task(doc_id, "review", "reviewer", source_first=True)
    store.review_candidate(
        doc_id, task["task_id"], task["lease_token"], reading(store, doc_id, task)
    )
    submit(
        store,
        doc_id,
        task,
        {
            "verdict": "corrections",
            "issues": [{"candidate_quote": "绥", "source_quote": "警"}],
            "markdown": original.replace("绥", "警"),
            "corrections": [{"before": "绥", "after": "警", "reason": "原图清楚"}],
        },
    )
    with pytest.raises(ValueError, match="separate"):
        store.claim_task(doc_id, "verify", "reviewer", source_first=True)
    verify = store.claim_task(doc_id, "verify", "verifier", source_first=True)
    with pytest.raises(ValueError, match="original-first"):
        submit(store, doc_id, verify, {"verdict": "approved", "issues": []})
    store.review_candidate(
        doc_id, verify["task_id"], verify["lease_token"], reading(store, doc_id, verify)
    )
    submit(store, doc_id, verify, {"verdict": "approved", "issues": []})
    page = store.status(doc_id)["pages"][0]
    assert page["approved"]["markdown"] == original.replace("绥", "警")
    assert page["ocr"]["markdown"] == original
