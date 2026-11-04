"""The existing reader/review/export contracts consume native Codex evidence."""

from uuid import uuid4

import pytest
from hrs_codex.server import prepare
from PIL import Image
from sqlalchemy import insert, update

from hrs_platform import models as db
from hrs_platform.schemas import ReviewDecision, RunSummary
from hrs_platform.services.books import get_run
from hrs_platform.services.codex_bridge import CodexBridge, article_groups
from hrs_platform.services.documents.library import Library
from hrs_platform.services.exports import Exports


def finish(store, doc_id, stage, result):
    task = store.claim_task(doc_id, stage, "synthetic-" + stage)
    store.submit_task(
        doc_id, task["task_id"], task["lease_token"], {"input_sha256": task["input_sha256"], **result}
    )


def test_native_pending_review_old_ui_confirmation_reader_and_export(platform, tmp_path, monkeypatch):
    settings, engine = platform
    settings = settings.model_copy(update={"codex_root": tmp_path / "native"})
    bridge = CodexBridge(settings, engine)
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (60, 80), "white").save(source)
    doc_id = prepare(bridge.store, str(source), "Synthetic integration")["id"]
    book_id, run_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book_id, title="Synthetic integration", state="processing")
        )
        connection.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="running",
                stage="ocr",
                source=bridge.objects.put_file(source),
                result={"codex_document_id": doc_id, "processing_backend": "codex"},
            )
        )
    original = "Synthetic[^1]\n\n[^1]: A source-owned footnote.\n"
    finish(bridge.store, doc_id, "ocr", {"markdown": original})
    bridge.sync(get_run(engine, run_id))
    assert RunSummary.model_validate(get_run(engine, run_id)).page_progress.model_dump() == {
        "total": 1,
        "extracted": 1,
        "reviewed": 0,
        "approved": 0,
        "pending": 0,
    }
    finish(bridge.store, doc_id, "review", {"verdict": "pending", "issues": ["Synthetic test handoff"]})
    bridge.sync(get_run(engine, run_id))
    assert RunSummary.model_validate(get_run(engine, run_id)).page_progress.model_dump() == {
        "total": 1,
        "extracted": 1,
        "reviewed": 1,
        "approved": 0,
        "pending": 1,
    }
    issues = bridge.review.list(run_id)
    assert len(issues) == 1 and not Library(settings, engine).chapters(book_id)
    issue = bridge.review.get(issues[0]["id"])
    assert len(issue["footnotes"]) == 1
    bridge.review.decide(
        issue["id"],
        ReviewDecision(
            decision_id=uuid4(),
            expected_revision=issue["revision"],
            expected_text_sha256=issue["text_sha256"],
            action="confirm",
        ),
    )
    bridge.sync(get_run(engine, run_id))
    assert bridge.store.status(doc_id)["pages"][0]["state"] == "approved"
    assert any(
        t["stage"] == "organization" and t["state"] == "ready" for t in bridge.store.status(doc_id)["tasks"]
    )
    finish(bridge.store, doc_id, "organization", {"articles": [{"title": "Synthetic", "pages": [1]}]})
    bridge.store.publish(doc_id)
    bridge.sync(get_run(engine, run_id))
    library = Library(settings, engine)
    chapters = library.chapters(book_id)
    assert len(chapters) == 1
    chapter = library.chapter(chapters[0]["id"])
    assert len(chapter["footnotes"]) == 1 and chapter["text"] == original + "\n\n"
    assert chapter["parts"][0]["source"]["pages"] == [1]
    title, exported = Exports(settings, engine).book(book_id)
    assert title == "Synthetic integration"
    assert bridge.objects.read_bytes(exported).decode("utf8").count("[^c1-1]") == 2

    # Completion is stable after indexing; a later erratum preserves the old reader
    # until the replacement has passed independent review and organization.
    indexed = []
    monkeypatch.setattr(
        "hrs_platform.services.retrieval.indexing.BookIndexer.index",
        lambda self, run_id: indexed.append(run_id),
    )
    bridge.sync(get_run(engine, run_id))
    bridge.sync(get_run(engine, run_id))
    assert get_run(engine, run_id)["state"] == "completed" and indexed == [run_id]
    candidate = library.amend(
        run_id,
        [
            {
                "chapter_id": chapter["id"],
                "changes": [
                    {
                        "start": original.index("source-owned"),
                        "before": "source-owned",
                        "after": "editor-owned",
                    }
                ],
            }
        ],
    )
    assert candidate["content_approved"] is False
    bridge.sync(get_run(engine, run_id))
    assert library.chapter(chapter["id"])["text"] == chapter["text"]
    assert RunSummary.model_validate(get_run(engine, run_id)).page_progress.reviewed == 0
    finish(bridge.store, doc_id, "review", {"verdict": "approved"})
    finish(bridge.store, doc_id, "organization", {"articles": [{"title": "Synthetic", "pages": [1]}]})
    bridge.store.publish(doc_id)
    bridge.sync(get_run(engine, run_id))
    bridge.sync(get_run(engine, run_id))
    revised = library.chapter(chapter["id"])
    assert "editor-owned" in revised["text"] and "source-owned" not in revised["text"]
    assert indexed == [run_id, run_id] and get_run(engine, run_id)["state"] == "completed"
    _, revised_export = Exports(settings, engine).book(book_id)
    assert revised_export != exported
    assert b"editor-owned" in bridge.objects.read_bytes(revised_export)
    with engine.begin() as connection:
        result = get_run(engine, run_id)["result"]
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run_id)
            .values(result={**result, "content_acceptance": "workflow_test"})
        )
    _, workflow_export = Exports(settings, engine).book(book_id)
    assert workflow_export != revised_export
    assert b"content_verified: false" in bridge.objects.read_bytes(workflow_export)


