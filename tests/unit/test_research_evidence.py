"""Source coordinates and retrieval identity remain explicit across adapters."""

import copy
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from hrs.library.errors import Problem, ServiceError
from hrs.library.footnotes import resolve_footnotes
from hrs.library.records import text_digest
from hrs.research import sources
from hrs.research.api import Research, evidence_markdown
from hrs.research.projection import located_card
from hrs.research.sources import retrieval_preview


def test_located_card_preserves_original_unicode_source_coordinates():
    text = "# 文章标题\n\n人物甲提出意见。[^1]\n\n[^1]: 注释。\n"
    source = {
        "pages": [7],
        "page": 7,
        "original_start": 120,
        "original_end": 120 + len(text),
        "image_sha256": "a" * 64,
    }
    snapshot = {
        "source_run_id": "source-run",
        "book_title": "资料",
        "articles": [
            {
                "id": "article",
                "text": text,
                "text_sha256": text_digest(text),
                "parts": [{"start": 0, "end": len(text), "text": text, "source": source}],
            }
        ],
    }
    start, end = text.index("人物甲"), text.index("。") + 1
    card = {
        "id": "card",
        "evidence": [
            {
                "id": "e1",
                "article_id": "article",
                "start": start,
                "end": end,
                "quote": text[start:end],
            }
        ],
        "mentions": [{"id": "m1"}],
    }
    located = located_card(card, snapshot)
    target = located["evidence"][0]["reading_target"]
    assert target["start"] == start and target["end"] == end
    assert target["physical_page"] == 7 and target["text_sha256"] == text_digest(text)
    assert located["mentions"][0]["entity_id"]
    assert "reading_target" not in card["evidence"][0]
    assert source["original_start"] == 120


def test_research_search_calls_shared_backend_and_never_silently_falls_back(monkeypatch):
    calls = []

    class Retrieval:
        def __init__(self, settings, engine):
            self.client = SimpleNamespace(indices=SimpleNamespace(exists=lambda **kwargs: True))

        def search(self, query, book_id, **options):
            calls.append((query, book_id, options))
            options["metrics"]["evidence_status"] = "candidates"
            return [{"id": "source-hit"}]

    monkeypatch.setattr(sources, "Search", Retrieval)
    monkeypatch.setattr(
        sources.ResearchEvidence, "article_scope", lambda self, book: {"local-chapter"}
    )
    adapter = sources.ResearchEvidence(SimpleNamespace(opensearch_index="index"), None)
    result = adapter.search("人物甲", chapter_ids=["local-chapter", "foreign-chapter"])
    assert result["backend"] == "hybrid" and result["available"]
    assert result["hits"] == [{"id": "source-hit"}]
    assert calls[0][2]["chapter_ids"] == ["local-chapter"]
    assert calls[0][2]["semantic"] is True

    def unavailable(*args, **kwargs):
        raise ImportError("model dependency unavailable")

    monkeypatch.setattr(Retrieval, "search", unavailable)
    with pytest.raises(Problem) as error:
        adapter.search("人物甲")
    assert error.value.status == 503
    assert error.value.code == "research_retrieval_unavailable"


def test_source_read_returns_only_requested_text_and_bounds_parts(monkeypatch):
    text = "前文。人物甲。后文。"
    chapter = {
        "id": "article",
        "book_id": "book",
        "run_id": "run",
        "text": text,
        "text_sha256": text_digest(text),
        "parts": [{"text": text, "source": {"pages": [2]}}],
    }
    monkeypatch.setattr(
        sources, "Library", lambda *args: SimpleNamespace(chapter=lambda _: chapter)
    )
    monkeypatch.setattr(sources, "get_run", lambda *args: {"result": {}})
    monkeypatch.setattr(sources.ResearchEvidence, "require_workspace", lambda *args: None)
    result = sources.ResearchEvidence(None, None).read_source("article", 3, 6)
    assert result["text"] == text[3:6]
    assert result["parts"] == [{"start": 3, "end": 6, "source": {"pages": [2]}}]
    with pytest.raises(ServiceError):
        sources.ResearchEvidence(None, None).read_source("article", 0, len(text), max_chars=2)


