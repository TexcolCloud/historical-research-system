"""Real SQL/S3/OpenSearch publication; deterministic vectors isolate indexing from models."""
from hrs_platform.services.retrieval.indexing import BookIndexer

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from hrs_platform.domain.errors import TaskError
from test_card_evidence import invoke
from tokenizers import Tokenizer, models, pre_tokenizers

from hrs_platform import models as db
from hrs_platform.services.retrieval import search as module
from hrs_platform.services.retrieval import indexing
from hrs_platform.main import create_app
from hrs_platform.services.books import get_run
from hrs_platform.services.cards.evidence import CardEvidence
from hrs_platform.services.cards import Cards
from hrs_platform.domain.card_rules import CardTopic
from hrs_platform.services.cards.reading import reading_units
from hrs_platform.services.retrieval.evaluation import evaluate
from hrs_platform.services.retrieval.search import Search


def test_versioned_index_only_exposes_complete_generation_and_recovers_cached_vectors(platform, monkeypatch):
    settings, engine = platform
    settings = settings.model_copy(update={"opensearch_index": "test-structure-" + uuid4().hex})
    search = Search(settings, engine)
    book, run, chapter = [str(uuid4()) for _ in range(3)]
    original = "# 合成运输\n\n" + "甲地运入120吨粮食，不包括乙地。" * 150
    body = {"parts": [{"span_id": "synthetic", "start": 0, "text": original, "source": {"pages": [3, 4]}}]}
    ref = search.outputs.objects.put_bytes(json.dumps(body, ensure_ascii=False).encode())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="独立运输样本", state="ready"))
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
                title="运输",
                kind="article",
                content=ref,
                pages=[3, 4],
                codepoints=len(original),
            )
        )
    embeddings = []

    def compute(operation, texts, **options):
        if operation == "embed":
            embeddings.append(texts)
            return {"vectors": [[1.0] + [0.0] * 1023 for _ in texts], "identity": {"fixture": True}}
        assert all("独立运输样本" in text and "运输" in text for text in texts)
        return {"scores": [1.0] * len(texts)}

    # The API constructs its own Search instance; isolate those calls as well.
    monkeypatch.setattr(Search, "compute", lambda self, operation, texts, **options: compute(operation, texts, **options))
    monkeypatch.setattr(module, "query_vector", lambda *_: [1.0] + [0.0] * 1023)
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    monkeypatch.setattr(Search, "tokenizer", lambda self, model: tokenizer)
    bulk = indexing.helpers.bulk
    try:
        with engine.begin() as connection:
            connection.execute(update(db.runs).where(db.runs.c.id == run).values(result={}))
        with pytest.raises(ValueError, match="published book"):
            BookIndexer(search).index(run)
        assert embeddings == []
        with engine.begin() as connection:
            connection.execute(update(db.runs).where(db.runs.c.id == run).values(result={"published": True}))
        search.outputs.put(run, "retrieval-chunks", {"chunks": ["obsolete"]}, {"legacy": True})
        first = BookIndexer(search).index(run)
        assert all(text.startswith("独立运输样本\n") for text in embeddings[0])
        assert len(embeddings) == 1
        assert BookIndexer(search).index(run) == first
        assert len(embeddings) == 1
        hits = search.search("糧食", book, context_chars=6000)
        assert hits and all(h["generation"] == first["generation"] for h in hits)
        assert search.search("粮食", str(uuid4())) == []
        assert search.search("粮食", book, semantic=True)
        assert search.search("粮食", book, chapter_ids=[chapter])
        assert search.search("粮食", book, chapter_ids=[str(uuid4())]) == []
        child = Cards(settings, engine).create_run(run)["run_id"]
        units = reading_units(search.library.chapter(chapter))
        snapshot = {"chapters": [{"id": chapter}], "units": units}
        evidence_tools = CardEvidence(settings, engine, search.outputs, search.library)
        wrong_provenance = deepcopy(snapshot)
        wrong_provenance["units"][0]["sources"][0]["pages"] = [99]
        with pytest.raises(TaskError) as wrong_pages:
            evidence_tools.pin(get_run(engine, child), wrong_provenance)
        assert wrong_pages.value.type == "card_corpus_changed"
        corpus = evidence_tools.pin(get_run(engine, child), snapshot)
        topic = CardTopic(name="运输", objective="核对粮食运输范围", unit_ids=[u["unit_id"] for u in units])
        completed_reads = []

        async def agent(run_id, key, instructions, payload, output_type, **kwargs):
            search_tool, read_tool = kwargs["tools"]
            receipts = await invoke(search_tool, queries=[
                {"query": "粮食运输", "purpose": "support"},
                {"query": "不包括乙地", "purpose": "counter"},
                {"query": "运输范围", "purpose": "qualify"},
            ])
            assert all(row["hits"] for row in receipts)
            ids = list({uid for row in receipts for hit in row["hits"] for uid in hit["unit_ids"]})
            read = await invoke(read_tool, unit_ids=ids)
            assert "".join(u["text"] for u in read["units"] if u["unit_id"] in topic.unit_ids) == original
            completed_reads.append(read)
            if len(completed_reads) == 1:
                raise OSError("after persisted evidence")
            value = output_type(source_unit_ids=ids, findings=[dict(category="limitation", text="仅甲地，不包括乙地", source_unit_ids=ids)],
                                unresolved_questions=[], sufficient=True)
            kwargs["validate"](value)
            return value

        def research(tools):
            return asyncio.run(tools.research(SimpleNamespace(run=agent), child, "integration", corpus,
                                              topic, [], units, None))

        with pytest.raises(OSError, match="persisted evidence"):
            research(evidence_tools)
        # Fresh instance and real SQL/S3 receipts resume without issuing new search requests.
        restored = CardEvidence(settings, engine, search.outputs, search.library)
        restored.search = SimpleNamespace(search=lambda *a, **k: pytest.fail("Query repeated"))
        receipt = research(restored)
        assert receipt["assessment"]["sufficient"] and completed_reads[0] == completed_reads[1]
        evidence = original.index("120吨")
        report = evaluate(
            search,
            run,
            [
                {
                    "query": "糧食",
                    "expected": [{"chapter_id": chapter, "start": evidence, "end": evidence + 4}],
                },
                {
                    "query": "zxqvuniquemissingterm",
                    "expected": [{"chapter_id": chapter, "start": evidence, "end": evidence + 4}],
                },
            ],
        )
        assert report["evidence_recall"] == 0.5
        assert report["cases"][0]["source_integrity"] == 1
        assert report["cases"][1]["returned"] == 0
        assert report["cases_sha256"] and report["p95_ms"] >= report["p50_ms"]
        with TestClient(create_app(settings, engine)) as client:
            response = client.get("/api/v2/search", params={"q": "粮食", "book_id": book, "semantic": False})
            assert response.status_code == 200 and response.json()
            assert "lexical;dur=" in response.headers["Server-Timing"]
            assert "total;dur=" in response.headers["Server-Timing"]
        count_before_rebuild = len(embeddings)
        # Calibrated rejection is scoped to this exact book generation and semantic mode.
        with engine.begin() as connection:
            current = connection.scalar(select(db.runs.c.result).where(db.runs.c.id == run))
            connection.execute(update(db.runs).where(db.runs.c.id == run).values(result={**current,
                'retrieval_policy':{'rule':module.EVIDENCE_RULE,'generation':first['generation'],
                    'reranker_revision':module.MODEL_REVISIONS['BAAI/bge-reranker-v2-m3'],
                    'min_top_score':2,'candidate_limit':30,'rerank_limit':20}}))
        with TestClient(create_app(settings, engine)) as client:
            response = client.get('/api/v2/search', params={'q':'粮食','book_id':book})
            assert response.json() == [] and response.headers['X-Retrieval-Evidence'] == 'low_relevance'
        assert search.search('粮食',book,semantic=False)
        assert search.search('粮食',book,use_calibration=False)
        assert search.search('粮食')  # A global search must not inherit a book threshold.
        monkeypatch.setattr(indexing, "CHUNK_RULE", indexing.CHUNK_RULE + "-test-new-version")

        def partial(client, actions, **kwargs):
            bulk(client, [next(iter(actions))], **kwargs)
            raise OSError("injected partial index failure")

        monkeypatch.setattr(indexing.helpers, "bulk", partial)
        with pytest.raises(OSError, match="partial index"):
            BookIndexer(search).index(run)
        with engine.connect() as connection:
            metrics = connection.scalar(select(db.runs.c.result).where(db.runs.c.id == run))[
                "retrieval_metrics"
            ]
            assert metrics["status"] == "failed" and metrics["total_ms"] > 0
        assert all(h["generation"] == first["generation"] for h in search.search("粮食", book, use_calibration=False))
        monkeypatch.setattr(indexing.helpers, "bulk", bulk)
        second = BookIndexer(search).index(run)
        assert second["generation"] != first["generation"]
        with pytest.raises(TaskError) as changed:
            evidence_tools.pin(get_run(engine, child), snapshot)
        assert changed.value.type == "card_corpus_changed"
        assert len(embeddings) == count_before_rebuild
        assert all(h["generation"] == second["generation"] for h in search.search("粮食", book))
        assert search.client.count(index=settings.opensearch_index)["count"] == second["chunks"]
        search.client.indices.delete(index=settings.opensearch_index)
        assert BookIndexer(search).index(run) == second
        assert len(embeddings) == count_before_rebuild
    finally:
        search.client.indices.delete(index=settings.opensearch_index, ignore=[404])
