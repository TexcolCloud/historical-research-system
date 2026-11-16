"""Execute book-scoped hybrid queries and expand source-bound evidence."""

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from hrs.library.catalog import Library
from hrs.library.runs.outputs import Outputs, fingerprint
from hrs.search.chunks import expand_hits
from hrs.search.compute import (
    EMBEDDING_IDENTITY,
    RetrievalCompute,
    measured,
    normalizer,
    query_vector,
    search_client,
)
from hrs.search.directory_scope import DirectoryScope
from hrs.search.inputs import ranking_windows
from hrs.search.projection import (
    SOURCE_SCOPES,
    prepare_projection,
    project_hit,
    source_excerpts,
    source_ranges,
)
from hrs.search.ranking import (
    candidates_for_rerank,
    fuse,
    group_exact_results,
    lexical_query,
    multipart_queries,
)
from hrs.search.settings import MODEL_REVISIONS
from hrs.storage import models as db

EVIDENCE_RULE = "book-scoped-query-relevance-v1"
logger = logging.getLogger(__name__)
SEARCH_SOURCE_EXCLUDES = [
    "vector",
    "text_search",
    "title_search",
    "text_aliases",
    "title_aliases",
    "raw_text_search",
    "raw_text_aliases",
    "raw_text",
    "snippet",
    "snippet_segments",
    "ranges",
]


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
        or (
            result.get("codex_projected_navigation_sha256") is not None
            and policy.get("navigation_sha256") != result["codex_projected_navigation_sha256"]
        )
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
        selected = list(book_id) if isinstance(book_id, (list, tuple)) else None
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(db.runs.c.book_id, db.runs.c.result)
                .join(db.books, db.books.c.id == db.runs.c.book_id)
                .where(
                    db.runs.c.kind == "book",
                    db.books.c.state.not_in(["deleting", "delete_failed", "deleted"]),
                    *(
                        [db.runs.c.book_id.in_(selected)]
                        if selected is not None
                        else [db.runs.c.book_id == str(book_id)]
                        if book_id
                        else []
                    ),
                )
            ).all()
        published = [
            (book, result.get("retrieval_generation"))
            for book, result in rows
            if result
            and result.get("published")
            and not (
                result.get("retrieval_chapters_sha256")
                and (result.get("codex_reading_projection") or {}).get("chapters_sha256")
                and result["retrieval_chapters_sha256"]
                != result["codex_reading_projection"]["chapters_sha256"]
            )
        ]
        # Never transfer one book's calibration to a global or ambiguous search scope.
        policies = [
            calibrated_policy(result) for _, result in rows if result and result.get("published")
        ]
        singleton = len(selected) == 1 if selected is not None else bool(book_id)
        self._policy = policies[0] if singleton and len(policies) == 1 else {}
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
        include_navigation=False,
        toc_entry_id=None,
        book_ids=None,
        source_scope="content",
    ):
        if source_scope not in SOURCE_SCOPES:
            raise ValueError("source_scope must be content, metadata, or all")
        if include_navigation and source_scope == "content":
            source_scope = "all"
        if source_scope == "all":
            semantic = False
        if book_ids is not None:
            if book_id is not None:
                raise ValueError("Single-book and multi-book search scopes cannot be combined")
            book_id = tuple(dict.fromkeys(map(str, book_ids)))
        directory_book = book_id
        if isinstance(book_id, tuple):
            directory_book = book_id[0] if len(book_id) == 1 else None
            if toc_entry_id and directory_book is None:
                raise ValueError("A directory entry needs exactly one selected book")
        self._include_navigation = include_navigation
        self._source_scope = source_scope
        metrics = metrics if metrics is not None else {}
        directory_scope = (
            DirectoryScope.resolve(self.library, directory_book, toc_entry_id)
            if toc_entry_id
            else None
        )
        if directory_scope:
            directory_scope.source_scope = source_scope
        try:
            with measured(metrics, "total_ms"):
                chapter_cache = {}
                excerpt = source_excerpts()
                parts = multipart_queries(query) if semantic and decompose else []
                if parts:
                    result = self.search_parts(
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
                        include_navigation=include_navigation,
                        source_scope=source_scope,
                        toc_entry_id=toc_entry_id,
                        directory_scope=directory_scope,
                        excerpt=excerpt,
                    )
                else:
                    result = self._search(
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
                        directory_scope,
                        chapter_cache,
                        excerpt=excerpt,
                    )
                return self.reading_results(
                    group_exact_results(result), chapters=chapter_cache, excerpt=excerpt
                )
        finally:
            logger.info("retrieval.search %s", json.dumps(metrics))

    def search_parts(
        self,
        parts,
        book_id,
        limit,
        context_chars,
        total_chars,
        metrics,
        trace,
        *,
        directory_scope=None,
        excerpt=None,
        **options,
    ):
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
        result = expand_hits(
            hits,
            self.library.source_chapter,
            limit,
            context_chars,
            total_chars,
            metrics=metrics,
            source_scope=getattr(self, "_source_scope", "content"),
            excerpt=excerpt,
        )
        if directory_scope:
            result = directory_scope.clip_results(result, self.library.source_chapter)
        metrics.update(
            subqueries=timings,
            results=len(result),
            evidence_status=(
                "candidates"
                if all(rankings)
                else "partial_evidence"
                if any(rankings)
                else "low_relevance"
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
        directory_scope=None,
        chapter_cache=None,
        excerpt=None,
    ):
        name = self.settings.opensearch_index
        if not self.client.indices.exists(index=name):
            return []
        scope = self.active_scope(book_id)
        source_scope = getattr(self, "_source_scope", "content")
        if source_scope == "content":
            scope.append(
                {
                    "bool": {
                        "should": [
                            {"terms": {"source_scope": ["content", "mixed"]}},
                            {"bool": {"must_not": [{"exists": {"field": "source_scope"}}]}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        elif source_scope == "metadata":
            scope.append({"terms": {"source_scope": ["metadata", "navigation", "mixed"]}})
        if chapter_ids is not None:
            scope.append({"terms": {"chapter_id": list(chapter_ids)}})
        if directory_scope:
            scope.append(directory_scope.query_filter())
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
        lexical = lexical_query(
            query, scope, normalizer().convert(query), raw=source_scope == "all"
        )
        chapters, plans = chapter_cache if chapter_cache is not None else {}, {}

        def project_candidates(rows, purpose="vector"):
            if source_scope == "all":
                purpose = "lexical"
            projected = []
            for row in rows:
                identity = str(row["chapter_id"])
                if identity not in chapters:
                    with measured(metrics, "chapter_load_ms"):
                        chapters[identity] = self.library.source_chapter(identity)
                if identity not in plans:
                    plans[identity] = prepare_projection(chapters[identity])
                if purpose != "rerank":
                    if source_ranges(
                        chapters[identity],
                        row["start"],
                        row["end"],
                        source_scope=source_scope,
                        include_furniture=source_scope == "all",
                        plan=plans[identity],
                    ):
                        projected.append(row)
                    continue
                item = project_hit(
                    chapters[identity],
                    row,
                    purpose=purpose,
                    source_scope=source_scope,
                    plan=plans[identity],
                    include_view=purpose == "rerank",
                )
                if any(r["usage"] == "primary" for r in item["ranges"]):
                    projected.append(item)
            return projected

        def fetch_lexical():
            with measured(metrics, "lexical_ms"):
                return self.client.search(
                    index=name,
                    body={
                        "size": candidates,
                        "query": lexical,
                        "_source": {"excludes": SEARCH_SOURCE_EXCLUDES},
                    },
                )

        if semantic:
            with ThreadPoolExecutor(max_workers=1) as pool:
                lexical_future = pool.submit(fetch_lexical)
                with measured(metrics, "query_embedding_ms"):
                    vector = query_vector(
                        str(self.settings.models_root),
                        self.settings.retrieval_device,
                        self.settings.retrieval_endpoint,
                        fingerprint(EMBEDDING_IDENTITY),
                        query,
                    )
                result = lexical_future.result()
        else:
            result = fetch_lexical()
        found = {
            row["_id"]: dict(row["_source"], score=row["_score"]) for row in result["hits"]["hits"]
        }
        if directory_scope:
            found = {
                row["id"]: row
                for row in directory_scope.clip_candidates(
                    found.values(), self.library.source_chapter
                )
            }
        found = {row["id"]: row for row in project_candidates(found.values())}
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
                        "_source": {"excludes": SEARCH_SOURCE_EXCLUDES},
                    },
                )
            rows = fuse(result["hits"]["hits"], more["hits"]["hits"])
            if trace is not None:
                trace["vector"] = [r["_source"] for r in more["hits"]["hits"]]
                trace["fused"] = list(rows)
            if not fusion:
                union = {
                    r["_id"]: {**r["_source"], "score": r["_score"]} for r in result["hits"]["hits"]
                }
                for row in more["hits"]["hits"]:
                    union.setdefault(row["_id"], {**row["_source"], "score": row["_score"]})
                rows = list(union.values())
            if directory_scope:
                rows = directory_scope.clip_candidates(rows, self.library.source_chapter)
            rows = project_candidates(rows)
            metrics["fused_candidates"] = len(rows)
            if fusion and lane_quota:
                rows = candidates_for_rerank(
                    [result["hits"]["hits"], more["hits"]["hits"]],
                    max(limit, rerank_limit),
                    lane_quota,
                )
                if directory_scope:
                    rows = directory_scope.clip_candidates(rows, self.library.source_chapter)
                rows = project_candidates(rows)
            rows = rows[: max(limit, rerank_limit)]
            if trace is not None:
                trace["rerank_input"] = list(rows)
            if rows:
                with measured(metrics, "rerank_ms"):
                    texts, owners = ranking_windows(
                        query,
                        [row["retrieval_text"] for row in project_candidates(rows, "rerank")],
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
            key = str(identity)
            if key not in chapters:
                with measured(metrics, "chapter_load_ms"):
                    chapters[key] = self.library.source_chapter(identity)
            return chapters[key]

        with measured(metrics, "context_ms"):
            result = expand_hits(
                list(found.values()),
                load_chapter,
                limit,
                context_chars,
                total_chars,
                diverse=diverse,
                metrics=metrics,
                source_scope=source_scope,
                excerpt=excerpt,
            )
        metrics["context_assembly_ms"] = round(
            max(0, metrics["context_ms"] - metrics.get("chapter_load_ms", 0)), 3
        )
        metrics.update(candidates=len(found), results=len(result))
        if directory_scope:
            result = directory_scope.clip_results(result, self.library.source_chapter)
        return result

    def reading_results(self, hits, *, chapters=None, excerpt=None):
        """Resolve immutable result ranges through the reader's shared source contract."""
        from hrs.library.reading_locations import reading_target

        chapters = chapters if chapters is not None else {}
        excerpt = source_excerpts() if excerpt is None else excerpt
        targets, navigation_links = {}, {}

        def target(chapter, start, end):
            key = (str(chapter["id"]), start, end)
            if key not in targets:
                targets[key] = reading_target(chapter, start, end)
            value = targets[key]
            return dict(value) if value is not None else None

        expanded = [item for hit in hits for item in [hit, *hit.get("occurrences", [])]]
        for hit in expanded:
            chapter_id = str(hit["chapter_id"])
            if chapter_id not in chapters:
                chapters[chapter_id] = self.library.source_chapter(chapter_id)
            chapter = chapters[chapter_id]
            same_text = chapter["text"][hit["start"] : hit["end"]] == hit["text"]
            if same_text and 0 <= hit["start"] < hit["end"] <= len(chapter["text"]):
                current = excerpt(chapter, hit["start"], hit["end"])
                for field in (
                    "doc_kind",
                    "toc_path",
                    "toc_entry_id",
                    "navigation_version",
                    "printed_pages",
                ):
                    hit[field] = current[field]
            if hit.get("doc_kind") == "navigation":
                if chapter_id not in navigation_links:
                    navigation_links[chapter_id] = self.library.toc_links(chapter)
                links = [
                    link
                    for link in navigation_links[chapter_id]
                    if link["source_start"] < hit["end"] and link["source_end"] > hit["start"]
                ]
                identity = hit.get("toc_entry_id")
                if identity:
                    links = [link for link in links if link["id"] == identity]
                hit["directory_links"] = links if same_text else []
                hit["reading_target"] = (
                    links[0]["target"] if same_text and len(links) == 1 else None
                )
            else:
                hit["reading_target"] = (
                    target(chapter, hit["start"], hit["end"]) if same_text else None
                )
            hit["reading_targets"] = [
                {
                    **target(chapter, r["start"], r["end"]),
                    "role": r.get("role", "unknown"),
                    "layer": r.get("layer", "unknown"),
                    "usage": r.get("usage", "primary"),
                }
                for r in hit.get("ranges", [{"start": hit["start"], "end": hit["end"]}])
                if same_text and 0 <= r["start"] < r["end"] <= len(chapter["text"])
            ]
            for extra in hit.get("context", []):
                extra["reading_target"] = (
                    target(chapter, extra["start"], extra["end"])
                    if chapter["text"][extra["start"] : extra["end"]] == extra["text"]
                    else None
                )
        return hits