def test_shared_page_article_projection_is_complete_and_refuses_unlocated_boundaries():
    doc = {
        "organization": {
            "articles": [
                {"title": "A", "pages": [1]},
                {"title": "B", "pages": [1], "boundaries": [{"page": 1, "start_text": "# B"}]},
            ]
        },
        "pages": [
            {
                "page": 1,
                "image_sha256": "a" * 64,
                "approved": {
                    "markdown": "# A\ntext[^1]\n\n[^1]: note A\n\n# B\ntext B",
                    "sha256": "b" * 64,
                    "approval_type": "machine",
                },
            }
        ],
    }
    groups = article_groups(doc, str(uuid4()))
    assert (
        "".join(p["text"] for g in groups for p in g["parts"])
        == doc["pages"][0]["approved"]["markdown"] + "\n\n"
    )
    assert "note A" in groups[0]["parts"][0]["text"] and "# B" not in groups[0]["parts"][0]["text"]
    doc["organization"]["articles"][1]["boundaries"][0]["start_text"] = "missing"
    with pytest.raises(ValueError, match="unique start_text"):
        article_groups(doc, str(uuid4()))
    doc["organization"]["articles"][1]["boundaries"][0]["start_text"] = "B\ntext B"
    groups = article_groups(doc, str(uuid4()))
    assert groups[1]["parts"][0]["text"].startswith("# B")
    assert not groups[0]["parts"][0]["text"].endswith("# ")


