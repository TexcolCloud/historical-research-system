"""Structure-aware retrieval projections over immutable chapter character ranges.

Canonical text is never rewritten. Repeated table headings and linked notes are
separate, source-mapped supplements. Card reading reuses this parser with larger,
non-overlapping units and validates complete chapter coverage independently.
"""

import re
from functools import lru_cache

from langchain_text_splitters import RecursiveCharacterTextSplitter
from markdown_it import MarkdownIt

from hrs.library.footnotes import resolve_footnotes
from hrs.library.parsing import TableRows as TableRows
from hrs.library.parsing import blocks as blocks
from hrs.search.projection import (
    PROJECTION_RULE,
    prepare_projection,
    primary_ranges,
    project_hit,
    source_blocks,
    source_excerpts,
    source_relations,
    visible_text,
)
from hrs.search.projection import (
    source_excerpt as source_excerpt,
)

CHUNK_RULE = "structure-1400-160-v12-source-facts"
VECTOR_INPUT_RULE = PROJECTION_RULE

# Compatibility export for callers that clean arbitrary model text.
embedding_text = visible_text


def table_groups(text, block, size):
    start, end = block["start"], block["end"]
    raw = text[start:end]
    if block["kind"] == "table":
        offsets = [0]
        for line in raw.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        header_end = offsets[min(2, len(offsets) - 1)]
        rows = [(offsets[i], offsets[i + 1], 1, False) for i in range(2, len(offsets) - 1)]
    else:
        rows = TableRows(raw).rows
        header_rows = 0
        while header_rows < len(rows) and rows[header_rows][3]:
            header_rows += 1
        # First row remains context even when the source uses td for column labels.
        header_rows = max(1, header_rows) if rows else 0
        header_end = rows[header_rows - 1][1] if rows else len(raw)
        if any(i + row[2] > header_rows for i, row in enumerate(rows[:header_rows])):
            return [(start, end)], (start, start + header_end)
        rows = rows[header_rows:]
    if not rows or end - start <= size:
        return [(start, end)], (start, start + header_end)
    groups, group_start, covered_until = [], 0, -1
    for index, (row_start, row_end, rowspan, _) in enumerate(rows):
        if row_end - group_start > size and row_start > group_start and index > covered_until:
            # Do not emit a header-only chunk.
            if group_start or index:
                groups.append((start + group_start, start + row_start))
                group_start = row_start
        covered_until = max(covered_until, index + rowspan - 1)
    groups.append((start + group_start, end))
    return groups, (start, start + header_end)


