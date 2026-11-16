"""Pure, source-mapped search views of immutable chapter text.

Source facts say what a fragment is. This module alone decides where it is
searchable, removes transport/markup text, and accounts for every shown range.
No model, database, or inferred page-furniture classifications belong here.
"""

import re
from bisect import bisect_right
from collections import deque
from functools import lru_cache
from heapq import heappop, heappush
from html import unescape
from types import MappingProxyType
from typing import NamedTuple
from uuid import UUID, uuid5

from hrs.library.parsing import TableRows, blocks
from hrs.processing.navigation import fragment_navigation

PROJECTION_RULE = "source-structure-visible-ranges-v2"
SOURCE_SCOPES = {"content", "metadata", "all"}
EXCLUDED_ROLES = {"header", "footer", "page_number"}
METADATA_SUBKINDS = {"title_page", "copyright", "imprint"}
LINKED_CONTEXT = {
    "footnote",
    "note_owner",
    "table_header",
    "table_note",
    "table_intro",
    "section_intro",
    "continuation",
    "figure_caption",
    "figure",
}
IMAGE = re.compile(r"!?\[([^\]\n]*)\]\((?:<[^>\n]*>|(?:[^()\n]|\([^()\n]*\))*)\)")
ENTITY = re.compile(r"&(?:#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z][a-zA-Z0-9]+);")
HTML_LAYOUT_TAGS = frozenset(
    {
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "caption",
        "col",
        "colgroup",
        "p",
        "div",
        "span",
        "br",
        "b",
        "strong",
        "i",
        "em",
        "sup",
        "sub",
        "a",
        "ul",
        "ol",
        "li",
        "blockquote",
        "hr",
        "img",
        "figure",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)


class _ContextEdge(NamedTuple):
    trigger_start: int
    trigger_end: int
    target_start: int
    target_end: int
    context_role: str
    target_fragment_id: str | None
    relation_kind: str


def source_excerpt(chapter, start, end):
    """Retain the established canonical envelope and its physical-page evidence."""
    if not 0 <= start < end <= len(chapter["text"]):
        raise ValueError("Invalid immutable chapter range")
    offset, sources = 0, []
    for part in chapter["parts"]:
        stop = offset + len(part["text"])
        if offset < end and stop > start:
            sources.append(
                {
                    "span_id": part["span_id"],
                    "start": part["start"] + max(0, start - offset),
                    "end": part["start"] + min(len(part["text"]), end - offset),
                    "pages": part["source"]["pages"],
                    "unit_start": max(offset, start) - start,
                    "unit_end": min(stop, end) - start,
                    "source_record": part["source"],
                }
            )
        offset = stop
    pages = sorted({p for s in sources for p in s["pages"]})
    origins = {p["span_id"]: p["start"] for p in chapter["parts"]}
    spans = [
        (
            s["source_record"].get("page", s["pages"][0]),
            s["source_record"].get("original_start", 0) + s["start"] - origins[s["span_id"]],
            s["source_record"].get("original_start", 0) + s["end"] - origins[s["span_id"]],
        )
        for s in sources
    ]
    return {
        **fragment_navigation(chapter.get("navigation", {}), pages, spans),
        "id": str(uuid5(UUID(chapter["id"]), f"{start}:{end}")),
        "book_id": str(chapter["book_id"]),
        "chapter_id": str(chapter["id"]),
        "run_id": str(chapter["run_id"]),
        "title": chapter["title"],
        "text": chapter["text"][start:end],
        "start": start,
        "end": end,
        "sources": sources,
        "pages": pages,
    }


def source_excerpts():
    """Reuse source envelopes only within one request over immutable chapter snapshots.

    Each result has independent envelope containers. Source records and each
    source's page list retain the established references to the supplied chapter.
    No hit role, projection view, reading target, or storage validation is cached.
    """
    values, chars = {}, 0

    def excerpt(chapter, start, end):
        nonlocal chars
        if not 0 <= start < end <= len(chapter["text"]):
            raise ValueError("Invalid immutable chapter range")
        # Python numeric keys compare equal across types; retain slicing behavior.
        if type(start) is not int or type(end) is not int:
            return source_excerpt(chapter, start, end)
        key = (id(chapter), start, end)
        cached = values.get(key)
        if cached is None:
            value = source_excerpt(chapter, start, end)
            if len(values) >= 512 or end - start > 16384 or chars + end - start > 131072:
                return value
            template = MappingProxyType(
                {
                    **value,
                    "sources": tuple(MappingProxyType(dict(s)) for s in value["sources"]),
                    "pages": tuple(value["pages"]),
                    "toc_path": tuple(value["toc_path"]),
                    "printed_pages": tuple(
                        MappingProxyType(dict(p)) for p in value["printed_pages"]
                    ),
                }
            )
            # Retain the snapshot so its Python identity cannot be recycled.
            cached = values[key] = (chapter, template)
            chars += end - start
        value = cached[1]
        return {
            **value,
            "sources": [dict(s) for s in value["sources"]],
            "pages": list(value["pages"]),
            "toc_path": list(value["toc_path"]),
            "printed_pages": [dict(p) for p in value["printed_pages"]],
        }

    return excerpt


def _classification(fact):
    role, layer = fact.get("role", "unknown"), fact.get("layer", "unknown")
    ready = fact.get("status") == "ready"
    if not ready:
        return role, layer, "content", False
    scope = (
        "navigation"
        if role in {"toc", "index"}
        else "metadata"
        if role == "bibliography"
        or (role == "frontmatter" and fact.get("subkind") in METADATA_SUBKINDS)
        else "content"
    )
    return (
        role,
        layer,
        scope,
        role in EXCLUDED_ROLES or (role == "watermark" and layer == "capture"),
    )


def prepare_projection(chapter, *, footnotes=None):
    """Prepare one immutable interval plan per chapter value, independent of storage.

    Callers may retain this plan during a request. Bounded memoization keys include
    the complete canonical text and source facts, so changed facts cannot reuse a
    stale classification and no cache is written onto the chapter object.
    """
    fields = ("start", "end", "role", "layer", "status", "subkind", "id")
    facts = tuple(
        tuple(b.get(k) for k in fields)
        for b in chapter.get("source_structure", {}).get("blocks", [])
    )
    by_id = {
        b.get("id"): b.get("role", "unknown")
        for b in chapter.get("source_structure", {}).get("blocks", [])
        if b.get("id")
    }
    relations = tuple(
        (
            r["kind"],
            tuple(
                tuple(m.get(k) for k in ("start", "end", "fragment_id", "endpoint"))
                + (m.get("role", by_id.get(m.get("fragment_id"), "unknown")),)
                for m in r["members"]
            ),
        )
        for r in [
            *chapter.get("structure", []),
            *chapter.get("source_structure", {}).get("relations", []),
        ]
        if r.get("status") == "ready"
    )
    notes = chapter.get("footnotes", []) if footnotes is None else footnotes
    note_values = tuple(
        (
            n["note"]["start"],
            n["note"]["end"],
            n.get("id"),
            tuple((r["start"], r["end"]) for r in n["references"]),
        )
        for n in notes
    )
    return _prepared_source(chapter["text"], facts, relations, note_values)


@lru_cache(maxsize=32)
def _ready_relations(relation_values):
    kinds = {
        "continues": "continuation",
        "table_continues": "table",
    }
    relations = tuple(
        MappingProxyType(
            {
                "kind": kinds.get(kind, kind),
                "fact_kind": kind,
                "status": "ready",
                "members": tuple(
                    MappingProxyType(
                        {
                            k: v
                            for k, v in zip(
                                ("start", "end", "fragment_id", "endpoint", "role"),
                                member,
                                strict=True,
                            )
                            if v is not None
                        }
                    )
                    for member in members
                ),
            }
        )
        for kind, members in relation_values
    )
    return relations


@lru_cache(maxsize=16)
def _prepared_source(text, fact_values, relation_values, note_values):
    fields = ("start", "end", "role", "layer", "status", "subkind", "id")
    facts = [
        dict(zip(fields, values, strict=True))
        for values in fact_values
        if type(values[0]) is int
        and type(values[1]) is int
        and 0 <= values[0] < values[1] <= len(text)
    ]
    for fact in facts:
        fact["role"] = fact["role"] or "unknown"
        fact["layer"] = fact["layer"] or "unknown"
        fact["status"] = fact["status"] or "unknown"
    relations = _ready_relations(relation_values)
    relation_cuts = {
        point
        for r in relations
        for m in r["members"]
        for point in (m["start"], m["end"])
        if 0 < point < len(text)
    }
    cuts = sorted(
        {0, len(text), *relation_cuts, *(f["start"] for f in facts), *(f["end"] for f in facts)}
    )
    ranges = []
    for start, end in zip(cuts, cuts[1:]):
        covering = [f for f in facts if f["start"] <= start and end <= f["end"]]
        covering.sort(
            key=lambda f: (not _classification(f)[3], f["status"] != "ready", f["end"] - f["start"])
        )
        fact = covering[0] if covering else {}
        role, layer, scope, excluded = _classification(fact)
        item = {
            "start": start,
            "end": end,
            "role": role,
            "layer": layer,
            "source_scope": "furniture" if excluded else scope,
            "status": fact.get("status", "unknown"),
        }
        if fact.get("subkind"):
            item["subkind"] = fact["subkind"]
        if fact.get("id"):
            item["block_id"] = fact["id"]
        if (
            ranges
            and start not in relation_cuts
            and all(ranges[-1].get(k) == v for k, v in item.items() if k not in {"start", "end"})
        ):
            ranges[-1]["end"] = end
        else:
            ranges.append(item)
    plan = {
        "text": text,
        "ranges": tuple(MappingProxyType(r) for r in ranges),
        "ends": tuple(r["end"] for r in ranges),
        "relations": relations,
        "relation_values": relation_values,
    }
    plan["blocks"] = (
        tuple(
            MappingProxyType(
                {**b, "path": tuple(b["path"]), "projection_scope": tuple(b["projection_scope"])}
            )
            for b in _build_source_blocks(text, plan)
        )
        if text
        else ()
    )
    plan["block_ends"] = tuple(block["end"] for block in plan["blocks"])
    plan["edge_values"] = _context_edges(plan, note_values)
    plan["owner_edges"] = tuple(
        e for e in plan["edge_values"] if e.context_role in {"note_owner", "figure"}
    )
    plan["owner_targets"] = MappingProxyType(
        {
            role: tuple(e for e in plan["owner_edges"] if e.context_role == role)
            for role in ("note_owner", "figure")
        }
    )
    plan["table_inheritance"] = tuple(
        e for e in plan["edge_values"] if e.relation_kind == "table_continues"
    )
    return MappingProxyType(plan)


def source_ranges(chapter, start, end, *, source_scope="all", include_furniture=False, plan=None):
    """Clip the prepared canonical interval plan without rescanning source facts."""
    if source_scope not in SOURCE_SCOPES:
        raise ValueError("source_scope must be content, metadata, or all")
    if not 0 <= start < end <= len(chapter["text"]):
        raise ValueError("Invalid immutable chapter range")
    plan = prepare_projection(chapter) if plan is None else plan
    result = []
    for row in plan["ranges"][bisect_right(plan["ends"], start) :]:
        if row["start"] >= end:
            break
        scope = row["source_scope"]
        if scope == "furniture" and not include_furniture:
            continue
        if source_scope == "content" and scope != "content":
            continue
        if source_scope == "metadata" and scope not in {"metadata", "navigation"}:
            continue
        result.append({**row, "start": max(start, row["start"]), "end": min(end, row["end"])})
    return result


def _build_source_blocks(text, plan):
    """Keep canonical coverage while preventing furniture headings owning body paths."""
    chapter = {"text": text}
    result, path, section = [], [], 0
    for block in blocks(chapter["text"]):
        kept = source_ranges(chapter, block["start"], block["end"], plan=plan)
        if block["kind"] == "heading" and any(
            visible_text(chapter["text"][r["start"] : r["end"]]).strip() for r in kept
        ):
            raw = chapter["text"][block["start"] : block["end"]]
            match = re.match(r" {0,3}(#{1,6})[ \t]+", raw)
            level = len(match[1]) if match else (1 if re.search(r"\n=+", raw) else 2)
            title = " ".join(
                "".join(visible_text(chapter["text"][r["start"] : r["end"]]) for r in kept).split()
            )
            path = [(n, value) for n, value in path if n < level] + [(level, title)]
            section = block["start"]
        cuts = sorted(
            {block["start"], block["end"], *(r["start"] for r in kept), *(r["end"] for r in kept)}
        )
        if block["kind"] in {"table", "html_table", "fence", "code_block"}:
            cuts = [block["start"], block["end"]]
        for a, b in zip(cuts, cuts[1:]):
            facts = [r for r in kept if r["start"] < b and r["end"] > a]
            scopes = {r["source_scope"] for r in facts}
            result.append(
                {
                    **block,
                    "start": a,
                    "end": b,
                    "path": [v for _, v in path],
                    "section": section,
                    "projection_scope": sorted(scopes),
                    **({"kind": "excluded"} if not facts else {}),
                }
            )
    return result


def source_blocks(chapter, *, plan=None):
    """Return independent containers over the cached source-checked Markdown parse."""
    plan = prepare_projection(chapter) if plan is None else plan
    return [
        {**b, "path": list(b["path"]), "projection_scope": list(b["projection_scope"])}
        for b in plan["blocks"]
    ]


def source_relations(chapter, *, plan=None):
    """Only retained, reviewed relationships can supply reading order or context."""
    return (prepare_projection(chapter) if plan is None else plan)["relations"]


def _ordered_ranges(relations, ranges):
    edges = [set() for _ in ranges]
    for relation in relations:
        if relation["kind"] not in {"continuation", "reading_next"}:
            continue
        groups = [
            [i for i, r in enumerate(ranges) if m["start"] < r["end"] and m["end"] > r["start"]]
            for m in relation["members"]
        ]
        for before, after in zip(groups, groups[1:]):
            for a in before:
                edges[a].update(b for b in after if a != b)
    incoming = [0] * len(ranges)
    for successors in edges:
        for index in successors:
            incoming[index] += 1
    ready = []
    for i, degree in enumerate(incoming):
        if not degree:
            heappush(ready, i)
    ordered = []
    while ready:
        index = heappop(ready)
        ordered.append(ranges[index])
        for successor in edges[index]:
            incoming[successor] -= 1
            if not incoming[successor]:
                heappush(ready, successor)
    # Conflicting retained relationships cannot manufacture a reading sequence.
    return ordered if len(ordered) == len(ranges) else ranges


def _source_segments(segments, start, end):
    result, pending = [], []
    for s in segments:
        if s["synthetic"]:
            pending.append(s)
            continue
        a, b = max(start, s["start"]), min(end, s["end"])
        if a >= b:
            pending = []
            continue
        if result:
            result.extend(pending)
        pending = []
        result.append(
            {
                **s,
                "start": a,
                "end": b,
                "text": s["text"]
                if s.get("transformation")
                else s["text"][a - s["start"] : b - s["start"]],
            }
        )
    return result


def _follows_context(kind, role, primary_trigger):
    """Qualification flow is directed; ordering never creates semantic context."""
    if kind == "reading_next":
        return False
    if role in {"note_owner", "figure"}:
        return primary_trigger
    return kind in {"note_of", "caption_of", "continues", "table_continues"}


def _context_edges(plan, note_values):
    edges, table_groups = [], []

    def connect(triggers, targets, role, kind):
        for trigger in triggers:
            for target in targets:
                if (trigger["start"], trigger["end"]) != (target["start"], target["end"]):
                    label = role(target) if callable(role) else role
                    edges.append(
                        _ContextEdge(
                            trigger["start"],
                            trigger["end"],
                            target["start"],
                            target["end"],
                            label,
                            target.get("fragment_id"),
                            kind,
                        )
                    )

    for relation in plan["relations"]:
        members = relation["members"]
        if relation["kind"] == "note_of":
            notes = [m for m in members if m.get("endpoint") == "from"]
            owners = [m for m in members if m.get("endpoint") == "to"]
            if not notes and len(members) == 2:
                notes, owners = members[:1], members[1:]
            connect(owners, notes, "footnote", "note_of")
            connect(notes, owners, "note_owner", "note_of")
        elif relation["kind"] == "caption_of":
            connect(
                members,
                members,
                lambda m: (
                    "figure_caption"
                    if m.get("role") == "caption"
                    or (m.get("role", "unknown") == "unknown" and m.get("endpoint") == "from")
                    else "figure"
                ),
                "caption_of",
            )
        elif relation["kind"] == "continuation":
            connect(members, members, "continuation", "continues")
        elif relation["kind"] == "table":
            group = {(m["start"], m["end"]): m for m in members}
            joined = [g for g in table_groups if set(g).intersection(group)]
            for old in joined:
                group = {**old, **group}
                table_groups.remove(old)
            table_groups.append(group)
    # A continued table inherits its reviewed headers and qualifications, not
    # every unrelated data row of preceding parts. Shared captions stay auxiliary.
    qualifiers = [e for e in edges if e.context_role in {"footnote", "figure_caption"}]
    for group in table_groups:
        members = list(group.values())
        for edge in qualifiers:
            if any(
                m["start"] < edge.trigger_end and m["end"] > edge.trigger_start for m in members
            ):
                target = {
                    "start": edge.target_start,
                    "end": edge.target_end,
                    "fragment_id": edge.target_fragment_id,
                }
                connect(members, [target], edge.context_role, "table_continues")
        first = next((m for m in members if m.get("endpoint") != "to"), members[0])
        block = next(
            (
                b
                for b in plan["blocks"]
                if b["start"] <= first["start"] < b["end"] and b["kind"] in {"table", "html_table"}
            ),
            None,
        )
        if block:
            raw = plan["text"][block["start"] : block["end"]]
            if block["kind"] == "html_table":
                rows = TableRows(raw).rows
                header_rows = 0
                while header_rows < len(rows) and rows[header_rows][3]:
                    header_rows += 1
                header_end = rows[max(1, header_rows) - 1][1] if rows else 0
            else:
                header_end = sum(map(len, raw.splitlines(keepends=True)[:2]))
            if header_end:
                header = {
                    "start": block["start"],
                    "end": block["start"] + header_end,
                    "fragment_id": str(first.get("fragment_id")) + ":header",
                }
                connect(members, [header], "table_header", "table_continues")
    explicit_owners = [e for e in edges if e.context_role == "note_owner"]
    for start, end, identity, references in note_values:
        note = {"start": start, "end": end, "fragment_id": "markdown-note:" + str(identity)}
        for a, b in references:
            reference = {"start": a, "end": b}
            connect([reference], [note], "footnote", "note_of")
            # Source-checked ownership is more precise than a Markdown paragraph.
            if any(
                e.trigger_start <= start
                and end <= e.trigger_end
                and e.target_start <= a < b <= e.target_end
                for e in explicit_owners
            ):
                continue
            owner = next(
                (block for block in plan["blocks"] if block["start"] <= a < block["end"]), None
            )
            if owner:
                connect([note], [owner], "note_owner", "note_of")
    return tuple(dict.fromkeys(edges))


@lru_cache(maxsize=2048)
def _related_spans(edges, primary, context_key):
    # Only ready source links and retained unambiguous Markdown note links enter
    # this finite graph. Each target fragment/range and each queued range runs once.
    semantic = [
        (a, b) for a, b, role in context_key if role not in {"neighbor", "unassigned_context"}
    ]
    occupied, additions = [*primary, *semantic], []
    queue = deque([*primary, *semantic])
    queued, visited = set(queue), set()
    maximum = len(edges) + len(queue)
    processed = 0
    while queue:
        left, right = queue.popleft()
        processed += 1
        if processed > maximum:
            raise ValueError("Source context graph exceeds its finite range bound")
        for a, b, target_start, target_end, role, identity, kind in edges:
            if not left < b or not right > a:
                continue
            if not _follows_context(
                kind, role, any(start < b and end > a for start, end in primary)
            ):
                continue
            key = (identity, target_start, target_end)
            if key in visited:
                continue
            visited.add(key)
            target = (target_start, target_end)
            for x, y in _subtract(target_start, target_end, occupied):
                additions.append((x, y, role))
                occupied.append((x, y))
            if target not in queued:
                queued.add(target)
                queue.append(target)
    return tuple(additions)


def _context_excerpt(chapter, start, end, physical, excerpt):
    if physical:
        return excerpt(chapter, start, end)
    return {
        "chapter_id": str(chapter["id"]),
        "start": start,
        "end": end,
        "text": chapter["text"][start:end],
        "sources": [],
        "pages": [],
    }


def _qualified_context(primary, extras, plan):
    """Reviewed owners override proximity; unknown proximity remains explicitly unassigned."""
    result = []
    for extra in extras:
        hint = extra.get("context_hint", extra.get("role"))
        if hint == "continuation":
            if not any(e.relation_kind == "continues" for e in plan["edge_values"]):
                result.append(
                    {
                        **extra,
                        "role": "unassigned_context",
                        "context_hint": hint,
                        "ownership_status": "unknown",
                    }
                )
            continue  # Rebuild known continuation only from current reviewed edges.
        if hint in {"note_owner", "figure"}:
            incoming = [
                e
                for e in plan["owner_targets"][hint]
                if e.target_start < extra["end"] and e.target_end > extra["start"]
            ]
            if incoming and not any(
                _follows_context(
                    e.relation_kind,
                    hint,
                    any(start < e.trigger_end and end > e.trigger_start for start, end in primary),
                )
                for e in incoming
            ):
                continue
            result.append(extra)
            continue
        heuristic = hint in {"table_note", "table_intro", "section_intro"}
        if not heuristic and hint not in {"footnote", "figure_caption"}:
            result.append(extra)
            continue
        owners = [
            e
            for e in plan["owner_edges"]
            if e.trigger_start < extra["end"] and e.trigger_end > extra["start"]
        ]
        direct = any(
            e.target_start < end and e.target_end > start for e in owners for start, end in primary
        )
        inherited = any(
            any(e.trigger_start < end and e.trigger_end > start for start, end in primary)
            and e.target_start < extra["end"]
            and e.target_end > extra["start"]
            for e in plan["table_inheritance"]
        )
        if owners and not (direct or inherited):
            continue
        if not heuristic:
            result.append(extra)
            continue
        result.append(
            {
                **extra,
                "ownership_status": "ready" if owners else "unknown",
                "context_hint": hint,
                "role": hint if owners else "unassigned_context",
            }
        )
    return result


def _related_context(chapter, hit, plan, physical, primary, excerpt):
    primary_key = tuple((r["start"], r["end"]) for r in primary)
    extras = _qualified_context(primary_key, hit.get("context", []), plan)
    context_key = tuple((e["start"], e["end"], e.get("role", "context")) for e in extras)
    neighbors = [e for e in extras if e.get("role") in {"neighbor", "unassigned_context"}]
    extras = [e for e in extras if e.get("role") not in {"neighbor", "unassigned_context"}]
    extras.extend(
        dict(
            _context_excerpt(chapter, start, end, physical, excerpt),
            role=role,
            ownership_status="ready",
        )
        for start, end, role in _related_spans(plan["edge_values"], primary_key, context_key)
    )
    return extras + neighbors


@lru_cache(maxsize=1024)
def _visible_segments(raw):
    """Remove presentation syntax while mapping every visible run to its source.

    Synthetic separators have null source coordinates. Decoded entities carry
    their complete original entity range, rather than fabricated character offsets.
    """
    start, edits = 0, []
    for match in IMAGE.finditer(raw):
        a, b = match.span(1)
        edits.extend([(match.start(), a, ""), (b, match.end(), "")])
        if raw[match.start()] == "!" and match[1].strip().lower() in {"", "image"}:
            edits.append((a, b, ""))
    for pattern in (
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        r"(?s)<!--.*?-->",
        r"(?m)^ {0,3}\[(?!\^)[^\]\n]+\]:[^\n]*(?:\n|$)",
        r"(?m)^ {0,3}#{1,6}[ \t]+|[ \t]+#+[ \t]*(?=\r?$)",
        r"(?m)^ {0,3}(?:=+|-{3,})[ \t]*(?:\r?\n|$)",
        r"(?m)^\s*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*(?:\r?\n|$)",
        r"(?m)^ {0,3}`{3,}[^\n]*(?:\n|$)|^ {0,3}~{3,}[^\n]*(?:\n|$)",
    ):
        edits.extend((*m.span(), "") for m in re.finditer(pattern, raw))
    for match in re.finditer(r"</?([a-zA-Z][\w:-]*)(?:\s[^>\n]*)?/?>", raw):
        if match[1].lower() not in HTML_LAYOUT_TAGS:
            continue
        separator = (
            "\n"
            if re.match(r"</?(?:tr|p|div|br)\b", match[0], re.I)
            else ("\t" if re.match(r"</(?:td|th)\b", match[0], re.I) else "")
        )
        edits.append((*match.span(), separator))
    for match in re.finditer(r"(?<![\w\\])(\*\*|__|~~|`|\*)(?=\S)(.+?)(?<=\S)\1(?!\w)", raw):
        size = len(match[1])
        edits.extend(
            [(match.start(), match.start() + size, ""), (match.end() - size, match.end(), "")]
        )
    # Edits are removals except explicitly synthetic HTML layout separators.
    mask, separators = bytearray(len(raw)), {}
    for a, b, replacement in edits:
        mask[a:b] = b"\1" * (b - a)
        if replacement:
            separators[b] = replacement
    protected, covered_until = [], -1
    for opening in re.finditer(r"(?m)^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)", raw):
        if opening.start() < covered_until:
            continue
        closing = re.search(
            r"(?m)^ {0,3}"
            + re.escape(opening[1][0])
            + "{"
            + str(len(opening[1]))
            + r",}[ \t]*(?:\r?\n|$)",
            raw[opening.end() :],
        )
        stop = opening.end() + closing.start() if closing else len(raw)
        protected.append((opening.end(), stop))
        covered_until = opening.end() + closing.end() if closing else len(raw)
    protected.extend(match.span(2) for match in re.finditer(r"(?<!`)(`+)(?!`)(.+?)\1(?!`)", raw))
    code_mask = bytearray(len(raw))
    for a, b in protected:
        mask[a:b] = b"\0" * (b - a)
        code_mask[a:b] = b"\1" * (b - a)
        separators = {point: value for point, value in separators.items() if not a < point <= b}
    for tag in re.finditer(r"<img\b[^>\n]*>", raw, re.I):
        alt = re.search(r"\balt\s*=\s*([\"'])(.*?)\1", tag[0], re.I)
        if alt:
            a, b = tag.start() + alt.start(2), tag.start() + alt.end(2)
            mask[a:b] = b"\0" * (b - a)
    result, cursor = [], 0
    while cursor < len(raw):
        if mask[cursor]:
            cursor += 1
            if cursor in separators:
                result.append(
                    {"text": separators[cursor], "start": None, "end": None, "synthetic": True}
                )
            continue
        entity = ENTITY.match(raw, cursor) if raw[cursor] == "&" and not code_mask[cursor] else None
        if entity and not any(mask[entity.start() : entity.end()]):
            result.append(
                {
                    "text": unescape(entity[0]),
                    "start": start + cursor,
                    "end": start + entity.end(),
                    "synthetic": False,
                    "transformation": "html_entity",
                }
            )
            cursor = entity.end()
            continue
        stop = cursor + 1
        while (
            stop < len(raw)
            and not mask[stop]
            and (raw[stop] != "&" or code_mask[stop] or not ENTITY.match(raw, stop))
        ):
            stop += 1
        result.append(
            {
                "text": raw[cursor:stop],
                "start": start + cursor,
                "end": start + stop,
                "synthetic": False,
            }
        )
        cursor = stop
    return tuple(MappingProxyType(segment) for segment in result)


def visible_segments(text, start=0, end=None):
    """Return fresh mapped containers over a bounded immutable visible-text cache."""
    return [
        {
            **segment,
            "start": segment["start"] + start if segment["start"] is not None else None,
            "end": segment["end"] + start if segment["end"] is not None else None,
        }
        for segment in _visible_segments(text[start:end])
    ]


def visible_text(text):
    return "".join(s["text"] for s in _visible_segments(text))


def _subtract(start, end, occupied):
    ranges = [(start, end)]
    for a, b in occupied:
        ranges = [
            (x, y)
            for left, right in ranges
            for x, y in (
                [(left, min(right, a)), (max(left, b), right)]
                if left < b and a < right
                else [(left, right)]
            )
            if x < y
        ]
    return ranges


def projection_reserve(chapter, hit, count, limit, *, plan=None):
    """Reserve fitting, source-linked qualifications before splitting primary text."""
    plan = prepare_projection(chapter) if plan is None else plan
    empty = {**hit, "context": []}
    prefix = project_hit(
        chapter,
        empty,
        count=count,
        limit=limit,
        primary_segments=[],
        include_relations=False,
        plan=plan,
    )["retrieval_text"]
    clean = project_hit(chapter, hit, plan=plan)
    for extra in sorted(clean["context"], key=lambda c: c["role"] != "table_header"):
        if extra["role"] not in {"table_header", "footnote", "table_note"}:
            continue
        addition = "\n" + visible_text(extra["text"])
        ceiling = limit // 3 if extra["role"] == "table_header" else limit * 3 // 4
        if count(prefix + addition) <= ceiling:
            prefix += addition
    return prefix


def primary_ranges(chapter, start, end, *, purpose="vector", source_scope="all", plan=None):
    """Share source eligibility between deferred planning and complete views."""
    model = source_ranges(chapter, start, end, source_scope=source_scope, plan=plan)
    primary = (
        source_ranges(
            chapter, start, end, source_scope=source_scope, include_furniture=True, plan=plan
        )
        if purpose in {"lexical", "display"} and source_scope == "all"
        else model
    )
    return primary, bool(model)


def project_hit(
    chapter,
    hit,
    *,
    purpose="vector",
    source_scope="all",
    count=None,
    limit=None,
    primary_segments=None,
    include_relations=True,
    plan=None,
    include_view=True,
    excerpt=None,
):
    """Build a visible view, canonical fragments, and bounded nonduplicate context."""
    plan = prepare_projection(chapter) if plan is None else plan
    excerpt = source_excerpt if excerpt is None else excerpt
    include_furniture = purpose in {"lexical", "display"} and source_scope == "all"
    primary, model_eligible = primary_ranges(
        chapter, hit["start"], hit["end"], purpose=purpose, source_scope=source_scope, plan=plan
    )
    ranges = [dict(r, usage="primary") for r in primary]
    occupied = [(r["start"], r["end"]) for r in primary]
    context = []
    extras = (
        _related_context(chapter, hit, plan, include_view, primary, excerpt)
        if include_relations
        else hit.get("context", [])
    )
    for extra in extras:
        if str(extra.get("chapter_id", hit["chapter_id"])) != str(hit["chapter_id"]):
            continue
        context_scope = "all" if extra.get("role") in LINKED_CONTEXT else source_scope
        for fact in source_ranges(
            chapter,
            extra["start"],
            extra["end"],
            source_scope=context_scope,
            include_furniture=include_furniture,
            plan=plan,
        ):
            for a, b in _subtract(fact["start"], fact["end"], occupied):
                context.append(
                    {
                        **extra,
                        **_context_excerpt(chapter, a, b, include_view, excerpt),
                        "role": extra.get("role", "context"),
                        "source_role": fact["role"],
                        "layer": fact["layer"],
                        "source_scope": fact["source_scope"],
                    }
                )
                ranges.append(
                    {
                        **fact,
                        "start": a,
                        "end": b,
                        "usage": "context",
                        "context_role": extra.get("role", "context"),
                    }
                )
                occupied.append((a, b))
    ranges = _ordered_ranges(plan["relations"], ranges)
    roles, layers = {r["role"] for r in primary}, {r["layer"] for r in primary}
    scopes = {r["source_scope"] for r in primary}
    common = {
        **hit,
        "context": context,
        "ranges": ranges,
        "role": next(iter(roles)) if len(roles) == 1 else "mixed",
        "layer": next(iter(layers)) if len(layers) == 1 else "mixed",
        "source_scope": next(iter(scopes))
        if len(scopes) == 1
        else ("mixed" if scopes else "furniture"),
        "projection_rule": PROJECTION_RULE,
        "model_eligible": model_eligible,
        "doc_kind": "navigation"
        if scopes == {"navigation"}
        else ("metadata" if scopes == {"metadata"} else hit.get("doc_kind", "body")),
    }
    if not include_view:
        return {**common, "snippet": None, "snippet_segments": [], "retrieval_text": ""}
    path = [chapter["title"]]
    if primary:
        first, stop = min(r["start"] for r in primary), max(r["end"] for r in primary)
        for block in plan["blocks"][bisect_right(plan["block_ends"], first) :]:
            if block["start"] >= stop:
                break
            if block["kind"] != "excluded" and any(
                r["start"] < block["end"] and r["end"] > block["start"] for r in primary
            ):
                path.extend(block["path"])
    common["section_path"] = list(dict.fromkeys(path))
    segments, pieces_by_range = [], []
    for r in ranges:
        if segments:
            segments.append(
                {"text": "\n", "start": None, "end": None, "synthetic": True, "usage": r["usage"]}
            )
        if primary_segments is not None and r["usage"] == "primary":
            pieces = _source_segments(primary_segments, r["start"], r["end"])
        else:
            pieces = visible_segments(chapter["text"], r["start"], r["end"])
        if r.get("context_role") == "unassigned_context":
            pieces = [
                {"text": "〔附近原文，归属未定〕\n", "start": None, "end": None, "synthetic": True},
                *pieces,
            ]
        pieces_by_range.append(pieces)
        segments.extend(
            {
                **s,
                "role": s.get("role", r["role"]),
                "layer": s.get("layer", r["layer"]),
                "usage": r["usage"],
            }
            for s in pieces
        )
    labels = [hit.get("book_title", ""), " / ".join(common["section_path"])]
    if purpose == "rerank":
        labels = [*hit.get("toc_path", []), *labels]
    prefix = "\n".join(visible_text(label) for label in labels if label) + "\n"
    if count is not None and limit is not None and count(prefix) > limit // 4:
        # Labels are mutable metadata. They never consume the source's entire budget.
        while prefix and count(prefix) > limit // 4:
            prefix = prefix[: len(prefix) // 2]

    def assembled(selected):
        return prefix + "\n".join(
            "".join(s["text"] for s in pieces)
            for i, pieces in enumerate(pieces_by_range)
            if i in selected
        )

    selected = {i for i, r in enumerate(ranges) if r["usage"] == "primary"}
    model_text, omitted = assembled(selected), False
    if count is not None and limit is not None:
        if count(model_text) > limit:
            raise ValueError("Primary source view exceeds the projection budget")
        candidates = [i for i, r in enumerate(ranges) if r["usage"] == "context"]
        candidates.sort(
            key=lambda i: (
                ranges[i].get("context_role") not in {"table_header", "footnote", "table_note"}
            )
        )
        for i in candidates:
            extended = assembled(selected | {i})
            if count(extended) <= limit:
                model_text = extended
                selected.add(i)
            else:
                omitted = True
    else:
        model_text = assembled(set(range(len(ranges))))
    cursor = 0
    for segment in segments:
        segment["snippet_start"], segment["snippet_end"] = cursor, cursor + len(segment["text"])
        cursor = segment["snippet_end"]
    return {
        **common,
        "snippet": "".join(s["text"] for s in segments),
        "snippet_segments": segments,
        "retrieval_text": model_text,
        "raw_text": visible_text(chapter["text"][hit["start"] : hit["end"]]),
        "projection_context_omitted": omitted,
    }


def slice_segments(segments, start, end):
    """Slice a visible view without treating removed markup as source characters."""
    result, offset = [], 0
    for segment in segments:
        stop = offset + len(segment["text"])
        a, b = max(start, offset), min(end, stop)
        if a < b:
            item = {**segment, "text": segment["text"][a - offset : b - offset]}
            if not segment["synthetic"] and not segment.get("transformation"):
                item["start"] = segment["start"] + a - offset
                item["end"] = segment["start"] + b - offset
            result.append(item)
        offset = stop
    offset = 0
    for segment in result:
        segment["snippet_start"], segment["snippet_end"] = offset, offset + len(segment["text"])
        offset = segment["snippet_end"]
    return result


def lexical_text(row):
    """Original glyphs remain the primary lexical field; aliases live separately."""
    labels = list(row.get("toc_path", []))
    segments = row.get("snippet_segments")
    if segments is not None:
        body = "".join(
            segment["text"] if not segment["synthetic"] or segment["text"].isspace() else "\n"
            for segment in segments
        )
        labels.extend([row.get("book_title", ""), " / ".join(row.get("section_path", []))])
    else:
        body = row.get("retrieval_text", row.get("snippet"))
        if body is None:
            body = visible_text(row["text"])
    return "\n".join([*(visible_text(value) for value in labels if value), body])
