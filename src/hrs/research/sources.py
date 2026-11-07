"""Full immutable publications and shared hybrid evidence retrieval adapters."""

import copy
import json

from sqlalchemy import Float, case, column, exists, func, literal_column, not_, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from hrs.library.books import get_run
from hrs.library.catalog import Library
from hrs.library.errors import Problem, ServiceError
from hrs.library.footnotes import resolve_footnotes
from hrs.library.native_workspace import WrongWorkspace, require_workspace, workspace_runs
from hrs.library.reading_locations import reading_target
from hrs.library.records import text_digest
from hrs.processing.workspace import identity
from hrs.search.search import Search
from hrs.storage import models as db


def retrieval_preview(result):
    """Project research-only selection leads; complete source evidence is fetched separately."""
    from hrs.processing.hashing import digest

    query = result.get("query", "")
    original = result.get("hits", [])
    output = {
        "query": query[:1000],
        "backend": result.get("backend"),
        "available": result.get("available", False),
        "evidence_status": result.get("evidence_status"),
        "preview_only": True,
        "total_hits": len(original),
        "hits": [],
        "read_evidence": "Use research_read_evidence(article_id=chapter_id); read the complete frozen article before citing. Previews are selection leads only.",
    }
    for rank, hit in enumerate(original[:8], 1):
        text = hit.get("text", "")
        position = text.find(query) if query else -1
        left = max(0, position - max(0, 320 - len(query)) // 2) if position >= 0 else 0
        right = min(len(text), left + 320)
        preview = text[left:right]
        target = hit.get("reading_target") or {}
        output["hits"].append(
            {
                "rank": rank,
                "id": hit.get("id"),
                "book_id": hit.get("book_id"),
                "chapter_id": hit.get("chapter_id"),
                "run_id": hit.get("run_id"),
                "title": hit.get("title", "")[:80],
                "book_title": hit.get("book_title", "")[:60],
                "score": hit.get("score"),
                "pages": hit.get("pages", []),
                "start": hit.get("start"),
                "end": hit.get("end"),
                "text_sha256": target.get("text_sha256"),
                "snippet_sha256": digest(text),
                "preview": preview,
                "preview_start": hit.get("start", 0) + left,
                "preview_end": hit.get("start", 0) + right,
                "preview_length": len(preview),
                "preview_sha256": digest(preview),
                "preview_truncated": left > 0 or right < len(text),
                "context_roles": sorted({item.get("role", "") for item in hit.get("context", [])}),
                "context_omitted": bool(hit.get("context")),
                "has_shared_cells": bool(hit.get("table_scopes")),
                "metadata_truncated": len(hit.get("title", "")) > 80
                or len(hit.get("book_title", "")) > 60,
            }
        )
    output["returned_hits"] = len(output["hits"])
    output["omitted_hits"] = len(original) - len(output["hits"])
    output["truncated"] = bool(output["omitted_hits"]) or any(
        hit["preview_truncated"] or hit["context_omitted"] or hit["metadata_truncated"]
        for hit in output["hits"]
    )
    # Bound the indented JSON emitted by MCP, including provenance and explicit omissions.
    while len(json.dumps(output, ensure_ascii=False, indent=2)) >= 5800:
        if not output["hits"]:
            break
        output["hits"].pop()
        output.update(
            returned_hits=len(output["hits"]),
            omitted_hits=len(original) - len(output["hits"]),
            truncated=True,
        )
    return output


def indexed_version(run):
    result = run.get("result") or {}
    publication = result.get("codex_publication_sha256")
    chapters = (result.get("codex_reading_projection") or {}).get("chapters_sha256")
    if (
        result.get("published")
        and result.get("content_acceptance") != "workflow_test"
        and publication
        and chapters
        and result.get("codex_indexed_publication_sha256") == publication
        and result.get("codex_indexed_chapters_sha256") == chapters
    ):
        return {"publication_sha256": publication, "chapters_sha256": chapters}
    return None


def current_source_conditions(projection, source, *, indexed=False):
    """Compare both immutable source versions; an old index never makes old cards current."""
    conditions = [
        source.c.result["published"].astext == "true",
        func.coalesce(source.c.result["content_acceptance"].astext, "") != "workflow_test",
        projection.c.summary["publication_sha256"].astext
        == source.c.result["codex_publication_sha256"].astext,
        projection.c.summary["chapters_sha256"].astext
        == source.c.result["codex_reading_projection"]["chapters_sha256"].astext,
        current_external_sources(projection),
    ]
    if indexed:
        conditions.extend(
            [
                projection.c.summary["publication_sha256"].astext
                == source.c.result["codex_indexed_publication_sha256"].astext,
                projection.c.summary["chapters_sha256"].astext
                == source.c.result["codex_indexed_chapters_sha256"].astext,
            ]
        )
    return conditions


def current_external_sources(projection):
    """A cited external publication also becomes historical after its source changes."""
    external = (
        func.jsonb_array_elements(
            func.coalesce(
                projection.c.summary["external_source_versions"], func.jsonb_build_array()
            )
        )
        .table_valued(column("value", JSONB))
        .alias()
    )
    source, chapter, book = db.runs.alias(), db.chapters.alias(), db.books.alias()
    metadata = external.c.value
    stale = (
        select(1)
        .select_from(
            external.outerjoin(
                source, source.c.id == metadata["source_run_id"].astext.cast(db.runs.c.id.type)
            )
            .outerjoin(
                chapter, chapter.c.id == metadata["article_id"].astext.cast(db.chapters.c.id.type)
            )
            .outerjoin(book, book.c.id == source.c.book_id)
        )
        .where(
            or_(
                source.c.id.is_(None),
                chapter.c.id.is_(None),
                book.c.state.in_(["deleted", "deleting", "delete_failed"]),
                source.c.book_id.is_distinct_from(
                    metadata["book_id"].astext.cast(db.books.c.id.type)
                ),
                chapter.c.run_id.is_distinct_from(source.c.id),
                source.c.result["codex_publication_sha256"].astext.is_distinct_from(
                    metadata["publication_sha256"].astext
                ),
                chapter.c.content["sha256"].astext.is_distinct_from(
                    metadata["content_sha256"].astext
                ),
            )
        )
        .correlate(projection)
    )
    return not_(exists(stale))


def visible_research_run(workspace_id):
    """Keep the previous complete revision visible while its replacement is being reviewed."""
    candidate, source = db.research_runs.alias(), db.runs.alias()
    return (
        select(candidate.c.id)
        .join(source, source.c.id == candidate.c.source_run_id)
        .where(
            candidate.c.book_id == db.books.c.id,
            candidate.c.workspace_id == workspace_id,
            source.c.result["codex_workspace_id"].astext == workspace_id,
            candidate.c.state != "cancelled",
            *current_source_conditions(candidate, source),
        )
        .order_by(
            case((candidate.c.state == "completed", 0), else_=1),
            candidate.c.summary["created_at"].astext.cast(Float).desc(),
            candidate.c.id.desc(),
        )
        .limit(1)
        .correlate(db.books)
        .scalar_subquery()
    )


def publication_snapshot(settings, engine, store, objects, run, *, article_ids=None):
    """Freeze complete article objects, never a retrieval-window reconstruction."""
    run = require_workspace(engine, run, store)
    version = indexed_version(run)
    if not version:
        raise ServiceError(409, "本版本原文尚未完成发布和检索索引。")
    with engine.begin() as connection:
        current = (
            connection.execute(
                select(db.runs).where(db.runs.c.id == run["id"]).with_for_update(read=True)
            )
            .mappings()
            .one_or_none()
        )
        if current is None or indexed_version(current) != version:
            raise ServiceError(409, "来源版本在冻结期间已变更，请重新读取。")
        title = connection.scalar(
            select(db.books.c.title).where(
                db.books.c.id == run["book_id"],
                db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
            )
        )
        if title is None:
            raise ServiceError(409, "本书正在删除或已删除，不能继续研究。")
        query = (
            select(db.chapters)
            .where(db.chapters.c.run_id == run["id"])
            .order_by(db.chapters.c.position)
        )
        if article_ids is not None:
            query = query.where(db.chapters.c.id.in_(article_ids))
        rows = connection.execute(query).mappings().all()
    if not rows:
        raise ServiceError(409, "已索引版本没有完整文章对象。")
    articles = []
    for row in rows:
        body = json.loads(objects.read_bytes(row["content"]))
        acceptance = body.get("acceptance") or {}
        publication = acceptance.get("publication") or {}
        if (
            acceptance.get("content_acceptance") == "workflow_test"
            or publication.get("approval_type") == "workflow_test"
            or publication.get("validation_scope") == "workflow_only"
            or publication.get("content_verified") is False
        ):
            raise ServiceError(409, "流程测试发布没有内容认证，不能生成研究卡片。")
        if (
            acceptance.get("content_acceptance") != "machine_or_human"
            or publication.get("markdown_sha256") != version["publication_sha256"]
        ):
            raise ServiceError(409, "完整文章缺少与已索引版本一致的内容认证。")
        text = "".join(part["text"] for part in body["parts"])
        chapter = {**row, "text": text, "parts": body["parts"]}
        parts, cursor = [], 0
        for original in body["parts"]:
            end = cursor + len(original["text"])
            parts.append(
                {
                    **copy.deepcopy(original),
                    "id": original.get("span_id", f"{row['id']}:{cursor}"),
                    "start": cursor,
                    "end": end,
                    "source": {
                        **copy.deepcopy(original["source"]),
                        "chapter_id": str(row["id"]),
                        "reading_start": cursor,
                        "reading_end": end,
                        "publication_start": original.get("start"),
                        "publication_end": original.get("end"),
                    },
                }
            )
            cursor = end
        articles.append(
            {
                "id": str(row["id"]),
                "title": row["title"],
                "kind": row["kind"],
                "text": text,
                "text_sha256": text_digest(text),
                "parts": parts,
                "pages": row["pages"],
                "bibliography": body.get("bibliography", {"status": "unclaimed"}),
                "structure": body.get("structure", []),
                "footnotes": resolve_footnotes(text, parts),
                "reading_target": reading_target(chapter, 0, len(text)) if text else None,
                "content": row["content"],
                "source_file": copy.deepcopy(run["source"]),
                "conversion": copy.deepcopy(run["conversion"]),
                "acceptance": acceptance,
            }
        )
    return {
        "book_id": str(run["book_id"]),
        "book_title": title,
        "source_run_id": str(run["id"]),
        "document_id": run["result"]["codex_document_id"],
        "workspace_id": identity(store)["workspace_id"],
        **version,
        "articles": articles,
        "source": copy.deepcopy(run["source"]),
        "content_verified": True,
        "validation_scope": "content",
    }


class ResearchEvidence:
    """HTTP and MCP share the exact Search.search and canonical reader contracts."""

    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine

    def require_workspace(self, run):
        from hrs.processing.store import Store
        from hrs.processing.workspace import resolve_workspace

        return require_workspace(
            self.engine, run, Store(resolve_workspace(self.settings.codex_root))
        )

    def freeze_source(self, article_id):
        """Server-owned external evidence acquisition; callers cannot supply source text."""
        from hrs.library.object_ownership import owned_objects
        from hrs.processing.store import Store
        from hrs.processing.workspace import resolve_workspace

        with self.engine.connect() as connection:
            source_run_id = connection.scalar(
                select(db.chapters.c.run_id).where(db.chapters.c.id == str(article_id))
            )
        if source_run_id is None:
            raise ServiceError(404, "外书证据文章不存在。")
        store = Store(resolve_workspace(self.settings.codex_root))
        return publication_snapshot(
            self.settings,
            self.engine,
            store,
            owned_objects(self.settings, self.engine),
            get_run(self.engine, source_run_id),
            article_ids=[str(article_id)],
        )

    def search(self, query, book_id=None, *, semantic=True, **options):
        metrics = {}
        try:
            scope = self.article_scope(book_id)
            requested = options.pop("chapter_ids", None)
            options["chapter_ids"] = (
                sorted(scope.intersection(map(str, requested)))
                if requested is not None
                else sorted(scope)
            )
            if not options["chapter_ids"]:
                return {
                    "query": query,
                    "backend": "hybrid" if semantic else "lexical",
                    "available": True,
                    "hits": [],
                    "evidence_status": "no_published_sources"
                    if not scope
                    else "no_matching_sources",
                    "metrics": {"retrieval_executed": False, "scope_articles": 0},
                }
            retrieval = Search(self.settings, self.engine)
            if not retrieval.client.indices.exists(index=self.settings.opensearch_index):
                raise Problem(
                    "research_index_unavailable",
                    "研究证据检索索引尚不可用。",
                    status=503,
                    retryable=True,
                )
            hits = retrieval.search(query, book_id, semantic=semantic, metrics=metrics, **options)
        except Problem:
            raise
        except Exception as error:  # noqa: BLE001 - present an explicit unavailable backend
            raise Problem(
                "research_retrieval_unavailable",
                "研究证据检索不可用，请检查 PostgreSQL、OpenSearch 与检索模型服务。",
                status=503,
                retryable=True,
            ) from error
        return {
            "query": query,
            "backend": "hybrid" if semantic else "lexical",
            "available": True,
            "hits": hits,
            "evidence_status": metrics.get("evidence_status", "unassessed"),
            "metrics": metrics,
        }

    def article_scope(self, book_id=None):
        from hrs.processing.store import Store
        from hrs.processing.workspace import resolve_workspace

        store = Store(resolve_workspace(self.settings.codex_root))
        with self.engine.connect() as connection:
            runs = (
                connection.execute(
                    select(db.runs)
                    .join(db.books)
                    .where(
                        workspace_runs(store),
                        db.runs.c.kind == "book",
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                        *([db.runs.c.book_id == str(book_id)] if book_id else []),
                    )
                )
                .mappings()
                .all()
            )
        accepted = []
        for run in runs:
            if not indexed_version(run):
                continue
            try:
                require_workspace(self.engine, run, store)
            except WrongWorkspace:
                continue
            accepted.append(run["id"])
        with self.engine.connect() as connection:
            return set(
                map(
                    str,
                    connection.scalars(
                        select(db.chapters.c.id).where(db.chapters.c.run_id.in_(accepted))
                    ),
                )
            )

    def entities(self, query="", book_id=None, limit=20, *, entity_ids=None):
        """Query bounded validated identities in SQL, including literal Chinese aliases."""
        from hrs.processing.store import Store
        from hrs.processing.workspace import resolve_workspace

        if (
            not isinstance(query, str)
            or len(query) > 1000
            or type(limit) is not int
            or not 1 <= limit <= 50
        ):
            raise ServiceError(400, "实体检索词或结果数量无效。")
        store = Store(resolve_workspace(self.settings.codex_root))
        conditions = [
            db.research_runs.c.workspace_id == identity(store)["workspace_id"],
            db.research_entities.c.summary["state"].astext.in_(["verified", "human_accepted"]),
            db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
            *current_source_conditions(db.research_runs, db.runs, indexed=True),
            db.research_runs.c.id == visible_research_run(identity(store)["workspace_id"]),
        ]
        if book_id:
            conditions.append(db.research_entities.c.book_id == str(book_id))
        if entity_ids is not None:
            conditions.append(db.research_entities.c.id.in_(entity_ids))
        if query:
            pattern = (
                "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            conditions.append(
                or_(
                    db.research_entities.c.name.ilike(pattern, escape="\\"),
                    db.research_entities.c.search_text.ilike(pattern, escape="\\"),
                    func.to_tsvector(
                        literal_column("'simple'::regconfig"), db.research_entities.c.search_text
                    ).op("@@")(func.plainto_tsquery(literal_column("'simple'::regconfig"), query)),
                )
            )
        relation = (
            db.research_entities.join(db.research_runs)
            .join(db.runs, db.runs.c.id == db.research_runs.c.source_run_id)
            .join(db.books, db.books.c.id == db.research_entities.c.book_id)
        )
        with self.engine.connect() as connection:
            return list(
                connection.scalars(
                    select(db.research_entities.c.summary)
                    .select_from(relation)
                    .where(*conditions)
                    .order_by(
                        case((db.research_entities.c.name == query, 0), else_=1),
                        db.research_entities.c.name,
                        db.research_entities.c.id,
                    )
                    .limit(limit)
                )
            )

    def require_entity(self, entity_id):
        """Authorize graph/detail tools through the same current-source SQL identity scope."""
        rows = self.entities(limit=1, entity_ids=[str(entity_id)])
        if not rows:
            raise ServiceError(404, "实体来源已更新、删除或尚未核验。")
        return rows[0]

    def authorized_entity_ids(self, entity_ids):
        identities = sorted(set(map(str, entity_ids)))
        authorized = set()
        for offset in range(0, len(identities), 50):
            authorized.update(
                row["id"]
                for row in self.entities(limit=50, entity_ids=identities[offset : offset + 50])
            )
        return authorized

    def read_source(
        self,
        article_id=None,
        start=0,
        end=None,
        *,
        chapter_id=None,
        max_chars=16000,
        expected_sha256=None,
    ):
        identity = article_id or chapter_id
        if not identity:
            raise ServiceError(400, "需要完整文章 ID。")
        if type(start) is not int or type(max_chars) is not int or not 1 <= max_chars <= 60000:
            raise ServiceError(400, "原文范围或读取长度无效。")
        chapter = Library(self.settings, self.engine).chapter(identity)
        if expected_sha256 and chapter["text_sha256"] != expected_sha256:
            raise ServiceError(409, "原文版本已变更，请重新获取证据。")
        stop = min(len(chapter["text"]), start + max_chars) if end is None else end
        if (
            type(stop) is not int
            or not 0 <= start < stop <= len(chapter["text"])
            or stop - start > max_chars
        ):
            raise ServiceError(400, "原文范围或读取长度无效。")
        run = get_run(self.engine, str(chapter["run_id"]))
        self.require_workspace(run)
        parts, cursor = [], 0
        for part in chapter["parts"]:
            right = cursor + len(part["text"])
            if cursor < stop and right > start:
                parts.append(
                    {"start": max(start, cursor), "end": min(stop, right), "source": part["source"]}
                )
            cursor = right
        return {
            "article_id": str(chapter["id"]),
            "book_id": str(chapter["book_id"]),
            "source_run_id": str(chapter["run_id"]),
            "publication_sha256": (run["result"] or {}).get("codex_publication_sha256"),
            "text": chapter["text"][start:stop],
            "start": start,
            "end": stop,
            "total": len(chapter["text"]),
            "text_sha256": chapter["text_sha256"],
            "reading_target": reading_target(chapter, start, stop),
            "parts": parts,
            "footnotes": [
                note
                for note in chapter.get("footnotes", [])
                if any(
                    span.get("start", stop) < stop and span.get("end", start) > start
                    for span in [note.get("note", {}), *note.get("references", [])]
                )
            ],
        }