def retrieval_chunks(chapter, book_title, size=1400, overlap=160):
    text = chapter["text"]
    notes = chapter.get("footnotes")
    if notes is None:
        notes = resolve_footnotes(text, chapter["parts"])
    plan = prepare_projection(chapter, footnotes=notes)
    structure = source_blocks(chapter, plan=plan)
    relations = source_relations(chapter, plan=plan)
    split_structure = []
    for block in structure:
        cuts = sorted(
            {
                block["start"],
                block["end"],
                *(
                    point
                    for n in notes
                    for point in (n["note"]["start"], n["note"]["end"])
                    if block["start"] < point < block["end"]
                ),
            }
        )
        for start, end in zip(cuts, cuts[1:]):
            note = next((n for n in notes if n["note"]["start"] <= start < n["note"]["end"]), None)
            if (
                note
                and split_structure
                and split_structure[-1].get("note_start") == note["note"]["start"]
            ):
                split_structure[-1]["end"] = end
            else:
                split_structure.append(
                    {
                        **block,
                        "start": start,
                        "end": end,
                        **({"kind": "note", "note_start": note["note"]["start"]} if note else {}),
                    }
                )
    structure = split_structure
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=["\n\n", "。", "；", "！", "？", "\n", " ", ""],
        keep_separator="end",
        strip_whitespace=False,
        add_start_index=True,
    )
    units, pending = [], None
    for block in structure:
        ordinary = block["kind"] in {"paragraph", "heading"}
        if (
            ordinary
            and pending
            and (
                pending.get("headings_only")
                or (block["kind"] != "heading" and pending["section"] == block["section"])
            )
            and block["end"] - pending["start"] <= size
            and pending.get("projection_scope") == block.get("projection_scope")
        ):
            pending["end"] = block["end"]
            pending["path"] = list(dict.fromkeys([*pending["path"], *block["path"]]))
            pending["section"] = block["section"]
            pending["headings_only"] = (
                pending.get("headings_only", False) and block["kind"] == "heading"
            )
        else:
            if pending:
                units.append(pending)
            pending = dict(block, headings_only=block["kind"] == "heading")
        if not ordinary:
            units.append(pending)
            pending = None
    if pending:
        units.append(pending)
    for block in units:
        support = []
        if block["kind"] in {"table", "html_table"}:
            ranges, header = table_groups(text, block, size)
            support.append((*header, "table_header"))
            i = next(i for i, b in enumerate(structure) if b["start"] == block["start"])
            support.extend(table_support(text, structure, i))
        elif (
            block["kind"] in {"bullet_list", "ordered_list"}
            and block["end"] - block["start"] > size
        ):
            raw = text[block["start"] : block["end"]]
            offsets = [0]
            for line in raw.splitlines(keepends=True):
                offsets.append(offsets[-1] + len(line))
            starts = [
                offsets[t.map[0]]
                for t in MarkdownIt().parse(raw)
                if t.type == "list_item_open" and t.level == 1 and t.map
            ]
            boundaries = sorted({0, len(raw), *starts})
            ranges, start = [], 0
            for a, b in zip(boundaries, boundaries[1:]):
                if b - start > size and a > start:
                    ranges.append((block["start"] + start, block["start"] + a))
                    start = a
            ranges.append((block["start"] + start, block["end"]))
        elif block["kind"] in {"note", "fence", "code_block"}:
            # Preserve atomic structures, including long notes and list items.
            ranges = [(block["start"], block["end"])]
        else:
            ranges = [
                (
                    block["start"] + d.metadata["start_index"],
                    block["start"] + d.metadata["start_index"] + len(d.page_content),
                )
                for d in splitter.create_documents([text[block["start"] : block["end"]]])
            ]
        for start, end in ranges:
            hit = source_excerpt(chapter, start, end)
            additions = list(support)
            for note in notes:
                a, b = note["note"]["start"], note["note"]["end"]
                if any(start <= ref["start"] < end for ref in note["references"]):
                    additions.append((a, b, "footnote"))
                if start < b and end > a:
                    for owner in structure:
                        if any(
                            owner["start"] <= ref["start"] < owner["end"]
                            for ref in note["references"]
                        ):
                            additions.append((owner["start"], owner["end"], "note_owner"))
            for relation in relations:
                if relation["status"] != "ready" or not any(
                    m["start"] < b and m["end"] > a
                    for m in relation["members"]
                    for a, b in [
                        (start, end),
                        *((a, b) for a, b, role in additions if role == "note_owner"),
                    ]
                ):
                    continue
                if relation["kind"] == "table":
                    first = relation["members"][0]
                    owner = next(
                        (
                            b
                            for b in structure
                            if b["start"] == first["start"] and b["kind"] in {"table", "html_table"}
                        ),
                        None,
                    )
                    if owner:
                        _, shared_header = table_groups(text, owner, size)
                        additions = [a for a in additions if a[2] != "table_header"]
                        additions.append((*shared_header, "table_header"))
                    for m in relation["members"]:
                        index = next(
                            (i for i, b in enumerate(structure) if b["start"] == m["start"]), None
                        )
                        if index is not None:
                            additions.extend(table_support(text, structure, index))
            context = [
                dict(source_excerpt(chapter, a, b), role=role)
                for a, b, role in dict.fromkeys(additions)
                if not start <= a < b <= end
            ]
            # Mutable directory labels are lexical/rerank metadata, not vector inputs.
            path = list(dict.fromkeys([chapter["title"], *block["path"]]))
            yield project_hit(
                chapter,
                {
                    **hit,
                    "book_title": book_title,
                    "section_path": path,
                    "kind": block["kind"],
                    "context": context,
                    "oversized": end - start > size,
                },
                plan=plan,
            )


@lru_cache(maxsize=16)
def context_blocks(text):
    """Read-only Markdown ranges reused during result expansion."""
    return blocks(text)


def indexable_chunks(chapter, book_title):
    """Use reviewed source facts, never page position or number/text heuristics."""
    for chunk in retrieval_chunks(chapter, book_title):
        if chunk["raw_text"].strip():
            yield chunk


