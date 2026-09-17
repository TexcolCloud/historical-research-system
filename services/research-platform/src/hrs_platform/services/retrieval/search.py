"""Execute book-scoped hybrid queries and expand source-bound evidence."""

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from hrs_platform import models as db
from hrs_platform.domain.settings import MODEL_REVISIONS
from hrs_platform.services.documents.library import Library
from hrs_platform.services.retrieval.chunks import expand_hits
from hrs_platform.services.retrieval.compute import (
    EMBEDDING_IDENTITY,
    RetrievalCompute,
    measured,
    normalizer,
    query_vector,
    search_client,
)
from hrs_platform.services.retrieval.inputs import ranking_windows
from hrs_platform.services.retrieval.ranking import (
    candidates_for_rerank,
    fuse,
    lexical_query,
    multipart_queries,
)
from hrs_platform.services.runs.outputs import Outputs, fingerprint

EVIDENCE_RULE = "book-scoped-query-relevance-v1"
logger = logging.getLogger(__name__)


def calibrated_policy(result):
    """A calibration is valid only for the exact indexed source and scoring model."""
    policy = result.get("retrieval_policy") or {}
    if not isinstance(policy, dict):
        return {}
    if (
        policy.get("rule") != EVIDENCE_RULE
        or not result.get("retrieval_generation")
        or policy.get("generation") != result["retrieval_generation"]
        or policy.get("reranker_revision") != MODEL_REVISIONS["BAAI/bge-reranker-v2-m3"]
    ):
        return {}
    score = policy.get("min_top_score")
    if type(score) not in {int, float} or not math.isfinite(score):
        return {}
    if any(
        type(policy.get(k)) is not int or not 1 <= policy[k] <= 100
        for k in ["candidate_limit", "rerank_limit"]
    ):
        return {}
    return policy


