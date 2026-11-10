"""Literal multi-page navigation fragments and compatibility with retained single-page evidence."""

import re
import unicodedata

from hrs.processing.hashing import digest
from hrs.processing.page_observation import RUNNING_FIELDS, running_line


def source_spans(entry):
    """Read old entries through the same fragment interface without rewriting their receipts."""
    if entry.get("source_spans"):
        return entry["source_spans"]
    anchor = entry.get("source_anchor", "")
    bounds = (
        {"start": entry["source_start"], "end": entry["source_start"] + len(anchor)}
        if "source_start" in entry
        else {}
    )
    return [
        {
            "page": entry["source_page"],
            "anchor": anchor,
            **bounds,
            "markdown_sha256": entry["source_sha256"],
            "image_sha256": entry.get("source_image_sha256"),
        }
    ]


def target_spans(target):
    if target.get("title_spans"):
        return target["title_spans"]
    anchor = target.get("anchor", "")
    bounds = (
        {"start": target["start"], "end": target["start"] + len(anchor)}
        if "start" in target
        else {}
    )
    return [
        {
            "page": target["physical_page"],
            "anchor": anchor,
            **bounds,
            "markdown_sha256": target["markdown_sha256"],
            "image_sha256": target.get("image_sha256"),
        }
    ]


def source_text(entry):
    return "\n".join(span["anchor"] for span in source_spans(entry))


def spans_sha256(spans):
    return digest(spans)


def is_page_folio(page, start, end):
    """Only a complete boundary line may use the current reviewed printed page label."""
    approved = page.get("approved") or {}
    observation = page.get("observation") or {}
    label = observation.get("printed_label")
    text = approved.get("markdown", "")
    if (
        not label
        or observation.get("markdown_sha256") != approved.get("sha256")
        or observation.get("image_sha256") != page.get("image_sha256")
        or not 0 <= start < end <= len(text)
        or (start and text[start - 1] != "\n")
        or (end < len(text) and text[end] not in "\r\n")
        or (text[:start].strip() and text[end:].strip())
    ):
        return False
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text[start:end])).casefold()
    label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", label)).casefold()
    return value == label or bool(
        re.fullmatch(rf"(?P<edge>[·•⋅∙・.\-—–]+){re.escape(label)}(?P=edge)", value)
    )


def _furniture(page, text, *, start=0):
    """Skip only known TOC markers, bound folios and independently observed outer furniture."""
    approved = page.get("approved") or {}
    observation = page.get("observation") or {}
    current = observation.get("markdown_sha256") == approved.get("sha256") and observation.get(
        "image_sha256"
    ) == page.get("image_sha256")
    offset = start
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        folio = is_page_folio(page, offset, offset + len(line))
        offset += len(raw)
        value = re.sub(r"^[ \t]*(?:#{1,6}[ \t]*|(?:\*\*|__))[ \t]*", "", line).strip()
        value = re.sub(r"(?:\*\*|__)$", "", value).strip()
        plain = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
        if not plain or plain in {
            "目录",
            "目錄",
            "目录(续)",
            "目錄(續)",
            "续目录",
            "續目錄",
            "contents",
            "tableofcontents",
            "contents(continued)",
            "tableofcontents(continued)",
        }:
            continue
        if folio:
            continue
        if current and any(
            line.strip() == observed.strip()
            and running_line(approved.get("markdown", ""), observed, field)
            for field in RUNNING_FIELDS
            for observed in observation.get(field, [])
        ):
            continue
        return False
    return True


def validated_spans(doc, spans, *, toc=False, boundary=False):
    """Resolve bounded adjacent literal fragments and reject stale or arbitrary splicing."""
    if not isinstance(spans, list) or not 1 <= len(spans) <= 4:
        raise ValueError("Navigation requires one to four literal page fragments")
    output = []
    for value in spans:
        if not isinstance(value, dict):
            raise ValueError("Navigation page fragments must be objects")
        number, anchor = value.get("page"), value.get("anchor")
        if type(number) is not int or not 1 <= number <= len(doc["pages"]):
            raise ValueError("Navigation fragment physical page is invalid")
        page = doc["pages"][number - 1]
        approved = page.get("approved")
        if not approved or (
            toc and page.get("observation", {}).get("kind", page.get("page_kind")) != "toc"
        ):
            raise ValueError(
                "Navigation fragments require approved directory source pages"
                if toc
                else "Navigation fragments require approved pages"
            )
        text = approved["markdown"]
        if not isinstance(anchor, str) or not anchor or text.count(anchor) != 1:
            raise ValueError("Navigation fragment requires one unique source anchor")
        start = text.index(anchor)
        resolved = {
            "page": number,
            "anchor": anchor,
            "start": start,
            "end": start + len(anchor),
            "markdown_sha256": approved["sha256"],
            "image_sha256": page["image_sha256"],
        }
        if any(key in value and type(value[key]) is not int for key in ("start", "end")):
            raise ValueError("Navigation fragment offsets must be integer codepoint positions")
        if any(
            key in value and value[key] != resolved[key]
            for key in ("start", "end", "markdown_sha256", "image_sha256")
        ):
            raise ValueError("Navigation fragment offsets or source hashes changed")
        if output and number != output[-1]["page"] + 1:
            raise ValueError(
                "Navigation fragments must follow adjacent physical pages in source order"
            )
        output.append(resolved)
    if boundary and len(output) > 1:
        for index, span in enumerate(output):
            page = doc["pages"][span["page"] - 1]
            text = page["approved"]["markdown"]
            if (index and not _furniture(page, text[: span["start"]], start=0)) or (
                index < len(output) - 1
                and not _furniture(page, text[span["end"] :], start=span["end"])
            ):
                raise ValueError("Cross-page fragments must continue at the physical page boundary")
    return output


def spans_current(doc, spans):
    """Read-side evidence checks accept missing legacy image hashes, never stale retained text."""
    if not isinstance(spans, list) or not 1 <= len(spans) <= 4:
        return False
    try:
        previous = None
        for span in spans:
            number = span.get("page")
            if (
                type(number) is not int
                or not 1 <= number <= len(doc["pages"])
                or (previous is not None and number != previous + 1)
            ):
                return False
            previous = number
            page = doc["pages"][number - 1]
            approved = page.get("approved", {})
            anchor = span.get("anchor")
            if (
                not anchor
                or approved.get("sha256") != span.get("markdown_sha256")
                or (span.get("image_sha256") and page["image_sha256"] != span["image_sha256"])
                or approved.get("markdown", "").count(anchor) != 1
            ):
                return False
            start = approved["markdown"].index(anchor)
            if "start" in span and span["start"] != start:
                return False
            if "end" in span and span["end"] != start + len(anchor):
                return False
    except (KeyError, TypeError):
        return False
    return bool(spans)
