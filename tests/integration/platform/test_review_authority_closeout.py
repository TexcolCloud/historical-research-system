"""Actual Store, SQL projection and local decisions exercise retained review authority."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import insert, select, update

from hrs.api.main import create_app
from hrs.api.schemas import ReviewDecision, ReviewDraft
from hrs.library.books import get_run
from hrs.library.errors import ServiceError
from hrs.library.exports import Exports
from hrs.library.processing_bridge import CodexBridge
from hrs.processing.application import prepare
from hrs.processing.snapshots import atomic_json
from hrs.storage import models as db

from .test_codex_bridge import finish


@pytest.fixture
def pending_native(platform, tmp_path):
    settings, engine = platform
    settings = settings.model_copy(update={"codex_root": tmp_path / "native"})
    bridge = CodexBridge(settings, engine)
    source = tmp_path / "technical-source.png"
    Image.new("RGB", (40, 60), "white").save(source)
    identity = prepare(bridge.store, str(source), "Technical authority regression")["id"]
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="Technical", state="running"))
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="running",
                stage="ocr",
                source=bridge.objects.put_file(source),
                result={"processing_backend": "codex", "codex_document_id": identity},
            )
        )
    finish(bridge.store, identity, "ocr", {"markdown": "Original technical text."})
    pending_review(bridge, identity)
    bridge.sync(get_run(engine, run))
    return settings, engine, bridge, identity, book, run


def pending_review(bridge, identity):
    finish(
        bridge.store,
        identity,
        "review",
        {
            "verdict": "pending",
            "disposition": "unreadable",
            "issues": ["Technical unresolved source"],
        },
    )


def decide(bridge, issue, **extra):
    return bridge.review.decide(
        issue["id"],
        ReviewDecision(
            decision_id=uuid4(),
            expected_revision=issue["revision"],
            expected_text_sha256=issue["text_sha256"],
            action="confirm",
            **extra,
        ),
    )


@pytest.mark.parametrize("with_draft", [False, True])
def test_retried_pending_page_always_has_an_operable_human_issue(pending_native, with_draft):
    _, engine, bridge, identity, _, run = pending_native
    old = bridge.review.list(run)[0]
    if with_draft:
        bridge.review.save_draft(
            old["id"], ReviewDraft(expected_revision=old["revision"], text="Retained manual draft.")
        )
    task = next(t for t in bridge.store.status(identity)["tasks"] if t["state"] == "pending")
    bridge.store.retry_task(identity, task["task_id"])
    bridge.sync(get_run(engine, run))
    pending_review(bridge, identity)
    bridge.sync(get_run(engine, run))
    pending = bridge.review.list(run)
    assert len(pending) == get_run(engine, run)["pending_count"] == 1
    issue = bridge.review.get(pending[0]["id"])
    assert issue["id"] == old["id"]
    assert issue["draft_text"] == ("Retained manual draft." if with_draft else None)
    assert not issue["draft_conflict"]
    decide(bridge, issue)
    bridge.sync(get_run(engine, run))
    assert bridge.store.status(identity)["pages"][0]["state"] == "approved"
    assert bridge.review.list(run) == []


def test_retry_with_changed_candidate_keeps_draft_and_requires_new_issue_resolutions(
    pending_native,
):
    _, engine, bridge, identity, _, run = pending_native
    old = bridge.review.list(run)[0]
    bridge.review.save_draft(
        old["id"], ReviewDraft(expected_revision=old["revision"], text="Retained human edit.")
    )
    bridge.store.fallback_page(
        identity, 1, "Technical new source evidence", new_evidence="Independent technical detail"
    )
    bridge.sync(get_run(engine, run))
    finish(bridge.store, identity, "ocr", {"markdown": "Changed technical candidate."})
    pending_review(bridge, identity)
    bridge.sync(get_run(engine, run))
    issue = bridge.review.get(bridge.review.list(run)[0]["id"])
    assert issue["text"] == "Changed technical candidate."
    assert issue["draft_text"] == "Retained human edit."
    assert issue["draft_conflict"] and issue["draft_base_text_sha256"] == old["text_sha256"]
    assert issue["resolutions"] == [] and issue["draft_locations"] is None
    bridge.review.save_draft(
        issue["id"], ReviewDraft(expected_revision=issue["revision"], text=issue["draft_text"])
    )
    rebound = bridge.review.get(issue["id"])
    assert not rebound["draft_conflict"]
    assert rebound["draft_base_text_sha256"] == rebound["text_sha256"]


def test_test_publication_cannot_be_promoted_by_removing_workspace_permission(
    pending_native, monkeypatch
):
    settings, engine, bridge, identity, book, run = pending_native
    marker = bridge.store.root / ".workflow-test-authorization.json"
    atomic_json(marker, {"enabled": True, "scope": "workflow_only"})
    decide(bridge, bridge.review.list(run)[0])
    bridge.sync(get_run(engine, run))
    finish(
        bridge.store, identity, "organization", {"articles": [{"title": "Technical", "pages": [1]}]}
    )
    publication = bridge.store.publish(identity)
    assert publication["approval_type"] == "workflow_test"
    marker.unlink()
    monkeypatch.setattr("hrs.search.indexing.BookIndexer.index", lambda self, run_id: None)
    replacement = CodexBridge(settings, engine)
    replacement.sync(get_run(engine, run))
    replacement.sync(get_run(engine, run))
    assert get_run(engine, run)["result"]["content_acceptance"] == "workflow_test"
    # A stale or incorrect SQL projection must not erase the immutable source's restriction.
    with engine.begin() as connection:
        result = connection.scalar(select(db.runs.c.result).where(db.runs.c.id == run))
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run)
            .values(result={**result, "content_acceptance": "machine_or_human"})
        )
    _, export = Exports(settings, engine).book(book)
    assert "content_verified: false" in bridge.objects.read_bytes(export).decode("utf-8")


@pytest.mark.parametrize("marker", [{"enabled": False}, "invalid-json"])
def test_revoked_or_malformed_permission_does_not_commit_a_stuck_sql_decision(
    pending_native, marker
):
    _, engine, bridge, identity, _, run = pending_native
    path = bridge.store.root / ".workflow-test-authorization.json"
    path.write_text(json.dumps(marker) if isinstance(marker, dict) else marker, "utf-8")
    issue = bridge.review.list(run)[0]
    with pytest.raises(ServiceError) as rejected:
        decide(bridge, issue, decision_scope="workflow_test")
    assert rejected.value.status_code == 409
    assert bridge.review.list(run)[0]["state"] == "pending"
    assert bridge.store.status(identity)["pages"][0]["state"] == "pending"
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(db.review_decisions.c.id).where(
                    db.review_decisions.c.issue_id == issue["id"]
                )
            )
            is None
        )
    atomic_json(path, {"enabled": False})
    decide(bridge, issue, decision_scope="content")
    bridge.sync(get_run(engine, run))
    assert bridge.store.status(identity)["pages"][0]["approved"]["approval_type"] == "human"


def test_permission_revoked_after_click_reopens_the_issue_without_losing_the_receipt(
    pending_native,
):
    _, engine, bridge, identity, _, run = pending_native
    marker = bridge.store.root / ".workflow-test-authorization.json"
    atomic_json(marker, {"enabled": True})
    issue = bridge.review.list(run)[0]
    receipt = decide(bridge, issue, decision_scope="workflow_test")
    atomic_json(marker, {"enabled": False})
    bridge.sync(get_run(engine, run))
    assert bridge.store.status(identity)["pages"][0]["state"] == "pending"
    reopened = bridge.review.list(run)
    assert len(reopened) == 1 and reopened[0]["revision"] > issue["revision"]
    with engine.connect() as connection:
        rejected = connection.scalar(
            select(db.review_decisions.c.receipt).where(
                db.review_decisions.c.id == receipt["decision_id"]
            )
        )
    assert (
        rejected["application_state"] == "rejected"
        and rejected["decision_scope"] == "workflow_test"
    )
    decide(bridge, reopened[0], decision_scope="content")
    bridge.sync(get_run(engine, run))
    assert bridge.store.status(identity)["pages"][0]["state"] == "approved"


def test_stale_corrected_decision_returns_the_submitted_text_as_a_retained_draft(pending_native):
    _, engine, bridge, identity, _, run = pending_native
    issue = bridge.review.list(run)[0]
    bridge.review.decide(
        issue["id"],
        ReviewDecision(
            decision_id=uuid4(),
            expected_revision=issue["revision"],
            expected_text_sha256=issue["text_sha256"],
            action="correct",
            text="Latest submitted human correction.",
        ),
    )
    task = next(
        task for task in bridge.store.status(identity)["tasks"] if task["state"] == "pending"
    )
    bridge.store.retry_task(identity, task["task_id"])
    pending_review(bridge, identity)
    bridge.sync(get_run(engine, run))
    reopened = bridge.review.get(bridge.review.list(run)[0]["id"])
    assert reopened["draft_text"] == "Latest submitted human correction."
    assert bridge.store.status(identity)["pages"][0]["state"] == "pending"


def test_native_retry_and_retained_draft_remain_operable_through_actual_http_routes(pending_native):
    settings, engine, bridge, identity, _, run = pending_native
    with TestClient(create_app(settings, engine)) as client:
        issue = client.get(f"/api/v2/reviews?run_id={run}").raise_for_status().json()[0]
        response = client.put(
            f"/api/v2/reviews/{issue['id']}/draft",
            json={
                "expected_revision": issue["revision"],
                "text": "Retained browser draft.",
            },
        )
        assert response.status_code == 200
        task = next(t for t in bridge.store.status(identity)["tasks"] if t["state"] == "pending")
        bridge.store.retry_task(identity, task["task_id"])
        bridge.sync(get_run(engine, run))
        pending_review(bridge, identity)
        bridge.sync(get_run(engine, run))
        pending = client.get(f"/api/v2/reviews?run_id={run}").raise_for_status().json()
        assert len(pending) == 1
        current = client.get(f"/api/v2/reviews/{pending[0]['id']}").raise_for_status().json()
        assert current["draft_text"] == "Retained browser draft."
        response = client.post(
            f"/api/v2/reviews/{current['id']}/decisions",
            json={
                "decision_id": str(uuid4()),
                "expected_revision": current["revision"],
                "expected_text_sha256": current["text_sha256"],
                "action": "correct",
                "text": current["draft_text"],
                "decision_scope": "content",
            },
        )
        assert response.status_code == 200, response.text
        bridge.sync(get_run(engine, run))
        assert (
            bridge.store.status(identity)["pages"][0]["approved"]["markdown"]
            == "Retained browser draft."
        )
        assert client.get(f"/api/v2/reviews?run_id={run}").json() == []


def test_unreadable_authorization_is_visible_and_cannot_acknowledge_a_final_http_decision(
    pending_native,
):
    settings, engine, bridge, identity, _, run = pending_native
    (bridge.store.root / ".workflow-test-authorization.json").write_text("broken", "utf-8")
    issue = bridge.review.list(run)[0]
    with TestClient(create_app(settings, engine)) as client:
        runtime = client.get("/api/v2/runtime")
        assert runtime.status_code == 503
        response = client.post(
            f"/api/v2/reviews/{issue['id']}/decisions",
            json={
                "decision_id": str(uuid4()),
                "expected_revision": issue["revision"],
                "expected_text_sha256": issue["text_sha256"],
                "action": "confirm",
            },
        )
        assert response.status_code == 409
        assert len(client.get(f"/api/v2/reviews?run_id={run}").json()) == 1
        assert bridge.store.status(identity)["pages"][0]["state"] == "pending"


def test_draft_saved_during_projection_preparation_is_not_replaced_by_its_old_snapshot(
    pending_native, monkeypatch
):
    _, engine, bridge, identity, _, run = pending_native
    issue = bridge.review.list(run)[0]
    task = next(t for t in bridge.store.status(identity)["tasks"] if t["state"] == "pending")
    bridge.store.retry_task(identity, task["task_id"])
    pending_review(bridge, identity)
    prepare_issues = bridge.artifacts.issues

    def save_between_prepare_and_commit(*args):
        prepared = prepare_issues(*args)
        bridge.review.save_draft(
            issue["id"],
            ReviewDraft(
                expected_revision=issue["revision"],
                text="Human edit concurrent with projection.",
            ),
        )
        return prepared

    monkeypatch.setattr(bridge.artifacts, "issues", save_between_prepare_and_commit)
    bridge.sync(get_run(engine, run))
    current = bridge.review.get(bridge.review.list(run)[0]["id"])
    assert current["draft_text"] == "Human edit concurrent with projection."
