"""Build embeddings and atomically publish complete index generations."""

import json
import logging

from opensearchpy import RequestError, helpers
from sqlalchemy import func, insert, select, update

from hrs.library.books import get_run
from hrs.library.catalog import Library
from hrs.library.chapter_content import chapter_content
from hrs.library.navigation_projection import (
    book_locators,
    book_navigation,
    book_source_structure,
    chapter_signature,
)
from hrs.library.runs.outputs import Outputs, fingerprint
from hrs.library.structure import POLICY as STRUCTURE_POLICY
from hrs.library.structure import project_source_structure, project_structure
from hrs.search.chunks import CHUNK_RULE, VECTOR_INPUT_RULE, indexable_chunks
from hrs.search.compute import (
    EMBEDDING_IDENTITY,
    RetrievalCompute,
    measured,
    normalizer,
    search_client,
)
from hrs.search.inputs import INPUT_RULE, bounded_chunks
from hrs.search.navigation import lexical_text, navigation_fields
from hrs.search.projection import PROJECTION_RULE
from hrs.storage import models as db

logger = logging.getLogger(__name__)
EMBEDDING_BATCH = 8
INDEX_RULE = "complete-source-generation-v3"


def index_rules(settings):
    return {
        "index_rule": INDEX_RULE,
        "chunk_rule": CHUNK_RULE,
        "input_rule": INPUT_RULE,
        "vector_rule": VECTOR_INPUT_RULE,
        "projection_rule": PROJECTION_RULE,
        "structure_policy": STRUCTURE_POLICY,
        "embedding": {**EMBEDDING_IDENTITY, "device": settings.retrieval_device},
    }


def current_index_matches(settings, run, chapters, book_title):
    result = run.get("result") or {}
    manifest = result.get("retrieval_manifest") or {}
    return bool(
        manifest
        and fingerprint(manifest) == result.get("retrieval_generation")
        and manifest.get("source") == source_manifest(run, chapters, book_title)
        and manifest.get("rules") == index_rules(settings)
        and all(result.get(key) == value for key, value in indexed_fields(manifest).items())
    )


def source_manifest(run, chapters, book_title):
    """SQL-visible publication identities compared again at the activation seam."""
    result = run.get("result") or {}
    projection = result.get("codex_reading_projection") or {}
    return {
        "book_id": str(run["book_id"]),
        "book_title": book_title,
        "source_sha256": (run.get("source") or {}).get("sha256"),
        "publication_sha256": result.get("codex_publication_sha256"),
        "projection_policy": result.get("codex_projection_policy"),
        "organization_sha256": result.get("codex_organization_sha256"),
        "chapters_sha256": chapter_signature(chapters),
        "chapters": [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "content_sha256": row["content"]["sha256"],
            }
            for row in sorted(chapters, key=lambda row: str(row["id"]))
        ],
        "navigation_sha256": result.get("codex_projected_navigation_sha256"),
        "navigation_object_sha256": (projection.get("navigation") or {}).get("sha256"),
        "structure_sha256": projection.get("structure_sha256"),
        "structure_version": projection.get("structure_version", 0),
        "structure_object_sha256": (projection.get("source_structure") or {}).get("sha256"),
        "locators_sha256": (projection.get("locators") or {}).get("sha256"),
        "conversion_sha256": (run.get("conversion") or {}).get("sha256"),
    }


def indexed_fields(manifest):
    source, rules = manifest["source"], manifest["rules"]
    return {
        "codex_indexed_publication_sha256": source["publication_sha256"],
        "codex_indexed_projection_policy": source["projection_policy"],
        "codex_indexed_navigation_sha256": source["navigation_sha256"],
        "codex_indexed_chapters_sha256": source["chapters_sha256"],
        "codex_indexed_structure_sha256": source["structure_sha256"],
        "codex_indexed_chunk_rule": rules["chunk_rule"],
        "codex_indexed_input_rule": rules["input_rule"],
        "codex_indexed_vector_rule": rules["vector_rule"],
        "codex_indexed_manifest_rule": rules["index_rule"],
    }


