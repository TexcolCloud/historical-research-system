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
    metrics = {}
    assert search.search('运输', rerank_limit=12, min_rerank_score=100, metrics=metrics) == []
    assert metrics['relevance_rejected'] == 12
    assert search.search('运输', rerank_limit=12, min_top_score=100, metrics=metrics) == []
    assert metrics['evidence_status'] == 'low_relevance'
    assert search.search('运输', rerank_limit=12, min_top_score=5, metrics=metrics)
    assert metrics['relevance_rejected'] == 0  # Query gate preserves lower-scoring supporting hits.
    search._policy = {'candidate_limit':30, 'rerank_limit':20, 'min_top_score':100}
    assert search.search('运输') == []
    assert search.search('运输', use_calibration=False)
    assert search.search('运输', semantic=False)  # Lexical search never inherits a model threshold.


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


def test_budget_rejection_refills_from_later_candidates():
    sources = [chapter('长' * 150), chapter('短证据甲。'), chapter('短证据乙。')]
    hits = [{**next(retrieval_chunks(c, '书')), 'score': 3-i} for i,c in enumerate(sources)]
    metrics = {}
    result = expand_hits(hits, {c['id']:c for c in sources}.__getitem__, limit=2,
                         context_chars=100, total_chars=100, metrics=metrics)
    assert [h['text'] for h in result] == ['短证据甲。', '短证据乙。']
    assert metrics['budget_rejected'] == 1


def test_diversity_does_not_promote_a_remote_weak_chapter():
    hits = [{'chapter_id':'a', 'score':i} for i in range(20, 0, -1)] + [{'chapter_id':'b', 'score':-100}]
    assert all(h['chapter_id'] == 'a' for h in diverse_order(hits, relevance_window=8)[:8])


def test_candidate_reservation_keeps_single_lane_evidence_and_budget():
    from hrs_platform.retrieval_ranking import candidates_for_rerank
    def row(i):
        return {'_id':i, '_source':{'id':i}}
    lanes = [[row('lexical'), row('shared1'), row('shared2')],
             [row('dense'), row('shared1'), row('shared2')]]
    result = candidates_for_rerank(lanes, 2, lane_quota=1)
    assert {r['id'] for r in result} == {'lexical', 'dense'}


def test_only_explicit_two_part_requests_are_split_without_rewriting_facts():
    from hrs_platform.retrieval_ranking import multipart_queries
    assert multipart_queries('请分别指出甲军调动的日期，以及乙地税率所据的月份。') == ['甲军调动的日期', '乙地税率所据的月份']
    assert multipart_queries('分别找出甲地部队的人数，以及乙地部队的番号？') == ['甲地部队的人数', '乙地部队的番号']
    for query in ['两支部队分别有多少人？', '请分别指出日期，以及人数。',
                  '请分别指出甲军调动的日期，以及乙地税率以及丙地税率。',
                  '请分别指出“甲军调动的日期，以及乙地税率所据的月份”。']:
        assert multipart_queries(query) == []


def test_two_part_search_retains_each_clause_and_respects_shared_budget():
    sources = [chapter('甲军三月调动。'), chapter('乙地税率按照四月材料。')]
    hits = [{**next(retrieval_chunks(c, '书')), 'score': 100-i*200} for i,c in enumerate(sources)]
    search = object.__new__(Search)
    search.library = SimpleNamespace(chapter={c['id']:c for c in sources}.__getitem__)
    calls = []
    def single(query, book_id, **options):
        calls.append((query, book_id, options))
        return [hits[len(calls)-1]]
    search.search = single
    metrics = {}
    result = search.search_parts(['甲军调动的日期', '乙地税率所据月份'], 'book', 2, 100, 100, metrics, None)
    assert {h['chapter_id'] for h in result} == {c['id'] for c in sources}
    assert all(book == 'book' and not options['decompose'] for _,book,options in calls)
    assert sum(len(h['text']) + sum(len(c['text']) for c in h['context']) for h in result) <= 100
    calls.clear()
    result = search.search_parts(['甲军调动的日期', '乙地税率所据月份'], 'book', 1, 100, 100, {}, None)
    assert len(result) == 1
    search.search = lambda query, *args, **kwargs: [hits[0]] if query == 'known' else []
    search.search_parts(['known', 'missing'], 'book', 2, 100, 100, metrics, None)
    assert metrics['evidence_status'] == 'partial_evidence'


def test_structured_table_windows_preserve_rowspans_and_long_notes_keep_owner():
    from hrs_platform.retrieval_inputs import bounded_chunks
    tokenizer = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=list(text)))
    text = '<table><tr><th>地</th><th>量</th></tr>' + ''.join(
        '<tr><td rowspan="2">甲</td><td>120</td></tr><tr><td>130</td></tr>' for _ in range(8)) + '</table>'
    source = chapter(text)
    rows = list(bounded_chunks(source, retrieval_chunks(source, '书'), tokenizer, limit=160))
    assert len(rows) > 1
    assert all(r['text'].count('<tr>') == r['text'].count('</tr>') for r in rows)
    assert all('rowspan' not in r['text'] or '<tr><td>130</td></tr>' in r['text'] for r in rows)
    assert all(len(r['retrieval_text']) <= 160 for r in rows)
    source = chapter('正文[^a]。\n\n[^a]: 第一段的说明。\n\n    第二段的说明。\n\n    第三段的说明。\n')
    notes = [r for r in bounded_chunks(source, retrieval_chunks(source, '书'), tokenizer, limit=40)
             if r['kind'] == 'note']
    assert notes and all(any(c['role'] == 'note_owner' for c in r['context']) for r in notes)


def test_no_answer_thresholds_are_diagnostics_not_probabilities():
    from hrs_platform.retrieval_evaluation import threshold_diagnostics
    result = threshold_diagnostics([
        {'unanswerable':False, 'timing':{'top_rerank_score':3}},
        {'unanswerable':True, 'timing':{'top_rerank_score':1}},
    ])
    assert result[-2] == {'threshold':3, 'answerable_retention':1, 'unanswerable_rejection':1}
    assert result[-1]['answerable_retention'] == 0 and result[-1]['unanswerable_rejection'] == 1
    assert threshold_diagnostics([{'unanswerable':False, 'timing':{}}]) == []


def test_year_boost_recognizes_year_next_to_chinese_characters():
    clauses = lexical_query('查询1939年运输', [])['bool']['should']
    assert any(c.get('match_phrase', {}).get('text_search', {}).get('query') == '1939' for c in clauses)


def test_synthetic_benchmark_is_reproducible_and_never_self_approved():
    import runpy
    from pathlib import Path
    build = runpy.run_path(str(Path(__file__).resolve().parents[3] / 'scripts/build_retrieval_benchmark.py'))['build']
    data = build()
    assert data == build()
    assert len(data['cases']) == 100 and len(data['chapters']) == 40
    assert sum(not c['expected'] for c in data['cases']) == 20
    assert all(c['development_review'] == {} for c in data['chapters'])
    chapters = {c['id']:c for c in data['chapters']}
    for case in data['cases']:
        for part in case['expected']:
            assert 0 <= part['start'] < part['end'] <= len(chapters[part['chapter_id']]['text'])
