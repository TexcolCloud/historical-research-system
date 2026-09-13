"""Token-bounded retrieval windows; canonical chapter content stays untouched."""

from functools import lru_cache

from tokenizers import Tokenizer

from .domain.settings import MODEL_REVISIONS
from .retrieval_chunks import source_excerpt, table_groups

INPUT_RULE = "bge-projection-6144-essential-context-v2"
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
        low, high, end = start + 1, min(len(text), start + max_chars) if max_chars else len(text), start
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
                text.rfind(separator, start + (end - start) * 3 // 4, end) for separator in ("\n", "。", "；")
            )
            if boundary >= start and count(text[start : boundary + 1]) <= limit:
                end = boundary + 1
        yield start, end
        start = end


def bounded_chunks(chapter, chunks, tokenizer, limit=INPUT_TOKENS):
    count = lambda text: len(tokenizer.encode(text).ids)
    for chunk in chunks:
        if count(chunk["retrieval_text"]) <= limit and len(chunk["text"]) <= 6000:
            yield chunk
            continue
        prefix = chunk["book_title"] + "\n" + " / ".join(chunk["section_path"]) + "\n"
        # An unusually long title is metadata, not a reason to lose the source.
        if count(prefix) > limit // 4:
            _, stop = next(windows(prefix, count, limit // 4))
            prefix = prefix[:stop] + "\n"
        context = list(chunk["context"])
        if chunk["kind"] in {"table", "html_table"} and not any(c["role"] == "table_header" for c in context):
            _, (a, b) = table_groups(chapter["text"], chunk, len(chunk["text"]))
            context.insert(0, dict(source_excerpt(chapter, a, b), role="table_header"))
        header = next((c["text"] for c in context if c["role"] == "table_header"), "")
        if header and count(prefix + header) <= limit // 3:
            prefix += header + "\n"
        else:
            header = ""
        reserved = set()
        for extra in context:
            if extra['role'] in {'footnote', 'table_note'} and count(prefix + extra['text']) <= limit * 3 // 4:
                prefix += extra['text'] + '\n'
                reserved.add((extra['start'], extra['end'], extra['role']))
        for start, end in windows(
            chunk["text"], lambda text, prefix=prefix: count(prefix + text), limit, max_chars=6000
        ):
            item = {**chunk, **source_excerpt(chapter, chunk["start"] + start, chunk["start"] + end)}
            projection = prefix + item["text"]
            omitted = False
            for extra in context:
                if (extra['start'], extra['end'], extra['role']) in reserved:
                    continue
                if header and extra["role"] == "table_header":
                    continue
                extended = projection + "\n" + extra["text"]
                if count(extended) <= limit:
                    projection = extended
                else:
                    omitted = True
            # Full supplements remain source-mapped in context and in the reader.
            yield {
                **item,
                "context": context,
                "retrieval_text": projection,
                "projection_context_omitted": omitted,
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
