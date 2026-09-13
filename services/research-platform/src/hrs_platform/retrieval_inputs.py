"""Token-bounded retrieval windows; canonical chapter content stays untouched."""

from functools import lru_cache
from hashlib import sha256
from html.parser import HTMLParser
from uuid import UUID, uuid5

from tokenizers import Tokenizer

from .domain.settings import MODEL_REVISIONS
from .retrieval_chunks import blocks, source_excerpt, table_groups

INPUT_RULE = "bge-projection-6144-atomic-tables-notes-v3"
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


class CellText(HTMLParser):
    """Model-only text view; immutable HTML evidence is never rewritten."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(text)

    def handle_data(self, data):
        self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in {'td', 'th', 'tr', 'p', 'br'}:
            self.parts.append('\n')


def structural_windows(chapter, chunk, count, limit):
    """Keep rows/rowspans atomic; split only the model view of an oversized row."""
    text = chunk['text']
    if chunk['kind'] not in {'table', 'html_table', 'note'}:
        for a, b in windows(text, count, limit, max_chars=6000):
            yield a, b, text[a:b], False
        return
    if chunk['kind'] == 'note':
        boundaries = [0]
        import re
        boundaries.extend(m.end() for m in re.finditer(r'\n\s*\n', text))
        boundaries.append(len(text))
        units = list(zip(boundaries, boundaries[1:]))
    else:
        container = next(b for b in blocks(chapter['text']) if b['start'] <= chunk['start'] < b['end'])
        groups, _ = table_groups(chapter['text'], container, 1)
        units = [(max(a, chunk['start']) - chunk['start'], min(b, chunk['end']) - chunk['start'])
                 for a, b in groups if a < chunk['end'] and b > chunk['start']]
    for a, b in units:
        raw = text[a:b]
        if not raw:
            continue
        if count(raw) <= limit and len(raw) <= 6000:
            yield a, b, raw, False
        elif chunk['kind'] == 'note':
            for x, y in windows(raw, count, limit, max_chars=6000):
                yield a + x, a + y, raw[x:y], False
        else:
            # An indivisible cell/rowspan can exceed the model limit. Keep its
            # complete source range; only the non-authoritative model view splits.
            view = ''.join(CellText(raw).parts) if chunk['kind'] == 'html_table' else raw
            for x, y in windows(view or raw, count, limit, max_chars=6000):
                yield a, b, (view or raw)[x:y], True


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
        emitted = set()
        for start, end, model_text, atomic in structural_windows(
            chapter, chunk, lambda text, prefix=prefix: count(prefix + text), limit
        ):
            item = {**chunk, **source_excerpt(chapter, chunk["start"] + start, chunk["start"] + end)}
            projection = prefix + model_text
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
            identity = str(uuid5(UUID(item['id']), sha256(projection.encode()).hexdigest())) if atomic else item['id']
            if identity in emitted:
                continue
            emitted.add(identity)
            # Full supplements remain source-mapped in context and in the reader.
            yield {
                **item,
                'id': identity,
                "context": context,
                "retrieval_text": projection,
                "atomic_source_oversized": atomic,
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