def test_external_evidence_keeps_external_reader_identity():
    text = "# 外书篇名\n\n外书人物提出意见。\n"
    article = {
        "id": "source-frozen",
        "original_article_id": "external-chapter",
        "source_run_id": "external-run",
        "text": text,
        "text_sha256": text_digest(text),
        "parts": [
            {
                "start": 0,
                "end": len(text),
                "text": text,
                "source": {"pages": [12], "image_sha256": "e" * 64},
            }
        ],
    }
    start, end = text.index("外书人物"), len(text) - 1
    card = {
        "id": "card",
        "evidence": [
            {
                "id": "external-e1",
                "article_id": "source-frozen",
                "start": start,
                "end": end,
                "quote": text[start:end],
                "book_id": "external-book",
                "source_run_id": "external-run",
                "sources": [{"text": text}],
            }
        ],
    }
    result = located_card(card, {"source_run_id": "main-run", "articles": []}, [article])
    span = result["evidence"][0]
    assert span["reading_target"]["chapter_id"] == "external-chapter"
    assert span["reading_target"]["run_id"] == "external-run"
    assert span["reading_target"]["physical_page"] == 12
    assert span["reading_target"]["text_sha256"] == text_digest(text)
    assert "text" not in span["sources"][0]
    service = object.__new__(Research)
    result.update(
        title="外书引用",
        summary="提要",
        book_id="main-book",
        run_id="research-run",
        source_run_id="main-run",
        items=[],
    )
    service.card = lambda _: result
    service.workflow = SimpleNamespace(
        snapshot=lambda _: {"articles": []}, sources=lambda _: [article]
    )
    exported = service.export("card", base_url="http://localhost:8123")
    href = re.search(r"\[阅读原文\]\(([^)]+)\)", exported)[1]
    location = urlsplit(href)
    assert location.netloc == "localhost:8123"
    path, _, query = location.fragment.partition("?")
    assert path == "/books/external-book"
    assert parse_qs(query) == {
        "tab": ["reader"],
        "section": ["text"],
        "chapter": ["external-chapter"],
        "physicalPage": ["12"],
        "start": [str(start)],
        "end": [str(end)],
        "textSha": [text_digest(text)],
        "run": ["external-run"],
    }


def test_research_export_namespaces_source_owned_footnote_links():
    text = "甲[^1]提出主张。\n\n[^1]: 编者原注。\n"
    parts = [{"start": 0, "end": len(text), "text": text, "source": {"pages": [5]}}]
    note = resolve_footnotes(text, parts)[0]
    stop = text.index("\n")
    span = {"start": 0, "end": stop, "quote": text[:stop], "footnotes": [note]}
    exported = evidence_markdown(span, {"text": text, "parts": parts}, "research-e1")
    assert "甲[^research-e1-1]提出主张。" in exported
    assert "[^research-e1-1]: 编者原注。" in exported


def test_research_preview_bounds_serialized_transport_without_mutating_full_audit_result():
    text = "原文" * 1200 + "检索词" + "后文" * 1200
    result = {
        "query": "检索词",
        "backend": "hybrid",
        "available": True,
        "evidence_status": "candidates",
        "hits": [
            {
                "id": str(i),
                "book_id": "book",
                "chapter_id": f"chapter-{i}",
                "run_id": "run",
                "title": "篇名" * 80,
                "book_title": "书名",
                "start": 100,
                "end": 100 + len(text),
                "pages": [3],
                "score": 1.0 - i / 100,
                "text": text,
                "retrieval_text": text * 3,
                "reading_target": {"anchor": text, "text_sha256": "a" * 64},
                "sources": [{"text": text}],
                "context": [{"role": "footnote", "text": text}],
                "table_scopes": [{"value": text}],
            }
            for i in range(20)
        ],
    }
    retained = copy.deepcopy(result)
    preview = retrieval_preview(result)
    assert result == retained
    assert len(json.dumps(preview, ensure_ascii=False, indent=2)) < 6000
    assert 1 <= len(preview["hits"]) <= 8
    assert preview["returned_hits"] + preview["omitted_hits"] == 20
    assert preview["preview_only"] is True and preview["truncated"] is True
    assert "research_read_evidence" in preview["read_evidence"]
    for index, hit in enumerate(preview["hits"]):
        assert hit["id"] == str(index) and hit["rank"] == index + 1
        assert hit["text_sha256"] == "a" * 64
        assert hit["snippet_sha256"] == text_digest(text)
        assert hit["preview_sha256"] == text_digest(hit["preview"])
        assert hit["preview_length"] == len(hit["preview"]) <= 400
        assert "检索词" in hit["preview"]
        assert text[hit["preview_start"] - 100 : hit["preview_end"] - 100] == hit["preview"]
        assert hit["context_roles"] == ["footnote"] and hit["context_omitted"] is True
        assert hit["has_shared_cells"] is True and hit["metadata_truncated"] is True
        assert not {"retrieval_text", "context", "sources", "reading_target"}.intersection(hit)


def test_research_preview_preserves_empty_or_failed_search_status():
    result = {
        "query": "query",
        "backend": "hybrid",
        "available": False,
        "evidence_status": "no_published_sources",
        "hits": [],
    }
    preview = retrieval_preview(result)
    assert preview["available"] is False and preview["evidence_status"] == "no_published_sources"
    assert preview["hits"] == [] and preview["truncated"] is False
