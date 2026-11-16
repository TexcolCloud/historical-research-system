"""The source view shares publication visibility and immutable content with the reader."""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from hrs.library import catalog, chapter_content, navigation_projection
from hrs.library.errors import ServiceError


def test_source_view_keeps_canonical_fields_fresh_and_does_not_share_reader_containers(monkeypatch):
    chapter, book, run = (str(uuid4()) for _ in range(3))
    text = "# 标题\n\n正文[^a]。\n\n[^a]: 不包括乙地。"

    def body(value):
        return {
            "parts": [{"span_id": "p", "start": 0, "text": value, "source": {"pages": [1]}}],
            "navigation": {"obsolete_navigation_audit": [{"old": "retained"}]},
        }

    bodies = {"first": body(text), "second": body("修订后的正文。")}
    row = {
        "id": chapter,
        "book_id": book,
        "run_id": run,
        "title": "章",
        "pages": [1],
        "content": {"sha256": "first", "byte_length": 100},
    }
    current = [row]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, _):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: current[0]))

    settings = SimpleNamespace(
        s3_endpoint="https://test.invalid",
        s3_bucket="source-view-test",
        s3_region="test",
        s3_access_key=SecretStr("test"),
        s3_secret_key=SecretStr("test"),
    )
    library = object.__new__(catalog.Library)
    library.settings, library.engine = settings, SimpleNamespace(connect=Connection)
    library.chapters = lambda _: [current[0]]
    monkeypatch.setattr(catalog, "get_run", lambda *_: {"id": run, "result": {}})
    monkeypatch.setattr(
        chapter_content,
        "read_json",
        lambda _, reference: deepcopy(bodies[json.loads(reference)["sha256"]]),
    )
    monkeypatch.setattr(navigation_projection, "book_navigation", lambda *_: {"entries": []})
    monkeypatch.setattr(
        navigation_projection,
        "chapter_source_structure",
        lambda *_: {"schema_version": 1, "blocks": [], "relations": []},
    )
    chapter_content.cached_content.cache_clear()
    try:
        source, reader = library.source_chapter(chapter), library.chapter(chapter)
        fields = (
            "text",
            "text_sha256",
            "parts",
            "structure",
            "footnotes",
            "navigation",
            "source_structure",
        )
        assert all(source[field] == reader[field] for field in fields)
        assert source["footnotes"] and reader["reading_footnotes"]
        assert {key for key in reader if key.startswith("reading_")} == {
            "reading_segments",
            "reading_text",
            "reading_parts",
            "reading_footnotes",
        }
        assert not any(key.startswith("reading_") for key in source)
        assert "toc_links" not in source and reader["toc_links"] == []
        source["parts"][0]["source"]["pages"].append(99)
        source["footnotes"][0]["label"] = "caller edit"
        source["navigation"]["entries"].append({"caller": "edit"})
        assert library.chapter(chapter)["parts"][0]["source"]["pages"] == [1]
        assert (
            library.source_chapter(chapter)["footnotes"][0]["label"]
            == reader["footnotes"][0]["label"]
        )
        assert library.source_chapter(chapter)["navigation"] == {"entries": []}
        current[0] = {**row, "content": {"sha256": "second", "byte_length": 100}, "title": "新标题"}
        assert library.source_chapter(chapter)["text"] == "修订后的正文。"
        assert library.source_chapter(chapter)["title"] == "新标题"
        current[0] = None
        for read in (library.chapter, library.source_chapter):
            with pytest.raises(ServiceError) as error:
                read(chapter)
            assert error.value.status_code == 404
    finally:
        chapter_content.cached_content.cache_clear()


def test_toc_links_reuses_source_and_checks_current_targets_each_time():
    text = "目录：第一章 8"
    source = {
        "run_id": "run",
        "pages": [1],
        "text": text,
        "parts": [{"text": text, "source": {"pages": [1]}}],
        "navigation": {
            "entries": [
                {
                    "id": "first",
                    "title": "第一章",
                    "level": 1,
                    "source_page": 1,
                    "source_anchor": "第一章 8",
                }
            ]
        },
    }
    original = deepcopy(source)
    library = object.__new__(catalog.Library)
    library.chapter = library.source_chapter = lambda _: pytest.fail("must reuse validated source")
    calls, current = [], [{"first": {"chapter_id": "current"}}]

    def targets(run_id, navigation):
        calls.append((run_id, navigation))
        return current[0]

    library.navigation_targets = targets
    links = library.toc_links(source)
    assert links[0]["target"] == {"chapter_id": "current"}
    assert text[links[0]["source_start"] : links[0]["source_end"]] == "第一章 8"
    current[0] = {}  # A changed SQL locator must invalidate an otherwise identical source link.
    assert library.toc_links(source) == []
    assert len(calls) == 2 and all(call == ("run", source["navigation"]) for call in calls)
    assert source == original
    assert library.toc_links({**source, "pages": [2]}) == []
    assert len(calls) == 2
