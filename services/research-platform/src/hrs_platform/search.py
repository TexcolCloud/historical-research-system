"""Rebuildable OpenSearch index; immutable reviewed chapters remain authoritative."""

from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid5

from langchain_text_splitters import RecursiveCharacterTextSplitter
from opensearchpy import OpenSearch, RequestError, helpers

from .books import get_run
from .library import Library
from .outputs import Outputs


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
        offset, sources = 0, []
        for part in chapter["parts"]:
            part_end = offset + len(part["text"])
            if offset < end and part_end > start:
                sources.append(
                    {
                        "span_id": part["span_id"],
                        "start": part["start"] + max(0, start - offset),
                        "end": part["start"] + min(len(part["text"]), end - offset),
                        "pages": part["source"]["pages"],
                        "unit_start": max(offset, start) - start,
                        "unit_end": min(part_end, end) - start,
                        "source_record": part["source"],
                    }
                )
            offset = part_end
        yield {
            "id": str(uuid5(UUID(chapter["id"]), f"{start}:{end}")),
            "book_id": chapter["book_id"],
            "chapter_id": chapter["id"],
            "run_id": chapter["run_id"],
            "title": chapter["title"],
            "text": item.page_content,
            "start": start,
            "end": end,
            "sources": sources,
            "pages": sorted({page for source in sources for page in source["pages"]}),
        }


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
        chapters = self.library.chapters(run["book_id"])
        if not chapters:
            raise ValueError("Only published chapters can be indexed.")
        saved = self.outputs.get(run_id, "retrieval-chunks")
        if saved is None:
            chunks = [chunk for row in chapters for chunk in chapter_chunks(self.library.chapter(row["id"]))]
            result = self.compute("embed", [chunk["text"] for chunk in chunks])
            saved = self.outputs.put(
                run_id,
                "retrieval-chunks",
                {
                    "chunks": [
                        {**chunk, "vector": vector}
                        for chunk, vector in zip(chunks, result["vectors"], strict=True)
                    ],
                    "model": result["identity"],
                },
                {
                    "chapters": [row["content"]["sha256"] for row in chapters],
                    "chunk_rule": "recursive-1400-160-v1",
                },
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
        helpers.bulk(
            self.client,
            (
                {
                    "_index": name,
                    "_id": row["id"],
                    "_source": {
                        **row,
                        "text_search": normalizer().convert(row["text"]),
                        "title_search": normalizer().convert(row["title"]),
                    },
                }
                for row in saved["chunks"]
            ),
            chunk_size=100,
            refresh="wait_for",
        )
        return {"run_id": run_id, "chunks": len(saved["chunks"])}

    def search(self, query, book_id=None, semantic=False, limit=20):
        name = self.settings.opensearch_index
        if not self.client.indices.exists(index=name):
            return []
        scope = [{"term": {"book_id": str(book_id)}}] if book_id else []
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
            index=name, body={"size": limit, "query": lexical, "_source": {"excludes": ["vector"]}}
        )
        found = {row["_id"]: dict(row["_source"], score=row["_score"]) for row in result["hits"]["hits"]}
        if semantic:
            vector = self.compute("embed", [query], query=True)["vectors"][0]
            knn = {"vector": vector, "k": limit, **({"filter": {"bool": {"filter": scope}}} if scope else {})}
            more = self.client.search(
                index=name,
                body={"size": limit, "query": {"knn": {"vector": knn}}, "_source": {"excludes": ["vector"]}},
            )
            for row in more["hits"]["hits"]:
                found.setdefault(row["_id"], dict(row["_source"], score=row["_score"]))
            rows = list(found.values())
            if rows:
                scores = self.compute("rerank", [row["text"] for row in rows], query=query)["scores"]
                found = {row["id"]: {**row, "score": score} for row, score in zip(rows, scores, strict=True)}
        return sorted(found.values(), key=lambda row: row["score"], reverse=True)[:limit]
