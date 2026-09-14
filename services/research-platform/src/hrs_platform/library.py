"""Publish reviewed source as coherent chapters with physical-page provenance."""

import json
import re
from functools import cached_property
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update

from . import schema as db
from .books import get_run
from .domain.book_structure import (
    BOOK_SYSTEM,
    batches_for,
    classify_ranges,
    validate_batch,
    validate_outline,
    validate_outline_boundaries,
)
from .footnotes import resolve_footnotes
from .outputs import Outputs, fingerprint
from .review import Review, digest
from .structure_views import POLICY as STRUCTURE_POLICY
from .structure_views import describe_structure, project_structure, reading_view


class OutlineItem(BaseModel):
    title: str
    role: str


class Outline(BaseModel):
    mode: str
    items: list[OutlineItem]
    reason: str


class Boundary(BaseModel):
    unit_id: str
    quote: str
    kind: str
    title: str
    reason: str


class OrganizationBatch(BaseModel):
    reviewed_unit_ids: list[str]
    boundaries: list[Boundary]


class BookOutline(BaseModel):
    organization_outline: Outline


def outline_context(spans):
    text = "".join(row["selected_text"] for row in spans)
    headings = []
    for match in re.finditer(
        r"(?m)^[ \t]*(?:#{1,6}\s+[^\n]+|第[一二三四五六七八九十百零〇0-9]+[编篇章节卷][^\n]*)$", text
    ):
        headings.append({"offset": match.start(), "text": match.group().strip()})
    return {
        "opening": text[:18000],
        "ending": text[-4000:],
        "heading_candidates": headings,
        "total_codepoints": len(text),
        "opening_is_complete_book": len(text) <= 18000,
    }


def reviewed_spans(markdown, pages, corrections, run_id):
    """Apply explicit human revisions while retaining their original-page ranges."""
    corrections = sorted(corrections, key=lambda row: row["start"])
    if any(a["end"] > b["start"] for a, b in zip(corrections, corrections[1:])):
        raise ValueError("Overlapping human revisions require conflict resolution.")
    cuts = sorted({point for row in [*pages, *corrections] for point in (row["start"], row["end"])})
    result, offset = [], 0
    for start, end in zip(cuts, cuts[1:]):
        edit = next((row for row in corrections if row["start"] <= start < row["end"]), None)
        if edit and start != edit["start"]:
            continue
        if edit:
            end = edit["end"]
        original_pages = [page["page"] for page in pages if page["start"] < end and page["end"] > start]
        if not original_pages:
            if result and not markdown[start:end].strip():
                gap = markdown[start:end]
                result[-1]["selected_text"] += gap
                result[-1]["end_offset"] += len(gap)
                result[-1]["text_sha256"] = digest(result[-1]["selected_text"])
                offset += len(gap)
            continue
        value = edit["text"] if edit else markdown[start:end]
        if not value:
            continue
        # Page boundaries supply provenance; they never imply a chapter boundary.
        result.append(
            {
                "id": str(uuid5(UUID(run_id), f"span:{start}:{end}")),
                "start_offset": offset,
                "end_offset": offset + len(value),
                "selected_text": value,
                "text_sha256": digest(value),
                "source_record": {
                    "page": original_pages[0],
                    "pages": original_pages,
                    "original_start": start,
                    "original_end": end,
                    "human_decision_id": edit.get("decision_id") if edit and edit.get("reviewer", "human-ui") == "human-ui" else None,
                    "machine_decision_id": edit.get("decision_id") if edit and edit.get("reviewer", "human-ui") != "human-ui" else None,
                },
            }
        )
        offset += len(value)
    return result


