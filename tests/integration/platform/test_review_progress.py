"""Terminal page review stays complete after the final human fallback supersedes a machine task."""

from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import insert

from hrs.api.main import create_app
from hrs.library.books import get_run
from hrs.library.processing_bridge import CodexBridge
from hrs.processing.application import prepare
from hrs.processing.checkpoints import atomic_json
from hrs.storage import models as db

from .test_codex_bridge import finish


def test_terminal_review_coverage_survives_human_fallback_and_publication(
    platform, tmp_path, monkeypatch
):
    settings, engine = platform
    root = tmp_path / "native"
    atomic_json(root / ".workflow-test-authorization.json", {"enabled": True})
    settings = settings.model_copy(update={"codex_root": root})
    bridge = CodexBridge(settings, engine)
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=200, height=300)
    stream = BytesIO()
    writer.write(stream)
    source = tmp_path / "synthetic-two-pages.pdf"
    source.write_bytes(stream.getvalue())
    identity = prepare(bridge.store, str(source))["id"]
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book, title="终审进度技术夹具", state="processing")
        )
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
    for text in ("Machine reviewed page.", "Human fallback page."):
        finish(bridge.store, identity, "ocr", {"markdown": text})
    finish(bridge.store, identity, "review", {"verdict": "approved"})
    finish(
        bridge.store,
        identity,
        "review",
        {
            "verdict": "pending",
            "disposition": "unreadable",
            "issues": ["Synthetic final handoff"],
        },
    )
    bridge.sync(get_run(engine, run))
    issue = bridge.review.list(run)[0]
    with TestClient(create_app(settings, engine)) as client:
        response = client.post(
            f"/api/v2/reviews/{issue['id']}/decisions",
            json={
                "decision_id": str(uuid4()),
                "expected_revision": issue["revision"],
                "expected_text_sha256": issue["text_sha256"],
                "action": "confirm",
            },
        )
        assert response.status_code == 200, response.text
        bridge.sync(get_run(engine, run))
        organizing = client.get(f"/api/v2/runs/{run}").raise_for_status().json()
        assert organizing["stage"] == "organization"
        assert organizing["page_progress"]["reviewed"] == 2
        assert organizing["page_progress"]["machine_reviewed"] == 1
        assert organizing["progress"]["completed"] == 2
        evidence = bridge.store.status(identity)
        assert (
            sum(t["stage"] == "review" and t["state"] == "completed" for t in evidence["tasks"])
            == 1
        )
        assert (
            sum(
                t["stage"] == "human_review" and t["state"] == "completed"
                for t in evidence["tasks"]
            )
            == 1
        )
        finish(
            bridge.store,
            identity,
            "organization",
            {"articles": [{"title": "Synthetic", "pages": [1, 2]}]},
        )
        bridge.store.publish(identity)
        monkeypatch.setattr("hrs.search.indexing.BookIndexer.index", lambda self, run_id: None)
        bridge.sync(get_run(engine, run))
        bridge.sync(get_run(engine, run))
        completed = client.get(f"/api/v2/runs/{run}").raise_for_status().json()
        assert completed["state"] == "completed"
        assert completed["page_progress"]["reviewed"] == completed["page_progress"]["total"] == 2
        assert completed["page_progress"]["machine_reviewed"] == 1
        assert completed["content_acceptance"] == "workflow_test"
