"""Navigation revisions retain canonical ranges and atomically change real search generations."""

from uuid import uuid4

from PIL import Image
from sqlalchemy import insert
from tokenizers import Tokenizer, models, pre_tokenizers

from hrs.api.schemas import NavigationDecision
from hrs.library.books import get_run
from hrs.library.catalog import Library
from hrs.library.navigation import Navigation
from hrs.library.processing_bridge import CodexBridge
from hrs.processing.store import digest
from hrs.search.compute import RetrievalCompute
from hrs.search.indexing import BookIndexer
from hrs.search.search import Search
from hrs.storage import models as db

from .test_codex_bridge import finish


def test_navigation_metadata_reindexes_without_changing_body_or_footnotes(
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
        {"verdict": "approved", "page_observation": {"kind": "toc", "headings": []}},
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
    bridge.store.publish(doc_id)
    bridge.sync(get_run(engine, run))
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    monkeypatch.setattr(RetrievalCompute, "tokenizer", lambda *args: tokenizer)
    monkeypatch.setattr(
        RetrievalCompute,
        "compute",
        lambda self, operation, texts, **options: {
            "vectors": [[1.0] + [0.0] * 1023 for _ in texts],
            "identity": {"synthetic": True},
        },
    )
    indexer = BookIndexer(settings, engine)
    try:
        first = indexer.index(run)
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
        nav = Navigation(settings, engine).status(run)
        assert nav["entries"][0]["target_chapter_id"] == before["id"]
        entry = nav["entries"][0]
        Navigation(settings, engine).decide(
            run, entry["id"], NavigationDecision(expected_sha256=nav["sha256"], target_page=None)
        )
        bridge.sync(get_run(engine, run))
        second = indexer.index(run)
        assert first["generation"] != second["generation"]
        after = library.chapter(before["id"])
        assert (after["text"], after["parts"], after["footnotes"]) == (
            before["text"],
            before["parts"],
            before["footnotes"],
        )
        assert all(not h["toc_path"] for h in search.search("运输记录", book, semantic=False))
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
