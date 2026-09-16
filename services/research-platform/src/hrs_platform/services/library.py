"""Publish reviewed source as coherent chapters with physical-page provenance."""

import json
import re
from functools import cached_property
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update

from hrs_platform import models as db
from hrs_platform.domain.book_structure import (
    BOOK_SYSTEM,
    batches_for,
    classify_ranges,
    validate_batch,
    validate_outline,
    validate_outline_boundaries,
)
from hrs_platform.services.books import get_run
from hrs_platform.services.chapter_content import chapter_content
from hrs_platform.services.outputs import Outputs, fingerprint
from hrs_platform.services.review import Review, digest
from hrs_platform.services.structure_views import POLICY as STRUCTURE_POLICY
from hrs_platform.services.structure_views import describe_structure, project_structure


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
        from hrs_platform.services.agents import Models

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
            content = self.review.objects.put_bytes(json.dumps(group, ensure_ascii=False).encode("utf-8"), run_id=run_id)
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
            from hrs_platform.services.deletion import require_active
            require_active(connection, run["book_id"])
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

    def amend(self, run_id, amendments):
        """Apply located, same-length errata after local original-image verification.

        This narrow operation preserves all existing coordinates and human decisions.
        Larger edits require a new source revision and repartitioning, not offset guessing.
        """
        import os
        import subprocess

        run = get_run(self.engine, run_id)
        if not run['result'].get('published') or run['pending_count']:
            raise ValueError('Errata require a published book with no pending review.')
        dependency = {'amendments': amendments, 'conversion': run['conversion']}
        step = 'library-amendment:' + fingerprint(dependency)
        prior = self.outputs.get(run_id, step, dependency)
        if prior:
            return prior
        chapters = [self.chapter(r['id']) for r in self.chapters(run['book_id'])]
        bodies, originals, scopes = {}, {}, {}
        for request in amendments:
            if request['chapter_id'] in bodies:
                raise ValueError('Combine errata for the same chapter in one request.')
            chapter = next(c for c in chapters if str(c['id']) == request['chapter_id'])
            if chapter['content']['sha256'] != request['expected_content_sha256']:
                raise ValueError('Chapter changed before errata verification.')
            body = self.review.read_json(chapter['content'])
            seen = set()
            for edit in request['changes']:
                start, before, after = edit['start'], edit['before'], edit['after']
                end = start + len(before)
                if start < 0 or not before or len(before) != len(after) or chapter['text'][start:end] != before:
                    raise ValueError('Errata must match a nonempty same-length source range.')
                if seen.intersection(range(start, end)):
                    raise ValueError('Overlapping errata.')
                seen.update(range(start, end))
                offset = 0
                for part in body['parts']:
                    stop = offset + len(part['text'])
                    if offset <= start < end <= stop:
                        pages = part['source']['pages']
                        if len(pages) != 1 or part['source'].get('human_decision_id'):
                            raise ValueError('Errata cannot overwrite an explicit human edit or an ambiguous page range.')
                        page = pages[0]
                        part['text'] = part['text'][:start-offset] + after + part['text'][end-offset:]
                        scopes.setdefault(page, []).append(after)
                        break
                    offset = stop
                else:
                    raise ValueError('Erratum crosses source ownership boundaries.')
            originals[request['chapter_id']] = chapter['content']
            bodies[request['chapter_id']] = body
        if not scopes:
            raise ValueError('No errata supplied.')
        bundle = self.review.read_json(run['conversion'])
        page_text = {}
        for chapter in chapters:
            parts = bodies.get(str(chapter['id']), chapter)['parts']
            for part in parts:
                if len(part['source']['pages']) == 1:
                    page = part['source']['pages'][0]
                    page_text[page] = page_text.get(page, '') + part['text']
        receipts = []
        output = self.settings.cache_root / str(run_id) / step.replace(':', '-')
        for number, revised in scopes.items():
            if any(page_text[number].count(text) != 1 for text in revised):
                raise ValueError('The revised review scope is not unique on its page.')
            pages = []
            for page in bundle['manifest']['pages']:
                if abs(page['page'] - number) > 1:
                    continue
                image = self.review.objects.materialize(bundle['files'][page['image']], output / page['image'])
                pages.append({**page, 'text': page_text.get(page['page'], ''), 'image_path': str(image),
                    'verified': False, 'receipts': [], 'changes': [], 'concerns': [],
                    **({'review_scope': [{'text': text, 'start': page_text[number].index(text),
                        'end': page_text[number].index(text)+len(text), 'reasons': ['核对这一处修订后的原文，保留其余既有审核决定。']}
                        for text in revised]} if page['page'] == number else {})})
            packet = output / f'{number}-input.json'
            packet.write_text(json.dumps(pages, ensure_ascii=False, default=str), 'utf-8')
            target = output / f'{number}-result.json'
            root = self.settings.project_root / 'services/document-extraction'
            python = root / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
            # Reuse the conversion runtime rather than installing OCR dependencies in the platform.
            process = subprocess.run([str(python), '-c',
                'import json,sys; from pathlib import Path; '
                'from document_extraction.settings import Settings; '
                'from document_extraction.semantic_completion import complete_document; '
                'pages=json.loads(Path(sys.argv[1]).read_text("utf-8")); '
                'pages=[dict(p,image_path=Path(p["image_path"])) for p in pages]; '
                'r=complete_document(pages, '
                'Settings.load(Path(sys.argv[2])).vision_review, Path(sys.argv[3]), target_pages={int(sys.argv[4])}); '
                'Path(sys.argv[5]).write_text(json.dumps(r,ensure_ascii=False,default=str),"utf-8")',
                str(packet), str(root/'config/default.json'), str(output/str(number)), str(number), str(target)],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=900, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if process.returncode:
                raise ValueError('Conversion review runtime failed: ' + process.stderr[-2000:])
            result = json.loads(target.read_text('utf-8'))
            checked = next(p for p in result['pages'] if p['page'] == number)
            receipt = {k: checked[k] for k in ('page', 'verified', 'receipts', 'concerns', 'input_text_sha256', 'final_text_sha256')}
            receipt['reviewer'] = result['reviewer']
            self.outputs.put(run_id, step+':check:'+fingerprint(receipt), receipt, dependency)
            if not checked['verified'] or checked['text'] != page_text[number]:
                raise ValueError(f'Local original-image verification did not accept page {number}; source unchanged.')
            receipts.append(receipt)
        evidence = {'changes': amendments, 'receipts': receipts, 'machine_review': True, 'human_review': False,
                    'previous_content': originals}
        evidence_ref = self.review.objects.put_bytes(json.dumps(evidence, ensure_ascii=False).encode(), run_id=run_id)
        references = {}
        for identity, body in bodies.items():
            body.setdefault('amendments', []).append(evidence_ref)
            for part in body['parts']:
                if any(p in scopes for p in part['source']['pages']):
                    part['source'].setdefault('amendments', []).append(evidence_ref)
            references[identity] = self.review.objects.put_bytes(json.dumps(body, ensure_ascii=False).encode(), run_id=run_id)
        saved = {'pages': sorted(scopes), 'content': references, 'evidence': evidence_ref, 'reindex_required': True}
        saved_ref = self.review.objects.put_bytes(json.dumps(saved, ensure_ascii=False).encode(), run_id=run_id)
        with self.engine.begin() as connection:
            current = connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update()).mappings().one()
            if current['conversion'] != run['conversion'] or current['pending_count']:
                raise ValueError('Source or review state changed during verification.')
            for identity, reference in references.items():
                row = connection.execute(select(db.chapters).where(db.chapters.c.id == identity).with_for_update()).mappings().one()
                if row['content'] != originals[identity]:
                    raise ValueError('Chapter changed during verification.')
                connection.execute(update(db.chapters).where(db.chapters.c.id == identity).values(content=reference))
            connection.execute(update(db.runs).where(db.runs.c.id == run_id).values(
                result={**current['result'], 'source_amendment': evidence_ref,
                        'retrieval_generation': 'pending-amendment-' + evidence_ref['sha256']},
                revision=current['revision']+1))
            connection.execute(insert(db.events).values(book_id=run['book_id'], run_id=run_id,
                kind='library.revised', payload={'pages': sorted(scopes), 'chapters': list(references)}))
            # Commit the checkpoint with the chapter pointers, so retries cannot reapply an edit.
            connection.execute(insert(db.stage_outputs).values(run_id=run_id, step=step,
                input_sha256=fingerprint(dependency), reference=saved_ref))
        return saved

    def chapter(self, chapter_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(db.chapters).where(db.chapters.c.id == str(chapter_id)))
                .mappings()
                .one_or_none()
            )
        if not row:
            raise HTTPException(404, "章节尚未发布。")
        return {**row, **chapter_content(self.settings, row['content'])}
