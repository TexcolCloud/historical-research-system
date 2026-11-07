"""Bounded bookmark/BGE recall for directory targets; never grants an approval."""

import math
from array import array
from collections import OrderedDict
from difflib import SequenceMatcher
from itertools import islice
from pathlib import Path
from threading import RLock

from hrs.library.errors import Problem
from hrs.processing.hashing import digest
from hrs.processing.navigation_matching import comparison_text, target_catalog
from hrs.processing.navigation_spans import (
    source_spans,
    source_text,
    spans_current,
    spans_sha256,
    target_spans,
)
from hrs.search.compute import EMBEDDING_IDENTITY, RetrievalCompute

ENTRY_BUDGET = 12
PAGE_ANCHOR_BUDGET = 4
RESULT_BUDGET = 3
VECTOR_CACHE_SIZE = 1024
_vectors_cache = OrderedDict()
_cache_lock = RLock()


def _body(page):
    return bool(page.get("approved")) and (
        page.get("observation", {}).get("kind", page.get("page_kind")) != "toc"
    )


def _valid(candidate, entry, doc):
    pages = {p["page"]: p for p in doc["pages"]}
    page = pages.get(candidate.get("physical_page"))
    anchor = candidate.get("anchor")
    return (
        page is not None
        and _body(page)
        and candidate.get("source_sha256") == entry["source_sha256"]
        and spans_current(doc, source_spans(entry))
        and spans_current(doc, target_spans(candidate))
        and (
            candidate.get("source_spans_sha256") == spans_sha256(source_spans(entry))
            or not candidate.get("source_spans_sha256")
            and len(source_spans(entry)) == 1
        )
        and candidate.get("markdown_sha256") == page["approved"]["sha256"]
        and candidate.get("image_sha256") == page["image_sha256"]
        and isinstance(anchor, str)
        and bool(anchor)
        and page["approved"]["markdown"].count(anchor) == 1
        and candidate.get("match_method") in {"semantic", "bookmark"}
    )


def _unmatched(doc, pages):
    navigation = doc.get("navigation") or {}
    previous = navigation.get("supplemental_candidates") or {}
    result = []
    for entry in navigation.get("entries", []):
        if entry.get("status") not in {"conflict", "not_found", "unresolved", "waiting_evidence"}:
            continue
        source = pages.get(entry.get("source_page"), {}).get("approved")
        if (
            not source
            or source["sha256"] != entry.get("source_sha256")
            or not spans_current(doc, source_spans(entry))
        ):
            continue
        if any(_valid(candidate, entry, doc) for candidate in previous.get(entry["id"], [])):
            continue
        result.append(entry)
        if len(result) == ENTRY_BUDGET:
            break
    return result


def _catalog(doc, pages):
    result, counts = [], {}
    for row in target_catalog(doc):
        number = row["target"]["physical_page"]
        if not _body(pages[number]) or counts.get(number, 0) == PAGE_ANCHOR_BUDGET:
            continue
        counts[number] = counts.get(number, 0) + 1
        result.append(row)
    return result


def _bookmarks(source_path):
    """Malformed/missing outlines are optional hints, not a refresh failure."""
    import pypdfium2 as pdfium

    try:
        source = Path(source_path)
        if source.suffix.lower() != ".pdf":
            return []
        with pdfium.PdfDocument(str(source)) as pdf:
            result = []
            for bookmark in islice(pdf.get_toc(max_depth=16), 4096):
                destination = bookmark.get_dest()
                index = destination.get_index() if destination else None
                title = bookmark.get_title()
                if index is not None and 0 <= index < len(pdf) and title:
                    result.append((comparison_text(title[:512]), index + 1))
            return result
    except (OSError, ValueError, RuntimeError, TypeError):
        return []


def _similarity(left, right):
    return SequenceMatcher(None, left, right, autojunk=False).ratio() if left and right else 0


def _candidate(entry, target, method, score):
    return {
        **target,
        "source_sha256": entry["source_sha256"],
        "source_spans_sha256": spans_sha256(source_spans(entry)),
        "match_method": method,
        "match_score": round(min(1.0, max(0.0, score)), 6),
        "match_signals": [
            "PDF书签指向已核验正文" if method == "bookmark" else "BGE语义召回，仍需核验"
        ],
    }


def _bookmark_candidates(entry, catalog, bookmarks):
    title = comparison_text(entry.get("target_title") or entry["title"])
    result = []
    for bookmark, number in bookmarks:
        score = _similarity(title, bookmark)
        if score < 0.65:
            continue
        for row in catalog:
            if row["target"]["physical_page"] != number:
                continue
            anchor_score = _similarity(title, row["title"])
            if anchor_score >= 0.45:
                result.append(
                    _candidate(entry, row["target"], "bookmark", 0.6 * score + 0.4 * anchor_score)
                )
    return result


