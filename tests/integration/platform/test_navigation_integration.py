"""Navigation revisions retain canonical ranges and update metadata without re-embedding."""

from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import func, insert, select
from tokenizers import Tokenizer, models, pre_tokenizers

from hrs.api.schemas import NavigationDecision
from hrs.library.books import get_run
from hrs.library.catalog import Library
from hrs.library.navigation import Navigation
from hrs.library.processing_bridge import CodexBridge
from hrs.processing.source_structure import ROLES
from hrs.processing.store import digest
from hrs.search.compute import RetrievalCompute
from hrs.search.indexing import BookIndexer
from hrs.search.search import Search
from hrs.storage import models as db

from .test_codex_bridge import finish


def source_facts(page, role, text, *, folio=False):
    fragments = [{"fragment_id": f"p{page}:{role}", "role": role, "layer": "source", "exact": text}]
    if folio:
        fragments.append(
            {
                "fragment_id": f"p{page}:folio",
                "role": "page_number",
                "layer": "source",
                "start": len(text) - 1,
                "end": len(text),
                "text_sha256": digest(text[-1]),
            }
        )
    return {
        "schema_version": 1,
        "coverage": [
            {
                "role": name,
                "status": "present" if any(f["role"] == name for f in fragments) else "unknown",
            }
            for name in sorted(ROLES)
        ],
        "fragments": fragments,
        "relations": [],
    }


