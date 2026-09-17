"""Independent synthetic input-budget and restart checks; no model downloads."""
from hrs_platform.services.retrieval.indexing import BookIndexer

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select

from hrs_platform import models as db
from hrs_platform.services.retrieval import indexing as module
from hrs_platform.services.retrieval.chunks import retrieval_chunks
from hrs_platform.services.retrieval.inputs import bounded_chunks
from hrs_platform.services.retrieval.inputs import ranking_windows
from hrs_platform.services.retrieval.inputs import windows
from hrs_platform.services.retrieval.search import Search


class CharacterTokens:
    def encode(self, text, pair=None):
        return SimpleNamespace(ids=list(range(len(text) + len(pair or "") + (4 if pair is not None else 2))))


def test_index_chunks_keep_exact_cross_page_sources_after_input_bounding():
    parts = [
        {"span_id": "a", "start": 13, "text": "合成第一页正文。" * 20, "source": {"pages": [8]}},
        {"span_id": "b", "start": 20, "text": "合成第二页正文。" * 20, "source": {"pages": [9]}},
    ]
    chapter = {
        **{key: str(uuid4()) for key in ("id", "book_id", "run_id")},
        "title": "合成章", "text": "".join(part["text"] for part in parts), "parts": parts,
    }
    chunks = list(bounded_chunks(chapter, retrieval_chunks(chapter, "合成书"), CharacterTokens(), limit=80))
    assert len(chunks) > 1
    assert {page for row in chunks for page in row["pages"]} == {8, 9}
    assert {i for row in chunks for i in range(row["start"], row["end"])} == set(range(len(chapter["text"])))
    originals = {part["span_id"]: part for part in parts}
    for row in chunks:
        assert row["text"] == chapter["text"][row["start"]:row["end"]]
        for source in row["sources"]:
            part = originals[source["span_id"]]
            assert row["text"][source["unit_start"]:source["unit_end"]] == part["text"][
                source["start"] - part["start"]:source["end"] - part["start"]
            ]


def test_bounded_projection_does_not_reintroduce_image_paths_from_supplements():
    text = '正文[^a]。' * 40 + '\n\n[^a]: 图见![部署图](C:/cache/private-image.png)。'
    chapter = {**{k: str(uuid4()) for k in ('id', 'book_id', 'run_id')}, 'title': '书',
        'text': text, 'parts': [{'span_id': 'p', 'start': 0, 'text': text, 'source': {'pages': [1]}}]}
    chunks = list(bounded_chunks(chapter, retrieval_chunks(chapter, '书'), CharacterTokens(), limit=120))
    assert len(chunks) > 2
    assert all('private-image' not in c['retrieval_text'] for c in chunks)
    assert all(len(CharacterTokens().encode(c['retrieval_text']).ids) <= 120 for c in chunks)
    assert any('private-image' in x['text'] for c in chunks for x in [c, *c['context']])


def test_long_table_input_has_complete_source_coverage_and_bounded_projection():
    text = "<table><tr><th>数量</th></tr><tr><td>" + "甲地120吨，不含乙地。" * 100 + "</td></tr></table>"
    chapter = {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "run_id": str(uuid4()),
        "title": "合成表格",
        "text": text,
        "parts": [{"span_id": "p1", "start": 7, "text": text, "source": {"pages": [3]}}],
    }
    original = list(retrieval_chunks(chapter, "合成书籍"))
    assert len(original) == 1
    chunks = list(bounded_chunks(chapter, original, CharacterTokens(), limit=120))
    assert len(chunks) > 1
    assert all(row['text'] == text[row['start']:row['end']] for row in chunks)
    assert min(row['start'] for row in chunks) == 0 and max(row['end'] for row in chunks) == len(text)
    assert all(row['text'].count('<tr>') == row['text'].count('</tr>') for row in chunks)
    assert len({row['id'] for row in chunks}) == len(chunks)
    assert all(UUID(row['id']) for row in chunks)
    assert any(row['atomic_source_oversized'] for row in chunks)
    assert all(len(CharacterTokens().encode(row["retrieval_text"]).ids) <= 120 for row in chunks)
    assert all(row["sources"][0]["start"] == 7 + row["start"] and row["pages"] == [3] for row in chunks)


