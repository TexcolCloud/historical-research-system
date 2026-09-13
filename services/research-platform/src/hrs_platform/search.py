"""Rebuildable OpenSearch index; immutable reviewed chapters remain authoritative."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from time import perf_counter

from langchain_text_splitters import RecursiveCharacterTextSplitter
from opensearchpy import OpenSearch, RequestError, helpers
from sqlalchemy import select, update

from . import schema as db
from .books import get_run
from .domain.settings import MODEL_REVISIONS
from .library import Library
from .outputs import Outputs, fingerprint
from .retrieval_chunks import CHUNK_RULE, expand_hits, retrieval_chunks, source_excerpt
from .retrieval_inputs import INPUT_RULE, bounded_chunks, ranking_windows, tokenizer_for
from .retrieval_ranking import fuse, lexical_query

logger = logging.getLogger(__name__)
EMBEDDING_BATCH = 8
EMBEDDING_IDENTITY = {"revision": MODEL_REVISIONS["BAAI/bge-m3"], "adapter": "bge-cls-l2-float32-v1"}


@contextmanager
def measured(metrics, name):
    started = perf_counter()
    try:
        yield
    finally:
        metrics[name] = metrics.get(name, 0) + round((perf_counter() - started) * 1000, 3)


@lru_cache(maxsize=1)
def normalizer():
    from opencc import OpenCC

    return OpenCC("t2s")


@lru_cache(maxsize=4)
def search_client(url):
    return OpenSearch(url, timeout=30, max_retries=2, retry_on_timeout=True)


@lru_cache(maxsize=1)
def local_models(root):
    from .domain.retrieval_models import LocalModels
    from .domain.settings import RetrievalSettings

    return LocalModels(RetrievalSettings(models_root=Path(root), device="cpu"))


@lru_cache(maxsize=256)
def query_vector(root, device, endpoint, identity, query):
    if device == 'cuda':
        return remote_compute(endpoint, 'embed', [query], query=True)['vectors'][0]
    return local_models(root).embed([query], query=True)[0]


def remote_compute(endpoint, operation, texts, **options):
    import httpx

    from .domain.errors import Problem
    result = {'vectors': [], 'scores': []}
    for offset in range(0, len(texts), 32):
        try:
            response = httpx.post(endpoint, json={'operation': operation, 'texts': texts[offset:offset + 32], **options}, timeout=1800)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise Problem('retrieval_unavailable', '本地 GPU 检索服务暂不可用，请确认主机运行服务已启动后重试。', status=503, retryable=True) from error
        value = response.json()
        key = 'vectors' if operation == 'embed' else 'scores'
        if len(value[key]) != len(texts[offset:offset + 32]):
            raise ValueError('Retrieval response count differs from request')
        result[key].extend(value[key])
        if 'identity' in value:
            result['identity'] = value['identity']
    return result


def chapter_chunks(chapter, size=1400, overlap=160):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "；", "！", "？", " ", ""],
        keep_separator="end",
        strip_whitespace=False,
        add_start_index=True,
    )
    for item in splitter.create_documents([chapter["text"]]):
        start = item.metadata["start_index"]
        end = start + len(item.page_content)
        if start < 0 or chapter["text"][start:end] != item.page_content:
            raise ValueError("The chunk could not be located in its immutable chapter.")
        yield source_excerpt(chapter, start, end)


class Search:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.client = search_client(settings.opensearch_url)
        self.library, self.outputs = Library(settings, engine), Outputs(settings, engine)

    def tokenizer(self, model):
        return tokenizer_for(self.settings.project_root / "models/document-retrieval", model)

    def embed_cached(self, run_id, chunks, metrics):
        identity = {**EMBEDDING_IDENTITY, 'device': self.settings.retrieval_device}
        prefix = "retrieval-embeddings:" + fingerprint(identity) + ":"
        wanted = {fingerprint(row["retrieval_text"]): row["retrieval_text"] for row in chunks}
        cached = {}
        model = None
        with measured(metrics, "cache_read_ms"):
            with self.engine.connect() as connection:
                references = connection.scalars(
                    select(db.stage_outputs.c.reference).where(
                        db.stage_outputs.c.run_id == run_id,
                        db.stage_outputs.c.step.startswith(prefix),
                    )
                ).all()
            for reference in references:
                saved = json.loads(self.outputs.objects.read_bytes(reference))
                cached.update({key: value for key, value in saved["vectors"].items() if key in wanted})
                model = saved["model"]
        metrics["reused_vectors"] = len(cached)
        metrics["computed_vectors"] = 0
        missing = [key for key in wanted if key not in cached]
        for offset in range(0, len(missing), EMBEDDING_BATCH):
            keys = missing[offset : offset + EMBEDDING_BATCH]
            with measured(metrics, "embedding_ms"):
                result = self.compute("embed", [wanted[key] for key in keys])
            vectors = dict(zip(keys, result["vectors"], strict=True))
            dependency = {"embedding": identity, "texts": keys}
            with measured(metrics, "checkpoint_ms"):
                self.outputs.put(
                    run_id,
                    prefix + fingerprint(keys),
                    {"vectors": vectors, "model": result["identity"]},
                    dependency,
                )
            cached.update(vectors)
            metrics["computed_vectors"] += len(keys)
            model = result["identity"]
        return {
            "chunks": [{**row, "vector": cached[fingerprint(row["retrieval_text"])]} for row in chunks],
            "model": model,
        }

    def compute(self, operation, texts, **options):
        if self.settings.retrieval_device == 'cuda':
            return remote_compute(self.settings.retrieval_endpoint, operation, texts, **options)
        models = local_models(str(self.settings.project_root / "models/document-retrieval"))
        if operation == "embed":
            return {
                "vectors": models.embed(texts, query=options.get("query", False)),
                "identity": models.identity(),
            }
        if operation == "rerank":
            return {"scores": models.rerank(options["query"], texts)}
        raise ValueError("Unsupported local model operation")

    def index(self, run_id):
        metrics = {"status": "failed"}
        try:
            with measured(metrics, "total_ms"):
                result = self._index(run_id, metrics)
            metrics["status"] = "completed"
            return result
        finally:
            logger.info("retrieval.index %s", json.dumps({"run_id": str(run_id), **metrics}))
            # Diagnostics do not replace or mask the indexing failure.
            try:
                with self.engine.begin() as connection:
                    current = connection.scalar(
                        select(db.runs.c.result).where(db.runs.c.id == run_id).with_for_update()
                    )
                    connection.execute(
                        update(db.runs)
                        .where(db.runs.c.id == run_id)
                        .values(result={**(current or {}), "retrieval_metrics": metrics})
                    )
            except Exception:
                logger.exception("Could not save retrieval timing for run %s", run_id)

    def _index(self, run_id, metrics):
        run = get_run(self.engine, run_id)
        if run["kind"] != "book" or not (run["result"] or {}).get("published"):
            raise ValueError("Only published book runs can be indexed.")
        chapters = self.library.chapters(run["book_id"])
        if not chapters:
            raise ValueError("Only published chapters can be indexed.")
        with self.engine.connect() as connection:
            book_title = connection.scalar(select(db.books.c.title).where(db.books.c.id == run["book_id"]))
        dependency = {
            "book_id": run["book_id"],
            "book_title": book_title,
            "chapters": [
                {"id": row["id"], "title": row["title"], "sha256": row["content"]["sha256"]}
                for row in chapters
            ],
            "chunk_rule": CHUNK_RULE,
            "input_rule": INPUT_RULE,
            "embedding": {**EMBEDDING_IDENTITY, 'device': self.settings.retrieval_device},
        }
        generation = fingerprint(dependency)
        step = "retrieval-chunks:" + generation
        saved = self.outputs.get(run_id, step, dependency)
        if saved is None:
            with measured(metrics, "chunking_ms"):
                tokenizer = self.tokenizer("BAAI/bge-m3")
                chunks = []
                for row in chapters:
                    chapter = self.library.chapter(row["id"])
                    chunks.extend(bounded_chunks(chapter, retrieval_chunks(chapter, book_title), tokenizer))
            saved = self.embed_cached(run_id, chunks, metrics)
            with measured(metrics, "checkpoint_ms"):
                saved = self.outputs.put(run_id, step, saved, dependency)
        else:
            metrics.update(reused_vectors=len(saved["chunks"]), computed_vectors=0)
        metrics["chunks"] = len(saved["chunks"])
        with measured(metrics, "index_write_ms"):
            self.publish_index(run_id, run, generation, saved)
        return {"run_id": run_id, "chunks": len(saved["chunks"]), "generation": generation}

    def publish_index(self, run_id, run, generation, saved):
        name = self.settings.opensearch_index
        self.ensure_index(name)
        helpers.bulk(
            self.client,
            (
                {
                    "_index": name,
                    "_id": generation + ":" + row["id"],
                    "_source": {
                        **row,
                        "generation": generation,
                        "text_search": normalizer().convert(row["retrieval_text"]),
                        "title_search": normalizer().convert(row["title"]),
                    },
                }
                for row in saved["chunks"]
            ),
            chunk_size=100,
            refresh="wait_for",
        )
        # Readers select only the SQL-committed generation. Failed bulk writes stay invisible.
        from .deletion import require_active

        with self.engine.begin() as connection:
            require_active(connection, run["book_id"])
            current = connection.scalar(
                select(db.runs.c.result).where(db.runs.c.id == run_id).with_for_update()
            )
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(result={**(current or {}), "retrieval_generation": generation})
            )
        self.client.delete_by_query(
            index=name,
            body={
                "query": {
                    "bool": {
                        "filter": [{"term": {"book_id": run["book_id"]}}],
                        "must_not": [{"term": {"generation": generation}}],
                    }
                }
            },
            refresh=True,
            conflicts="proceed",
        )

    def ensure_index(self, name):
        if not self.client.indices.exists(index=name):
            try:
                self.client.indices.create(
                    index=name,
                    body={
                        "settings": {"index": {"knn": True, "number_of_shards": 1, "number_of_replicas": 0}},
                        "mappings": {
                            "properties": {
                                "book_id": {"type": "keyword"},
                                "chapter_id": {"type": "keyword"},
                                "run_id": {"type": "keyword"},
                                "generation": {"type": "keyword"},
                                "retrieval_text": {"type": "text", "index": False},
                                "context": {"type": "object", "enabled": False},
                                "text": {"type": "text", "index": False},
                                "title": {"type": "text", "index": False},
                                "text_search": {"type": "text", "analyzer": "cjk"},
                                "title_search": {"type": "text", "analyzer": "cjk"},
                                "sources": {"type": "object", "enabled": False},
                                "vector": {
                                    "type": "knn_vector",
                                    "dimension": 1024,
                                    "method": {
                                        "name": "hnsw",
                                        "engine": "lucene",
                                        "space_type": "cosinesimil",
                                    },
                                },
                            }
                        },
                    },
                )
            except RequestError as error:
                if error.error != "resource_already_exists_exception":
                    raise
        # Add mappings explicitly when upgrading an already populated v1 index.
        self.client.indices.put_mapping(
            index=name,
            body={
                "properties": {
                    "generation": {"type": "keyword"},
                    "retrieval_text": {"type": "text", "index": False},
                    "context": {"type": "object", "enabled": False},
                }
            },
        )

    def active_scope(self, book_id):
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(db.runs.c.book_id, db.runs.c.result)
                .join(db.books, db.books.c.id == db.runs.c.book_id)
                .where(
                    db.runs.c.kind == "book",
                    db.books.c.state.not_in(["deleting", "delete_failed", "deleted"]),
                    *([db.runs.c.book_id == str(book_id)] if book_id else []),
                )
            ).all()
        published = [
            (book, result.get("retrieval_generation"))
            for book, result in rows
            if result and result.get("published")
        ]
        active = [generation for _, generation in published if generation]
        legacy = [book for book, generation in published if not generation]
        return [
            {"terms": {"book_id": [book for book, _ in published]}},
            {
                "bool": {
                    "should": [
                        {"terms": {"generation": active}},
                        {
                            "bool": {
                                "filter": [{"terms": {"book_id": legacy}}],
                                "must_not": [{"exists": {"field": "generation"}}],
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
        ]

    def search(
        self,
        query,
        book_id=None,
        semantic=True,
        limit=8,
        candidate_limit=50,
        context_chars=6000,
        total_chars=24000,
        metrics=None,
        rerank_limit=30,
        diverse=False,
        fusion=True,
    ):
        metrics = metrics if metrics is not None else {}
        try:
            with measured(metrics, "total_ms"):
                return self._search(
                    query, book_id, semantic, limit, candidate_limit, context_chars, total_chars, metrics, rerank_limit, diverse, fusion
                )
        finally:
            logger.info("retrieval.search %s", json.dumps(metrics))

    def _search(self, query, book_id, semantic, limit, candidate_limit, context_chars, total_chars, metrics, rerank_limit, diverse, fusion):
        name = self.settings.opensearch_index
        if not self.client.indices.exists(index=name):
            return []
        scope = self.active_scope(book_id)
        candidates = max(limit, candidate_limit)
        lexical = lexical_query(normalizer().convert(query), scope)
        def fetch_lexical():
            with measured(metrics, 'lexical_ms'):
                return self.client.search(index=name, body={'size': candidates, 'query': lexical, '_source': {'excludes': ['vector']}})
        if semantic:
            with ThreadPoolExecutor(max_workers=1) as pool:
                lexical_future = pool.submit(fetch_lexical)
                with measured(metrics, 'query_embedding_ms'):
                    vector = query_vector(str(self.settings.project_root / 'models/document-retrieval'),
                                          self.settings.retrieval_device, self.settings.retrieval_endpoint,
                                          fingerprint(EMBEDDING_IDENTITY), query)
                result = lexical_future.result()
        else:
            result = fetch_lexical()
        found = {row["_id"]: dict(row["_source"], score=row["_score"]) for row in result["hits"]["hits"]}
        if semantic:
            knn = {
                "vector": vector,
                "k": candidates,
                **({"filter": {"bool": {"filter": scope}}} if scope else {}),
            }
            with measured(metrics, "vector_search_ms"):
                more = self.client.search(
                    index=name,
                    body={
                        "size": candidates,
                        "query": {"knn": {"vector": knn}},
                        "_source": {"excludes": ["vector"]},
                    },
                )
            rows = fuse(result['hits']['hits'], more['hits']['hits'])
            if not fusion:
                union = {r['_id']: {**r['_source'], 'score': r['_score']} for r in result['hits']['hits']}
                for row in more['hits']['hits']:
                    union.setdefault(row['_id'], {**row['_source'], 'score': row['_score']})
                rows = list(union.values())
            metrics['fused_candidates'] = len(rows)
            rows = rows[:max(limit, rerank_limit)]
            if rows:
                with measured(metrics, "rerank_ms"):
                    texts, owners = ranking_windows(
                        query,
                        [row.get("retrieval_text", row["text"]) for row in rows],
                        self.tokenizer("BAAI/bge-reranker-v2-m3"),
                    )
                    window_scores = self.compute("rerank", texts, query=query)["scores"]
                    scores = [float("-inf")] * len(rows)
                    for owner, score in zip(owners, window_scores, strict=True):
                        scores[owner] = max(scores[owner], score)
                found = {row["id"]: {**row, "score": score} for row, score in zip(rows, scores, strict=True)}
        with measured(metrics, "context_ms"):
            result = expand_hits(
                list(found.values()), self.library.chapter, limit, context_chars, total_chars, diverse=diverse
            )
        metrics.update(candidates=len(found), results=len(result))
        return result