class Search:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.runtime = RetrievalCompute(settings)
        self.client = search_client(settings.opensearch_url)
        self.library, self.outputs = Library(settings, engine), Outputs(settings, engine)

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
        policies = [calibrated_policy(result) for _, result in rows if result and result.get("published")]
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
                    return self.search_parts(
                        parts,
                        book_id,
                        limit,
                        context_chars,
                        total_chars,
                        metrics,
                        trace,
                        candidate_limit=candidate_limit,
                        rerank_limit=rerank_limit,
                        fusion=fusion,
                        lane_quota=lane_quota,
                        min_rerank_score=min_rerank_score,
                        min_top_score=min_top_score,
                        use_calibration=use_calibration,
                        chapter_ids=chapter_ids,
                    )
                return self._search(
                    query,
                    book_id,
                    semantic,
                    limit,
                    candidate_limit,
                    context_chars,
                    total_chars,
                    metrics,
                    rerank_limit,
                    diverse,
                    fusion,
                    lane_quota,
                    min_rerank_score,
                    trace,
                    min_top_score,
                    use_calibration,
                    chapter_ids,
                )
        finally:
            logger.info("retrieval.search %s", json.dumps(metrics))

    def search_parts(self, parts, book_id, limit, context_chars, total_chars, metrics, trace, **options):
        """Reserve evidence from each explicit clause using the existing search path."""
        rankings, timings = [], []
        for part in parts:
            timing, stages = {}, {} if trace is not None else None
            hits = self.search(
                part,
                book_id,
                limit=limit,
                context_chars=context_chars,
                total_chars=total_chars,
                metrics=timing,
                trace=stages,
                decompose=False,
                **options,
            )
            rankings.append([{"_id": hit["id"], "_source": hit} for hit in hits])
            timings.append(timing)
            if trace is not None:
                for stage, candidates in stages.items():
                    trace.setdefault(stage, []).extend(candidates)
        # RRF balances independently scored clauses instead of comparing logits
        # across queries. Source ranges and the original context budget still apply.
        hits = fuse(*rankings)
        result = expand_hits(hits, self.library.chapter, limit, context_chars, total_chars, metrics=metrics)
        metrics.update(
            subqueries=timings,
            results=len(result),
            evidence_status=(
                "candidates" if all(rankings) else "partial_evidence" if any(rankings) else "low_relevance"
            ),
            calibration_applied=any(t.get("calibration_applied") for t in timings),
        )
        return result

    def _search(
        self,
        query,
        book_id,
        semantic,
        limit,
        candidate_limit,
        context_chars,
        total_chars,
        metrics,
        rerank_limit,
        diverse,
        fusion,
        lane_quota,
        min_rerank_score,
        trace,
        min_top_score,
        use_calibration,
        chapter_ids=None,
    ):
        name = self.settings.opensearch_index
        if not self.client.indices.exists(index=name):
            return []
        scope = self.active_scope(book_id)
        if chapter_ids is not None:
            scope.append({"terms": {"chapter_id": list(chapter_ids)}})
        policy = getattr(self, "_policy", {}) if semantic and use_calibration else {}
        candidate_limit = (
            candidate_limit if candidate_limit is not None else policy.get("candidate_limit", 50)
        )
        rerank_limit = rerank_limit if rerank_limit is not None else policy.get("rerank_limit", 30)
        min_top_score = min_top_score if min_top_score is not None else policy.get("min_top_score")
        metrics.update(
            candidate_limit=candidate_limit,
            rerank_limit=rerank_limit,
            evidence_status="unassessed",
            calibration_applied=bool(policy),
        )
        candidates = max(limit, candidate_limit)
        lexical = lexical_query(normalizer().convert(query), scope)

        def fetch_lexical():
            with measured(metrics, "lexical_ms"):
                return self.client.search(
                    index=name,
                    body={"size": candidates, "query": lexical, "_source": {"excludes": ["vector"]}},
                )

        if semantic:
            with ThreadPoolExecutor(max_workers=1) as pool:
                lexical_future = pool.submit(fetch_lexical)
                with measured(metrics, "query_embedding_ms"):
                    vector = query_vector(
                        str(self.settings.project_root / "models/document-retrieval"),
                        self.settings.retrieval_device,
                        self.settings.retrieval_endpoint,
                        fingerprint(EMBEDDING_IDENTITY),
                        query,
                    )
                result = lexical_future.result()
        else:
            result = fetch_lexical()
        found = {row["_id"]: dict(row["_source"], score=row["_score"]) for row in result["hits"]["hits"]}
        if trace is not None:
            trace["lexical"] = list(found.values())
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
            rows = fuse(result["hits"]["hits"], more["hits"]["hits"])
            if trace is not None:
                trace["vector"] = [r["_source"] for r in more["hits"]["hits"]]
                trace["fused"] = list(rows)
            if not fusion:
                union = {r["_id"]: {**r["_source"], "score": r["_score"]} for r in result["hits"]["hits"]}
                for row in more["hits"]["hits"]:
                    union.setdefault(row["_id"], {**row["_source"], "score": row["_score"]})
                rows = list(union.values())
            metrics["fused_candidates"] = len(rows)
            if fusion and lane_quota:
                rows = candidates_for_rerank(
                    [result["hits"]["hits"], more["hits"]["hits"]], max(limit, rerank_limit), lane_quota
                )
            rows = rows[: max(limit, rerank_limit)]
            if trace is not None:
                trace["rerank_input"] = list(rows)
            if rows:
                with measured(metrics, "rerank_ms"):
                    texts, owners = ranking_windows(
                        query,
                        [row.get("retrieval_text", row["text"]) for row in rows],
                        self.runtime.tokenizer("BAAI/bge-reranker-v2-m3"),
                    )
                    window_scores = self.runtime.compute("rerank", texts, query=query)["scores"]
                    scores = [float("-inf")] * len(rows)
                    for owner, score in zip(owners, window_scores, strict=True):
                        scores[owner] = max(scores[owner], score)
                metrics["top_rerank_score"] = max(scores)
                found = {
                    row["id"]: {**row, "score": score}
                    for row, score in zip(rows, scores, strict=True)
                    if min_rerank_score is None or score >= min_rerank_score
                }
                metrics["relevance_rejected"] = len(rows) - len(found)
                # Gate the query, not each hit: low-scoring supporting evidence can
                # still be necessary for cross-chapter or qualified answers.
                metrics["evidence_status"] = "candidates"
                if min_top_score is not None and max(scores) < min_top_score:
                    found = {}
                    metrics["evidence_status"] = "low_relevance"
        if trace is not None:
            trace["ranked"] = sorted(found.values(), key=lambda h: h["score"], reverse=True)

        def load_chapter(identity):
            with measured(metrics, "chapter_load_ms"):
                return self.library.chapter(identity)

        with measured(metrics, "context_ms"):
            result = expand_hits(
                list(found.values()),
                load_chapter,
                limit,
                context_chars,
                total_chars,
                diverse=diverse,
                metrics=metrics,
            )
        metrics["context_assembly_ms"] = round(
            max(0, metrics["context_ms"] - metrics.get("chapter_load_ms", 0)), 3
        )
        metrics.update(candidates=len(found), results=len(result))
        return result
