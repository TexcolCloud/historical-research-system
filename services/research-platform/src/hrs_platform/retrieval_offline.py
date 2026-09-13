"""Source-bound development evaluation in a disposable index, never publication."""

import hashlib
from uuid import uuid4

from opensearchpy import helpers

from .outputs import fingerprint
from .retrieval_chunks import retrieval_chunks
from .retrieval_evaluation import evaluate_cases
from .retrieval_inputs import bounded_chunks
from .search import Search, normalizer, query_vector


def evaluate_offline(settings, engine, dataset):
    chapters = {c["id"]: c for c in dataset["chapters"]}
    if not chapters or len(chapters) != len(dataset["chapters"]):
        raise ValueError("Offline chapters must be nonempty and unique")
    for chapter in chapters.values():
        evidence = chapter["development_review"]
        if (
            evidence.get("human_review") is not False
            or not evidence.get("original_first")
            or evidence.get("text_sha256") != hashlib.sha256(chapter["text"].encode()).hexdigest()
            or not evidence.get("image_sha256")
        ):
            raise ValueError(
                "Offline samples require original-first development evidence and matching text hashes"
            )
    book_ids = {c["book_id"] for c in chapters.values()}
    if len(book_ids) != 1:
        raise ValueError("One offline dataset must describe one book")
    book_id = next(iter(book_ids))
    index = "hrs-offline-" + uuid4().hex
    search = Search(settings.model_copy(update={"opensearch_index": index}), engine)
    # Only this private instance can see development samples. No runs/chapters are written.
    search.active_scope = lambda _: [{"term": {"book_id": book_id}}]
    search.library.chapter = chapters.__getitem__
    try:
        search.ensure_index(index)
        chunks = []
        for chapter in chapters.values():
            chunks.extend(
                bounded_chunks(
                    chapter, retrieval_chunks(chapter, dataset["title"]), search.tokenizer("BAAI/bge-m3")
                )
            )
        for offset in range(0, len(chunks), 8):
            batch = chunks[offset : offset + 8]
            vectors = search.compute("embed", [c["retrieval_text"] for c in batch])["vectors"]
            helpers.bulk(
                search.client,
                [
                    {
                        "_index": index,
                        "_id": c["id"],
                        "_source": {
                            **c,
                            "vector": v,
                            "title": chapters[c["chapter_id"]]["title"],
                            "title_search": normalizer().convert(c["title"]),
                            "text_search": normalizer().convert(c["retrieval_text"]),
                        },
                    }
                    for c, v in zip(batch, vectors, strict=True)
                ],
                refresh=True,
            )
        results = {}
        variants = dataset.get('variants') or [
            ("lexical", False, {}),
            ("previous_hybrid", True, {"fusion": False, "candidate_limit": 20, "rerank_limit": 40}),
            ("hybrid", True, {}),
            ("hybrid_50", True, {"rerank_limit": 50}),
            ("hybrid_reserved", True, {"lane_quota": 3}),
        ]
        for name, semantic, options in variants:
            query_vector.cache_clear()
            results[name] = evaluate_cases(
                search,
                index,
                book_id,
                dataset["cases"],
                chapters.__getitem__,
                semantic=semantic,
                limit=8,
                search_options=options,
            )
        return {
            "dataset_sha256": fingerprint(dataset),
            "development_only": True,
            "production_release_approval": False,
            "results": results,
        }
    finally:
        search.client.indices.delete(index=index, ignore=[404])