def test_native_deletion_keeps_shared_workspace_until_last_owner_stops(platform, tmp_path):
    from hrs_platform.services.deletion import receipt, request_deletion

    settings, engine = platform
    settings = settings.model_copy(
        update={"codex_root": tmp_path / "native", "cache_root": tmp_path / "cache"}
    )
    bridge = CodexBridge(settings, engine)
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (60, 80), "white").save(source)
    doc_id = prepare(bridge.store, str(source))["id"]
    owners = [(str(uuid4()), str(uuid4())) for _ in range(2)]
    for _, run_id in owners:
        intake = bridge.store.root / "platform-intake" / run_id
        intake.mkdir(parents=True)
        (intake / "original.pdf").write_bytes(b"synthetic-intake-copy")
    with engine.begin() as connection:
        for book_id, run_id in owners:
            connection.execute(
                insert(db.books).values(id=book_id, title="Synthetic shared source", state="running")
            )
            connection.execute(
                insert(db.runs).values(
                    id=run_id,
                    book_id=book_id,
                    state="running",
                    stage="ocr",
                    result={"codex_document_id": doc_id, "processing_backend": "codex"},
                )
            )
    bridge.store.request_run(doc_id)
    running = bridge.store.claim_run("synthetic-owned-runner")
    request_deletion(engine, owners[0][0])
    bridge.delete_pending()
    assert receipt(engine, owners[0][0])["state"] == "completed"
    assert not (bridge.store.root / "platform-intake" / owners[0][1]).exists()
    assert (bridge.store.root / "platform-intake" / owners[1][1]).exists()
    assert bridge.store._directory(doc_id).exists()
    assert not bridge.store.status(doc_id)["run_request"].get("cancel_requested")
    request_deletion(engine, owners[1][0])
    bridge.delete_pending()
    assert receipt(engine, owners[1][0])["state"] == "pending"
    assert bridge.store.status(doc_id)["run_request"]["cancel_requested"]
    with pytest.raises(ValueError, match="cancellation"):
        bridge.store.claim_task(doc_id, "ocr", "late-worker")
    bridge.store.finish_run(doc_id, running["token"], error="Synthetic cancellation")
    bridge.delete_pending()
    assert receipt(engine, owners[1][0])["state"] == "completed"
    assert not bridge.store._directory(doc_id).exists() and source.exists()
    assert not (bridge.store.root / "platform-intake" / owners[1][1]).exists()


def test_native_table_continuation_preserves_notes_and_original_offsets(platform, tmp_path):
    from hrs_platform.services.retrieval.chunks import retrieval_chunks

    settings, engine = platform
    settings = settings.model_copy(update={"codex_root": tmp_path / "native"})
    bridge = CodexBridge(settings, engine)
    source = tmp_path / "synthetic.pdf"
    page = Image.new("RGB", (60, 80), "white")
    page.save(source, save_all=True, append_images=[page.copy()])
    doc_id = prepare(bridge.store, str(source))["id"]
    book_id, run_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book_id, title="Synthetic tables", state="running"))
        connection.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="running",
                stage="ocr",
                result={"processing_backend": "codex", "codex_document_id": doc_id},
            )
        )
    first = "<table><tr><th>Area</th><th>Count</th></tr><tr><td>A</td><td>25</td></tr></table>"
    second = "<table><tr><th>Area</th><th>Count</th></tr><tr><td>B[^1]</td><td>35</td></tr></table>"
    values = [first, second + "\n\n[^1]: Synthetic translator note."]
    for value in values:
        finish(bridge.store, doc_id, "ocr", {"markdown": value})
    for _ in values:
        finish(bridge.store, doc_id, "review", {"verdict": "approved"})
    merged = first.replace("</table>", "<tr><td>B[^1]</td><td>35</td></tr></table>")
    finish(
        bridge.store,
        doc_id,
        "organization",
        {
            "articles": [{"title": "Synthetic tables", "pages": [1, 2]}],
            "reading_structure": {
                "tables": [
                    {
                        "pages": [1, 2],
                        "merged_html": merged,
                        "members": [
                            {"page": 1, "original_html": first},
                            {"page": 2, "original_html": second},
                        ],
                    }
                ]
            },
        },
    )
    bridge.store.publish(doc_id)
    bridge.sync(get_run(engine, run_id))
    library = Library(settings, engine)
    chapter = library.chapter(library.chapters(book_id)[0]["id"])
    assert chapter["text"].count("<table>") == 2
    assert chapter["reading_text"].count("<table>") == 1
    assert len(chapter["reading_footnotes"]) == 1
    chunks = list(retrieval_chunks(chapter, "Synthetic tables"))
    assert any(c["role"] == "table_header" for item in chunks for c in item["context"])
    for item in chunks:
        assert item["text"] == chapter["text"][item["start"] : item["end"]]
