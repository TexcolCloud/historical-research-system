"""Current permission controls new actions; retained receipts control old publications."""

import json

import pytest

from hrs.processing.acceptance import decision_scope, publication_acceptance, workspace_scope
from hrs.processing.snapshots import atomic_json
from hrs.processing.store import Store
from tests.unit.processing.test_navigation import (
    test_directory_parses_before_book_complete_and_targets_resolve_later as build_navigation,
)
from tests.unit.processing.test_store import complete, document


def test_current_permission_is_explicit_and_cannot_silently_change_a_submitted_scope(tmp_path):
    assert workspace_scope(tmp_path) == "content"
    marker = tmp_path / ".workflow-test-authorization.json"
    atomic_json(marker, {"enabled": True})
    assert workspace_scope(tmp_path) == "workflow_only"
    assert decision_scope(tmp_path) == "workflow_test"
    with pytest.raises(ValueError, match="differs"):
        decision_scope(tmp_path, "content")
    atomic_json(marker, {"enabled": False})
    assert workspace_scope(tmp_path) == "content"
    with pytest.raises(ValueError, match="differs"):
        decision_scope(tmp_path, "workflow_test")
    marker.write_text("invalid-json", "utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        workspace_scope(tmp_path)
    marker.write_text(json.dumps({"enabled": "false"}), "utf-8")
    with pytest.raises(ValueError, match="boolean"):
        workspace_scope(tmp_path)


def test_revision_scope_preserves_the_active_publication_until_replacement():
    doc = {
        "publication": None,
        "active_publication": {"publication": {"approval_type": "machine"}},
        "pages": [{"approved": {"approval_type": "workflow_test"}}],
    }
    assert publication_acceptance(doc) == "machine_or_human"
    assert publication_acceptance(doc, include_active=False) == "workflow_test"
    doc["publication"] = {"approval_type": "workflow_test", "content_verified": False}
    assert publication_acceptance(doc) == "workflow_test"


def test_test_navigation_decision_retains_provenance_and_is_not_human_content_approval(tmp_path):
    build_navigation(tmp_path)
    store = Store(tmp_path / "data")
    identity = store.list_documents()[0]["id"]
    atomic_json(store.root / ".workflow-test-authorization.json", {"enabled": True})
    doc = store.status(identity)
    nav = store.navigation_decision(
        identity,
        doc["navigation"]["entries"][0]["id"],
        expected_sha256=doc["navigation"]["sha256"],
        target_page=None,
        decision_scope="workflow_test",
        decision_id="workflow-navigation",
    )
    receipt = next(iter(nav["decisions"].values()))
    assert receipt["approval_type"] == "workflow_test"
    assert receipt["content_verified"] is False and receipt["content_approval"] is False
    assert publication_acceptance({"navigation": nav}) == "workflow_test"


def test_draft_can_be_saved_when_permission_is_unreadable_without_losing_located_issues(tmp_path):
    store, identity = document(tmp_path)
    complete(store, identity, "ocr", "ocr", {"markdown": "Technical text"})
    issues = [
        {
            "anchor": {"exact": "Technical text"},
            "focus": "Technical",
            "explanation": "Technical issue",
        }
    ]
    complete(store, identity, "review", "review", {"verdict": "pending", "issues": issues})
    packet = store.human_packet(identity, 1)
    (store.root / ".workflow-test-authorization.json").write_text("invalid", "utf-8")
    saved = store.record_human_review(
        identity,
        1,
        input_sha256=packet["input_sha256"],
        markdown="Retained local draft",
        verdict="pending",
        resolutions=[{"id": "technical", "action": "keep"}],
    )
    assert saved["receipt"]["human_approval"] is False
    doc = store.status(identity)
    task = next(t for t in reversed(doc["tasks"]) if t["stage"] == "human_review")
    assert task["result"]["issues"] == issues
    assert task["result"]["resolutions"] == [{"id": "technical", "action": "keep"}]
    store.verify_evidence(doc)


def test_navigation_decision_allows_paused_review_and_rejects_cancelled_document(tmp_path):
    build_navigation(tmp_path)
    store = Store(tmp_path / "data")
    identity = store.list_documents()[0]["id"]
    store.pause_document(identity)
    before = store.status(identity)
    entry = before["navigation"]["entries"][0]["id"]
    nav = store.navigation_decision(
        identity,
        entry,
        expected_sha256=before["navigation"]["sha256"],
        target_page=None,
        decision_id="paused-review",
    )
    assert store.status(identity)["run_request"]["pause_requested"]
    store.cancel_document(identity)
    cancelled = store.status(identity)
    with pytest.raises(ValueError, match="cancellation"):
        store.navigation_decision(
            identity,
            entry,
            expected_sha256=nav["sha256"],
            target_page=None,
            decision_id="cancelled-review",
        )
    assert store.status(identity) == cancelled