def _unavailable():
    return Problem(
        "navigation_suggestions_unavailable",
        "目录补充候选暂不可用，已保留现有匹配结果；可稍后重试。",
        status=503,
        retryable=True,
    )


def _embedding_vectors(backend, namespace, texts, *, query):
    keys = [(namespace, query, digest(text)) for text in texts]
    values, missing = {}, {}
    with _cache_lock:
        for key, text in zip(keys, texts, strict=True):
            if key in _vectors_cache:
                values[key] = _vectors_cache[key]
                _vectors_cache.move_to_end(key)
            else:
                missing[key] = text
    operation = backend.compute if hasattr(backend, "compute") else backend
    rows = list(missing.items())
    for offset in range(0, len(rows), 32):
        batch = rows[offset : offset + 32]
        try:
            vectors = operation("embed", [text for _, text in batch], query=query)["vectors"]
            if len(vectors) != len(batch):
                raise ValueError("Embedding count differs")
            for (key, _), vector in zip(batch, vectors, strict=True):
                floats = [float(value) for value in vector]
                if not 0 < len(floats) <= 2048 or not all(math.isfinite(value) for value in floats):
                    raise ValueError("Invalid embedding")
                norm = math.sqrt(sum(value * value for value in floats))
                if not math.isfinite(norm) or not norm:
                    raise ValueError("Invalid embedding norm")
                values[key] = array("f", (value / norm for value in floats))
        except Problem:
            raise
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            raise _unavailable() from None
        with _cache_lock:
            for key, _ in batch:
                _vectors_cache[key] = values[key]
                _vectors_cache.move_to_end(key)
            while len(_vectors_cache) > VECTOR_CACHE_SIZE:
                _vectors_cache.popitem(last=False)
    return [values[key] for key in keys]


def supplemental_candidates(doc, source_path, settings, *, compute=None):
    """Recall up to three literal targets for at most twelve still-unmatched entries.

    The caller supplies a frozen document snapshot and computes outside its lock.
    Existing source/target-bound supplements are skipped so later refreshes can
    advance to the next batch. No-result entries are retried as evidence grows.
    PDF outline failures yield no bookmark hints. Retrieval failures raise Problem;
    the caller retains previous deterministic candidates and reports the warning.
    """
    pages = {page["page"]: page for page in doc["pages"]}
    entries = _unmatched(doc, pages)
    if not entries:
        return {}
    catalog = _catalog(doc, pages)
    if not catalog:
        return {}
    bookmarks = _bookmarks(source_path)
    backend = compute if compute is not None else RetrievalCompute(settings)
    namespace = (
        compute
        if compute is not None
        else (
            str(settings.models_root),
            settings.retrieval_device,
            settings.retrieval_endpoint,
            EMBEDDING_IDENTITY["revision"],
            EMBEDDING_IDENTITY["adapter"],
        )
    )
    try:
        hash(namespace)
    except TypeError:
        # Injection need not be hashable; a per-call namespace prevents unsafe reuse.
        namespace = object()
    query_texts = [
        (entry.get("target_title") or entry["title"]) + "\n" + source_text(entry)[:500]
        for entry in entries
    ]
    document_texts = [
        (row["target"].get("title") or row["target"]["anchor"])
        + "\n"
        + pages[row["target"]["physical_page"]]["approved"]["markdown"][
            row["target"]["start"] : row["target"]["start"] + 700
        ]
        for row in catalog
    ]
    queries = _embedding_vectors(backend, namespace, query_texts, query=True)
    vectors = _embedding_vectors(backend, namespace, document_texts, query=False)
    if len({len(vector) for vector in [*queries, *vectors]}) != 1:
        raise _unavailable()
    result = {}
    for entry, query in zip(entries, queries, strict=True):
        candidates = _bookmark_candidates(entry, catalog, bookmarks)
        for row, vector in zip(catalog, vectors, strict=True):
            score = sum(a * b for a, b in zip(query, vector, strict=True))
            if score >= 0.45:
                candidates.append(_candidate(entry, row["target"], "semantic", score))
        unique = {}
        for candidate in candidates:
            key = (
                candidate["physical_page"],
                candidate["anchor"],
                spans_sha256(target_spans(candidate)),
            )
            # An actual outline reference is retained when semantic recall finds
            # the same literal anchor; neither method supplies an approval.
            if key not in unique or candidate["match_method"] == "bookmark":
                unique[key] = candidate
        ranked = sorted(
            unique.values(), key=lambda c: (-c["match_score"], c["physical_page"], c["start"])
        )
        if ranked:
            result[entry["id"]] = ranked[:RESULT_BUDGET]
    return result
