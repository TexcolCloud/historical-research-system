"""Research projections keep source search and derived discovery separate."""

from types import SimpleNamespace

import pytest

from hrs.library.errors import ServiceError
from hrs.research import sources


def test_selected_books_share_one_authorized_hybrid_search(monkeypatch):
    scopes, calls = [], []

    def article_scope(self, book=None, *, book_ids=None):
        scopes.append((book, book_ids))
        return {"chapter-a", "chapter-b"}

    class Retrieval:
        def __init__(self, *args):
            self.client = SimpleNamespace(indices=SimpleNamespace(exists=lambda **kw: True))

        def search(self, query, book_id, **options):
            calls.append((query, book_id, options))
            return []

    monkeypatch.setattr(sources.ResearchEvidence, "article_scope", article_scope)
    monkeypatch.setattr(sources, "Search", Retrieval)
    adapter = sources.ResearchEvidence(SimpleNamespace(opensearch_index="source"), None)
    adapter.search("shared event", book_ids=["book-a", "book-b"])
    assert scopes == [(None, ["book-a", "book-b"])]
    assert len(calls) == 1
    assert calls[0][2]["book_ids"] == ["book-a", "book-b"]
    assert calls[0][2]["chapter_ids"] == ["chapter-a", "chapter-b"]


def test_server_frozen_corpus_scope_can_exceed_external_selection_limit(monkeypatch):
    selected = [f"book-{number}" for number in range(250)]
    scopes = []
    monkeypatch.setattr(
        sources.ResearchEvidence,
        "article_scope",
        lambda self, book=None, *, book_ids=None: scopes.append(book_ids) or set(),
    )
    adapter = sources.ResearchEvidence(SimpleNamespace(), None)
    with pytest.raises(ServiceError, match="多书检索范围无效"):
        adapter.search("query", book_ids=selected)
    assert adapter.search("query", book_ids=selected, trusted_scope=True)["hits"] == []
    assert scopes == [selected]


def test_discovery_chunks_are_derived_records_not_source_evidence():
    from hrs.research.discovery import discovery_records

    cards = [
        {
            "id": "card",
            "state": "verified",
            "book_id": "a",
            "run_id": "r",
            "version": 2,
            "title": "A title",
            "summary": "An interpretation " * 200,
            "items": [{"title": "Claim", "text": "A supported qualification"}],
            "evidence": [{"quote": "literal source quote"}],
            "tags": ["letters"],
        },
        {"id": "pending", "state": "pending", "title": "Do not index"},
    ]
    rows = discovery_records(cards, [], workspace_id="w", generation="g")
    assert len(rows) > 1
    assert all(r["record_id"] == "card" and r["record_kind"] == "card" for r in rows)
    assert all(r["derived"] is True and r["source_evidence"] is False for r in rows)
    assert all(len(r["text"]) <= 1400 for r in rows)
    assert "literal source quote" not in "".join(r["text"] for r in rows)


def test_task_preview_exposes_bounded_real_counterpart_ids():
    import json

    cards = [
        {
            "id": f"card-{index}",
            "version": index + 1,
            "title": "可追踪目标" * 12,
            "publication_sha256": "a" * 64,
        }
        for index in range(5)
    ]
    preview = sources.retrieval_preview(
        {
            "query": "目标",
            "available": True,
            "backend": "hybrid",
            "hits": [
                {
                    "id": "hit",
                    "chapter_id": "chapter",
                    "text": "原文",
                    "research_cards": cards,
                    "cards_omitted": 3,
                }
            ],
        }
    )
    assert [card["id"] for card in preview["hits"][0]["research_cards"]] == ["card-0", "card-1"]
    assert preview["hits"][0]["cards_omitted"] == 6
    assert len(json.dumps(preview, ensure_ascii=False, indent=2)) < 5800
