"""Rebuildable OpenSearch index; immutable reviewed chapters remain authoritative."""

from functools import lru_cache
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from opensearchpy import OpenSearch, RequestError, helpers
from sqlalchemy import select, update

from . import schema as db
from .books import get_run
from .domain.settings import MODEL_REVISIONS
from .library import Library
from .outputs import Outputs, fingerprint
from .retrieval_chunks import CHUNK_RULE, expand_hits, retrieval_chunks, source_excerpt


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

    def compute(self, operation, texts, **options):
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
            "embedding": MODEL_REVISIONS["BAAI/bge-m3"],
            "embedding_adapter": "bge-cls-l2-float32-v1",
        }
        generation = fingerprint(dependency)
        step = "retrieval-chunks:" + generation
        saved = self.outputs.get(run_id, step, dependency)
        if saved is None:
            chunks = [
                chunk
                for row in chapters
                for chunk in retrieval_chunks(self.library.chapter(row["id"]), book_title)
            ]
            result = self.compute("embed", [chunk["retrieval_text"] for chunk in chunks])
            saved = self.outputs.put(
                run_id,
                step,
                {
                    "chunks": [
                        {**chunk, "vector": vector}
                        for chunk, vector in zip(chunks, result["vectors"], strict=True)
                    ],
                    "model": result["identity"],
                },
                dependency,
            )
        name = self.settings.opensearch_index
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
        return {"run_id": run_id, "chunks": len(saved["chunks"]), "generation": generation}

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
        semantic=False,
        limit=20,
        candidate_limit=20,
        context_chars=6000,
        total_chars=24000,
    ):
        name = self.settings.opensearch_index
        if not self.client.indices.exists(index=name):
            return []
        scope = self.active_scope(book_id)
        candidates = max(limit, candidate_limit)
        lexical = {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": normalizer().convert(query),
                            "fields": ["title_search^2", "text_search"],
                        }
                    }
                ],
                "filter": scope,
            }
        }
        result = self.client.search(
            index=name, body={"size": candidates, "query": lexical, "_source": {"excludes": ["vector"]}}
        )
        found = {row["_id"]: dict(row["_source"], score=row["_score"]) for row in result["hits"]["hits"]}
        if semantic:
            vector = self.compute("embed", [query], query=True)["vectors"][0]
            knn = {
                "vector": vector,
                "k": candidates,
                **({"filter": {"bool": {"filter": scope}}} if scope else {}),
            }
            more = self.client.search(
                index=name,
                body={
                    "size": candidates,
                    "query": {"knn": {"vector": knn}},
                    "_source": {"excludes": ["vector"]},
                },
            )
            for row in more["hits"]["hits"]:
                found.setdefault(row["_id"], dict(row["_source"], score=row["_score"]))
            rows = list(found.values())
            if rows:
                scores = self.compute(
                    "rerank", [row.get("retrieval_text", row["text"]) for row in rows], query=query
                )["scores"]
                found = {row["id"]: {**row, "score": score} for row, score in zip(rows, scores, strict=True)}
        return expand_hits(list(found.values()), self.library.chapter, limit, context_chars, total_chars)
