"""Explicit note-marker links over immutable text, scoped by original physical page."""

import re
import unicodedata
from collections import defaultdict

from markdown_it import MarkdownIt

DEFINITION = re.compile(r"^ {0,3}(?:\[\^([^\]\n]+)\]:|([①-⑳]))")
REFERENCE = re.compile(
    r"\[\^([^\]\n]+)\](?!:)|([①-⑳])|<sup>\s*(\d{1,2}|[①-⑳])\s*</sup>"
    r"|（([0-9]{1,2})）|〔([0-9]{1,2})〕|\(([0-9]{1,2})\)",
    re.I,
)


def _key(label):
    return (
        str(int(unicodedata.numeric(label))) if len(label) == 1 and "①" <= label <= "⑳" else label
    )


EXPLICIT_REFERENCE = re.compile(r"(?<!\\)\[\^([^\]\n]+)\](?!:)")


def _excluded_ranges(text):
    lines, offsets = text.splitlines(keepends=True), [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return [
        (offsets[token.map[0]], offsets[token.map[1]])
        for token in MarkdownIt().parse(text)
        if token.type in {"fence", "code_block"} and token.map
    ] + [
        (match.start(), match.end())
        for match in re.finditer(r"(`+)[\s\S]*?\1|<(?!/?sup\b)[^>]*>", text, re.I)
    ]


def _hidden(offset, ranges):
    return any(start <= offset < end for start, end in ranges)


def _page_ranges(parts):
    ranges, offset = [], 0
    for part in parts or ():
        end = offset + len(part["text"])
        ranges.append((offset, end, part["source"]["pages"]))
        offset = end
    return ranges


def _page_at(offset, ranges):
    return next(
        (pages[0] for start, end, pages in ranges if start <= offset < end and len(pages) == 1),
        None,
    )


def _definitions(text, excluded):
    lines, offsets = text.splitlines(keepends=True), [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    definitions = []
    for index, line in enumerate(lines):
        match = DEFINITION.match(line)
        if not match or _hidden(offsets[index], excluded):
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
                        r"\s*[—–－-]+\s*(?:译者|译注|编者|编注|作者|原注)[。．.]?\s*",
                        lines[following],
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
                "note": {
                    "start": start,
                    "end": offsets[end],
                    "marker_end": offsets[index] + match.end(),
                },
                "references": [],
            }
        )
    return definitions


def _internal_references(text, note):
    # Remove the note's continuation indent only for parsing. Keep a direct map to
    # original offsets so actual code blocks stay literal and page ownership stays exact.
    normalized, positions, offset = [], [], note["marker_end"]
    for number, line in enumerate(text[note["marker_end"] : note["end"]].splitlines(keepends=True)):
        indent = (
            (4 if line.startswith("    ") else 1 if line.startswith("\t") else 0) if number else 0
        )
        normalized.append(line[indent:])
        positions.extend(range(offset + indent, offset + len(line)))
        offset += len(line)
    body = "".join(normalized)
    excluded = _excluded_ranges(body)
    for match in EXPLICIT_REFERENCE.finditer(body):
        if not _hidden(match.start(), excluded):
            yield positions[match.start()], positions[match.end() - 1] + 1, match[1]


def _apply_edits(text, edits):
    end = len(text)
    for start, stop, replacement in sorted(edits, reverse=True):
        if not 0 <= start < stop <= end:
            raise ValueError("Footnote export ranges overlap or escape canonical source")
        text = text[:start] + replacement + text[stop:]
        end = start
    return text


