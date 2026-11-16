"""Literal standalone-workspace adapter using the same pure source projection.

This adapter owns no workflow state and never reads candidates as publications.
The processing ledger remains independent of the library and search packages.
"""

import re
from uuid import NAMESPACE_URL, uuid5

from hrs.library.structure import project_source_structure
from hrs.processing.source_structure import structure_snapshot
from hrs.search.projection import SOURCE_SCOPES, project_hit, slice_segments, source_excerpt


def search_published(store, query, limit=20, *, source_scope="content"):
    if not isinstance(query, str) or not query.strip() or not 1 <= limit <= 100:
        raise ValueError("Nonempty query and limit between 1 and 100 are required")
    if source_scope not in SOURCE_SCOPES:
        raise ValueError("source_scope must be content, metadata, or all")
    needle, results = re.compile(re.escape(query), re.IGNORECASE), []
    for item in store.list_documents():
        doc = store.status(item["id"])
        pending = bool(doc.get("active_publication") and doc["state"] != "published")
        reason = doc.get("revision_history", [{}])[-1].get("reason") if pending else None
        if doc.get("active_publication"):
            doc = store.publications.published_snapshot(doc, doc["active_publication"])
        elif doc["state"] != "published":
            continue
        store.verify_evidence(doc)
        snapshot = structure_snapshot(doc)
        identity = str(uuid5(NAMESPACE_URL, "hrs-native:" + doc["id"]))
        for page in doc["pages"]:
            text = page["approved"]["markdown"]
            if not text:
                continue
            source = {
                "pages": [page["page"]],
                "page": page["page"],
                "original_start": 0,
                "image_sha256": page["image_sha256"],
                "markdown_sha256": page["approved"]["sha256"],
            }
            parts = [{"span_id": f"p{page['page']}", "start": 0, "text": text, "source": source}]
            chapter = {
                "id": str(uuid5(NAMESPACE_URL, f"{identity}:{page['page']}")),
                "book_id": identity,
                "run_id": identity,
                "title": doc["title"],
                "text": text,
                "parts": parts,
                "source_structure": project_source_structure(snapshot, parts),
            }
            view = project_hit(
                chapter,
                source_excerpt(chapter, 0, len(text)),
                purpose="lexical",
                source_scope=source_scope,
            )
            match = needle.search(view["snippet"])
            if match is None:
                continue
            left, right = max(0, match.start() - 80), min(len(view["snippet"]), match.end() + 160)
            segments = slice_segments(view["snippet_segments"], left, right)
            excerpt = "".join(segment["text"] for segment in segments)
            results.append(
                {
                    "document_id": doc["id"],
                    "title": doc["title"],
                    "page": page["page"],
                    "excerpt": excerpt,
                    "snippet_segments": segments,
                    "image": str(store.artifact_path(doc, page["image"])),
                    "image_sha256": page["image_sha256"],
                    "markdown_sha256": page["approved"]["sha256"],
                    "revision_pending": pending,
                    "revision_reason": reason,
                }
            )
            if len(results) >= limit:
                return results
    return results