def test_navigation_metadata_updates_without_changing_body_refs_vectors_or_footnotes(
    platform, tmp_path, monkeypatch
):
    settings, engine = platform
    settings = settings.model_copy(
        update={
            "codex_root": tmp_path / "native",
            "opensearch_index": "test-navigation-" + uuid4().hex,
        }
    )
    bridge = CodexBridge(settings, engine)
    monkeypatch.setattr(bridge.research, "tick", lambda: None)
    image = tmp_path / "source.png"
    Image.new("RGB", (60, 80), "white").save(image)
    doc_id = bridge.store.register(
        image,
        [
            {"page": p, "image": str(image), "image_sha256": digest(image.read_bytes())}
            for p in (1, 2)
        ],
    )["id"]
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="目录验证", state="running"))
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="running",
                stage="ocr",
                source=bridge.objects.put_file(image),
                result={"codex_document_id": doc_id, "processing_backend": "codex"},
            )
        )
    finish(bridge.store, doc_id, "ocr", {"markdown": "目录\n运输记录 1", "page_kind": "toc"})
    finish(
        bridge.store,
        doc_id,
        "review",
        {
            "verdict": "approved",
            "page_observation": {"kind": "toc", "headings": []},
            "source_structure": source_facts(1, "toc", "目录\n运输记录 1"),
        },
    )
    body = "# 运输记录\n\n甲地运入120吨粮食[^a]。\n\n[^a]: 不包括乙地。\n\n1"
    finish(bridge.store, doc_id, "ocr", {"markdown": body})
    finish(
        bridge.store,
        doc_id,
        "review",
        {
            "verdict": "approved",
            "page_observation": {"kind": "text", "headings": ["运输记录"], "printed_label": "1"},
            "source_structure": source_facts(2, "body", body, folio=True),
        },
    )
    task = bridge.store.claim_task(doc_id, "navigation", "synthetic-navigation")
    bridge.store.original_page(doc_id, 1, task_id=task["task_id"], lease_token=task["lease_token"])
    bridge.store.submit_task(
        doc_id,
        task["task_id"],
        task["lease_token"],
        {
            "input_sha256": task["input_sha256"],
            "entries": [
                {
                    "source_page": 1,
                    "source_anchor": "运输记录 1",
                    "title": "运输记录",
                    "target_label": "1",
                }
            ],
        },
    )
    finish(
        bridge.store,
        doc_id,
        "organization",
        {
            "articles": [
                {"title": "目录", "pages": [1], "kind": "toc"},
                {"title": "运输记录", "pages": [2]},
            ]
        },
    )
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    monkeypatch.setattr(RetrievalCompute, "tokenizer", lambda *args: tokenizer)
    model_calls = []
    monkeypatch.setattr(
        RetrievalCompute,
        "compute",
        lambda self, operation, texts, **options: (
            model_calls.append(operation)
            or {
                "vectors": [[1.0] + [0.0] * 1023 for _ in texts],
                "identity": {"synthetic": True},
            }
        ),
    )
    indexer = BookIndexer(settings, engine)
    try:
        bridge.store.publish(doc_id)
        bridge.tick()
        first = get_run(engine, run)
        assert first["state"] == "completed"
        assert first["result"]["codex_projected_sql_revision"] == first["revision"]
        search = Search(settings, engine)
        hits = search.search("运输记录", book, semantic=False)
        assert hits and all(h["doc_kind"] == "content" for h in hits)
        assert any(
            h["toc_path"] == ["运输记录"] and h["printed_pages"][0]["label"] == "1" for h in hits
        )
        assert any(
            h["doc_kind"] == "navigation"
            for h in search.search("运输记录", book, semantic=False, include_navigation=True)
        )
        library = Library(settings, engine)
        before = library.chapter(library.chapters(book)[1]["id"])
        previous_refs = [row["content"] for row in library.chapters(book)]
        model_calls.clear()
        nav = Navigation(settings, engine).status(run)
        assert nav["entries"][0]["target_chapter_id"] == before["id"]
        entry = nav["entries"][0]
        Navigation(settings, engine).decide(
            run, entry["id"], NavigationDecision(expected_sha256=nav["sha256"], target_page=None)
        )
        bridge.tick()
        second = get_run(engine, run)
        assert second["state"] == "completed"
        assert first["result"]["retrieval_generation"] != second["result"]["retrieval_generation"]
        assert model_calls == []
        assert [row["content"] for row in library.chapters(book)] == previous_refs
        after = library.chapter(before["id"])
        assert (after["text"], after["parts"], after["footnotes"]) == (
            before["text"],
            before["parts"],
            before["footnotes"],
        )
        assert all(not h["toc_path"] for h in search.search("运输记录", book, semantic=False))
        previous_structure = second["result"]["codex_reading_projection"]["structure_sha256"]
        bridge.store.backfill_structure(doc_id, pages=[2], force=True)
        finish(
            bridge.store,
            doc_id,
            "structure",
            {"source_structure": source_facts(2, "body", body, folio=True)},
        )
        bridge.tick()
        third = get_run(engine, run)
        assert third["state"] == "completed"
        assert third["result"]["retrieval_generation"] != second["result"]["retrieval_generation"]
        assert third["result"]["codex_indexed_structure_sha256"] != previous_structure
        assert third["result"]["codex_projected_sql_revision"] == third["revision"]
        assert [row["content"] for row in library.chapters(book)] == previous_refs
        assert model_calls == []
        assert bridge.changes.poll() == []

        # A failed build retains the previous complete generation while the SQL
        # projection describes the new, unindexed facts. Retry needs no native event.
        bridge.store.backfill_structure(doc_id, pages=[2], force=True)
        finish(
            bridge.store,
            doc_id,
            "structure",
            {"source_structure": source_facts(2, "body", body, folio=True)},
        )
        with monkeypatch.context() as failed_index:

            def unavailable(*args):
                raise RuntimeError("Synthetic index unavailable")

            failed_index.setattr(BookIndexer, "index", unavailable)
            with pytest.raises(RuntimeError, match="index unavailable"):
                bridge.sync(third)
        failed = get_run(engine, run)
        assert failed["state"] == "ready" and failed["stage"] == "indexing"
        assert failed["result"]["retrieval_generation"] == third["result"]["retrieval_generation"]
        assert (
            failed["result"]["codex_indexed_structure_sha256"]
            != failed["result"]["codex_reading_projection"]["structure_sha256"]
        )
        bridge.sync(failed)
        third = get_run(engine, run)
        assert third["state"] == "completed"
        assert third["result"]["retrieval_generation"] != failed["result"]["retrieval_generation"]
        assert third["result"]["codex_projected_sql_revision"] == third["revision"]
        assert model_calls == []
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(db.outbox)
                    .where(db.outbox.c.kind == "start_research")
                )
                == 1
            )

        def unexpected(*args, **options):
            raise AssertionError("Unchanged complete publication must do no projection/index work")

        monkeypatch.setattr(bridge.artifacts, "bundle", unexpected)
        monkeypatch.setattr(bridge.publication, "publish_chapters", unexpected)
        monkeypatch.setattr(bridge.publication, "index_if_needed", unexpected)
        bridge.sync(third)
        assert get_run(engine, run)["revision"] == third["revision"]
    finally:
        indexer.client.indices.delete(index=settings.opensearch_index, ignore=[404])


def test_navigation_read_reuses_only_the_same_committed_snapshot(platform, tmp_path, monkeypatch):
    from hrs.codex.mcp import prepare
    from hrs.library.navigation import Navigation
    from hrs.processing.store import Store

    settings, engine = platform
    source = tmp_path / "snapshot.png"
    Image.new("RGB", (30, 40), "white").save(source)
    store = Store(tmp_path / "native-cache")
    doc = prepare(store, str(source))
    service = Navigation(settings, engine)
    monkeypatch.setattr(service, "native", lambda _: (store, doc["id"]))
    reads = []
    status = store.status

    def tracked(identity):
        reads.append(identity)
        return status(identity)

    monkeypatch.setattr(Store, "status", lambda self, identity: tracked(identity))
    # Keep the original bound method to observe real JSON parsing, not recursive monkeypatches.
    first = service.status("00000000-0000-0000-0000-000000000001")
    first["state"] = "caller mutation"
    assert service.status("00000000-0000-0000-0000-000000000001")["state"] != "caller mutation"
    assert len(reads) == 1
    store.pause_document(doc["id"])
    service.status("00000000-0000-0000-0000-000000000001")
    assert len(reads) == 2
