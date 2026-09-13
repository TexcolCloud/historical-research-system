from types import SimpleNamespace
from uuid import uuid4

import pytest

from hrs_platform.retrieval_chunks import expand_hits, retrieval_chunks
from hrs_platform.retrieval_ranking import diverse_order, fuse, lexical_query
from hrs_platform.search import Search


def chapter(text):
    return {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "run_id": str(uuid4()),
        "title": "运输",
        "text": text,
        "parts": [{"span_id": "s", "start": 0, "text": text, "source": {"pages": [1]}}],
    }


def test_rrf_rewards_agreement_without_comparing_incompatible_scores():
    def row(i, score):
        return {"_id": i, "_score": score, "_source": {"id": i, "text": i}}

    result = fuse([row("a", 100), row("b", 90)], [row("b", 0.9), row("c", 0.8)])
    assert [h["id"] for h in result] == ["b", "a", "c"]
    query = lexical_query("“第110师团” 1940", [{"term": {"book_id": "b"}}])
    assert query["bool"]["filter"] == [{"term": {"book_id": "b"}}]
    assert any(
        c.get("match_phrase", {}).get("text_search", {}).get("query") == "第110师团"
        for c in query["bool"]["should"]
    )


def test_essential_note_displaces_a_lower_ranked_hit_instead_of_being_dropped():
    source = chapter("登记120吨[^a]。\n\n[^a]: 不包括乙地。\n")
    other = chapter("其他运输内容。")
    first = next(retrieval_chunks(source, "书"))
    second = next(retrieval_chunks(other, "书"))
    load = {c["id"]: c for c in [source, other]}.__getitem__
    budget = len(first["text"]) + sum(len(c["text"]) for c in first["context"])
    hits = expand_hits([{**first, "score": 2}, {**second, "score": 1}], load, total_chars=budget)
    assert len(hits) == 1
    assert any("不包括乙地" in c["text"] for c in hits[0]["context"])
    assert not hits[0]["context_truncated"]


def test_diversity_is_opt_in_and_preserves_all_candidates():
    hits = [{"chapter_id": chapter, "score": score} for chapter, score in [("a", 5), ("a", 4), ("b", 3)]]
    assert [h["score"] for h in diverse_order(hits)] == [5, 3, 4]


def test_hybrid_retrieval_widens_candidates_but_bounds_reranking(monkeypatch):
    from hrs_platform import search as module

    source = chapter("运输记录。")
    chunk = next(retrieval_chunks(source, "书"))
    rows = [{"_id": str(i), "_score": 1, "_source": {**chunk, "id": str(uuid4())}} for i in range(50)]
    calls = []
    search = object.__new__(Search)
    search.settings = SimpleNamespace(
        opensearch_index="test",
        project_root=module.Path("."),
        retrieval_device="cpu",
        retrieval_endpoint="unused",
    )
    search.client = SimpleNamespace(
        indices=SimpleNamespace(exists=lambda **_: True),
        search=lambda **kw: calls.append(kw["body"]) or {"hits": {"hits": rows}},
    )
    search.library = SimpleNamespace(chapter=lambda _: source)
    search.active_scope = lambda _: []
    search.tokenizer = lambda _: None
    monkeypatch.setattr(module, "query_vector", lambda *_: [0] * 1024)
    monkeypatch.setattr(module, "ranking_windows", lambda q, texts, t: (texts, list(range(len(texts)))))
    sizes = []
    search.compute = lambda op, texts, **kw: sizes.append(len(texts)) or {"scores": list(range(len(texts)))}
    result = search.search("运输", rerank_limit=12)
    assert sizes == [12]
    assert all(c["size"] == 50 for c in calls)
    assert result


def test_offline_unreviewed_sample_is_rejected_before_storage_or_inference():
    from hrs_platform.retrieval_offline import evaluate_offline

    source = {**chapter("未核对正文"), "development_review": {}}
    with pytest.raises(ValueError, match="original-first"):
        evaluate_offline(None, None, {"chapters": [source]})


def test_embedding_windows_reserve_linked_footnote_budget():
    from hrs_platform.retrieval_inputs import bounded_chunks

    source = chapter("运输登记。" * 50 + "[^a]\n\n[^a]: 不包括乙地。\n")
    tokenizer = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=list(text)))
    hits = list(bounded_chunks(source, retrieval_chunks(source, "书"), tokenizer, limit=100))
    with_notes = [h for h in hits if any(c["role"] == "footnote" for c in h["context"])]
    assert len(with_notes) > 1
    assert all("不包括乙地" in h["retrieval_text"] for h in with_notes)
    assert all(len(h["retrieval_text"]) <= 100 for h in hits)
