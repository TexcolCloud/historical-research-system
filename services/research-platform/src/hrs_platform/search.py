"""Rebuildable OpenSearch index; immutable reviewed chapters remain authoritative."""

import asyncio
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from threading import Event
from time import perf_counter
from uuid import uuid4

from opensearchpy import OpenSearch, RequestError, helpers
from opensearchpy.exceptions import TransportError
from sqlalchemy import select, update
from temporalio.exceptions import ApplicationError

from . import schema as db
from .books import get_run
from .domain.errors import Problem
from .domain.settings import MODEL_REVISIONS
from .library import Library
from .outputs import Outputs, fingerprint
from .retrieval_chunks import CHUNK_RULE, expand_hits, retrieval_chunks
from .retrieval_inputs import INPUT_RULE, bounded_chunks, ranking_windows, tokenizer_for
from .retrieval_ranking import candidates_for_rerank, fuse, lexical_query, multipart_queries
from .structure_views import POLICY as STRUCTURE_POLICY
from .structure_views import project_structure

logger = logging.getLogger(__name__)
EMBEDDING_BATCH = 8
EMBEDDING_IDENTITY = {"revision": MODEL_REVISIONS["BAAI/bge-m3"], "adapter": "bge-cls-l2-float32-v1"}
EVIDENCE_RULE = 'book-scoped-query-relevance-v1'
retrieval_owner = ContextVar('retrieval_owner', default=None)


async def task_search(search, endpoint, run_id, *args, **kwargs):
    """Keep the activity slot until its owned retrieval request actually exits."""
    owner = {'task_id': str(run_id), 'request_id': str(uuid4()), 'cancelled': Event()}
    token = retrieval_owner.set(owner)
    running = asyncio.create_task(asyncio.to_thread(search, *args, **kwargs))
    try:
        return await asyncio.shield(running)
    except asyncio.CancelledError:
        owner['cancelled'].set()
        try:
            if endpoint:
                import httpx

                with suppress(httpx.HTTPError):
                    await asyncio.to_thread(
                        httpx.post, endpoint.rsplit('/', 1)[0] + '/cancel-retrieval',
                        json={'request_ids': [owner['request_id']]}, timeout=30,
                    )
        finally:
            # Even an unreachable broker must not release the slot while the
            # original bounded HTTP/CPU call is still alive.
            while not running.done():
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(running)
            with suppress(Exception, asyncio.CancelledError):
                running.result()
        raise
    finally:
        retrieval_owner.reset(token)


async def recover_retrieval(engine, outputs, run_id, key, operation):
    """Persist service cooldown independently of paid model request attempts."""
    run = await asyncio.to_thread(get_run, engine, run_id)
    epoch = run.get("recovery_attempt", 0)
    prefix = f"{key}:retrieval-recovery:{epoch}"
    previous, attempt = None, 1
    while receipt := await asyncio.to_thread(outputs.get, run_id, f"{prefix}:{attempt}"):
        previous, attempt = receipt, attempt + 1

    def defer(receipt):
        exhausted = receipt["attempt"] >= 12
        raise ApplicationError(
            "检索服务恢复等待已达上限，进度保留，请稍后重试。"
            if exhausted
            else "检索服务暂不可用，保留查询和证据，等待自动恢复。",
            receipt,
            type="retrieval_exhausted" if exhausted else "retrieval_wait",
            non_retryable=True,
        ) from None

    if previous and (previous["attempt"] >= 12 or previous["retry_at"] > time.time()):
        defer(previous)
    try:
        return await operation()
    except (Problem, TransportError, ApplicationError) as error:
        retryable = (
            isinstance(error, Problem)
            and error.retryable
            or isinstance(error, TransportError)
            and (
                not isinstance(error.status_code, int)
                or error.status_code in {404, 408, 409, 429}
                or error.status_code >= 500
            )
            or isinstance(error, ApplicationError)
            and error.type == "card_index_missing"
        )
        if not retryable:
            raise
        receipt = {"attempt": attempt, "retry_at": time.time() + min(60 * 2 ** (attempt - 1), 300)}
        await asyncio.to_thread(outputs.put, run_id, f"{prefix}:{attempt}", receipt, {"key": key})
        defer(receipt)


