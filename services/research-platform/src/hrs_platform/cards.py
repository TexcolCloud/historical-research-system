"""Dynamic book assignments, source-bound candidates and machine-only adoption."""

import asyncio
import json
from functools import cached_property
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import schema as db
from .activities import Activities
from .books import get_run
from .domain import prompts
from .domain.generation_contracts import CardDraft, SemanticCheck
from .library import Library
from .outputs import Outputs
from .reading import check_candidate_coverage, passed, read_batch, scoped_check, synthesis_readings
from .review import Review
from .search import chapter_chunks


class Assignment(BaseModel):
    name: str
    objective: str
    chapter_ids: list[str] = Field(min_length=1)


class ResearchPlan(BaseModel):
    rationale: str
    assignments: list[Assignment] = Field(min_length=1, max_length=64)


def validate_plan(plan, chapter_ids):
    assigned = [chapter for task in plan.assignments for chapter in task.chapter_ids]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(chapter_ids):
        raise ValueError("Every chapter must belong to exactly one research assignment.")


def validate_candidate(card, units):
    known = {row["unit_id"]: row for row in units}
    for item in card.items:
        if set(item.source_unit_ids) - known.keys():
            raise ValueError("Candidate refers to unavailable source units.")
        if item.kind == "evidence" and not item.selections:
            raise ValueError("An evidence item requires exact quotations from fixed source units.")
        for selection in item.selections:
            unit = known.get(selection.unit_id)
            if unit is None or unit["text"].count(selection.quote) <= selection.occurrence:
                raise ValueError("An evidence quotation must match the fixed reviewed source exactly.")
        if item.kind == "argument" and not item.evidence_refs:
            raise ValueError("An argument must declare its supporting or limiting evidence.")
    if not any(item.kind == "evidence" for item in card.items):
        raise ValueError("No traceable evidence was produced.")


def validate_check(check, card):
    wanted = {item.item_id for item in card.items}
    if set(check.checked_object_ids) | set(check.unverified_object_ids) != wanted:
        raise ValueError("The independent check must account for every candidate item.")


def validate_final_candidate(candidate, original, units):
    validate_candidate(candidate, units)
    frozen = lambda card: [
        item.model_dump(mode="json", exclude={"attribution", "context", "limitations"})
        for item in card.items
        if item.kind == "evidence"
    ]
    if frozen(candidate) != frozen(original):
        raise ValueError(
            "Final narrative revision must preserve every visually checked quotation, anchor, text and interpretation exactly; only attribution, context and limitations may change."
        )


def quote_pages(unit, selection):
    start = -1
    for _ in range(selection.get("occurrence", 0) + 1):
        start = unit["text"].find(selection["quote"], start + 1)
        if start < 0:
            raise ValueError("Quotation is absent from the reviewed source.")
    end = start + len(selection["quote"])
    pages = sorted(
        {
            page
            for source in unit["sources"]
            if source["unit_start"] < end and source["unit_end"] > start
            for page in source["pages"]
        }
    )
    if not pages:
        raise ValueError("Quotation has no original page provenance.")
    return pages


def visual_groups(candidate, units):
    groups = {}
    for item in candidate["items"]:
        pages = sorted(
            {
                page
                for selection in item["selections"]
                for page in quote_pages(units[selection["unit_id"]], selection)
            }
        )
        if not pages:
            continue
        key = "pages-" + ",".join(map(str, pages))
        groups.setdefault(key, {"key": key, "pages": pages, "items": []})["items"].append(item)
    return list(groups.values())


def neighbor_context(all_units, selected):
    """Preserve adjoining text and book-opening provenance across agent assignments."""
    selected_ids = {unit["unit_id"] for unit in selected}
    indexes = {i for i, row in enumerate(all_units) if row["unit_id"] in selected_ids}
    wanted = {0}
    for index in indexes:
        wanted.update((index - 1, index + 1))
    return [
        row for index, row in enumerate(all_units) if index in wanted and row["unit_id"] not in selected_ids
    ]


