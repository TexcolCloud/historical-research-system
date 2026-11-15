"""Token-bounded retrieval windows; canonical chapter content stays untouched."""

from functools import lru_cache
from hashlib import sha256
from uuid import UUID, uuid5

from tokenizers import Tokenizer

from hrs.search.chunks import blocks, source_excerpt, table_groups
from hrs.search.projection import (
    prepare_projection,
    project_hit,
    projection_reserve,
    slice_segments,
)
from hrs.search.settings import MODEL_REVISIONS

INPUT_RULE = "bge-projection-6144-source-ranges-v5"
INPUT_TOKENS = 6144


@lru_cache(maxsize=4)
def tokenizer_for(root, model):
    tokenizer = Tokenizer.from_file(
        str(root / model.split("/")[-1] / MODEL_REVISIONS[model] / "tokenizer.json")
    )
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def windows(text, count, limit, max_chars=None):
    """Return lossless character ranges, preferring line/sentence boundaries.

    Count the actual assembled model input through the caller's count function.
    Binary search need not find the longest possible window, only a fitting one.
    """
    start = 0
    while start < len(text):
        if (max_chars is None or len(text) - start <= max_chars) and count(text[start:]) <= limit:
            yield start, len(text)
            break
        low, high, end = (
            start + 1,
            min(len(text), start + max_chars) if max_chars else len(text),
            start,
        )
        while low <= high:
            middle = (low + high) // 2
            if count(text[start:middle]) <= limit:
                end, low = middle, middle + 1
            else:
                high = middle - 1
        if end == start:
            raise ValueError("The query or retrieval labels leave no room for source evidence.")
        if end < len(text):
            boundary = max(
                text.rfind(separator, start + (end - start) * 3 // 4, end)
                for separator in ("\n", "。", "；")
            )
            if boundary >= start and count(text[start : boundary + 1]) <= limit:
                end = boundary + 1
        yield start, end
        start = end


def structural_windows(chapter, chunk, count, limit, *, plan=None):
    """Window the mapped visible view, keeping source rows and rowspans atomic."""
    text = chunk["text"]
    if chunk["kind"] == "note":
        import re

        boundaries = [0, *(m.end() for m in re.finditer(r"\n\s*\n", text)), len(text)]
        units = list(zip(boundaries, boundaries[1:]))
    elif chunk["kind"] in {"table", "html_table"}:
        container = next(
            b for b in blocks(chapter["text"]) if b["start"] <= chunk["start"] < b["end"]
        )
        groups, _ = table_groups(chapter["text"], container, 1)
        units = [
            (max(a, chunk["start"]) - chunk["start"], min(b, chunk["end"]) - chunk["start"])
            for a, b in groups
            if a < chunk["end"] and b > chunk["start"]
        ]
    else:
        units = [(0, len(text))]
    for a, b in units:
        if a == b:
            continue
        excerpt = source_excerpt(chapter, chunk["start"] + a, chunk["start"] + b)
        view = project_hit(chapter, excerpt, include_relations=False, plan=plan)
        atomic = chunk["kind"] in {"table", "html_table"} and (
            count(view["snippet"]) > limit or len(view["snippet"]) > 6000
        )
        if not view["snippet"]:
            yield a, b, [], True
            continue
        previous = a
        for x, y in windows(view["snippet"], count, limit, max_chars=6000):
            pieces = slice_segments(view["snippet_segments"], x, y)
            mapped = [s for s in pieces if not s["synthetic"]]
            if atomic or not mapped:
                yield a, b, pieces, True
                continue
            start = min(previous, min(s["start"] for s in mapped) - chunk["start"])
            end = b if y == len(view["snippet"]) else max(s["end"] for s in mapped) - chunk["start"]
            yield start, end, pieces, False
            previous = end


def bounded_chunks(chapter, chunks, tokenizer, limit=INPUT_TOKENS):
    count = lambda text: len(tokenizer.encode(text).ids)
    plan = prepare_projection(chapter)
    for chunk in chunks:
        chunk = project_hit(chapter, chunk, plan=plan)
        if not chunk["model_eligible"]:
            yield chunk
            continue
        if not any(r["usage"] == "primary" for r in chunk["ranges"]):
            continue
        if count(chunk["retrieval_text"]) <= limit and len(chunk["text"]) <= 6000:
            yield chunk
            continue
        context = list(chunk["context"])
        if chunk["kind"] in {"table", "html_table"} and not any(
            c["role"] == "table_header" for c in context
        ):
            _, (a, b) = table_groups(chapter["text"], chunk, len(chunk["text"]))
            context.insert(0, dict(source_excerpt(chapter, a, b), role="table_header"))
        prefix = projection_reserve(chapter, {**chunk, "context": context}, count, limit, plan=plan)
        emitted = set()
        for start, end, model_text, atomic in structural_windows(
            chapter, chunk, lambda text, prefix=prefix: count(prefix + text), limit, plan=plan
        ):
            item = {
                **chunk,
                **source_excerpt(chapter, chunk["start"] + start, chunk["start"] + end),
            }
            item = project_hit(
                chapter,
                {**item, "context": context},
                count=count,
                limit=limit,
                primary_segments=model_text,
                plan=plan,
            )
            if not any(r["usage"] == "primary" for r in item["ranges"]):
                continue
            projection = item["retrieval_text"]
            identity = (
                str(uuid5(UUID(item["id"]), sha256(projection.encode()).hexdigest()))
                if atomic
                else item["id"]
            )
            if identity in emitted:
                continue
            emitted.add(identity)
            # Full supplements remain source-mapped in context and in the reader.
            yield {
                **item,
                "id": identity,
                "atomic_source_oversized": atomic,
            }


def ranking_windows(query, texts, tokenizer, limit=8192):
    count = lambda text: len(tokenizer.encode(query, text).ids)
    if count("") >= limit:
        raise ValueError("The search query exceeds the reranker's token budget.")
    projected, owners = [], []
    for owner, text in enumerate(texts):
        for start, end in windows(text, count, limit):
            projected.append(text[start:end])
            owners.append(owner)
    return projected, owners