def table_support(text, structure, index):
    """Retain captions, event introductions and notes as immutable source ranges."""
    result = []
    for i in range(max(0, index - 2), min(len(structure), index + 3)):
        b = structure[i]
        if b["kind"] != "paragraph":
            continue
        raw = text[b["start"] : b["end"]].strip()
        note = re.match(r"^(?:表\s*[\d一二三四五六七八九十]+|单位[：:]|注[：:]|说明[：:])", raw)
        introduction = i == index - 1 and re.search(r"如下|下表|列于下|见表", raw)
        if note or introduction:
            result.append((b["start"], b["end"], "table_note" if note else "table_intro"))
    return result


def section_introductions(text, structure):
    """Carry an explicit list introduction across its numbered sibling headings."""
    result, active = {}, None
    for i, b in enumerate(structure):
        if b["kind"] != "heading":
            continue
        title = b["path"][-1] if b["path"] else ""
        numbered = re.match(r"([一二三四五六七八九十]+|\d+)[、.．]\s*", title)
        parent = b["path"][:-1]
        before = structure[max(0, i - 2) : i]
        if (
            before
            and before[-1]["kind"] == "paragraph"
            and re.search(
                r"(?:如下|下列|以下)[。:：\s]*$", text[before[-1]["start"] : before[-1]["end"]]
            )
        ):
            paragraphs = [before[-1]]
            if len(before) == 2 and before[0]["kind"] == "paragraph":
                paragraphs.insert(0, before[0])
            active = None
            if sum(p["end"] - p["start"] for p in paragraphs) <= 1200:
                active = (parent, [(p["start"], p["end"], "section_intro") for p in paragraphs])
                result[b["section"]] = active[1]
        elif active and numbered and numbered[1] not in {"一", "1"} and parent == active[0]:
            result[b["section"]] = active[1]
        else:
            active = None
    return result


def shared_table_scopes(text, structure, start, end):
    """Describe shared cells without assigning a group value to individual rows."""
    scopes = []
    for block in structure:
        if block["kind"] != "html_table" or not (block["start"] < end and block["end"] > start):
            continue
        table = TableRows(text[block["start"] : block["end"]])
        occupied = set()
        for cell in table.cells:
            rows = list(range(cell["row"], min(len(table.rows), cell["row"] + cell["rowspan"])))
            column = 0
            while (cell["row"], column) in occupied:
                column += 1
            columns = list(range(column, column + cell["colspan"]))
            occupied.update((r, c) for r in rows for c in columns)
            if cell["rowspan"] <= 1 and cell["colspan"] <= 1:
                continue
            scopes.append(
                {
                    "value": cell["text"].strip(),
                    "row_numbers": [r + 1 for r in rows],
                    "column_numbers": [c + 1 for c in columns],
                    "row_labels": [
                        [c["text"].strip() for c in table.cells if c["row"] == r and c != cell]
                        for r in rows
                    ],
                    "start": block["start"] + cell["start"],
                    "end": block["start"] + cell["end"],
                    "rule": "该单元格跨越所列行列；共享统计量不能拆分或当作任一单行、单列的独立数值。",
                }
            )
    return scopes


class _PendingCandidate:
    """Keep source geometry cheap until merging or result budgets require context."""

    def __init__(self, raw, materialize):
        self.raw, self.materialize, self.value = raw, materialize, None

    def ensure_context(self):
        if self.value is None:
            self.value = self.materialize()
        return self.value

    def merge(self, merged):
        self.value = self.ensure_context()
        self.value.update(merged)
        self.raw = self.value