class Cards:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.outputs = Outputs(settings, engine)
        self.library, self.review = Library(settings, engine), Review(settings, engine)

    def create_run(self, parent_id, request_id=None):
        parent = get_run(self.engine, parent_id)
        if not (parent["result"] or {}).get("published"):
            raise ValueError("Cards require a published, reviewed book.")
        identity = str(uuid5(UUID(parent_id), str(request_id) if request_id else "cards"))
        with self.engine.begin() as connection:
            from .deletion import require_active
            require_active(connection, parent["book_id"])
            created = connection.scalar(
                pg_insert(db.runs)
                .values(
                    id=identity,
                    book_id=parent["book_id"],
                    parent_run_id=parent_id,
                    kind="cards",
                    state="queued",
                    stage="planning",
                    source=parent["source"],
                    conversion=parent["conversion"],
                )
                .on_conflict_do_nothing()
                .returning(db.runs.c.id)
            )
            if created:
                connection.execute(
                    insert(db.events).values(
                        book_id=parent["book_id"],
                        run_id=identity,
                        kind="run.created",
                        payload={"run_kind": "cards", "state": "queued", "stage": "planning"},
                    )
                )
            child = (
                connection.execute(select(db.runs).where(db.runs.c.id == identity).with_for_update())
                .mappings()
                .one()
            )
            # A parent retry reuses durable child outputs, but grants one new bounded
            # request epoch. Replayed activity completions cannot grant it twice.
            marker = (child["result"] or {}).get("parent_recovery_attempt", 0)
            attempt = child["recovery_attempt"]
            if request_id is None and child["state"] == "failed" and parent["recovery_attempt"] > marker:
                attempt += 1
                connection.execute(
                    update(db.runs)
                    .where(db.runs.c.id == identity)
                    .values(
                        recovery_attempt=attempt,
                        state="queued",
                        error=None,
                        result={
                            **(child["result"] or {}),
                            "parent_recovery_attempt": parent["recovery_attempt"],
                        },
                    )
                )
        from .recovery import workflow_id

        return {
            "run_id": identity,
            "book_id": parent["book_id"],
            "workflow_id": workflow_id("cards", identity, attempt),
        }

    def start(self, book_id, request_id):
        with self.engine.connect() as connection:
            parent = connection.scalar(
                select(db.runs.c.id).where(db.runs.c.book_id == str(book_id), db.runs.c.kind == "book")
            )
        if parent is None:
            raise HTTPException(404, "书籍不存在。")
        if not (get_run(self.engine, parent)["result"] or {}).get("published"):
            raise HTTPException(409, "请先完成本书内容核对与入库。")
        created = self.create_run(parent, request_id)
        with self.engine.begin() as connection:
            connection.execute(
                pg_insert(db.outbox)
                .values(
                    dedup_key=f"cards:{created['run_id']}",
                    run_id=created["run_id"],
                    kind="start_cards",
                    payload={"run_kind": "cards"},
                )
                .on_conflict_do_nothing()
            )
        return get_run(self.engine, created["run_id"])

    @cached_property
    def models(self):
        from .agents import Models

        return Models(self.settings, self.engine)

    async def generate(self, run_id):
        from agents import function_tool

        cached = self.outputs.get(run_id, "generated-cards")
        if cached:
            return {"run_id": run_id, "candidates": len(cached["cards"])}
        run = get_run(self.engine, run_id)
        chapters = self.library.chapters(run["book_id"])
        bundle = Review(self.settings, self.engine).read_json(run["conversion"])
        image_pages = sorted(
            {
                page["page"]
                for page in bundle["manifest"].get("pages", [])
                if page.get("image") in bundle["files"]
            }
        )
        Activities(self.settings, self.engine).transition(run_id, "processing", "planning")
        main = str(uuid5(UUID(run_id), "主研究 Agent"))

        @function_tool
        async def read_chapter_opening(chapter_id: str) -> str:
            """Read the opening of a chapter in this book to inform research assignments."""
            if chapter_id not in {row["id"] for row in chapters}:
                raise ValueError("Chapter is outside this book.")
            key = f"章节预览:{chapter_id}"
            self.outputs.node(
                run_id, key, kind="tool", label="读取章节开头", objective=chapter_id, parent=main
            )
            chapter = await asyncio.to_thread(self.library.chapter, chapter_id)
            result = {
                "chapter_id": chapter_id,
                "text": chapter["text"][:3000],
                "complete": len(chapter["text"]) <= 3000,
            }
            self.outputs.node(
                run_id,
                key,
                kind="tool",
                label="读取章节开头",
                objective=chapter_id,
                parent=main,
                state="completed",
                details=result,
            )
            return json.dumps(result, ensure_ascii=False)

        plan = await self.models.run(
            run_id,
            "主研究 Agent",
            "根据本书目录自主规划研究分工。每一章分配且只分配给一个子 Agent；相邻章节可组合。"
            "分工数量按实际内容决定，无固定层数或节点数。可调用工具读取章首辅助判断。输出每个分工的名称、目标和 chapter_ids。",
            {
                "chapters": [
                    {key: row[key] for key in ("id", "title", "kind", "pages", "codepoints")}
                    for row in chapters
                ]
            },
            ResearchPlan,
            model=self.settings.reasoning_model,
            tools=[read_chapter_opening],
            validate=lambda value: validate_plan(value, [row["id"] for row in chapters]),
        )
        all_units = []
        for chapter_row in chapters:
            chapter = await asyncio.to_thread(self.library.chapter, chapter_row["id"])
            all_units.extend(
                {**row, "unit_id": row["id"], "physical_page": row["pages"][0] if row["pages"] else None}
                for row in chapter_chunks(chapter, size=5000, overlap=0)
            )
        produced = []
        for number, assignment in enumerate(plan.assignments):
            group = f"研究分工:{number}"
            with self.outputs.operation(
                run_id,
                group,
                assignment.name,
                kind="group",
                objective=assignment.objective,
                parent=main,
            ) as branch:
                units = [row for row in all_units if row["chapter_id"] in assignment.chapter_ids]
                context = neighbor_context(all_units, units)
                available = [*units, *context]
                research_state = {
                    "available_book_chapters": [{"title": row["title"], "id": row["id"]} for row in chapters],
                    "assigned_chapter_ids": assignment.chapter_ids,
                    "assignment_covers_all_book_chapters": set(assignment.chapter_ids)
                    == {row["id"] for row in chapters},
                    "source_evidence": {
                        "available_original_pages": image_pages,
                        "source_page_count": bundle["manifest"]["page_count"],
                        "text_agent_has_viewed_images": False,
                        "original_image_check": "separate_local_visual_stage"
                        if image_pages
                        else "no_images_supplied",
                    },
                    "scope": "本卡研究分工仅覆盖指定章节；相邻正文和全书开头作为上下文。其他分工章节仍在本书中，不能称为书籍缺失。",
                }
                readings = []
                for offset in range(0, len(units), 2):
                    batch = units[offset : offset + 2]

                    reading = await read_batch(
                        self.models,
                        run_id,
                        f"{assignment.name}:阅读:{number}:{offset}",
                        batch,
                        assignment.objective,
                        branch,
                        readings[-1] if readings else None,
                        context=neighbor_context(all_units, batch),
                        research_state=research_state,
                    )
                    readings.append(reading)
                source = {
                    "units": available,
                    "assigned_unit_ids": [row["unit_id"] for row in units],
                    "readings": readings,
                    "assignment": assignment.model_dump(),
                }
                self.outputs.put(run_id, f"{group}:sources", source, {"chapter_ids": assignment.chapter_ids})
                synthesis_records = await synthesis_readings(
                    self.models, run_id, group, readings, units, branch
                )
                # Candidate quotes retain the complete containing unit, preserving notes and attribution.
                chosen = {
                    quote["unit_id"]
                    for record in synthesis_records
                    for quote in record.get("quotation_candidates", [])
                }
                chosen.update(
                    row["unit_id"]
                    for record in synthesis_records
                    for row in record.get("readings", [])
                    if row["candidate_quotes"]
                )
                synthesis_units = [unit for unit in units if unit["unit_id"] in chosen]
                previous = None
                for revision in range(3):
                    card = await self.models.run(
                        run_id,
                        f"{assignment.name}:制卡:{number}:{revision}",
                        prompts.SYNTHESIZE,
                        {
                            "reading_records": synthesis_records,
                            "source_units": synthesis_units,
                            "context_units": context,
                            "research_state": research_state,
                            "previous_check": previous,
                            "objective": assignment.objective,
                        },
                        CardDraft,
                        model=self.settings.reasoning_model,
                        parent=branch,
                        validate=lambda value, units=available: validate_candidate(value, units),
                    )
                    referenced = {selection.unit_id for item in card.items for selection in item.selections}
                    referenced.update(unit for item in card.items for unit in item.source_unit_ids)
                    check = await self.models.run(
                        run_id,
                        f"{assignment.name}:独立核验:{number}:{revision}",
                        prompts.CHECK_CARD,
                        {
                            "candidate": card.model_dump(mode="json"),
                            "reading_records": synthesis_records,
                            "source_units": [unit for unit in available if unit["unit_id"] in referenced],
                            "context_units": context,
                            "research_state": research_state,
                        },
                        SemanticCheck,
                        model=self.settings.reasoning_model,
                        parent=branch,
                        validate=lambda value, card=card: validate_check(value, card),
                    )
                    previous = check.model_dump(mode="json")
                    if passed(check):
                        source_checks = await check_candidate_coverage(
                            self.models,
                            run_id,
                            f"{assignment.name}:完整来源核验:{number}:{revision}",
                            card,
                            units,
                            branch,
                            assignment.objective,
                            context,
                            research_state,
                        )
                        previous["source_checks"] = [
                            result.model_dump(mode="json") for result in source_checks
                        ]
                        if all(passed(result) for result in source_checks):
                            break
                        previous["conclusion"] = "needs_revision"
                produced.append(
                    {
                        "id": str(uuid5(UUID(run_id), group)),
                        "assignment_index": number,
                        "candidate": card.model_dump(mode="json"),
                        "text_check": previous,
                        "source_step": f"{group}:sources",
                        "parent_node": branch,
                    }
                )
        self.outputs.put(run_id, "generated-cards", {"cards": produced}, {"plan": plan.model_dump()})
        Activities(self.settings, self.engine).transition(run_id, "processing", "vision")
        return {"run_id": run_id, "candidates": len(produced)}

    def check_images(self, run_id):
        from .visual_review import VisualReview

        run = get_run(self.engine, run_id)
        generated = self.outputs.get(run_id, "generated-cards")
        bundle = self.review.read_json(run["conversion"])
        reviewer = VisualReview(self.settings, self.engine)
        for card in generated["cards"]:
            source = self.outputs.get(run_id, card["source_step"])
            units = {row["unit_id"]: row for row in source["units"]}
            for group in visual_groups(card["candidate"], units):
                reviewer.check(run, bundle, card, group)
        return {"run_id": run_id}

    async def finalize(self, run_id):
        """Reconcile narrative with original checks; keep quotations, anchors and readings immutable."""
        from .visual_review import result_key

        if self.outputs.get(run_id, "finalized-cards"):
            return {"run_id": run_id}
        Activities(self.settings, self.engine).transition(run_id, "processing", "finalization")
        generated = self.outputs.get(run_id, "generated-cards")
        sources = {card["id"]: self.outputs.get(run_id, card["source_step"]) for card in generated["cards"]}
        all_assigned = {identity for source in sources.values() for identity in source["assigned_unit_ids"]}
        finalized = []
        for card in generated["cards"]:
            source = sources[card["id"]]
            units = {row["unit_id"]: row for row in source["units"]}
            checks = [
                self.outputs.get(run_id, result_key(card["id"], group["key"]))
                for group in visual_groups(card["candidate"], units)
            ]
            if not checks or not all(
                check and all(item["result"] == "verified" for item in check["items"]) for check in checks
            ):
                finalized.append(card)
                continue
            original = CardDraft.model_validate(card["candidate"])
            candidate = original
            referenced = {identity for item in original.items for identity in item.source_unit_ids}
            referenced.update(selection.unit_id for item in original.items for selection in item.selections)
            review_units = [
                unit
                for unit in source["units"]
                if unit["unit_id"] in referenced or unit["unit_id"] not in source["assigned_unit_ids"]
            ]
            research_state = {
                "assigned_unit_ids": source["assigned_unit_ids"],
                "available_units_cover_whole_book": all_assigned
                <= {unit["unit_id"] for unit in review_units},
                "scope": "指定分工与实际取得的上下文是不同范围。已提供的邻章可用于回答问题，不得误称未提供。",
            }
            final_check = None
            for revision in range(3):
                key = f"原图后定稿v2:{card['id']}:{revision}"
                payload = {
                    "candidate": candidate.model_dump(mode="json"),
                    "source_units": review_units,
                    "original_checks": checks,
                    "research_state": research_state,
                }
                final_check = await self.models.run(
                    run_id,
                    key,
                    prompts.FINAL_CHECK,
                    payload,
                    scoped_check([item.item_id for item in candidate.items]),
                    model=self.settings.reasoning_model,
                    parent=card["parent_node"],
                    validate=lambda value, candidate=candidate: validate_check(value, candidate),
                )
                if passed(final_check) or revision == 2:
                    break
                candidate = await self.models.run(
                    run_id,
                    key + ":修订",
                    prompts.FINALIZE,
                    {**payload, "previous_check": final_check.model_dump(mode="json")},
                    CardDraft,
                    model=self.settings.reasoning_model,
                    parent=card["parent_node"],
                    validate=lambda value, original=original, source=source: validate_final_candidate(
                        value, original, source["units"]
                    ),
                )
            finalized.append(
                {
                    **card,
                    "candidate": candidate.model_dump(mode="json"),
                    "text_check": final_check.model_dump(mode="json"),
                    "initial_text_check": card["text_check"],
                    "original_candidate": card["candidate"],
                }
            )
        self.outputs.put(run_id, "finalized-cards", {"cards": finalized}, {"generated": generated})
        return {"run_id": run_id}

    def adopt(self, run_id):
        from .visual_review import result_key

        run = get_run(self.engine, run_id)
        generated = self.outputs.get(run_id, "finalized-cards")
        if generated is None:
            raise ValueError(
                "Final text review must incorporate original-image observations before adoption."
            )
        rows = []
        for card in generated["cards"]:
            source = self.outputs.get(run_id, card["source_step"])
            units = {row["unit_id"]: row for row in source["units"]}
            wanted = visual_groups(card["candidate"], units)
            checks = [self.outputs.get(run_id, result_key(card["id"], group["key"])) for group in wanted]
            semantic = card["text_check"]
            initial = card.get("initial_text_check", semantic)
            passed = (
                bool(wanted)
                and initial["conclusion"] == "pass"
                and not initial["unverified_object_ids"]
                and not any(finding["severity"] in {"serious", "material"} for finding in initial["findings"])
                and semantic["conclusion"] == "pass"
                and not semantic["unverified_object_ids"]
                and not any(
                    finding["severity"] in {"serious", "material"} for finding in semantic["findings"]
                )
                and all(
                    check and all(item["result"] == "verified" for item in check["items"]) for check in checks
                )
            )
            content = self.review.objects.put_bytes(
                json.dumps(
                    {"candidate": card["candidate"], "units": source["units"]}, ensure_ascii=False
                ).encode()
            )
            verdict = self.review.objects.put_bytes(
                json.dumps(
                    {
                        "text": semantic,
                        "initial_text": card.get("initial_text_check"),
                        "original_candidate": card.get("original_candidate"),
                        "visual": checks,
                        "machine_approval": passed,
                        "human_approval": False,
                    },
                    ensure_ascii=False,
                ).encode()
            )
            rows.append(
                {
                    "id": card["id"],
                    "book_id": run["book_id"],
                    "run_id": run_id,
                    "title": card["candidate"]["title"],
                    "state": "adopted" if passed else "needs_revision",
                    "content": content,
                    "checks": verdict,
                }
            )
        with self.engine.begin() as connection:
            for row in rows:
                connection.execute(pg_insert(db.cards).values(**row).on_conflict_do_nothing())
        state = "completed" if all(row["state"] == "adopted" for row in rows) else "needs_revision"
        Activities(self.settings, self.engine).transition(
            run_id, state, "complete" if state == "completed" else "machine_review"
        )
        return {
            "run_id": run_id,
            "state": state,
            "cards": len(rows),
            "adopted": sum(row["state"] == "adopted" for row in rows),
        }

    def list(self, book_id=None, offset=0, limit=100):
        query = (
            select(db.cards).order_by(db.cards.c.created_at.desc(), db.cards.c.id).offset(offset).limit(limit)
        )
        if book_id:
            query = query.where(db.cards.c.book_id == str(book_id))
        with self.engine.connect() as connection:
            return list(connection.execute(query).mappings())

    def get(self, card_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(db.cards).where(db.cards.c.id == str(card_id)))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise HTTPException(404, "卡片不存在。")
        return {
            **row,
            **self.review.read_json(row["content"]),
            "verdict": self.review.read_json(row["checks"]),
        }
