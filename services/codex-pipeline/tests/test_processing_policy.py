"""Retained human-only policies stay frozen; newer imports use bounded verification."""

import pytest
from PIL import Image

from hrs_codex.server import prepare
from hrs_codex.store import Store, atomic_json


def complete(store, doc_id, stage, result):
    task = store.claim_task(doc_id, stage, "synthetic-" + stage)
    return store.submit_task(
        doc_id,
        task["task_id"],
        task["lease_token"],
        {"input_sha256": task["input_sha256"], **result},
    )


def test_failed_sol_review_goes_to_human_and_test_release_is_not_content_acceptance(tmp_path):
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (40, 50), "white").save(image)
    store = Store(tmp_path / "workspace")
    doc = prepare(store, str(image))
    assert doc["processing_policy"]["ocr_model"] == "gpt-6.1-terra"
    assert doc["processing_policy"]["review_model"] == "gpt-6.1-sol"
    assert doc["processing_policy"]["review_failure"] == "bounded"
    # This historical fixture explicitly retains the earlier human-only policy.
    with store.lock:
        retained = store.checkpoints.read(doc["id"])
        retained["processing_policy"]["review_failure"] = "human"
        store.checkpoints.write(retained)
    complete(store, doc["id"], "ocr", {"markdown": "1938[^1]\n\n[^1]: Synthetic note."})
    complete(
        store,
        doc["id"],
        "review",
        {
            "verdict": "corrections",
            "markdown": "1939[^1]\n\n[^1]: Synthetic note.",
            "corrections": [{"before": "1938", "after": "1939", "reason": "Synthetic proposal"}],
        },
    )
    current = store.status(doc["id"])
    assert current["pages"][0]["state"] == "pending"
    assert not any(t["stage"] == "verify" for t in current["tasks"])
    assert [t["receipt"].get("configured_model") for t in current["tasks"]] == [
        "gpt-6.1-terra",
        "gpt-6.1-sol",
    ]
    packet = store.human_packet(doc["id"], 1)
    assert packet["markdown"].startswith("1938") and packet["suggested_markdown"].startswith("1939")
    with pytest.raises(ValueError, match="authorization"):
        store.record_human_review(
            doc["id"],
            1,
            input_sha256=packet["input_sha256"],
            markdown=packet["markdown"],
            verdict="approved",
            decision_scope="workflow_test",
        )
    atomic_json(store.root / ".workflow-test-authorization.json", {"enabled": True})
    store.record_human_review(
        doc["id"],
        1,
        input_sha256=packet["input_sha256"],
        markdown=packet["suggested_markdown"],
        verdict="approved",
        decision_scope="workflow_test",
    )
    complete(store, doc["id"], "organization", {"articles": [{"title": "Synthetic", "pages": [1]}]})
    publication = store.publish(doc["id"])
    assert (
        publication["approval_type"] == "workflow_test" and publication["content_verified"] is False
    )
    assert publication["human_reviewed_pages"] == [] and publication["workflow_test_pages"] == [1]
    assert publication["validation_scope"] == "workflow_only"


def test_explicit_errata_stays_a_candidate_and_keeps_old_publication(tmp_path):
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (40, 50), "white").save(image)
    store = Store(tmp_path / "workspace")
    doc = prepare(store, str(image))
    complete(store, doc["id"], "ocr", {"markdown": "old old"})
    complete(store, doc["id"], "review", {"verdict": "approved"})
    complete(store, doc["id"], "organization", {"articles": [{"title": "Synthetic", "pages": [1]}]})
    old = store.publish(doc["id"])
    result = store.propose_revision(
        doc["id"], [{"page": 1, "start": 4, "before": "old", "after": "new"}]
    )
    assert result["content_approved"] is False
    pending = store.status(doc["id"])
    assert pending["pages"][0]["ocr"]["markdown"] == "old new"
    assert pending["pages"][0]["state"] != "approved"
    assert store.search("old")[0]["revision_pending"] is True
    assert pending["active_publication"]["publication"] == old
    complete(store, doc["id"], "review", {"verdict": "approved"})
    complete(store, doc["id"], "organization", {"articles": [{"title": "Synthetic", "pages": [1]}]})
    assert store.publish(doc["id"])["markdown_sha256"] != old["markdown_sha256"]


def test_delete_waits_for_owned_execution_and_only_removes_workspace_copies(tmp_path):
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (40, 50), "white").save(image)
    store = Store(tmp_path / "workspace")
    doc = prepare(store, str(image))
    store.request_run(doc["id"])
    owned = store.claim_run("synthetic-runner")
    assert store.cancel_document(doc["id"])["state"] == "running"
    with pytest.raises(ValueError, match="stop"):
        store.forget_document(doc["id"])
    store.finish_run(doc["id"], owned["token"], error="Synthetic cancellation")
    assert store.status(doc["id"])["run_request"]["state"] == "cancelled"
    store.forget_document(doc["id"])
    assert not store.document_directory(doc["id"]).exists() and image.exists()
