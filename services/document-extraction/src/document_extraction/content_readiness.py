"""Readiness blocks and exact quotations tied to original evidence."""

import json
from pathlib import Path
import re
from .provenance import text_hash, ownership_hash
from .utils import sha256


def _blocks(markdown: str, mapping: dict) -> list[dict]:
    # HTML tables and figures remain atomic even with blank lines inside cells.
    pattern = re.compile(
        r"<table\b[\s\S]*?</table>|<figure\b[\s\S]*?</figure>|[^\n]+(?:\n(?!\s*\n)[^\n]+)*",
        re.I,
    )
    blocks = []
    for match in pattern.finditer(markdown):
        text = match.group()
        if text.startswith("<!-- source:"):
            continue
        sources = []
        for item in mapping["spans"]:
            start, end = (
                max(match.start(), item["start"]),
                min(match.end(), item["end"]),
            )
            if start < end:
                offset = start - item["start"]
                sources.append(
                    {
                        **item,
                        "start": start,
                        "end": end,
                        "source_start": item["source_start"] + offset,
                        "source_end": item["source_start"] + offset + end - start,
                        "text_sha256": text_hash(markdown[start:end]),
                    }
                )
        kind = (
            "table"
            if re.match(r"<table\b", text, re.I)
            else "image"
            if re.match(r"(?:<figure\b|!\[)", text, re.I)
            else "footnote"
            if sources and all(item["source_kind"] == "footnote" for item in sources)
            else "heading"
            if re.match(r"#{1,6}\s", text)
            else "paragraph"
        )
        blocks.append(
            {
                "block_id": f"block-{len(blocks) + 1:05d}",
                "start": match.start(),
                "end": match.end(),
                "kind": kind,
                "sources": sources,
                "pages": sorted({item["page"] for item in sources}),
                "article_ids": sorted(
                    {item["article_id"] for item in sources if item.get("article_id")}
                ),
                "dependencies": [],
                "risks": [],
                "restricted_fields": [],
                "status": "usable" if sources else "generated-not-source",
            }
        )
    return blocks


def verify_quote(
    output: Path,
    quote: str,
    *,
    start: int | None = None,
    expected_markdown_sha256: str | None = None,
    fields: dict[str, str] | None = None,
) -> dict:
    """Evidence interface for later card consumers; no card generation or LLM call."""
    markdown = (output / "document.candidate.md").read_text(encoding="utf-8")
    mapping = json.loads((output / "source-map.json").read_text(encoding="utf-8"))
    readiness = json.loads(
        (output / "content-readiness.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    digest = text_hash(markdown)
    stale = (
        digest != mapping["markdown_sha256"] or digest != readiness["markdown_sha256"]
    )
    stale |= bool(expected_markdown_sha256 and digest != expected_markdown_sha256)
    stale |= (
        mapping["source_sha256"] != readiness["source_sha256"]
        or mapping["source_sha256"] != manifest["source_sha256"]
    )
    if mapping.get("structure_sha256"):
        stale |= mapping["structure_sha256"] != ownership_hash(mapping["spans"])
        stale |= mapping["structure_sha256"] != readiness.get("structure_sha256")
    for name, expected in (manifest.get("evidence_hashes") or {}).items():
        path = output / name
        stale |= not path.is_file() or expected != sha256(path)
    if stale:
        return {"status": "needs-evidence", "reasons": ["stale-or-unbound-artifacts"]}
    if not quote.strip() or (start is None and markdown.count(quote) != 1):
        return {
            "status": "needs-evidence",
            "reasons": ["missing-or-ambiguous-exact-quote"],
        }
    start = markdown.find(quote) if start is None else start
    end = start + len(quote)
    if start < 0 or markdown[start:end] != quote:
        return {"status": "needs-evidence", "reasons": ["quote-offset-mismatch"]}
    blocks = [
        block
        for block in readiness["blocks"]
        if block["start"] < end and block["end"] > start
    ]
    reasons = []
    if not blocks or any(
        block["status"] not in {"usable", "usable-with-warning"} for block in blocks
    ):
        reasons.append("quote-or-dependency-not-ready")
    restricted = {field for block in blocks for field in block["restricted_fields"]}
    for key, value in (fields or {}).items():
        if (
            key in restricted
            or not isinstance(value, str)
            or not value
            or value not in quote
        ):
            reasons.append(f"field-evidence-required:{key}")
    sources = []
    for item in mapping["spans"]:
        left, right = max(start, item["start"]), min(end, item["end"])
        if left < right:
            source_start = item["source_start"] + left - item["start"]
            sources.append(
                {
                    **item,
                    "start": left,
                    "end": right,
                    "source_start": source_start,
                    "source_end": source_start + right - left,
                    "text_sha256": text_hash(markdown[left:right]),
                }
            )
    # Intersections alone do not prove that the entire quotation has a source.
    cursor = start
    for item in sorted(sources, key=lambda span: (span["start"], span["end"])):
        if item["start"] > cursor and markdown[cursor : item["start"]].strip():
            reasons.append("quote-source-coverage-gap")
            break
        cursor = max(cursor, item["end"])
    else:
        if markdown[cursor:end].strip():
            reasons.append("quote-source-coverage-gap")
    pages = [
        page
        for page in mapping["pages"]
        if page["page"] in {item["page"] for item in sources}
    ]
    source = Path(mapping["source"])
    if not source.is_file() or sha256(source) != mapping["source_sha256"]:
        reasons.append("source-missing-or-changed")
    for page in pages:
        image = output / page["image"]
        if not image.is_file() or sha256(image) != page["image_sha256"]:
            reasons.append(f"page-evidence-missing-or-changed:{page['page']}")
    return {
        "status": "needs-evidence" if reasons else "evidence-linked",
        "reasons": reasons,
        "source_sha256": mapping["source_sha256"],
        "markdown_sha256": digest,
        "quote": quote,
        "start": start,
        "end": end,
        "pages": pages,
        "sources": sources,
        "block_ids": [block["block_id"] for block in blocks],
        "restricted_fields": sorted(restricted),
        "review_route_if_disputed": "configured-local-qwen-vision",
        "bibliographic_identity_verified": False,
        "meaning": "Exact quotation and provenance check, not verification of a generated historical claim.",
    }
