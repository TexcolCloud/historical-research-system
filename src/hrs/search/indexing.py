"""Build embeddings and atomically publish complete index generations."""

import json
import logging

from opensearchpy import RequestError, helpers
from sqlalchemy import select, update

from hrs.library.books import get_run
from hrs.library.catalog import Library
from hrs.library.runs.outputs import Outputs, fingerprint
from hrs.library.structure import POLICY as STRUCTURE_POLICY
from hrs.library.structure import project_structure
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
from hrs.storage import models as db

logger = logging.getLogger(__name__)
EMBEDDING_BATCH = 8


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
                {**row, "vector": cached[fingerprint(row["retrieval_text"])]} for row in chunks
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
        with self.engine.connect() as connection:
            book_title = connection.scalar(
                select(db.books.c.title).where(db.books.c.id == run["book_id"])
            )
        structure = (
            self.library.structure(run_id)
            if run["conversion"] and run["result"].get("review_initialized")
            else {"available": False, "items": []}
        )
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
            "embedding": {**EMBEDDING_IDENTITY, "device": self.settings.retrieval_device},
        }
        generation = fingerprint(dependency)
        step = "retrieval-chunks:" + generation
        saved = self.outputs.get(run_id, step, dependency)
        if saved is None:
            with measured(metrics, "chunking_ms"):
                tokenizer = self.runtime.tokenizer("BAAI/bge-m3")
                chunks = []
                for row in chapters:
                    chapter = self.library.chapter(row["id"])
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
                saved = self.outputs.put(run_id, step, saved, dependency)
        else:
            metrics.update(reused_vectors=len(saved["chunks"]), computed_vectors=0)
        metrics["chunks"] = len(saved["chunks"])
        with measured(metrics, "index_write_ms"):
            self.publish_index(run_id, run, generation, saved, chapters)
        return {"run_id": run_id, "chunks": len(saved["chunks"]), "generation": generation}

    def publish_index(self, run_id, run, generation, saved, chapter_rows):
        from hrs.library.navigation_projection import chapter_signature

        name = self.settings.opensearch_index
        self.ensure_index(name)
        chapters = self.navigation_chapters(run, chapter_rows)
        expected_chapters = chapter_signature(chapter_rows)

        def records():
            for row in saved["chunks"]:
                source = {**row, **navigation_fields(chapters, row)}
                yield {
                    "_index": name,
                    "_id": generation + ":" + row["id"],
                    "_source": {
                        **source,
                        "generation": generation,
                        "text_search": normalizer().convert(lexical_text(source)),
                        "title_search": normalizer().convert(row["title"]),
                    },
                }

        helpers.bulk(
            self.client,
            records(),
            chunk_size=100,
            refresh="wait_for",
        )
        # Readers select only the SQL-committed generation. Failed bulk writes stay invisible.
        from hrs.library.deletion import require_active

        with self.engine.begin() as connection:
            require_active(connection, run["book_id"])
            current = connection.scalar(
                select(db.runs.c.result).where(db.runs.c.id == run_id).with_for_update()
            )
            visible = list(
                connection.execute(
                    select(db.chapters).where(db.chapters.c.book_id == run["book_id"])
                ).mappings()
            )
            if chapter_signature(visible) != expected_chapters:
                raise ValueError("Publication changed while building the search generation")
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(
                    result={
                        **(current or {}),
                        "retrieval_generation": generation,
                        "retrieval_chunk_rule": CHUNK_RULE,
                        "retrieval_input_rule": INPUT_RULE,
                        "retrieval_chapters_sha256": expected_chapters,
                    }
                )
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

    def navigation_chapters(self, run, rows=None):
        from hrs.library.navigation_projection import book_locators, book_navigation

        rows = self.library.chapters(run["book_id"]) if rows is None else rows
        navigation = book_navigation(self.settings, run, rows)
        metadata = {str(row["id"]): row for row in rows}
        return {
            body["id"]: {**metadata[body["id"]], **body, "navigation": navigation}
            for body in book_locators(self.settings, run, rows)
        }

    def refresh_navigation(self, run_id):
        """Update the active generation's source-bound navigation, with no model calls."""
        run = get_run(self.engine, run_id)
        generation = run["result"].get("retrieval_generation")
        if (
            not generation
            or run["result"].get("retrieval_chunk_rule") != CHUNK_RULE
            or run["result"].get("retrieval_input_rule") != INPUT_RULE
            or not self.client.indices.exists(index=self.settings.opensearch_index)
            or run["result"].get("retrieval_chapters_sha256")
            != (run["result"].get("codex_reading_projection") or {}).get("chapters_sha256")
        ):
            return self.index(run_id)
        chapters = self.navigation_chapters(run)
        changes, count = [], 0
        for hit in helpers.scan(
            self.client,
            index=self.settings.opensearch_index,
            query={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"run_id": str(run_id)}},
                            {"term": {"generation": generation}},
                        ]
                    }
                },
                "_source": {"excludes": ["vector"]},
            },
        ):
            row = hit["_source"]
            fields = navigation_fields(chapters, row)
            fields["text_search"] = normalizer().convert(lexical_text({**row, **fields}))
            count += 1
            if any(row.get(key) != value for key, value in fields.items()):
                changes.append(
                    {
                        "_op_type": "update",
                        "_index": self.settings.opensearch_index,
                        "_id": hit["_id"],
                        "doc": fields,
                    }
                )
        if changes:
            helpers.bulk(self.client, changes, chunk_size=100, refresh="wait_for")
        current = get_run(self.engine, run_id)["result"]
        if current.get("retrieval_generation") != generation or current.get(
            "codex_projected_navigation_sha256"
        ) != run["result"].get("codex_projected_navigation_sha256"):
            raise ValueError("Published navigation changed while refreshing search metadata")
        return {
            "run_id": run_id,
            "chunks": count,
            "generation": generation,
            "updated_chunks": len(changes),
            "computed_vectors": 0,
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
                    "retrieval_text": {"type": "text", "index": False},
                    "context": {"type": "object", "enabled": False},
                    "doc_kind": {"type": "keyword"},
                    "toc_entry_id": {"type": "keyword"},
                    "toc_path": {"type": "keyword"},
                    "printed_pages": {"type": "object", "enabled": False},
                }
            },
        )
