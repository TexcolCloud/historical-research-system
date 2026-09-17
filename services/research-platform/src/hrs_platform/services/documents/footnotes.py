"""Explicit note-marker links over immutable text, scoped by original physical page."""

import re
import unicodedata
from collections import defaultdict

from markdown_it import MarkdownIt

DEFINITION = re.compile(r"^ {0,3}(?:\[\^([^\]\n]+)\]:|([①-⑳]))")
REFERENCE = re.compile(r"\[\^([^\]\n]+)\](?!:)|([①-⑳])|<sup>\s*(\d{1,2}|[①-⑳])\s*</sup>", re.I)


def _key(label):
    return str(int(unicodedata.numeric(label))) if len(label) == 1 and "①" <= label <= "⑳" else label


def resolve_footnotes(text, parts):
    """Resolve explicit Markdown IDs and unique same-page printed circles; never nearest-note guessing.

    Returned offsets are Unicode codepoints into the unchanged input, including note markers.
    Unpaired or ambiguous notes remain ordinary source text.
    """
    lines, offsets = text.splitlines(keepends=True), [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    excluded = [
        (offsets[t.map[0]], offsets[t.map[1]])
        for t in MarkdownIt().parse(text)
        if t.type in {"fence", "code_block"} and t.map
    ]
    excluded += [(m.start(), m.end()) for m in re.finditer(r"(`+)[\s\S]*?\1|<(?!/?sup\b)[^>]*>", text, re.I)]

    def hidden(start):
        return any(a <= start < b for a, b in excluded)

    page_ranges, offset = [], 0
    for part in parts:
        end = offset + len(part["text"])
        page_ranges.append((offset, end, part["source"]["pages"]))
        offset = end

    def page_at(start):
        return next((pages[0] for a, b, pages in page_ranges if a <= start < b and len(pages) == 1), None)

    definitions = []
    for index, line in enumerate(lines):
        match = DEFINITION.match(line)
        if not match or hidden(offsets[index]):
            continue
        end = index + 1
        while end < len(lines):
            if DEFINITION.match(lines[end]):
                break
            if not lines[end].strip():
                following = end + 1
                while following < len(lines) and not lines[following].strip():
                    following += 1
                if following < len(lines) and (
                    (match[1] and lines[following].startswith(("    ", "\t")))
                    or re.fullmatch(
                        r"\s*[—–－-]+\s*(?:译者|译注|编者|编注|作者|原注)[。．.]?\s*", lines[following]
                    )
                ):
                    end = following + 1
                    continue
                break
            end += 1
        start = offsets[index] + len(line) - len(line.lstrip(" "))
        definitions.append(
            {
                "id": f"note-{start}",
                "label": match[1] or match[2],
                "key": _key(match[1] or match[2]),
                "explicit": bool(match[1]),
                "page": page_at(start),
                "note": {"start": start, "end": offsets[end], "marker_end": offsets[index] + match.end()},
                "references": [],
            }
        )
    by_key = defaultdict(list)
    for note in definitions:
        by_key[note["key"]].append(note)
    for match in REFERENCE.finditer(text):
        start = match.start()
        if hidden(start) or any(n["note"]["start"] <= start < n["note"]["end"] for n in definitions):
            continue
        candidates = by_key[_key(next(value for value in match.groups() if value))]
        explicit = [n for n in candidates if n["explicit"]] if match[1] else []
        if len(explicit) == 1 and len(candidates) == 1:
            chosen = explicit
        else:
            page = page_at(start)
            chosen = [n for n in candidates if page is not None and n["page"] == page]
        if len(chosen) == 1:
            chosen[0]["references"].append({"start": start, "end": match.end()})
    return [
        {key: note[key] for key in ("id", "label", "note", "references")}
        for note in definitions
        if note["references"]
    ]