def test_oversized_supplement_stays_available_without_overflowing_embedding():
    text = "正文[^a]。\n\n[^a]: " + "不包括其他地区。" * 100
    chapter = {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "run_id": str(uuid4()),
        "title": "注释",
        "text": text,
        "parts": [{"span_id": "p", "start": 0, "text": text, "source": {"pages": [1]}}],
    }
    chunks = list(bounded_chunks(chapter, retrieval_chunks(chapter, "书"), CharacterTokens(), limit=100))
    body = chunks[0]
    assert body["projection_context_omitted"]
    assert body["context"][0]["text"] == text[text.index("[^a]:") :]
    assert all(len(CharacterTokens().encode(row["retrieval_text"]).ids) <= 100 for row in chunks)


def test_rerank_accounts_for_query_and_retains_every_document_character():
    texts = ["甲地运输。" * 80, "乙地驻留。"]
    projected, owners = ranking_windows("运输数量", texts, CharacterTokens(), limit=80)
    assert all(len(CharacterTokens().encode("运输数量", value).ids) <= 80 for value in projected)
    for owner, text in enumerate(texts):
        assert "".join(value for value, index in zip(projected, owners) if index == owner) == text
    with pytest.raises(ValueError, match="query"):
        ranking_windows("甲" * 80, texts, CharacterTokens(), limit=80)
    assert list(windows("𠮷地\r\n运输。", len, 4)) == [(0, 4), (4, 7)]


def test_failed_embedding_resumes_completed_batches_and_only_recomputes_changed_text(platform, monkeypatch):
    settings, engine = platform
    search = Search(settings, engine)
    search.tokenizer = lambda _: CharacterTokens()
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="向量恢复技术样本", state="ready"))
        connection.execute(
            insert(db.runs).values(
                id=run, book_id=book, state="ready", stage="indexing", result={"published": True}
            )
        )
    monkeypatch.setattr(module, "EMBEDDING_BATCH", 2)
    calls = []
    fail = True

    def compute(operation, texts):
        calls.append(list(texts))
        if fail and len(calls) == 2:
            raise OSError("injected embedding failure")
        return {"vectors": [[1.0] + [0.0] * 1023 for _ in texts], "identity": {"fixture": True}}

    monkeypatch.setattr(search, "compute", compute)
    chunks = [{"retrieval_text": f"合成内容 {i}"} for i in range(5)]
    with pytest.raises(OSError, match="embedding failure"):
        BookIndexer(search).embed_cached(run, chunks, {})
    with engine.connect() as connection:
        assert (
            len(
                connection.scalars(
                    select(db.stage_outputs.c.step).where(db.stage_outputs.c.run_id == run)
                ).all()
            )
            == 1
        )
    fail = False
    calls.clear()
    metrics = {}
    result = BookIndexer(search).embed_cached(run, chunks, metrics)
    assert len(result["chunks"]) == 5
    assert [text for batch in calls for text in batch] == [row["retrieval_text"] for row in chunks[2:]]
    assert metrics["reused_vectors"] == 2 and metrics["computed_vectors"] == 3
    calls.clear()
    changed = [{**row, "retrieval_text": "仅这一段修改"} if i == 2 else row for i, row in enumerate(chunks)]
    BookIndexer(search).embed_cached(run, changed, {})
    assert calls == [["仅这一段修改"]]
    calls.clear()
    monkeypatch.setattr(
        module, "EMBEDDING_IDENTITY", {**module.EMBEDDING_IDENTITY, "adapter": "test-new-model"}
    )
    BookIndexer(search).embed_cached(run, changed, {})
    assert len([text for batch in calls for text in batch]) == 5
