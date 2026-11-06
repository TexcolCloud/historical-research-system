"""One navigation review policy across persisted counts and live/published reports."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import insert, select, update

from hrs.api.main import create_app
from hrs.library.books import get_run
from hrs.library.catalog import Library
from hrs.library.navigation import Navigation
from hrs.library.processing_bridge import CodexBridge
from hrs.storage import models as db

from .test_codex_bridge import finish


def seed_missing_navigation(platform, tmp_path):
    from hrs.codex.mcp import prepare

    settings, engine = platform
    settings = settings.model_copy(update={"codex_root": tmp_path / "native"})
    bridge = CodexBridge(settings, engine)
    source = tmp_path / "synthetic-directory.png"
    Image.new("RGB", (40, 60), "white").save(source)
    identity = prepare(bridge.store, str(source), "Synthetic directory queue")["id"]
    book_id, run_id = str(uuid4()), str(uuid4())
    with engine.begin() as conn:
        conn.execute(
            insert(db.books).values(id=book_id, title="Synthetic directory queue", state="running")
        )
        conn.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="running",
                stage="ocr",
                source=bridge.objects.put_file(source),
                result={"codex_document_id": identity, "processing_backend": "codex"},
            )
        )
    finish(bridge.store, identity, "ocr", {"markdown": "# 目录\n\n失项……9\n"})
    finish(
        bridge.store,
        identity,
        "review",
        {
            "verdict": "approved",
            "issues": [],
            "page_observation": {"kind": "toc", "headings": ["目录"]},
        },
    )
    task = bridge.store.claim_task(identity, "navigation", "synthetic-navigation")
    bridge.store.original_page(
        identity, 1, task_id=task["task_id"], lease_token=task["lease_token"]
    )
    bridge.store.submit_task(
        identity,
        task["task_id"],
        task["lease_token"],
        {
            "input_sha256": task["input_sha256"],
            "entries": [
                {
                    "source_page": 1,
                    "source_anchor": "失项……9",
                    "title": "失项",
                    "target_label": "9",
                    "level": 1,
                }
            ],
        },
    )
    return settings, engine, bridge, book_id, run_id, identity


def test_missing_directory_requires_review_in_counter_and_live_report(platform, tmp_path):
    settings, engine, bridge, _, run_id, identity = seed_missing_navigation(platform, tmp_path)
    original = bridge.store.status(identity)
    assert original["navigation"]["coverage_complete"]
    assert original["navigation"]["entries"][0]["status"] == "not_found"
    bridge.sync(get_run(engine, run_id))
    assert get_run(engine, run_id)["result"]["codex_navigation_pending_count"] == 1
    report = Navigation(settings, engine).status(run_id)
    assert report["pending_count"] == 1
    assert report["entries"][0]["needs_confirmation"] is True
    assert report["sha256"] == original["navigation"]["sha256"]
    assert bridge.store.status(identity)["navigation"] == original["navigation"]
    with TestClient(create_app(settings, engine)) as client:
        response = client.get(f"/api/v2/runs/{run_id}/navigation").raise_for_status().json()
    assert response["pending_count"] == 1
    assert response["entries"][0]["needs_confirmation"] is True


def test_published_missing_directory_exposes_same_review_policy_without_changing_snapshot(platform):
    settings, engine = platform
    library = Library(settings, engine)
    book_id, run_id, chapter_id = [str(uuid4()) for _ in range(3)]
    entry = {
        "id": "missing",
        "title": "失项",
        "level": 1,
        "path": ["失项"],
        "source_page": 1,
        "source_anchor": "失项……9",
        "target_label": "9",
        "status": "not_found",
        "reason": "未找到正文",
    }
    snapshot = {
        "state": "available",
        "entries": [entry],
        "version": 3,
        "sha256": "frozen-navigation",
        "coverage_complete": True,
    }
    value = "# 目录\n\n失项……9\n"
    reference = library.review.objects.put_bytes(
        json.dumps(
            {
                "parts": [
                    {
                        "text": value,
                        "start": 0,
                        "end": len(value),
                        "source": {"page": 1, "pages": [1]},
                    }
                ],
                "navigation": snapshot,
            },
            ensure_ascii=False,
        ).encode()
    )
    with engine.begin() as conn:
        conn.execute(
            insert(db.books).values(id=book_id, title="Synthetic frozen directory", state="ready")
        )
        conn.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="completed",
                stage="complete",
                result={"published": True},
            )
        )
        conn.execute(
            insert(db.chapters).values(
                id=chapter_id,
                run_id=run_id,
                book_id=book_id,
                position=0,
                title="目录",
                kind="directory",
                content=reference,
                pages=[1],
                codepoints=len(value),
            )
        )
    report = library.reading_navigation(book_id)
    assert report["pending_count"] == 1
    assert report["entries"][0]["needs_confirmation"] is True
    assert report["sha256"] == snapshot["sha256"]
    assert library.review.read_json(reference)["navigation"] == snapshot
    with TestClient(create_app(settings, engine)) as client:
        response = (
            client.get(f"/api/v2/books/{book_id}/reading-navigation").raise_for_status().json()
        )
    assert response["pending_count"] == 1
    assert response["entries"][0]["needs_confirmation"] is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("conflict", True),
        ("not_found", True),
        ("unresolved", True),
        ("waiting_evidence", False),
        ("matched", False),
        ("matched_manual", False),
        ("unlinked_manual", False),
    ],
)
def test_navigation_review_state_matrix(status, expected):
    from hrs.processing.navigation import navigation_requires_confirmation

    assert navigation_requires_confirmation({"status": status}) is expected


def test_queue_policy_change_invalidates_only_projection_input(platform, tmp_path, monkeypatch):
    from hrs.library.projection import facts

    _, _, bridge, _, _, identity = seed_missing_navigation(platform, tmp_path)
    doc = bridge.store.status(identity)
    before = facts.projection_input(doc)
    indexing_policy = facts.PROJECTION_POLICY
    monkeypatch.setattr(facts, "NAVIGATION_REVIEW_POLICY", "older-navigation-queue-policy")
    assert facts.projection_input(doc) != before
    assert facts.PROJECTION_POLICY == indexing_policy


def test_old_persisted_zero_is_refreshed_without_reindex_or_source_changes(
    platform, tmp_path, monkeypatch
):
    from hrs.library.projection import facts
    from hrs.processing.hashing import digest

    settings, engine, bridge, book_id, run_id, identity = seed_missing_navigation(
        platform, tmp_path
    )
    finish(
        bridge.store,
        identity,
        "organization",
        {"articles": [{"title": "Synthetic directory", "pages": [1]}]},
    )
    bridge.store.publish(identity)
    indexed = []
    monkeypatch.setattr(
        "hrs.search.indexing.BookIndexer.index", lambda self, key: indexed.append(key)
    )
    bridge.sync(get_run(engine, run_id))
    bridge.sync(get_run(engine, run_id))
    assert indexed == [run_id]
    doc = bridge.store.status(identity)
    source_hash = digest(doc)
    with engine.connect() as conn:
        chapters = list(
            conn.execute(select(db.chapters).where(db.chapters.c.run_id == run_id)).mappings()
        )
    current = get_run(engine, run_id)
    with monkeypatch.context() as old_policy:
        old_policy.setattr(facts, "NAVIGATION_REVIEW_POLICY", "legacy-conflicts-only")
        previous_input = facts.projection_input(doc)
    stale = {
        **current["result"],
        "codex_navigation_pending_count": 0,
        "codex_navigation_review_policy": "legacy-conflicts-only",
        "codex_projection_input": previous_input,
        "retrieval_generation": "unchanged-generation",
    }
    with engine.begin() as conn:
        conn.execute(update(db.runs).where(db.runs.c.id == run_id).values(result=stale))
    bridge.sync(get_run(engine, run_id))
    refreshed = get_run(engine, run_id)
    assert refreshed["result"]["codex_navigation_pending_count"] == 1
    assert refreshed["result"]["codex_navigation_review_policy"] == facts.NAVIGATION_REVIEW_POLICY
    assert refreshed["result"]["retrieval_generation"] == "unchanged-generation"
    assert refreshed["state"] == "completed"
    assert indexed == [run_id]
    assert digest(bridge.store.status(identity)) == source_hash
    with engine.connect() as conn:
        assert (
            list(conn.execute(select(db.chapters).where(db.chapters.c.run_id == run_id)).mappings())
            == chapters
        )
        assert conn.scalar(select(db.books.c.state).where(db.books.c.id == book_id)) == "ready"
    assert Navigation(settings, engine).status(run_id)["pending_count"] == 1