def calibrated_policy(result):
    """A calibration is valid only for the exact indexed source and scoring model."""
    policy = result.get('retrieval_policy') or {}
    if not isinstance(policy, dict):
        return {}
    if (policy.get('rule') != EVIDENCE_RULE or not result.get('retrieval_generation')
        or policy.get('generation') != result['retrieval_generation']
        or policy.get('reranker_revision') != MODEL_REVISIONS['BAAI/bge-reranker-v2-m3']):
        return {}
    score = policy.get('min_top_score')
    if type(score) not in {int, float} or not math.isfinite(score):
        return {}
    if any(type(policy.get(k)) is not int or not 1 <= policy[k] <= 100
           for k in ['candidate_limit', 'rerank_limit']):
        return {}
    return policy


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
        owner = retrieval_owner.get()
        if owner and owner['cancelled'].is_set():
            raise RuntimeError('retrieval_cancelled')
        identity = {key: owner[key] for key in ('task_id', 'request_id')} if owner else {}
        try:
            response = httpx.post(endpoint, json={'operation': operation, 'texts': texts[offset:offset + 32], **options, **identity}, timeout=1800)
            response.raise_for_status()
        except httpx.HTTPError as error:
            retryable = (not isinstance(error, httpx.HTTPStatusError)
                         or error.response.status_code in {408, 409, 429}
                         or error.response.status_code >= 500)
            raise Problem('retrieval_unavailable', '本地 GPU 检索请求失败，请检查主机运行服务及请求配置。',
                          status=503, retryable=retryable) from error
        value = response.json()
        key = 'vectors' if operation == 'embed' else 'scores'
        if len(value[key]) != len(texts[offset:offset + 32]):
            raise ValueError('Retrieval response count differs from request')
        result[key].extend(value[key])
        if 'identity' in value:
            result['identity'] = value['identity']
    return result


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
        if missing:
            tokenizer = self.tokenizer("BAAI/bge-m3")
            missing.sort(key=lambda key: len(tokenizer.encode(wanted[key]).ids))
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
        structure = (self.library.structure(run_id)
                     if run['conversion'] and run['result'].get('review_initialized')
                     else {'available': False, 'items': []})
        dependency = {
            "book_id": run["book_id"],
            "book_title": book_title,
            "chapters": [
                {"id": row["id"], "title": row["title"], "sha256": row["content"]["sha256"]}
                for row in chapters
            ],
            "chunk_rule": CHUNK_RULE,
            "input_rule": INPUT_RULE,
            "structure_policy": STRUCTURE_POLICY,
            "structure_sha256": fingerprint(structure),
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
                    # Refresh source-checked relations without republishing canonical chapters.
                    if structure['available']:
                        chapter = {**chapter, 'structure': project_structure(structure, chapter['parts'])}
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
        # Never transfer one book's calibration to a global or ambiguous search scope.
        policies = [calibrated_policy(result) for _, result in rows if result and result.get('published')]
        self._policy = policies[0] if book_id and len(policies) == 1 else {}
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
        candidate_limit=None,
        context_chars=6000,
        total_chars=24000,
        metrics=None,
        rerank_limit=None,
        diverse=False,
        fusion=True,
        lane_quota=0,
        min_rerank_score=None,
        trace=None,
        min_top_score=None,
        use_calibration=True,
        decompose=True,
        chapter_ids=None,
    ):
        metrics = metrics if metrics is not None else {}
        try:
            with measured(metrics, "total_ms"):
                parts = multipart_queries(query) if semantic and decompose else []
                if parts:
                    return self.search_parts(parts, book_id, limit, context_chars, total_chars, metrics, trace,
                        candidate_limit=candidate_limit, rerank_limit=rerank_limit, fusion=fusion,
                        lane_quota=lane_quota, min_rerank_score=min_rerank_score,
                        min_top_score=min_top_score, use_calibration=use_calibration, chapter_ids=chapter_ids)
                return self._search(
                    query, book_id, semantic, limit, candidate_limit, context_chars, total_chars, metrics, rerank_limit, diverse, fusion, lane_quota, min_rerank_score, trace, min_top_score, use_calibration, chapter_ids
                )
        finally:
            logger.info("retrieval.search %s", json.dumps(metrics))

    def search_parts(self, parts, book_id, limit, context_chars, total_chars, metrics, trace, **options):
        """Reserve evidence from each explicit clause using the existing search path."""
        rankings, timings = [], []
        for part in parts:
            timing, stages = {}, {} if trace is not None else None
            hits = self.search(part, book_id, limit=limit, context_chars=context_chars,
                total_chars=total_chars, metrics=timing, trace=stages, decompose=False, **options)
            rankings.append([{'_id': hit['id'], '_source': hit} for hit in hits])
            timings.append(timing)
            if trace is not None:
                for stage, candidates in stages.items():
                    trace.setdefault(stage, []).extend(candidates)
        # RRF balances independently scored clauses instead of comparing logits
        # across queries. Source ranges and the original context budget still apply.
        hits = fuse(*rankings)
        result = expand_hits(hits, self.library.chapter, limit, context_chars, total_chars, metrics=metrics)
        metrics.update(subqueries=timings, results=len(result),
            evidence_status=('candidates' if all(rankings) else 'partial_evidence' if any(rankings) else 'low_relevance'),
            calibration_applied=any(t.get('calibration_applied') for t in timings))
        return result

    def _search(self, query, book_id, semantic, limit, candidate_limit, context_chars, total_chars, metrics, rerank_limit, diverse, fusion, lane_quota, min_rerank_score, trace, min_top_score, use_calibration, chapter_ids=None):
        name = self.settings.opensearch_index
        if not self.client.indices.exists(index=name):
            return []
        scope = self.active_scope(book_id)
        if chapter_ids is not None:
            scope.append({"terms": {"chapter_id": list(chapter_ids)}})
        policy = getattr(self, '_policy', {}) if semantic and use_calibration else {}
        candidate_limit = candidate_limit if candidate_limit is not None else policy.get('candidate_limit', 50)
        rerank_limit = rerank_limit if rerank_limit is not None else policy.get('rerank_limit', 30)
        min_top_score = min_top_score if min_top_score is not None else policy.get('min_top_score')
        metrics.update(candidate_limit=candidate_limit, rerank_limit=rerank_limit,
                       evidence_status='unassessed', calibration_applied=bool(policy))
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
        if trace is not None:
            trace['lexical'] = list(found.values())
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
            if trace is not None:
                trace['vector'] = [r['_source'] for r in more['hits']['hits']]
                trace['fused'] = list(rows)
            if not fusion:
                union = {r['_id']: {**r['_source'], 'score': r['_score']} for r in result['hits']['hits']}
                for row in more['hits']['hits']:
                    union.setdefault(row['_id'], {**row['_source'], 'score': row['_score']})
                rows = list(union.values())
            metrics['fused_candidates'] = len(rows)
            if fusion and lane_quota:
                rows = candidates_for_rerank([result['hits']['hits'], more['hits']['hits']], max(limit, rerank_limit), lane_quota)
            rows = rows[:max(limit, rerank_limit)]
            if trace is not None:
                trace['rerank_input'] = list(rows)
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
                metrics['top_rerank_score'] = max(scores)
                found = {row["id"]: {**row, "score": score} for row, score in zip(rows, scores, strict=True)
                         if min_rerank_score is None or score >= min_rerank_score}
                metrics['relevance_rejected'] = len(rows) - len(found)
                # Gate the query, not each hit: low-scoring supporting evidence can
                # still be necessary for cross-chapter or qualified answers.
                metrics['evidence_status'] = 'candidates'
                if min_top_score is not None and max(scores) < min_top_score:
                    found = {}
                    metrics['evidence_status'] = 'low_relevance'
        if trace is not None:
            trace['ranked'] = sorted(found.values(), key=lambda h: h['score'], reverse=True)
        def load_chapter(identity):
            with measured(metrics, 'chapter_load_ms'):
                return self.library.chapter(identity)
        with measured(metrics, "context_ms"):
            result = expand_hits(
                list(found.values()), load_chapter, limit, context_chars, total_chars, diverse=diverse, metrics=metrics
            )
        metrics['context_assembly_ms'] = round(max(0, metrics['context_ms'] - metrics.get('chapter_load_ms', 0)), 3)
        metrics.update(candidates=len(found), results=len(result))
        return result