def export_footnotes(text, links, prefix, *, parts=None):
    """Namespace export links; only explicit, unique same-page note-internal targets resolve."""
    if parts is not None and "".join(part["text"] for part in parts) != text:
        raise ValueError("Footnote export source parts differ from the reading text")
    links = list(links)
    next_number = len(links) + 1
    excluded, ranges = _excluded_ranges(text), _page_ranges(parts)
    definitions = _definitions(text, excluded)
    by_id = {note["id"]: note for note in definitions}
    by_key = defaultdict(list)
    for note in definitions:
        by_key[note["key"]].append(note)
    names, selected, edits, internal = {}, [], [], {}
    for index, link in enumerate(links, 1):
        note = by_id.get(link["id"])
        if note is None or note["note"] != link["note"]:
            raise ValueError("Footnote export link differs from its canonical definition")
        if not text[note["note"]["marker_end"] : note["note"]["end"]].strip():
            continue
        names[note["id"]] = f"{prefix}-{index}"
        selected.append(note)
        edits.extend(
            (ref["start"], ref["end"], f"[^{names[note['id']]}]") for ref in link["references"]
        )
    for note in selected:  # Newly reachable definitions append once; cycles cannot duplicate them.
        rewritten = []
        for start, end, label in _internal_references(text, note["note"]):
            page = _page_at(start, ranges)
            matches = [
                target
                for target in by_key[_key(label)]
                if target["explicit"]
                and page is not None
                and _page_at(target["note"]["start"], ranges) == page
            ]
            target = matches[0] if len(matches) == 1 else None
            if target and text[target["note"]["marker_end"] : target["note"]["end"]].strip():
                if target["id"] not in names:
                    names[target["id"]] = f"{prefix}-{next_number}"
                    next_number += 1
                    selected.append(target)
                replacement = f"[^{names[target['id']]}]"
            else:
                replacement = "\\" + text[start:end]
            rewritten.append((start, end, replacement))
        internal[note["id"]] = rewritten
    for note in definitions:
        span = note["note"]
        if note["id"] not in names:
            if note["explicit"]:
                edits.append(
                    (
                        span["start"],
                        span["marker_end"],
                        "\\" + text[span["start"] : span["marker_end"]],
                    )
                )
            edits.extend(
                (start, end, "\\" + text[start:end])
                for start, end, _ in _internal_references(text, span)
            )
            continue
        body = text[span["marker_end"] : span["end"]]
        body = _apply_edits(
            body,
            [
                (start - span["marker_end"], end - span["marker_end"], value)
                for start, end, value in internal[note["id"]]
            ],
        )
        lines = body.strip(" \n\r").splitlines()
        definition = f"[^{names[note['id']]}]: " + lines[0]
        for line in lines[1:]:
            definition += "\n" + (
                line if not line.strip() or line.startswith(("    ", "\t")) else "    " + line
            )
        edits.append((span["start"], span["end"], definition + "\n\n"))
    for match in EXPLICIT_REFERENCE.finditer(text):
        start = match.start()
        if (
            _hidden(start, excluded)
            or any(note["note"]["start"] <= start < note["note"]["end"] for note in definitions)
            or any(left <= start < right for left, right, _ in edits)
        ):
            continue
        edits.append((start, match.end(), "\\" + match[0]))
    return _apply_edits(text, edits)


def resolve_footnotes(text, parts):
    """Resolve explicit Markdown IDs and unique same-page printed circles; never nearest-note guessing.

    Returned offsets are Unicode codepoints into the unchanged input, including note markers.
    Unpaired or ambiguous notes remain ordinary source text.
    """
    excluded, page_ranges = _excluded_ranges(text), _page_ranges(parts)

    def hidden(start):
        return _hidden(start, excluded)

    def page_at(start):
        return _page_at(start, page_ranges)

    definitions = _definitions(text, excluded)
    for note in definitions:
        note["page"] = page_at(note["note"]["start"])
    by_key = defaultdict(list)
    for note in definitions:
        by_key[note["key"]].append(note)
    for match in REFERENCE.finditer(text):
        start = match.start()
        if hidden(start) or any(
            n["note"]["start"] <= start < n["note"]["end"] for n in definitions
        ):
            continue
        parenthesized = any(match.group(n) for n in (4, 5, 6))
        if parenthesized and not text[text.rfind("\n", 0, start) + 1 : start].strip():
            continue  # Numbered paragraph openings are not inferred to be note references.
        candidates = by_key[_key(next(value for value in match.groups() if value))]
        explicit = [n for n in candidates if n["explicit"]] if match[1] or parenthesized else []
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