class BookIndexer:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.client = search_client(settings.opensearch_url)
        self.library, self.outputs = Library(settings, engine), Outputs(settings, engine)
        self.runtime = RetrievalCompute(settings)

    def embed_cached(self, run_id, chunks, metrics):
        identity = {
            **EMBEDDING_IDENTITY,
            "device": self.settings.retrieval_device,
            "projection_rule": VECTOR_INPUT_RULE,
        }
        prefix = "retrieval-embeddings:" + fingerprint(identity) + ":"
        wanted = {
            fingerprint(row["retrieval_text"]): row["retrieval_text"]
            for row in chunks
            if row.get("model_eligible", True)
        }
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
                cached.update(
                    {key: value for key, value in saved["vectors"].items() if key in wanted}
                )
                model = saved["model"]
        metrics["reused_vectors"] = len(cached)
        metrics["computed_vectors"] = 0
        missing = [key for key in wanted if key not in cached]
        if missing:
            tokenizer = self.runtime.tokenizer("BAAI/bge-m3")
            missing.sort(key=lambda key: len(tokenizer.encode(wanted[key]).ids))
        for offset in range(0, len(missing), EMBEDDING_BATCH):
            keys = missing[offset : offset + EMBEDDING_BATCH]
            with measured(metrics, "embedding_ms"):
                result = self.runtime.compute("embed", [wanted[key] for key in keys])
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
            "chunks": [
                {
                    **row,
                    **(
                        {"vector": cached[fingerprint(row["retrieval_text"])]}
                        if row.get("model_eligible", True)
                        else {}
                    ),
                }
                for row in chunks
            ],
            "model": model,
        }

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
        if any(str(row["run_id"]) != str(run_id) for row in chapters):
            raise ValueError("Only the book's current source publication can be indexed")
        projection = (run.get("result") or {}).get("codex_reading_projection") or {}
        if projection.get("chapters_sha256") and projection["chapters_sha256"] != chapter_signature(
            chapters
        ):
            raise ValueError("Published chapter identities do not match the source publication")
        with self.engine.connect() as connection:
            book_title = connection.scalar(
                select(db.books.c.title).where(db.books.c.id == run["book_id"])
            )
        structure = (
            self.library.structure(run_id)
            if run["conversion"] and run["result"].get("review_initialized")
            else {"available": False, "items": []}
        )
        source = source_manifest(run, chapters, book_title)
        rules = index_rules(self.settings)
        snapshot = book_source_structure(self.settings, run, chapters)
        navigation = book_navigation(self.settings, run, chapters)
        # Mutable navigation contributes to the generation, never the chunk/vector plan.
        plan = {
            "book_id": source["book_id"],
            "book_title": book_title,
            "chapters": source["chapters"],
            "rules": rules,
            "source_structure": fingerprint(snapshot),
            "reading_structure": fingerprint(structure),
        }
        manifest = {"source": source, "rules": rules, "plan_sha256": fingerprint(plan)}
        generation = fingerprint(manifest)
        step = "retrieval-chunks:" + manifest["plan_sha256"]
        saved = self.outputs.get(run_id, step, plan)
        if saved is None:
            with measured(metrics, "chunking_ms"):
                tokenizer = self.runtime.tokenizer("BAAI/bge-m3")
                chunks = []
                for row in chapters:
                    chapter = {
                        **row,
                        **chapter_content(self.settings, row["content"]),
                        "navigation": navigation,
                    }
                    chapter["source_structure"] = project_source_structure(
                        snapshot, chapter["parts"]
                    )
                    # Refresh source-checked relations without republishing canonical chapters.
                    if structure["available"]:
                        chapter = {
                            **chapter,
                            "structure": project_structure(structure, chapter["parts"]),
                        }
                    chunks.extend(
                        bounded_chunks(chapter, indexable_chunks(chapter, book_title), tokenizer)
                    )
            saved = self.embed_cached(run_id, chunks, metrics)
            with measured(metrics, "checkpoint_ms"):
                saved = self.outputs.put(run_id, step, saved, plan)
        else:
            metrics.update(
                reused_vectors=sum(row.get("model_eligible", True) for row in saved["chunks"]),
                computed_vectors=0,
            )
        metrics["chunks"] = len(saved["chunks"])
        with measured(metrics, "index_write_ms"):
            self.publish_index(run_id, run, generation, saved, chapters, manifest, navigation)
        return {"run_id": run_id, "chunks": len(saved["chunks"]), "generation": generation}

    def publish_index(self, run_id, run, generation, saved, chapter_rows, manifest, navigation):
        name = self.settings.opensearch_index
        self.ensure_index(name)
        metadata = {str(row["id"]): row for row in chapter_rows}
        chapters = {
            body["id"]: {**metadata[body["id"]], **body, "navigation": navigation}
            for body in book_locators(self.settings, run, chapter_rows)
        }

        def records():
            for row in saved["chunks"]:
                source = {**row, **navigation_fields(chapters, row)}
                yield {
                    "_index": name,
                    "_id": generation + ":" + row["id"],
                    "_source": {
                        **source,
                        "generation": generation,
                        "text_search": lexical_text(source),
                        "text_aliases": normalizer().convert(lexical_text(source)),
                        "title_search": row["title"],
                        "title_aliases": normalizer().convert(row["title"]),
                        "raw_text_search": row.get("raw_text", row["text"]),
                        "raw_text_aliases": normalizer().convert(row.get("raw_text", row["text"])),
                    },
                }

        written, failed = helpers.bulk(
            self.client,
            records(),
            chunk_size=100,
            refresh="wait_for",
        )
        if failed or written != len(saved["chunks"]):
            raise ValueError("Search bulk did not acknowledge the complete generation")
        self.require_complete_index(run_id, generation, len(saved["chunks"]))
        # This immutable receipt is repeatable after a lost response or a failed SQL commit.
        self.outputs.put(
            run_id,
            "retrieval-generation:" + generation,
            {
                "manifest": manifest,
                "chunks": len(saved["chunks"]),
                "ids_sha256": fingerprint([row["id"] for row in saved["chunks"]]),
            },
            manifest,
        )
        self.activate(run, generation, manifest)

    def require_complete_index(self, run_id, generation, count):
        actual = self.client.count(
            index=self.settings.opensearch_index,
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"run_id": str(run_id)}},
                            {"term": {"generation": generation}},
                        ]
                    }
                }
            },
        )["count"]
        if actual != count:
            raise ValueError("Search generation is incomplete")

    def activate(self, run, generation, manifest):
        """Commit one source-checked generation; all network I/O precedes this transaction."""
        from hrs.library.deletion import require_active

        with self.engine.begin() as connection:
            require_active(connection, run["book_id"])
            current = (
                connection.execute(
                    select(db.runs).where(db.runs.c.id == run["id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            visible = list(
                connection.execute(
                    select(db.chapters).where(db.chapters.c.book_id == run["book_id"])
                ).mappings()
            )
            title = connection.scalar(
                select(db.books.c.title).where(db.books.c.id == run["book_id"])
            )
            if (
                not (current["result"] or {}).get("published")
                or source_manifest(current, visible, title) != manifest["source"]
            ):
                raise ValueError("Publication changed while building the search generation")
            result = current["result"] or {}
            active = result.get("retrieval_generation")
            if active not in {(run["result"] or {}).get("retrieval_generation"), generation}:
                raise ValueError("Active search generation changed before activation")
            changed = active != generation
            indexed = {
                **result,
                **indexed_fields(manifest),
                "bridge_failures": 0,
                "bridge_not_before": 0,
                "retrieval_generation": generation,
                "retrieval_previous_generation": active
                if changed
                else result.get("retrieval_previous_generation"),
                "retrieval_manifest": manifest,
                "retrieval_chunk_rule": manifest["rules"]["chunk_rule"],
                "retrieval_input_rule": manifest["rules"]["input_rule"],
                "retrieval_chapters_sha256": manifest["source"]["chapters_sha256"],
            }
            native = bool(manifest["source"]["publication_sha256"])
            if (
                native
                and changed
                and result.get("codex_projected_sql_revision") == current["revision"]
            ):
                indexed["codex_projected_sql_revision"] = current["revision"] + 1
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run["id"])
                .values(
                    result=indexed,
                    **(
                        {
                            "state": "completed",
                            "stage": "complete",
                            "error": None,
                            "revision": db.runs.c.revision + int(changed),
                            "updated_at": func.now(),
                        }
                        if native
                        else {}
                    ),
                )
            )
            if native:
                if changed:
                    for kind, payload in (
                        (
                            "run.changed",
                            {
                                "state": "completed",
                                "stage": "complete",
                                "revision": current["revision"] + 1,
                            },
                        ),
                        (
                            "library.indexed",
                            {
                                "publication_sha256": manifest["source"]["publication_sha256"],
                                "chapters_sha256": manifest["source"]["chapters_sha256"],
                                "navigation_sha256": manifest["source"]["navigation_sha256"],
                                "retrieval_generation": generation,
                            },
                        ),
                    ):
                        connection.execute(
                            insert(db.events).values(
                                book_id=run["book_id"], run_id=run["id"], kind=kind, payload=payload
                            )
                        )
                if result.get("content_acceptance") != "workflow_test":
                    from hrs.research.bridge import enqueue_research_event

                    enqueue_research_event(
                        connection,
                        run["id"],
                        {
                            "publication_sha256": manifest["source"]["publication_sha256"],
                            "chapters_sha256": manifest["source"]["chapters_sha256"],
                        },
                    )
        # Keep complete generations immutable. In-place cleanup can race a concurrent
        # build/rollback; book deletion owns physical removal of retained index records.

    def refresh_navigation(self, run_id):
        """Compatibility entry: metadata revisions use the same immutable generation path."""
        return self.index(run_id)

    def rollback_generation(self, run_id, expected_generation):
        """CAS-select the retained complete generation of this exact source publication."""
        run = get_run(self.engine, run_id)
        result = run.get("result") or {}
        target = result.get("retrieval_previous_generation")
        if result.get("retrieval_generation") != expected_generation or not target:
            raise ValueError("Active generation changed or no previous generation is retained")
        saved = self.outputs.get(run_id, "retrieval-generation:" + target)
        if not saved or fingerprint(saved["manifest"]) != target:
            raise ValueError("Previous generation has no complete manifest")
        self.require_complete_index(run_id, target, saved["chunks"])
        manifest = saved["manifest"]
        from hrs.library.deletion import require_active

        with self.engine.begin() as connection:
            require_active(connection, run["book_id"])
            current = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            result = current["result"] or {}
            rows = list(
                connection.execute(
                    select(db.chapters).where(db.chapters.c.book_id == run["book_id"])
                ).mappings()
            )
            if (
                result.get("retrieval_generation") != expected_generation
                or result.get("retrieval_previous_generation") != target
                or not result.get("published")
                or result.get("codex_publication_sha256")
                != manifest["source"]["publication_sha256"]
                or (current.get("source") or {}).get("sha256")
                != manifest["source"]["source_sha256"]
                or chapter_signature(rows) != manifest["source"]["chapters_sha256"]
            ):
                raise ValueError("Source publication or active generation changed before rollback")
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(
                    result={
                        **result,
                        **indexed_fields(manifest),
                        "retrieval_generation": target,
                        "retrieval_previous_generation": expected_generation,
                        "retrieval_manifest": manifest,
                        "retrieval_chunk_rule": manifest["rules"]["chunk_rule"],
                        "retrieval_input_rule": manifest["rules"]["input_rule"],
                        "retrieval_chapters_sha256": manifest["source"]["chapters_sha256"],
                    }
                )
            )
            connection.execute(
                insert(db.events).values(
                    run_id=run_id,
                    book_id=run["book_id"],
                    kind="library.index_rolled_back",
                    payload={
                        "previous_generation": expected_generation,
                        "retrieval_generation": target,
                    },
                )
            )
        return {
            "run_id": run_id,
            "generation": target,
            "previous_generation": expected_generation,
            "chunks": saved["chunks"],
        }

    def ensure_index(self, name):
        if not self.client.indices.exists(index=name):
            try:
                self.client.indices.create(
                    index=name,
                    body={
                        "settings": {
                            "index": {"knn": True, "number_of_shards": 1, "number_of_replicas": 0}
                        },
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
                    "text_aliases": {"type": "text", "analyzer": "cjk"},
                    "title_aliases": {"type": "text", "analyzer": "cjk"},
                    "raw_text_search": {"type": "text", "analyzer": "cjk"},
                    "raw_text_aliases": {"type": "text", "analyzer": "cjk"},
                    "model_eligible": {"type": "boolean"},
                    "retrieval_text": {"type": "text", "index": False},
                    "context": {"type": "object", "enabled": False},
                    "ranges": {"type": "object", "enabled": False},
                    "snippet_segments": {"type": "object", "enabled": False},
                    "reading_targets": {"type": "object", "enabled": False},
                    "snippet": {"type": "text", "index": False},
                    "role": {"type": "keyword"},
                    "layer": {"type": "keyword"},
                    "source_scope": {"type": "keyword"},
                    "doc_kind": {"type": "keyword"},
                    "toc_entry_id": {"type": "keyword"},
                    "toc_path": {"type": "keyword"},
                    "printed_pages": {"type": "object", "enabled": False},
                }
            },
        )