class Library:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.outputs = Outputs(settings, engine)
        self.review = Review(settings, engine)

    @cached_property
    def models(self):
        from .agents import Models

        return Models(self.settings, self.engine)

    def prepare(self, run_id):
        cached = self.outputs.get(run_id, "reviewed-source")
        if cached is not None:
            return cached
        status = self.review.status(run_id)
        if status["pending_count"]:
            raise ValueError("Human review must finish before ingestion or partitioning.")
        run = get_run(self.engine, run_id)
        bundle = self.review.read_json(run["conversion"])
        spans = self._source_spans(run_id, bundle)
        if not spans:
            raise ValueError("No usable source text; the source remains preserved.")
        return self.outputs.put(
            run_id,
            "reviewed-source",
            {
                "spans": spans,
                "source_sha256": run["source"]["sha256"],
                "review_revision": status["revision"],
                "page_count": bundle['manifest']["page_count"],
            },
            {"conversion": run["conversion"]["sha256"], "review_revision": status["revision"]},
        )

    def _source_spans(self, run_id, bundle):
        files, manifest = bundle["files"], bundle["manifest"]
        markdown = self.review.objects.read_bytes(files[manifest["candidate_markdown"]]).decode("utf-8")
        pages = self.review.read_json(files[manifest["page_boundaries"]])["pages"]
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(db.review_issues, db.review_decisions.c.id.label("decision_id"), db.review_decisions.c.receipt.label("decision_receipt"))
                    .join(db.review_decisions, db.review_decisions.c.issue_id == db.review_issues.c.id)
                    .where(db.review_issues.c.run_id == run_id, db.review_issues.c.replacement.is_not(None))
                )
                .mappings()
                .all()
            )
        corrections = []
        for row in rows:
            # JSON null passes SQL IS NOT NULL; unchanged confirmations have no replacement.
            if row["replacement"] is None:
                continue
            body = self.review.read_json(row["content"])
            corrections.append(
                {
                    "start": body["start"],
                    "end": body["end"],
                    "decision_id": row["decision_id"],
                    "reviewer": row["decision_receipt"].get("reviewer", "human-ui"),
                    "text": self.review.objects.read_bytes(row["replacement"]).decode("utf-8"),
                }
            )
        return reviewed_spans(markdown, pages, corrections, run_id)

    def structure(self, run_id):
        """Reuse retained structure and current decisions; no OCR or model calls."""
        run = get_run(self.engine, run_id)
        status = self.review.status(run_id)
        dependency = {'policy': STRUCTURE_POLICY, 'conversion': run['conversion'],
                      'review_revision': status['revision']}
        key = 'reading-structure:' + fingerprint(dependency)
        if run['conversion']:
            cached = self.outputs.get(run_id, key, dependency)
            if cached is not None:
                return cached
            bundle = self.review.read_json(run['conversion'])
            files = bundle['files']
            spans = self._source_spans(run_id, bundle)
            parts = [{'span_id': s['id'], 'start': s['start_offset'], 'end': s['end_offset'],
                      'text': s['selected_text'], 'source': s['source_record']} for s in spans]
            completion = self.review.read_json(files['completion.json'])['pages'] if 'completion.json' in files else []
            pages = {p['page'] for p in bundle['manifest']['pages']}
            if (run['result'] or {}).get('review_initialized'):
                with self.engine.connect() as connection:
                    pending = connection.execute(select(db.review_issues.c.page, db.review_issues.c.content).where(
                        db.review_issues.c.run_id == run_id, db.review_issues.c.state == 'pending')).mappings().all()
                unresolved = {p for row in pending for p in self.review.read_json(row['content']).get('pages', [row['page']])}
                approved = pages - unresolved
            else:
                approved = {p['page'] for p in bundle['manifest']['pages'] if p['status'] == 'release-accepted'}
        else:
            draft = self.review.ocr_draft(run)
            if draft is None:
                return {'policy': STRUCTURE_POLICY, 'available': False, 'items': []}
            files, checkpoint = draft
            parts = [{'text': p['text'] + '\n\n', 'source': {'pages': [p['page']]}} for p in checkpoint['pages']]
            completion, approved = [], set()
        metadata = self.review.read_json(files['paddle-restructure.json']) if 'paddle-restructure.json' in files else {}
        document = {'text': ''.join(p['text'] for p in parts), 'parts': parts}
        report = describe_structure(document, metadata, completion, approved)
        return self.outputs.put(run_id, key, report, dependency) if run['conversion'] else report

    async def organize(self, run_id):
        source = self.prepare(run_id)
        spans = source["spans"]
        cached = self.outputs.get(run_id, "organization")
        if cached:
            return cached
        structure = self.structure(run_id)
        heading_evidence = [{k: item[k] for k in ('title', 'pages', 'level')}
                            for item in structure['items'] if item['kind'] == 'heading' and item['status'] == 'ready']
        batches = list(batches_for(spans, 12000))
        overview = await self.models.run(
            run_id,
            "全书章节总纲",
            BOOK_SYSTEM
            + "\n本步只制定 organization_outline。heading_candidates 来自整书扫描，可能包括小节和重复页眉。"
            "结合目录及首尾内容确定顶层章、序跋和附录，不把物理页或下级小节单独当成章。",
            {**outline_context(spans), 'source_heading_hierarchy': heading_evidence},
            BookOutline,
            validate=lambda value: validate_outline(value.model_dump()),
        )
        events, outline = [], validate_outline(overview.model_dump())
        for index, batch in enumerate(batches):
            payload = {
                "organization_outline": outline,
                'source_heading_hierarchy': [h for h in heading_evidence if any(
                    p in h['pages'] for s in batch for p in s['source_record']['pages'])],
                "opened_items": [event["title"] for event in events if event["kind"] == "article"],
                "active_boundary": events[-1] if events else None,
                "units": [
                    {
                        "id": row["unit_id"],
                        "physical_page": row["source_record"]["page"],
                        "text": row["selected_text"],
                    }
                    for row in batch
                ],
            }

            def validate(value, batch=batch, outline=outline):
                raw = value.model_dump()
                current = validate_batch(raw, batch)
                current_outline = outline or validate_outline(raw)
                validate_outline_boundaries(current_outline, events, current)

            raw = (
                await self.models.run(
                    run_id, f"章节组织:{index}", BOOK_SYSTEM, payload, OrganizationBatch, validate=validate
                )
            ).model_dump()
            current = validate_batch(raw, batch)
            outline = outline or validate_outline(raw)
            events.extend(current)
        _, ledger = classify_ranges(spans, events, [])
        # Non-article material stays in the book; it is not silently discarded from the reader.
        groups = []
        by_id = {str(row["id"]): row for row in spans}
        for row in ledger:
            key = (row["group_key"], row["title"])
            if not groups or groups[-1]["key"] != list(key):
                groups.append(
                    {
                        "key": list(key),
                        "title": row["title"] if row["group_key"] != -1 else "书前材料",
                        "kind": row["kind"],
                        "parts": [],
                    }
                )
            span = by_id[row["span_id"]]
            groups[-1]["parts"].append({**row, "source": span["source_record"]})
        if sum(len(row["text"]) for group in groups for row in group["parts"]) != sum(
            len(span["selected_text"]) for span in spans
        ):
            raise ValueError("Chapter organization did not cover the complete reviewed source.")
        return self.outputs.put(
            run_id,
            "organization",
            {"groups": groups, "outline": outline, "reviewed_batches": len(batches)},
            source,
        )

    def publish(self, run_id):
        if self.review.status(run_id)["pending_count"]:
            raise ValueError("The whole-book review gate is still closed.")
        run = get_run(self.engine, run_id)
        organization = self.outputs.get(run_id, "organization")
        if not organization:
            raise ValueError("Chapter organization is required.")
        structure = self.structure(run_id)
        rows = []
        for position, group in enumerate(organization["groups"]):
            group = {**group, 'structure_policy': STRUCTURE_POLICY,
                     'structure': project_structure(structure, group['parts'])}
            identity = str(uuid5(UUID(run_id), f"chapter:{position}"))
            content = self.review.objects.put_bytes(json.dumps(group, ensure_ascii=False).encode("utf-8"))
            rows.append(
                {
                    "id": identity,
                    "book_id": run["book_id"],
                    "run_id": run_id,
                    "position": position,
                    "title": group["title"],
                    "kind": group["kind"],
                    "content": content,
                    "pages": sorted({page for part in group["parts"] for page in part["source"]["pages"]}),
                    "codepoints": sum(len(part["text"]) for part in group["parts"]),
                }
            )
        with self.engine.begin() as connection:
            current = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            if (current["result"] or {}).get("published"):
                return {"book_id": run["book_id"], "chapters": len(rows)}
            if current["pending_count"]:
                raise ValueError("The whole-book review gate changed.")
            connection.execute(insert(db.chapters), rows)
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(
                    result={**current["result"], "published": True},
                    state="ready",
                    stage="indexing",
                    revision=current["revision"] + 1,
                    updated_at=func.now(),
                )
            )
            connection.execute(update(db.books).where(db.books.c.id == run["book_id"]).values(state="ready"))
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run_id,
                    kind="book.published",
                    payload={
                        "state": "ready",
                        "stage": "indexing",
                        "chapter_count": len(rows),
                        "revision": current["revision"] + 1,
                    },
                )
            )
        return {"book_id": run["book_id"], "chapters": len(rows)}

    def chapters(self, book_id):
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    select(db.chapters)
                    .where(db.chapters.c.book_id == str(book_id))
                    .order_by(db.chapters.c.position)
                ).mappings()
            )

    def chapter(self, chapter_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(db.chapters).where(db.chapters.c.id == str(chapter_id)))
                .mappings()
                .one_or_none()
            )
        if not row:
            raise HTTPException(404, "章节尚未发布。")
        body = self.review.read_json(row["content"])
        text = "".join(part["text"] for part in body["parts"])
        chapter = {**row, "parts": body["parts"], "text": text, 'structure': body.get('structure', []),
                   'footnotes':resolve_footnotes(text,body['parts'])}
        reading = reading_view(chapter)
        return {**chapter, 'reading_text': reading['text'], 'reading_footnotes': reading['footnotes']}
