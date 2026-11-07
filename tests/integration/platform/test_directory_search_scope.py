"""Directory filters bind search to retained publication positions, including same-page sections."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hrs.library.errors import ServiceError
from hrs.library.review import digest
from hrs.search.chunks import source_excerpt
from hrs.search.directory_scope import DirectoryScope


def fixture():
    book, run, chapter = [str(uuid4()) for _ in range(3)]
    text = "# 同名\n\n甲地粮食120吨[^a]。\n\n# 下级\n\n甲地粮食130吨。\n\n# 同名\n\n乙地粮食999吨。\n\n[^a]: 不包括乙地。\n"
    body = dict(
        id=chapter,
        book_id=book,
        run_id=run,
        title="目录范围",
        text=text,
        parts=[
            dict(
                span_id="p",
                start=0,
                end=len(text),
                text=text,
                source=dict(page=8, pages=[8], original_start=0, original_end=len(text)),
            )
        ],
    )

    def entry(identity, title, start, level):
        return dict(
            id=identity,
            title=title,
            level=level,
            path=[title],
            status="matched",
            reading_target=dict(
                chapter_id=chapter,
                run_id=run,
                physical_page=8,
                start=start,
                end=start + len(title),
                text_sha256=digest(text),
                anchor=title,
            ),
        )

    entries = [
        entry("first", "同名", 2, 1),
        entry("child", "下级", text.index("下级"), 2),
        entry("second", "同名", text.rindex("同名"), 1),
    ]
    library = SimpleNamespace(
        chapters=lambda _: [dict(id=chapter)],
        chapter=lambda _: deepcopy(body),
        reading_navigation=lambda _: dict(entries=deepcopy(entries)),
    )
    return book, body, entries, library


@pytest.mark.parametrize("status", ["matched", "matched_verified"])
def test_parent_scope_contains_children_but_stops_at_same_page_sibling(status):
    book, body, entries, library = fixture()
    entries[0]["status"] = status
    scope = DirectoryScope.resolve(library, book, "first")
    assert scope.ranges == {body["id"]: (0, body["text"].rindex("# 同名"))}
    crossing = {**source_excerpt(body, 0, len(body["text"])), "score": 1, "context": []}
    clipped = scope.clip_candidates([crossing], library.chapter)
    assert len(clipped) == 1
    assert "120吨" in clipped[0]["text"] and "130吨" in clipped[0]["text"]
    assert "999吨" not in clipped[0]["text"]
    assert clipped[0]["sources"][0]["source_record"]["page"] == 8


def test_scope_keeps_only_linked_mandatory_context_outside_range():
    book, body, entries, library = fixture()
    scope = DirectoryScope.resolve(library, book, "first")
    text = body["text"]
    start, end = text.index("甲地粮食120"), text.index("。", text.index("甲地粮食120")) + 1
    note = text.index("[^a]:")
    outside = text.index("乙地粮食999")
    hit = {
        **source_excerpt(body, start, end),
        "score": 1,
        "context": [
            {**source_excerpt(body, outside, outside + len("乙地粮食999吨。")), "role": "neighbor"},
            {**source_excerpt(body, note, len(text)), "role": "footnote"},
        ],
    }
    filtered = scope.clip_results([hit], library.chapter)
    assert len(filtered[0]["context"]) == 1
    assert filtered[0]["context"][0]["outside_scope"] is True
    assert "不包括乙地" in filtered[0]["context"][0]["text"]
    assert filtered[0]["context"][0]["sources"]


def test_missing_book_stale_hash_and_unresolved_end_do_not_fall_back_to_full_book():
    book, body, entries, library = fixture()
    with pytest.raises(ServiceError):
        DirectoryScope.resolve(library, None, "first")
    with pytest.raises(ServiceError):
        DirectoryScope.resolve(library, book, "unknown")
    entries[0]["reading_target"]["text_sha256"] = "stale"
    with pytest.raises(ServiceError):
        DirectoryScope.resolve(library, book, "first")
    entries[0]["reading_target"]["text_sha256"] = digest(body["text"])
    entries[2]["reading_target"] = None
    with pytest.raises(ServiceError):
        DirectoryScope.resolve(library, book, "first")


def test_scope_crops_do_not_inherit_notes_owned_only_by_excluded_text():
    book, body, entries, library = fixture()
    scope = DirectoryScope.resolve(library, book, "child")
    text = body["text"]
    row = {
        **source_excerpt(body, 0, len(text)),
        "score": 1,
        "context": [
            {**source_excerpt(body, text.index("[^a]:"), len(text)), "role": "footnote"},
            {**source_excerpt(body, 0, text.index("# 下级")), "role": "section_intro"},
        ],
    }
    clipped = scope.clip_candidates([row], library.chapter)
    assert "130吨" in clipped[0]["text"]
    assert clipped[0]["context"] == []


@pytest.mark.parametrize("following_heading", ["# 第二章", "   ### 第二章", "\n\n# 第二章"])
def test_scope_uses_canonical_heading_boundary_not_reader_visible_start(following_heading):
    from hrs.library.reading_locations import reading_target

    book, run = str(uuid4()), str(uuid4())
    chapters = []
    for page, text in (
        (8, "# 第一章\n\n本章正文。\n"),
        (9, following_heading + "\n\n旁章南方局。\n"),
    ):
        chapters.append(
            dict(
                id=str(uuid4()),
                book_id=book,
                run_id=run,
                title=next(line for line in text.splitlines() if line.strip()),
                text=text,
                parts=[
                    dict(
                        span_id=str(page),
                        start=0,
                        end=len(text),
                        text=text,
                        source=dict(
                            page=page, pages=[page], original_start=0, original_end=len(text)
                        ),
                    )
                ],
            )
        )
    entries = [
        dict(
            id=str(i),
            title=chapter["title"],
            level=1,
            status="matched",
            reading_target=reading_target(
                chapter,
                chapter["text"].index(chapter["title"]),
                chapter["text"].index(chapter["title"]) + len(chapter["title"]),
            ),
        )
        for i, chapter in enumerate(chapters)
    ]
    assert (
        entries[1]["reading_target"]["start"] > 0
    )  # UI excludes heading syntax, unlike scope ownership.
    by_id = {chapter["id"]: chapter for chapter in chapters}
    library = SimpleNamespace(
        chapters=lambda _: chapters,
        chapter=by_id.__getitem__,
        reading_navigation=lambda _: dict(entries=entries),
    )
    scope = DirectoryScope.resolve(library, book, "0")
    other = chapters[1]
    candidate = {**source_excerpt(other, 0, len(other["text"])), "score": 1, "context": []}
    assert scope.clip_candidates([candidate], library.chapter) == []
    assert other["id"] not in scope.ranges


def test_scope_does_not_treat_hash_text_inside_code_as_a_markdown_heading():
    book, body, _, library = fixture()
    body["text"] = "```\n# 字面示例\n```\n"
    body["parts"][0].update(text=body["text"], end=len(body["text"]))
    start = body["text"].index("字面示例")
    target = dict(chapter_id=body["id"], start=start, text_sha256=digest(body["text"]))
    library.reading_navigation = lambda _: dict(
        entries=[dict(id="literal", level=1, status="matched", reading_target=target)]
    )
    scope = DirectoryScope.resolve(library, book, "literal")
    assert scope.ranges[body["id"]][0] == start


def test_legacy_directory_hit_keeps_each_explicit_link_without_picking_first():
    from hrs.search.search import Search

    book, body, entries, library = fixture()
    text = body["text"]
    body["navigation"] = {"page_kinds": {"8": "toc"}}
    links = [
        dict(
            id=entry["id"],
            title=entry["title"],
            level=entry["level"],
            source_start=entry["reading_target"]["start"],
            source_end=entry["reading_target"]["end"],
            target=entry["reading_target"],
        )
        for entry in entries
    ]
    body["toc_links"] = links
    search = object.__new__(Search)
    search.library = library
    hit = {**source_excerpt(body, 0, len(text)), "score": 1, "context": []}
    hit.pop("doc_kind")  # The stored legacy index has no new directory metadata.
    result = search.reading_results([hit])[0]
    assert result["doc_kind"] == "navigation"
    assert result["reading_target"] is None
    assert [entry["id"] for entry in result["directory_links"]] == [e["id"] for e in entries]
    first = links[0]
    narrower = {
        **source_excerpt(body, first["source_start"], first["source_end"]),
        "score": 1,
        "context": [],
    }
    resolved = search.reading_results([narrower])[0]
    assert resolved["reading_target"] == first["target"]
    stale = {**narrower, "text": "旧版已经变化"}
    rejected = search.reading_results([stale])[0]
    assert rejected["reading_target"] is None and rejected["directory_links"] == []


def test_real_index_directory_scope_keeps_canonical_sources_and_body_targets(platform, monkeypatch):
    import json

    from fastapi.testclient import TestClient
    from sqlalchemy import insert
    from tokenizers import Tokenizer, models, pre_tokenizers

    from hrs.api.main import create_app
    from hrs.search import search as module
    from hrs.search.compute import RetrievalCompute
    from hrs.search.indexing import BookIndexer
    from hrs.search.search import Search
    from hrs.storage import models as db

    settings, engine = platform
    settings = settings.model_copy(
        update={"opensearch_index": "test-directory-scope-" + uuid4().hex}
    )
    book, body, entries, _ = fixture()
    chapter, run, text = body["id"], body["run_id"], body["text"]
    body["parts"][0]["source"].update(image_sha256="a" * 64, markdown_sha256=digest(text))
    navigation = {
        "entries": [],
        "version": 1,
        "sha256": "snapshot",
        "state": "available",
        "coverage_complete": True,
        "page_labels": [],
        "page_kinds": {"8": "text"},
    }
    for entry in entries:
        nav = {k: v for k, v in entry.items() if k != "reading_target"}
        nav.update(
            source_page=1,
            source_anchor=entry["title"] + " 1",
            target_label="1",
            reason="",
            target=dict(
                physical_page=8,
                printed_label="1",
                anchor=entry["title"],
                start=entry["reading_target"]["start"],
                markdown_sha256=digest(text),
                image_sha256="a" * 64,
                sequence="body",
            ),
        )
        navigation["entries"].append(nav)
    search = Search(settings, engine)
    ref = search.outputs.objects.put_bytes(
        json.dumps({"parts": body["parts"], "navigation": navigation}, ensure_ascii=False).encode()
    )
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book, title="合成同页目录范围", state="ready")
        )
        connection.execute(
            insert(db.runs).values(
                id=run, book_id=book, state="ready", stage="indexing", result={"published": True}
            )
        )
        connection.execute(
            insert(db.chapters).values(
                id=chapter,
                book_id=book,
                run_id=run,
                position=0,
                title="同页多个条目",
                kind="article",
                content=ref,
                pages=[8],
                codepoints=len(text),
            )
        )
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    monkeypatch.setattr(RetrievalCompute, "tokenizer", lambda *args: tokenizer)
    monkeypatch.setattr(
        RetrievalCompute,
        "compute",
        lambda self, operation, texts, **kwargs: (
            {"vectors": [[1.0] + [0.0] * 1023 for _ in texts], "identity": {"synthetic": True}}
            if operation == "embed"
            else {"scores": [1.0] * len(texts)}
        ),
    )
    monkeypatch.setattr(module, "query_vector", lambda *_: [1.0] + [0.0] * 1023)
    indexer = BookIndexer(settings, engine)
    try:
        indexer.index(run)
        all_hits = search.search("粮食", book, semantic=False)
        assert any("999吨" in hit["text"] for hit in all_hits)
        for semantic in (False, True):
            scoped = search.search("粮食", book, semantic=semantic, toc_entry_id="first")
            assert scoped and all("999吨" not in hit["text"] for hit in scoped)
            assert all(hit["reading_target"]["chapter_id"] == chapter for hit in scoped)
            assert all(hit["reading_target"]["text_sha256"] == digest(text) for hit in scoped)
            contexts = [extra for hit in scoped for extra in hit["context"]]
            assert any(
                "不包括乙地" in extra["text"] and extra["outside_scope"] for extra in contexts
            )
            assert all("999吨" not in extra["text"] for extra in contexts)
        child = search.search("粮食", book, semantic=False, toc_entry_id="child")
        assert child and all(
            "120吨" not in hit["text"] and "999吨" not in hit["text"] for hit in child
        )
        multipart = search.search(
            "请分别指出甲地粮食的吨数，以及甲地粮食的限定范围。", book, toc_entry_id="first"
        )
        assert multipart and all("999吨" not in hit["text"] for hit in multipart)
        assert any(
            extra.get("outside_scope") and "不包括乙地" in extra["text"]
            for hit in multipart
            for extra in hit["context"]
        )
        with TestClient(create_app(settings, engine)) as client:
            response = client.get(
                "/api/v2/search",
                params={"q": "粮食", "book_id": book, "semantic": False, "toc_entry_id": "first"},
            )
            assert response.status_code == 200, response.text
            assert response.json()[0]["reading_target"]["chapter_id"] == chapter
            assert (
                client.get(
                    "/api/v2/search", params={"q": "粮食", "toc_entry_id": "first"}
                ).status_code
                == 422
            )
            assert (
                client.get(
                    "/api/v2/search", params={"q": "粮食", "book_id": book, "toc_entry_id": "stale"}
                ).status_code
                == 409
            )
    finally:
        indexer.client.indices.delete(index=settings.opensearch_index, ignore=[404])