def expand_hits(
    hits,
    load_chapter,
    limit=20,
    context_chars=6000,
    total_chars=24000,
    *,
    diverse=False,
    metrics=None,
    source_scope="all",
    excerpt=None,
):
    """Merge intersecting evidence and spend a separate, bounded context budget."""
    from hrs.search.ranking import diverse_order, group_exact_results

    metrics = metrics if metrics is not None else {}
    excerpt = source_excerpts() if excerpt is None else excerpt
    purpose = "lexical" if source_scope == "all" else "vector"
    metrics.update(budget_rejected=0, duplicate_rejected=0)
    selected, chapters, structures, introductions, plans = [], {}, {}, {}, {}
    duplicates = set()
    ordered = (
        diverse_order(hits, relevance_window=limit * 2)
        if diverse
        else sorted(hits, key=lambda h: h["score"], reverse=True)
    )
    for hit in ordered:
        duplicate = (
            hit["chapter_id"],
            hit["start"],
            hit["end"],
            tuple((c["start"], c["end"], c["role"]) for c in hit.get("context", [])),
        )
        if duplicate in duplicates:
            metrics["duplicate_rejected"] += 1
            continue
        duplicates.add(duplicate)
        chapter_id = hit["chapter_id"]
        if chapter_id not in chapters:
            chapters[chapter_id] = load_chapter(chapter_id)
            text = chapters[chapter_id]["text"]
            plans[chapter_id] = prepare_projection(chapters[chapter_id])
            structures[chapter_id] = source_blocks(chapters[chapter_id], plan=plans[chapter_id])
            introductions[chapter_id] = section_introductions(text, structures[chapter_id])
        chapter = chapters[chapter_id]
        plan = plans[chapter_id]
        hit = {**hit, "context": list(hit.get("context", []))}
        structure, text = structures[chapter_id], chapter["text"]
        additions = []
        for i, b in enumerate(structure):
            if b["start"] < hit["end"] and b["end"] > hit["start"]:
                additions.extend(introductions[chapter_id].get(b["section"], []))
                if b["kind"] in {"table", "html_table"}:
                    additions.extend(table_support(text, structure, i))
        for relation in source_relations(chapter, plan=plan):
            if (
                relation["kind"] == "table"
                and relation["status"] == "ready"
                and any(
                    m["start"] < hit["end"] and m["end"] > hit["start"] for m in relation["members"]
                )
            ):
                first = relation["members"][0]
                index = next(
                    (i for i, b in enumerate(structure) if b["start"] == first["start"]), None
                )
                if index is not None:
                    additions.extend(table_support(text, structure, index))
        primary, _ = primary_ranges(
            chapter, hit["start"], hit["end"], purpose=purpose, source_scope=source_scope, plan=plan
        )
        supplement_ranges = tuple(dict.fromkeys(additions))

        def materialize(hit=hit, chapter=chapter, plan=plan, additions=supplement_ranges):
            hit["context"].extend(
                dict(excerpt(chapter, a, b), role=role) for a, b, role in dict.fromkeys(additions)
            )
            return project_hit(
                chapter,
                hit,
                source_scope=source_scope,
                purpose=purpose,
                plan=plan,
                include_view=False,
                excerpt=excerpt,
            )

        candidate = _PendingCandidate(hit, materialize)
        # Preserve errors/ignores for malformed external contexts even beyond the
        # result limit; valid source ranges need no semantic context yet.
        ranges = [(c.get("start"), c.get("end")) for c in hit.get("context", [])]
        ranges.extend(additions_pair[:2] for additions_pair in additions)
        if any(
            type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text)
            for a, b in ranges
        ):
            candidate.ensure_context()
        if not primary:
            continue
        overlaps = [
            h
            for h in selected
            if h.raw["chapter_id"] == chapter_id
            and hit["start"] < h.raw["end"]
            and h.raw["start"] < hit["end"]
        ]
        if overlaps:
            hit = candidate.ensure_context()
            overlap_hits = [h.ensure_context() for h in overlaps]
            start = min(hit["start"], *(h["start"] for h in overlap_hits))
            end = max(hit["end"], *(h["end"] for h in overlap_hits))
            supplements = {
                (c["start"], c["end"]): c
                for h in [hit, *overlap_hits]
                for c in h.get("context", [])
                if c["role"] != "neighbor"
            }
            merged = project_hit(
                chapter,
                {
                    **hit,
                    **excerpt(chapter, start, end),
                    "context": list(supplements.values()),
                },
                source_scope=source_scope,
                purpose=purpose,
                plan=plan,
                include_view=False,
                excerpt=excerpt,
            )
            merged_size = sum(
                r["end"] - r["start"]
                for r in merged["ranges"]
                if r.get("context_role") not in {"neighbor", "unassigned_context"}
            )
            if merged_size <= min(context_chars, total_chars):
                overlap = overlaps[0]
                merged["score"] = max(hit["score"], *(h["score"] for h in overlap_hits))
                overlap.merge(merged)
                for other in overlaps[1:]:
                    selected.remove(other)
                continue
            if any(h["start"] <= hit["start"] < hit["end"] <= h["end"] for h in overlap_hits):
                continue
        selected.append(candidate)
    retained, remaining, primary_texts = [], total_chars, set()
    for candidate in selected:
        hit = candidate.raw
        if len(primary_texts) >= limit and hit["text"] not in primary_texts:
            continue
        hit = candidate.ensure_context()
        # Reserve source-linked constraints before spending on another primary hit.
        mandatory = {}
        for extra in hit["context"]:
            if extra["role"] not in {"neighbor", "unassigned_context"}:
                mandatory[(extra["start"], extra["end"])] = extra
        primary_size = sum(r["end"] - r["start"] for r in hit["ranges"] if r["usage"] == "primary")
        duplicate_primary = hit["text"] in primary_texts
        needed = (0 if duplicate_primary else primary_size) + sum(
            len(c["text"]) for c in mandatory.values()
        )
        if (
            needed <= remaining
            and primary_size + sum(len(c["text"]) for c in mandatory.values()) <= context_chars
        ):
            retained.append((hit, sum(len(c["text"]) for c in mandatory.values())))
            primary_texts.add(hit["text"])
            remaining -= needed
        else:
            metrics["budget_rejected"] += 1
    output = []
    for hit, reserved in retained:
        primary_size = sum(r["end"] - r["start"] for r in hit["ranges"] if r["usage"] == "primary")
        available = min(max(0, context_chars - primary_size), remaining + reserved)
        remaining += reserved
        structure, chapter = structures[hit["chapter_id"]], chapters[hit["chapter_id"]]
        plan = plans[hit["chapter_id"]]
        indexes = [
            i
            for i, b in enumerate(structure)
            if b["start"] < hit["end"] and hit["start"] < b["end"]
        ]
        # Neighbor expansions are recomputed from current boundaries, never accumulated on a second pass.
        additions = [c for c in hit["context"] if c["role"] != "neighbor"]
        if indexes and hit.get("kind") not in {"table", "html_table"}:
            first, last = indexes[0], indexes[-1]
            for i in range(max(0, first - 1), min(len(structure), last + 2)):
                b = structure[i]
                # Complete cut paragraphs; intact paragraphs rely on explicit semantic supplements.
                if i < first or (
                    i > last
                    and structure[last]["kind"] != "heading"
                    and not hit["text"].rstrip().endswith((":", "："))
                ):
                    continue
                if i > last and b["section"] != structure[last]["section"]:
                    continue
                # Complete a split paragraph before considering adjoining paragraphs.
                for start, end in [
                    (b["start"], min(b["end"], hit["start"])),
                    (max(b["start"], hit["end"]), b["end"]),
                ]:
                    if start < end:
                        additions.append(dict(excerpt(chapter, start, end), role="neighbor"))
        additions = project_hit(
            chapter,
            {**hit, "context": additions},
            source_scope=source_scope,
            purpose=purpose,
            plan=plan,
            include_view=False,
            excerpt=excerpt,
        )["context"]
        context, seen = [], set()
        occupied = [(r["start"], r["end"]) for r in hit["ranges"] if r["usage"] == "primary"]
        truncated = False
        for extra in additions:
            key = (extra["start"], extra["end"])
            if key in seen:
                continue
            seen.add(key)
            ranges = [key]
            for a, b in occupied:
                ranges = [
                    (x, y)
                    for start, end in ranges
                    for x, y in (
                        [(start, min(end, a)), (max(start, b), end)]
                        if start < b and a < end
                        else [(start, end)]
                    )
                    if x < y
                ]
            needed = sum(b - a for a, b in ranges)
            if needed > available:
                truncated = True
                continue
            for a, b in ranges:
                context.append({**extra, **excerpt(chapter, a, b), "role": extra["role"]})
                occupied.append((a, b))
            available -= needed
            remaining -= needed
        output.append(
            project_hit(
                chapter,
                {
                    **hit,
                    "context": context,
                    "context_truncated": truncated,
                    "table_scopes": shared_table_scopes(
                        chapter["text"], structure, hit["start"], hit["end"]
                    ),
                },
                source_scope=source_scope,
                purpose=purpose,
                plan=plan,
                excerpt=excerpt,
                include_relations=False,
            )
        )
    return group_exact_results(output)
