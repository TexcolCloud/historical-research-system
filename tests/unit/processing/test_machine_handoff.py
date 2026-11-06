"""Explicit synthetic audit disputes join the genuine human queue as machine evidence."""

import pytest
from PIL import Image

from hrs.codex.mcp import prepare
from hrs.processing.details import original_detail
from hrs.processing.store import Store
from tests.unit.processing.test_store import complete


def fixture(tmp_path):
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (80, 100), "white").save(source)
    store = Store(tmp_path / "workspace")
    doc_id = prepare(store, str(source), "Synthetic audit handoff fixture")["id"]
    complete(store, doc_id, "ocr", "synthetic-ocr", {"markdown": "Original synthetic text"})
    complete(store, doc_id, "review", "synthetic-review", {"verdict": "approved"})
    complete(
        store,
        doc_id,
        "organization",
        "synthetic-org",
        {"articles": [{"title": "Synthetic", "pages": [1]}]},
    )
    publication = store.publish(doc_id)
    detail, _ = original_detail(store, doc_id, 1, [0, 0, 1, 1])
    doc = store.status(doc_id)
    evidence = {
        "source_sha256": doc["source"]["sha256"],
        "image_sha256": doc["pages"][0]["image_sha256"],
        "markdown_sha256": doc["pages"][0]["approved"]["sha256"],
        "original_details": [detail],
        "observations": ["Synthetic source dispute for state testing only."],
    }
    return store, doc_id, evidence, publication


def test_audit_dispute_preserves_publication_blocks_machine_retry_and_resumes_after_human(tmp_path):
    store, doc_id, evidence, old = fixture(tmp_path)
    result = store.request_human_review(
        doc_id, 1, "Synthetic unresolved glyph", evidence, "synthetic-auditor"
    )
    assert result["receipt"]["approval_type"] == "machine"
    assert result["receipt"]["human_approval"] is False
    assert (
        store.request_human_review(
            doc_id, 1, "Synthetic unresolved glyph", evidence, "synthetic-auditor"
        )
        == result
    )
    doc = store.status(doc_id)
    assert doc["revision"] == 2 and doc["active_publication"]["publication"] == old
    assert doc["state"] == "pending" and doc["run_request"]["state"] == "waiting_human"
    assert store.search("Original")[0]["revision_pending"] is True
    assert store.human_queue()[0]["issues"] == ["Synthetic unresolved glyph"]
    with pytest.raises(ValueError, match="awaits human"):
        store.retry_task(doc_id, result["receipt"]["task_id"])
    with pytest.raises(ValueError, match="cannot be replaced"):
        store.fallback_page(doc_id, 1, "Attempt to bypass the audit dispute")
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(doc_id)
    packet = store.human_packet(doc_id, 1)
    store.record_human_review(
        doc_id,
        1,
        input_sha256=packet["input_sha256"],
        reviewer="Synthetic form test",
        markdown="Resolved synthetic text",
        note="Synthetic fixture confirmation only",
        verdict="approved",
    )
    assert store.status(doc_id)["run_request"]["state"] == "pending"
    complete(
        store,
        doc_id,
        "organization",
        "synthetic-new-org",
        {"articles": [{"title": "Synthetic", "pages": [1]}]},
    )
    assert store.publish(doc_id)["approval_type"] == "mixed"
    assert store.search("Resolved")[0]["revision_pending"] is False


def test_stale_audit_and_active_worker_cannot_replace_current_evidence(tmp_path):
    store, doc_id, evidence, old = fixture(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        store.request_human_review(
            doc_id,
            1,
            "Stale synthetic issue",
            {**evidence, "markdown_sha256": "0" * 64},
            "synthetic-auditor",
        )
    assert store.status(doc_id)["publication"] == old
    with pytest.raises(ValueError, match="machine auditor identity"):
        store.request_human_review(doc_id, 1, "Synthetic issue", evidence, " human:forged")
    with pytest.raises(ValueError, match="observations"):
        store.request_human_review(
            doc_id,
            1,
            "Synthetic issue",
            {**evidence, "observations": "not a list"},
            "synthetic-auditor",
        )
    with pytest.raises(ValueError, match="detail evidence"):
        store.request_human_review(
            doc_id,
            1,
            "Unsupported synthetic issue",
            {**evidence, "original_details": []},
            "synthetic-auditor",
        )
    store.begin_revision(doc_id, "Synthetic recheck")
    store.request_run(doc_id)
    store.claim_run("synthetic-active-coordinator")
    with pytest.raises(ValueError, match="active coordinator"):
        store.request_human_review(doc_id, 1, "Synthetic dispute", evidence, "synthetic-auditor")
    assert store.status(doc_id)["pages"][0]["state"] == "awaiting_review"


def test_audit_handoff_cannot_overwrite_an_existing_human_draft(tmp_path):
    store, doc_id, evidence, _ = fixture(tmp_path)
    store.request_human_review(doc_id, 1, "Synthetic source dispute", evidence, "synthetic-auditor")
    packet = store.human_packet(doc_id, 1)
    store.record_human_review(
        doc_id,
        1,
        input_sha256=packet["input_sha256"],
        reviewer="Synthetic drafter",
        markdown="Human synthetic draft",
        note="Synthetic fixture pending",
        verdict="pending",
    )
    with pytest.raises(ValueError, match="Human approvals and drafts"):
        store.request_human_review(
            doc_id, 1, "Another machine issue", evidence, "synthetic-second-auditor"
        )
    assert store.human_packet(doc_id, 1)["markdown"] == "Human synthetic draft"
