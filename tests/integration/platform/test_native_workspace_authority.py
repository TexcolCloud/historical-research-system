"""A SQL projection cannot acquire authority over another native workspace."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from hrs.api.main import create_app
from hrs.api.schemas import ReviewDecision, ReviewDraft
from hrs.library.books import get_run
from hrs.library.processing_bridge import CodexBridge
from hrs.processing.workspace import identity
from hrs.storage import models as db

from .test_review_authority_closeout import pending_native as pending_native  # noqa: F401


def snapshot(engine):
    with engine.connect() as connection:
        return {
            table.name: [dict(row) for row in connection.execute(select(table)).mappings()]
            for table in (db.books, db.runs, db.review_issues, db.review_decisions, db.outbox)
        }


@pytest.mark.parametrize("explicit", [False, True])
def test_other_workspace_reconciliation_preserves_sql_and_drafts(
    pending_native, tmp_path, explicit
):
    settings, engine, first, document, _, run = pending_native
    if explicit:
        with engine.begin() as connection:
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run)
                .values(
                    result={
                        **get_run(engine, run)["result"],
                        "codex_workspace_id": identity(first.store)["workspace_id"],
                    }
                )
            )
    issue = first.review.get(first.review.list(run)[0]["id"])
    first.review.save_draft(
        issue["id"], ReviewDraft(expected_revision=issue["revision"], text="Human retained draft.")
    )
    with engine.begin() as connection:
        connection.execute(
            insert(db.outbox).values(
                dedup_key=str(uuid4()), run_id=run, kind="review_changed", payload={}
            )
        )
    if not explicit:
        with engine.begin() as connection:
            retained = get_run(engine, run)["result"]
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run)
                .values(result={k: v for k, v in retained.items() if k != "codex_workspace_id"})
            )
    before = snapshot(engine)
    second = CodexBridge(settings.model_copy(update={"codex_root": tmp_path / "other"}), engine)
    second.tick()
    assert snapshot(engine) == before
    assert second.store.list_documents() == []
    assert first.store.status(document)["pages"][0]["state"] == "pending"


def test_wrong_workspace_mutations_return_conflict_without_sql_changes(pending_native, tmp_path):
    settings, engine, first, _, _, run = pending_native
    issue = first.review.get(first.review.list(run)[0]["id"])
    with engine.begin() as connection:
        connection.execute(update(db.runs).where(db.runs.c.id == run).values(state="failed"))
    before = snapshot(engine)
    other = settings.model_copy(update={"codex_root": tmp_path / "other"})
    with TestClient(create_app(other, engine)) as client:
        response = client.delete(f"/api/v2/books/{get_run(engine, run)['book_id']}")
        assert response.status_code == 409, response.text
        urls = [
            (f"/api/v2/runs/{run}/pause", {}),
            (f"/api/v2/runs/{run}/resume", {}),
            (f"/api/v2/runs/{run}/retry", {"request_id": str(uuid4())}),
            (
                f"/api/v2/reviews/{issue['id']}/decisions",
                ReviewDecision(
                    decision_id=uuid4(),
                    expected_revision=issue["revision"],
                    expected_text_sha256=issue["text_sha256"],
                    action="confirm",
                ).model_dump(mode="json"),
            ),
            (
                f"/api/v2/reviews/{issue['id']}/draft",
                {"expected_revision": issue["revision"], "text": "must not save"},
            ),
        ]
        for url, body in urls:
            response = (
                client.put(url, json=body)
                if url.endswith("/draft")
                else client.post(url, json=body)
            )
            assert response.status_code == 409, (url, response.status_code, response.text)
    assert snapshot(engine) == before


def test_legacy_mapping_binds_only_source_proven_workspace(pending_native):
    _, engine, bridge, _, _, run = pending_native
    from hrs.library.native_workspace import require_workspace

    original = get_run(engine, run)
    with engine.begin() as connection:
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run)
            .values(
                result={k: v for k, v in original["result"].items() if k != "codex_workspace_id"}
            )
        )
    current = require_workspace(engine, get_run(engine, run), bridge.store)
    assert current["result"]["codex_workspace_id"] == identity(bridge.store)["workspace_id"]
    assert (
        get_run(engine, run)["result"]["codex_workspace_id"]
        == current["result"]["codex_workspace_id"]
    )


def test_same_source_id_in_another_workspace_does_not_grant_authority(pending_native, tmp_path):
    settings, engine, first, document, _, run = pending_native
    from hrs.processing.application import prepare

    doc = first.store.status(document)
    second = CodexBridge(settings.model_copy(update={"codex_root": tmp_path / "copy-root"}), engine)
    source = first.store.artifact_path(doc, doc["source"]["path"])
    assert prepare(second.store, str(source))["id"] == document
    before = snapshot(engine)
    second.tick()
    assert snapshot(engine) == before
    assert first.store.status(document) == doc
    assert second.store.status(document)["pages"][0]["state"] != "pending"


def test_legacy_mapping_with_wrong_source_hash_is_not_adopted(pending_native):
    _, engine, bridge, _, _, run = pending_native
    from hrs.library.native_workspace import WrongWorkspace, require_workspace

    current = get_run(engine, run)
    with engine.begin() as connection:
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run)
            .values(
                result={k: v for k, v in current["result"].items() if k != "codex_workspace_id"},
                source={**current["source"], "sha256": "a" * 64},
            )
        )
    before = snapshot(engine)
    with pytest.raises(WrongWorkspace):
        require_workspace(engine, get_run(engine, run), bridge.store)
    assert snapshot(engine) == before


def test_foreign_outbox_backlog_cannot_starve_the_current_workspace(pending_native):
    _, engine, bridge, _, book, run = pending_native
    with engine.begin() as connection:
        for _ in range(55):
            foreign = str(uuid4())
            connection.execute(
                insert(db.runs).values(
                    id=foreign,
                    book_id=book,
                    state="queued",
                    stage="verify_upload",
                    result={"processing_backend": "codex", "codex_workspace_id": "other-workspace"},
                )
            )
            connection.execute(
                insert(db.outbox).values(
                    dedup_key=str(uuid4()), run_id=foreign, kind="start_book", payload={}
                )
            )
        event = connection.execute(
            insert(db.outbox)
            .values(dedup_key=str(uuid4()), run_id=run, kind="review_changed", payload={})
            .returning(db.outbox.c.id)
        ).scalar_one()
    bridge.tick()
    with engine.connect() as connection:
        assert connection.scalar(select(db.outbox.c.delivered).where(db.outbox.c.id == event))
        assert not any(
            connection.scalars(select(db.outbox.c.delivered).where(db.outbox.c.id != event))
        )
