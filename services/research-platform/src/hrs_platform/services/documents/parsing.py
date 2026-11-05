"""Lossless source block and table parsing, shared by reading and retrieval."""

import re
from html import unescape
from html.parser import HTMLParser

from markdown_it import MarkdownIt

NOTE = re.compile(r"(?m)^ {0,3}\[\^([^\]\n]+)\]:")


class TableRows(HTMLParser):
    """Locate complete top-level rows without normalizing HTML or cell contents."""

    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text, self.lines = text, [0]
        for line in text.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.depth, self.start, self.rowspan, self.header = 0, None, 1, False
        self.rows, self.cells, self.cell = [], [], None
        self.feed(text)

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
        if self.depth != 1:
            return
        if tag == "tr":
            self.start, self.rowspan, self.header = self.position(), 1, False
        if tag in {"td", "th"} and self.start is not None:
            value = dict(attrs).get("rowspan", "1")
            # rowspan=0 means all remaining rows; preserve the complete table.
            self.rowspan = max(
                self.rowspan, int(value) if value and value.isdigit() and int(value) else 10**9
            )
            self.header |= tag == "th"
            colspan = dict(attrs).get("colspan") or "1"
            self.cell = {
                "row": len(self.rows),
                "rowspan": int(value) if value and value.isdigit() and int(value) else 10**9,
                "colspan": int(colspan) if colspan.isdigit() and int(colspan) else 1,
                "text": "",
                "start": self.position(),
            }

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_entityref(self, name):
        self.handle_data(unescape("&" + name + ";"))

    def handle_charref(self, name):
        self.handle_data(unescape("&#" + name + ";"))

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None and self.depth == 1:
            self.cells.append({**self.cell, "end": self.text.index(">", self.position()) + 1})
            self.cell = None
        if tag == "tr" and self.depth == 1 and self.start is not None:
            self.rows.append(
                (self.start, self.text.index(">", self.position()) + 1, self.rowspan, self.header)
            )
            self.start = None
        if tag == "table":
            self.depth -= 1


def blocks(text):
    """Partition all characters, using Markdown line maps rather than rendered text."""
    lines = [0]
    for line in text.splitlines(keepends=True):
        lines.append(lines[-1] + len(line))
    found, path, section = [], [], 0
    for token in MarkdownIt("commonmark").enable("table").parse(text):
        if token.level != 0 or token.map is None:
            continue
        start, end = (lines[n] for n in token.map)
        if found and start < found[-1]["end"]:
            continue
        kind = token.type.removesuffix("_open")
        if kind == "heading":
            section = start
            level = int(token.tag[1:])
            title = re.sub(r"^\s*#+\s*|\s*#+\s*$", "", text[start:end].splitlines()[0]).strip()
            path = [(n, value) for n, value in path if n < level] + [(level, title)]
        if kind == "html_block" and re.search(r"<table\b", text[start:end], re.I):
            kind = "html_table"
        found.append(
            {"start": start, "end": end, "kind": kind, "path": [v for _, v in path], "section": section}
        )
    if not found:
        if not text:
            return []
        found = [{"start": 0, "end": len(text), "kind": "paragraph", "path": [], "section": 0}]
    # Blank lines and unrendered link definitions still belong to the canonical text.
    found[0]["start"] = 0
    for index, block in enumerate(found):
        block["end"] = found[index + 1]["start"] if index + 1 < len(found) else len(text)
    result = []
    for block in found:
        raw = text[block["start"] : block["end"]]
        notes = list(NOTE.finditer(raw)) if block["kind"] in {"paragraph", "heading"} else []
        boundaries = sorted({0, len(raw), *(match.start() for match in notes)})
        for start, end in zip(boundaries, boundaries[1:]):
            match = NOTE.match(raw, start)
            result.append(
                {
                    **block,
                    "start": block["start"] + start,
                    "end": block["start"] + end,
                    "kind": "note" if match else block["kind"],
                    "note": match[1] if match else None,
                }
            )
    return result
