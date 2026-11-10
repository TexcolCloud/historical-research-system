"""Shared source-bound quote rendering and exact reader destinations."""

from urllib.parse import urlencode

from hrs.library.footnotes import export_footnotes, resolve_footnotes


def evidence_url(span, card, *, source_run_id, base_url=""):
    target = span.get("reading_target") or {}
    query = urlencode(
        {
            "tab": "reader",
            "section": "text",
            "chapter": target.get("chapter_id", span["article_id"]),
            "physicalPage": target.get("physical_page", ""),
            "start": target.get("start", span["start"]),
            "end": target.get("end", span["end"]),
            "textSha": span["text_sha256"],
            "run": source_run_id,
        }
    )
    return f"{base_url}/#/books/{span.get('book_id', card['book_id'])}?{query}"


def evidence_markdown(span, article, prefix):
    """Reuse the source export's note ownership and namespacing for each quoted range."""
    start, end = span["start"], span["end"]
    parts = [
        {
            "text": article["text"][max(start, part["start"]) : min(end, part["end"])],
            "source": part["source"],
        }
        for part in article["parts"]
        if part["start"] < end and part["end"] > start
    ]
    for note in span.get("footnotes", []):
        left, right = note["note"]["start"], note["note"]["end"]
        if start <= left < right <= end:
            continue
        owner = next(part for part in article["parts"] if part["start"] <= left < part["end"])
        parts.append({"text": "\n\n" + article["text"][left:right], "source": owner["source"]})
    text = "".join(part["text"] for part in parts)
    return export_footnotes(text, resolve_footnotes(text, parts), prefix, parts=parts)
