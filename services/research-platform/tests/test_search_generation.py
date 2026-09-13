"""Real SQL/S3/OpenSearch publication; deterministic vectors isolate indexing from models."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from tokenizers import Tokenizer, models, pre_tokenizers

from hrs_platform import schema as db
from hrs_platform import search as module
from hrs_platform.api import create_app
from hrs_platform.retrieval_evaluation import evaluate
from hrs_platform.search import Search


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

    monkeypatch.setattr(search, "compute", compute)
    monkeypatch.setattr(module, "query_vector", lambda *_: [1.0] + [0.0] * 1023)
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    monkeypatch.setattr(search, "tokenizer", lambda _: tokenizer)
    bulk = module.helpers.bulk
    try:
        with engine.begin() as connection:
            connection.execute(update(db.runs).where(db.runs.c.id == run).values(result={}))
        with pytest.raises(ValueError, match="published book"):
            search.index(run)
        assert embeddings == []
        with engine.begin() as connection:
            connection.execute(update(db.runs).where(db.runs.c.id == run).values(result={"published": True}))
        search.outputs.put(run, "retrieval-chunks", {"chunks": ["obsolete"]}, {"legacy": True})
        first = search.index(run)
        assert all(text.startswith("独立运输样本\n") for text in embeddings[0])
        assert len(embeddings) == 1
        assert search.index(run) == first
        assert len(embeddings) == 1
        hits = search.search("糧食", book, context_chars=6000)
        assert hits and all(h["generation"] == first["generation"] for h in hits)
        assert search.search("粮食", str(uuid4())) == []
        assert search.search("粮食", book, semantic=True)
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
        monkeypatch.setattr(module, "CHUNK_RULE", module.CHUNK_RULE + "-test-new-version")

        def partial(client, actions, **kwargs):
            bulk(client, [next(iter(actions))], **kwargs)
            raise OSError("injected partial index failure")

        monkeypatch.setattr(module.helpers, "bulk", partial)
        with pytest.raises(OSError, match="partial index"):
            search.index(run)
        with engine.connect() as connection:
            metrics = connection.scalar(select(db.runs.c.result).where(db.runs.c.id == run))[
                "retrieval_metrics"
            ]
            assert metrics["status"] == "failed" and metrics["total_ms"] > 0
        assert all(h["generation"] == first["generation"] for h in search.search("粮食", book))
        monkeypatch.setattr(module.helpers, "bulk", bulk)
        second = search.index(run)
        assert second["generation"] != first["generation"]
        assert len(embeddings) == count_before_rebuild
        assert all(h["generation"] == second["generation"] for h in search.search("粮食", book))
        assert search.client.count(index=settings.opensearch_index)["count"] == second["chunks"]
        search.client.indices.delete(index=settings.opensearch_index)
        assert search.index(run) == second
        assert len(embeddings) == count_before_rebuild
    finally:
        search.client.indices.delete(index=settings.opensearch_index, ignore=[404])
