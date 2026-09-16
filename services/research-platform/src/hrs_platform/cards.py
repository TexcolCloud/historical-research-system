"""Dynamic book assignments, source-bound candidates and machine-only adoption."""

import asyncio
import json
from collections import deque
from functools import cached_property
from itertools import groupby
from math import ceil
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio.exceptions import ApplicationError

from . import schema as db
from .activities import Activities
from .books import get_run
from .card_evidence import PURPOSES, CardEvidence, EvidenceQuery
from .domain import prompts
from .domain.generation_contracts import CardDraft, CardRepair, SemanticCheck
from .domain.tokens import estimate_request
from .library import Library
from .outputs import Outputs, fingerprint
from .reading import (
    card_model,
    check_candidate_coverage,
    compact_readings,
    coverage,
    coverage_batches,
    neighbor_context,
    passed,
    read_batch,
    reading_units,
    scoped_check,
    synthesis_readings,
)
from .review import Review

TOPIC_PREFLIGHT_TOKENS = 24000
MAX_CARD_TOPICS = 256


class Assignment(BaseModel):
    name: str
    objective: str
    chapter_ids: list[str] = Field(min_length=1)


class ResearchPlan(BaseModel):
    rationale: str
    assignments: list[Assignment] = Field(min_length=1, max_length=64)


class CardTopic(BaseModel):
    name: str
    objective: str
    unit_ids: list[str] = Field(min_length=1)
    queries: list[EvidenceQuery] = Field(default_factory=list, max_length=3)


class CardPlan(BaseModel):
    rationale: str
    topics: list[CardTopic] = Field(min_length=1, max_length=MAX_CARD_TOPICS)


def validate_topics(plan, units):
    for topic in plan.topics:
        if topic.queries and {q.purpose for q in topic.queries} != PURPOSES:
            raise ValueError("A planned evidence search requires support, counter and qualify.")
    assigned = [identity for topic in plan.topics for identity in topic.unit_ids]
    if len(assigned) != len(set(assigned)) or set(assigned) != {unit["unit_id"] for unit in units}:
        raise ValueError(
            "Every read source unit must belong to exactly one card topic; context may be shared."
        )


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
    coverage(check, {item.item_id for item in card.items})


def apply_card_repair(original, patch):
    """Reassemble a complete candidate before reference and semantic validation."""
    items = {item.item_id: item.model_dump(mode="json") for item in original.items}
    changed = [item.item_id for item in patch.items]
    removed = set(patch.remove_item_ids)
    if (len(changed) != len(set(changed)) or len(removed) != len(patch.remove_item_ids)
            or removed - items.keys() or removed & set(changed)):
        raise ValueError("Card repair has duplicate, unknown or conflicting item IDs.")
    for identity in removed:
        del items[identity]
    items.update((item.item_id, item.model_dump(mode="json")) for item in patch.items)
    metadata = (patch.metadata.model_dump(mode="json") if patch.metadata is not None
                else original.model_dump(mode="json", exclude={"items"}))
    return CardDraft.model_validate({**metadata, "items": list(items.values())})


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


def quote_range(unit, selection):
    start = -1
    for _ in range(selection.get("occurrence", 0) + 1):
        start = unit["text"].find(selection["quote"], start + 1)
        if start < 0:
            raise ValueError("Quotation is absent from the reviewed source.")
    end = start + len(selection["quote"])
    return start, end


def quote_pages(unit, selection):
    start, end = quote_range(unit, selection)
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


def split_topic(topic, all_units):
    units = [unit for unit in all_units if unit["unit_id"] in topic.unit_ids]
    if len(units) < 2:
        return []
    boundaries = [
        i
        for i in range(1, len(units))
        if (units[i].get("chapter_id"), units[i].get("section_path"))
        != (units[i - 1].get("chapter_id"), units[i - 1].get("section_path"))
    ]
    weights = [estimate_request(unit)["input_tokens"] for unit in units]
    middle = min(boundaries or range(1, len(units)), key=lambda i: abs(sum(weights[:i]) - sum(weights) / 2))
    return [
        CardTopic(
            name=f"{topic.name} · {i + 1}",
            objective=topic.objective,
            unit_ids=[unit["unit_id"] for unit in group],
        )
        for i, group in enumerate((units[:middle], units[middle:]))
    ]


def card_stage(run, name):
    revision = (run.get("result") or {}).get("card_revision", 0)
    return f"{name}:revision:{revision}" if revision else name


def repair_sources(previous, units, chosen):
    if not previous:
        return
    known = {unit["unit_id"] for unit in units}
    for check in [previous, *previous.get("source_checks", [])]:
        for finding in check.get("findings", []):
            chosen.update(set(finding.get("source_unit_ids", [])) & known)
            if finding.get("object_id") in known:
                chosen.add(finding["object_id"])
        chosen.update(set(check.get("unverified_object_ids", [])) & known)
        # A failed verdict without located findings still has an explicit checked scope.
        if check.get("conclusion") != "pass" and not check.get("findings"):
            chosen.update(set(check.get("checked_object_ids", [])) & known)


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

    @cached_property
    def evidence(self):
        return CardEvidence(self.settings, self.engine, self.outputs, self.library)

    async def _plan_topics(self, run_id, readings, units, chapters, parent):
        dependency = {"readings": readings, "units": units, "chapters": chapters}
        receipt = self.outputs.get(run_id, "card-topic-plan", dependency)
        if receipt is None:
            stage = "digest"
            reason = None
            try:
                overview = await synthesis_readings(
                    self.models, run_id, "全书主题:v2", compact_readings(readings), units, parent
                )
                stage = "topic"
                plan = await card_model(
                    self.models,
                    run_id,
                    "卡片主题规划:v2",
                    "在完整逐段阅读后按研究主题规划史料卡。阅读 Agent 的分工不等于卡片主题。"
                    "同一分工可拆为多卡，同一主题可合并不同分工的单元；合并同一事件的重复叙述，保留观点冲突。"
                    "每个 unit_id 恰好分给一个主题，禁止漏掉非引文单元；标题与书目单元随相关正文归组。"
                    "主题应保持研究问题集中、原文数量适中；不要把整本长书塞入单卡。关联脚注和邻文可跨主题作为上下文。"
                    "每个主题同时提供 queries：support、counter、qualify 各一条具体检索词，包含本主题的实体、事件或数量口径。"
                    "程序会执行检索和读取原文；反证查询应查找相反记载，限定查询应查找归属、时间范围或注释条件。",
                    {
                        "reading_records": overview,
                        "source_units": [
                            {key: unit[key] for key in ("unit_id", "section_path", "kind")} for unit in units
                        ],
                    },
                    CardPlan,
                    model=self.settings.reasoning_model,
                    parent=parent,
                    validate=lambda value: validate_topics(value, units),
                )
            except ApplicationError as error:
                if error.type not in {"model_output_invalid", "model_output_limit", "card_input_budget"}:
                    raise
                reason = error.type
                titles = {chapter["id"]: chapter["title"] for chapter in chapters}
                groups = [
                    (chapter, list(rows)) for chapter, rows in groupby(units, lambda row: row["chapter_id"])
                ]
                # Reserve room for the existing local topic splitter; group only
                # adjacent chapters when the catalogue exceeds 64 initial topics.
                width = max(1, ceil(len(groups) / min(64, MAX_CARD_TOPICS)))
                plan = CardPlan(
                    rationale="全书规划未完成，按章节顺序建立基础研究范围；完整阅读记录保留，内容仍须独立核验。",
                    topics=[
                        CardTopic(
                            name=f"章节基础主题 {offset // width + 1} · {titles.get(groups[offset][0], '未命名章节')}",
                            objective="按来源顺序研究指定章节的完整内容，保留脚注归属、反证及限定；"
                            "相邻章节仅共享研究范围，不预设它们属于同一事件。",
                            unit_ids=[
                                unit["unit_id"]
                                for _, rows in groups[offset : offset + width]
                                for unit in rows
                            ],
                        )
                        for offset in range(0, len(groups), width)
                    ],
                )
                # The fallback is already executable, rather than handing whole
                # long chapters to evidence collection and waiting for overflow.
                bounded = deque(plan.topics)
                packed = []
                while bounded:
                    topic = bounded.popleft()
                    selected = [u for u in units if u["unit_id"] in topic.unit_ids]
                    children = split_topic(topic, units) if (
                        len(selected) > 8 or estimate_request(selected)["input_tokens"] > 6000
                    ) else []
                    if children and len(packed) + len(bounded) + len(children) <= MAX_CARD_TOPICS:
                        bounded.extendleft(reversed(children))
                    else:
                        packed.append(topic)
                plan.topics = packed
            validate_topics(plan, units)
            receipt = self.outputs.put(
                run_id,
                "card-topic-plan",
                {
                    "plan": plan.model_dump(mode="json"),
                    "fallback_stage": stage if reason else None,
                    "reason": reason,
                    "machine_approval": False,
                },
                dependency,
            )
        plan = CardPlan.model_validate(receipt["plan"])
        validate_topics(plan, units)
        if receipt["fallback_stage"]:
            self.outputs.node(
                run_id,
                "topic-plan-fallback",
                kind="component",
                label="按章节恢复主题规划",
                objective="仅建立完整研究范围，不代表内容核验通过",
                parent=parent,
                state="completed",
                details=receipt,
            )
        return plan

    async def generate(self, run_id, *, incremental=False):
        from agents import function_tool

        run = get_run(self.engine, run_id)
        generated_key = card_stage(run, "generated-cards")
        cached = self.outputs.get(run_id, generated_key)
        if cached:
            return {"run_id": run_id, "candidates": len(cached["cards"]), "batch_key": generated_key, "more": False}
        snapshot = self.outputs.get(run_id, "card-reading-sources:v2")
        if snapshot is None:
            chapters = [
                {key: row[key] for key in ("id", "title", "kind", "pages", "codepoints")}
                for row in self.library.chapters(run["book_id"])
            ]
            originals = [await asyncio.to_thread(self.library.chapter, row["id"]) for row in chapters]
            all_units = [unit for chapter in originals for unit in reading_units(chapter)]
            snapshot = self.outputs.put(
                run_id,
                "card-reading-sources:v2",
                {"chapters": chapters, "units": all_units},
                {"chapters": originals, "reading_rule": "structured-full-coverage-v2"},
            )
        chapters, all_units = snapshot["chapters"], snapshot["units"]
        corpus = await self.evidence.prepare(run, snapshot)
        if not all_units:
            raise ValueError("No reviewed source text is available for card reading.")
        revision = (run.get("result") or {}).get("card_revision", 0)
        saved_plan = self.outputs.get(run_id, "card-reading-plan")
        # Preflight is a lower-bound estimate, not permission to exceed the hard cap.
        minimum = (
            2 * ceil(len(all_units) / (2 if saved_plan else 8)) + ceil(len(all_units) / 8) + 4
        )
        budget = self.outputs.preflight(run_id, minimum, self.settings.model_max_calls)
        self.outputs.node(
            run_id,
            "card-budget",
            kind="component",
            label="制卡调用预算预检",
            objective="预计基础调用，不含修订和额外工具轮次",
            state="completed",
            details=budget,
        )
        prior_cards, prior_pending, adopted_ids = {}, {}, set()
        if revision:
            prior_run = {**run, "result": {**(run.get("result") or {}), "card_revision": revision - 1}}
            prior = (
                self.outputs.get(run_id, card_stage(prior_run, "finalized-cards"))
                or self.outputs.get(run_id, card_stage(prior_run, "generated-cards"))
                or {"cards": []}
            )
            prior_cards = {row["id"]: row for row in prior["cards"]}
            prior_pending = {(row["topic_index"], row.get("topic_path", "")): row for row in prior.get("pending_topics", [])}
            with self.engine.connect() as connection:
                adopted_ids = set(
                    map(
                        str,
                        connection.scalars(
                            select(db.cards.c.id).where(
                                db.cards.c.run_id == run_id, db.cards.c.state == "adopted"
                            )
                        ),
                    )
                )
        bundle = Review(self.settings, self.engine).read_json(run["conversion"])
        from .visual_review import VisualReview

        required_pages = {
            page for unit in all_units for source in unit["sources"] for page in source["pages"]
        }
        prepared = await asyncio.to_thread(
            VisualReview(self.settings, self.engine).prepare, run, bundle, required_pages
        )
        image_pages = [row["page"] for row in prepared["pages"]]
        self.outputs.node(
            run_id,
            "original-preflight",
            kind="component",
            label="原图来源预检",
            objective="检查原图并按固定 PDF 恢复缺失物理页",
            state="completed" if not prepared["issues"] else "failed",
            details=prepared,
        )
        Activities(self.settings, self.engine).transition(run_id, "processing", "planning")
        main = str(uuid5(UUID(run_id), "主研究 Agent:v2"))

        @function_tool
        async def read_chapter_opening(chapter_id: str) -> str:
            """Read the opening of a chapter in this book to inform research assignments."""
            if chapter_id not in {row["id"] for row in chapters}:
                raise ValueError("Chapter is outside this book.")
            key = f"章节预览:{chapter_id}"
            self.outputs.node(
                run_id, key, kind="tool", label="读取章节开头", objective=chapter_id, parent=main
            )
            text = "".join(unit["text"] for unit in all_units if unit["chapter_id"] == chapter_id)
            result = {
                "chapter_id": chapter_id,
                "text": text[:3000],
                "complete": len(text) <= 3000,
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

        research = self.outputs.get(run_id, "card-research-package")
        try:
            plan = (
                ResearchPlan.model_validate(research["plan"])
                if research
                else ResearchPlan.model_validate(saved_plan)
                if saved_plan
                else await card_model(
                    self.models,
                    run_id,
                    "主研究 Agent:v2",
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
            )
        except ApplicationError as error:
            if error.type not in {"model_output_invalid", "model_output_limit", "card_input_budget"}:
                raise
            width = max(1, ceil(len(chapters) / 64))
            plan = ResearchPlan(
                rationale="规划收尾失败，按原目录保留完整阅读范围。",
                assignments=[
                    Assignment(
                        name=f"目录通读：{chapters[offset]['title']}",
                        objective="按原目录逐章通读后再规划研究主题。",
                        chapter_ids=[row["id"] for row in chapters[offset : offset + width]],
                    )
                    for offset in range(0, len(chapters), width)
                ],
            )
            self.outputs.node(
                run_id,
                "reading-plan-fallback",
                kind="component",
                label="目录阅读分工回退",
                objective="仅分配阅读范围，不代表内容核验通过",
                parent=main,
                state="completed",
                details={"reason": error.type, "machine_approval": False},
            )
        validate_plan(plan, [row["id"] for row in chapters])
        self.outputs.put(run_id, "card-reading-plan", plan.model_dump(mode="json"), {"chapters": chapters})
        source_evidence = {
            "available_original_pages": image_pages,
            "source_page_count": bundle["manifest"]["page_count"],
            "text_agent_has_viewed_images": False,
            "original_image_check": "separate_local_visual_stage" if image_pages else "no_images_supplied",
        }

        def state(units):
            assigned = {unit["chapter_id"] for unit in units}
            return {
                "available_book_chapters": [{"title": row["title"], "id": row["id"]} for row in chapters],
                "assigned_chapter_ids": sorted(assigned),
                "assigned_unit_count": len(units),
                "assignment_covers_all_book_chapters": {unit["unit_id"] for unit in units}
                == {unit["unit_id"] for unit in all_units},
                "source_evidence": source_evidence,
                "scope": "研究范围是指定原文单元，可能仅为章内部分；关联上下文可旁读，不代表其他章节缺失。",
            }

        if research:
            readings = research["readings"]
            card_plan = CardPlan.model_validate(research["card_plan"])
        else:
            semaphore = asyncio.Semaphore(2)
            remaining_reads = [8] if incremental else None

            async def read_assignment(number, assignment):
                group = f"研究分工:v2:{number}"
                async with semaphore:
                    with self.outputs.operation(
                        run_id,
                        group,
                        assignment.name,
                        kind="group",
                        objective=assignment.objective,
                        parent=main,
                    ) as branch:
                        units = [row for row in all_units if row["chapter_id"] in assignment.chapter_ids]
                        research_state = state(units)
                        readings = []
                        batch_key = f"{group}:batches"
                        batches = self.outputs.get(run_id, batch_key)
                        if batches is None:
                            # Existing plans may already have two-unit reading checkpoints.
                            # Freeze that layout once; new plans use token-packed structural units.
                            groups = (
                                [units[i : i + 2] for i in range(0, len(units), 2)]
                                if saved_plan
                                else list(coverage_batches(units))
                            )
                            batches = self.outputs.put(
                                run_id,
                                batch_key,
                                [[u["unit_id"] for u in batch] for batch in groups],
                                {"units": units},
                            )
                        by_id = {u["unit_id"]: u for u in units}
                        offset = 0
                        for identities in batches:
                            batch = [by_id[identity] for identity in identities]
                            reading_key = f"{group}:completed-batch:{offset}"
                            reading = self.outputs.get(run_id, reading_key)
                            if reading is None:
                                if remaining_reads is not None:
                                    if remaining_reads[0] == 0:
                                        raise ApplicationError("阅读进度已保存，交还队列。", type="card_batch_yield", non_retryable=True)
                                    remaining_reads[0] -= 1
                                reading = await read_batch(
                                    self.models,
                                    run_id,
                                    f"{group}:阅读:{offset}",
                                    batch,
                                    assignment.objective,
                                    branch,
                                    readings[-1] if readings else None,
                                    context=neighbor_context(all_units, batch),
                                    research_state=research_state,
                                )
                                self.outputs.put(run_id, reading_key, reading, {"units": batch, "assignment": assignment.model_dump()})
                            readings.append(reading)
                            offset += len(batch)
                        self.outputs.put(
                            run_id,
                            f"{group}:readings:{fingerprint(readings)}",
                            {"readings": readings},
                            {"units": units, "assignment": assignment.model_dump()},
                        )
                        return readings

            # Only independent assignments overlap. Their own batches retain previous-reading order.
            async def guarded_assignment(number, assignment):
                try:
                    return await read_assignment(number, assignment)
                except ApplicationError as error:
                    if error.type not in {"model_transport_wait", "model_transport_exhausted", "card_batch_yield"}:
                        raise
                    # Drain siblings already in flight. Models blocks new provider
                    # sends until the workflow's cooldown, without faking readings.
                    return error

            try:
                async with asyncio.TaskGroup() as tasks:
                    jobs = [
                        tasks.create_task(guarded_assignment(i, assignment))
                        for i, assignment in enumerate(plan.assignments)
                    ]
            except ExceptionGroup as failure:
                # Do not serialize the giant sibling ExceptionGroup into Temporal.
                error = failure.exceptions[0]
                while isinstance(error, ExceptionGroup):
                    error = error.exceptions[0]
                raise error from None
            deferred = [job.result() for job in jobs if isinstance(job.result(), ApplicationError) and job.result().type != "card_batch_yield"]
            if deferred:
                terminal = next(
                    (error for error in deferred if error.type == "model_transport_exhausted"), None
                )
                error = terminal or max(deferred, key=lambda error: error.details[0]["retry_at"])
                raise error from None
            if any(isinstance(job.result(), ApplicationError) for job in jobs):
                return {"run_id": run_id, "more": True, "stage": "reading"}
            readings = [record for job in jobs for record in job.result()]
            card_plan = await self._plan_topics(run_id, readings, all_units, chapters, main)
            self.outputs.put(
                run_id,
                "card-research-package",
                {
                    "plan": plan.model_dump(mode="json"),
                    "readings": readings,
                    "card_plan": card_plan.model_dump(mode="json"),
                },
                {"snapshot": snapshot},
            )
        produced, pending = [], []
        queue = deque((number, "", topic, main) for number, topic in enumerate(card_plan.topics))
        total_topics = len(queue)
        processed = 0
        # Reserve all persisted descendants before permitting any new split. A
        # later sibling's old division must not consume an allowance twice.
        known = deque((f"卡片主题:v2:{number}", "") for number in range(len(card_plan.topics)))
        while known:
            root, path = known.popleft()
            key = root + (f":split:{path}" if path else "")
            division = self.outputs.get(run_id, f"{key}:division")
            if division:
                total_topics += len(division["topics"]) - 1
                known.extend(
                    (root, f"{path}.{i}" if path else str(i)) for i in range(len(division["topics"]))
                )
        while queue:
            if incremental and processed >= 1:
                break
            number, path, topic, parent = queue.popleft()
            group = f"卡片主题:v2:{number}" + (f":split:{path}" if path else "")
            base_group = group
            identity = str(uuid5(UUID(run_id), base_group))
            checkpoint = f"{base_group}:result:{revision}"
            saved = self.outputs.get(run_id, checkpoint)
            if saved:
                produced.append(saved)
                continue
            saved_pending = self.outputs.get(run_id, f"{base_group}:pending-result:{revision}")
            if saved_pending:
                pending.append(saved_pending)
                continue
            prior_card = prior_cards.get(identity)
            if identity in adopted_ids and prior_card:
                produced.append({**prior_card, "retained": True})
                continue
            division = self.outputs.get(run_id, f"{base_group}:division")
            if division:
                if prior_card:
                    self._supersede(identity)
                children = [CardTopic.model_validate(row) for row in division["topics"]]
                queue.extend(
                    (number, f"{path}.{i}" if path else str(i), child, str(uuid5(UUID(run_id), base_group)))
                    for i, child in enumerate(children)
                )
                continue
            if revision:
                group += f":repair:{revision}"
            processed += 1
            if prior_card:
                from .visual_review import result_key

                prior_source = self.outputs.get(run_id, prior_card["source_step"])
                prior_units = {row["unit_id"]: row for row in prior_source["units"]}
                prior_card["repair_visual_checks"] = [
                    self.outputs.get(
                        run_id, result_key(identity, window["key"], prior_card.get("visual_revision"))
                    )
                    for window in visual_groups(
                        prior_card.get("original_candidate", prior_card["candidate"]), prior_units
                    )
                ]
            try:
                with self.outputs.operation(
                    run_id,
                    group,
                    topic.name,
                    kind="group",
                    objective=topic.objective,
                    parent=parent,
                ) as branch:
                    units = [row for row in all_units if row["unit_id"] in topic.unit_ids]
                    context = neighbor_context(all_units, units)
                    available = [*units, *context]
                    research_state = state(units)
                    # Shared batch summaries are explicitly context, never evidence for another topic.
                    topic_readings = [
                        {
                            **record,
                            **(
                                {
                                    "source_only_unit_ids": [
                                        identity
                                        for identity in record["source_only_unit_ids"]
                                        if identity in topic.unit_ids
                                    ]
                                }
                                if "source_only_unit_ids" in record
                                else {}
                            ),
                            "readings": [
                                row for row in record["readings"] if row["unit_id"] in topic.unit_ids
                            ],
                            "questions": [
                                question
                                for question in record["questions"]
                                if set(question["source_unit_ids"]) <= set(topic.unit_ids)
                            ],
                            "batch_context_unit_ids": [row["unit_id"] for row in record["readings"]],
                        }
                        for record in readings
                        if any(row["unit_id"] in topic.unit_ids for row in record["readings"])
                    ]
                    source = {
                        "units": available,
                        "assigned_unit_ids": [row["unit_id"] for row in units],
                        "readings": topic_readings,
                        "topic": topic.model_dump(),
                        "reading_assignment_indexes": [
                            i
                            for i, a in enumerate(plan.assignments)
                            if set(a.chapter_ids) & {u["chapter_id"] for u in units}
                        ],
                    }
                    preflight_input = {
                        "instructions": prompts.COMMON + prompts.SYNTHESIZE,
                        "input": {
                            "source_units": units,
                            "context_units": context,
                            "reading_records": compact_readings(topic_readings),
                        },
                        "schema": CardDraft.model_json_schema(),
                    }
                    if estimate_request(preflight_input)["input_tokens"] > TOPIC_PREFLIGHT_TOKENS or (
                        prior_card
                        and (prior_card.get("processing_error") or {}).get("error_type")
                        in {"model_output_limit", "card_input_budget"}
                    ):
                        raise ApplicationError(
                            "主题需要缩小输入与输出范围。", type="card_input_budget", non_retryable=True
                        )
                    synthesis_records = await synthesis_readings(
                        self.models, run_id, group, compact_readings(topic_readings), units, branch
                    )
                    evidence = await self.evidence.research(
                        self.models, run_id, group, corpus, topic, synthesis_records, all_units, branch,
                        previous=prior_pending.get((number, path)),
                    )
                    if not evidence["assessment"]["sufficient"]:
                        raise ApplicationError(
                            "主题证据不足，已保存待补证问题。",
                            evidence["assessment"],
                            type="card_evidence_insufficient",
                            non_retryable=True,
                        )
                    chosen = {unit["unit_id"] for unit in evidence["units"]}
                    available = list(
                        {unit["unit_id"]: unit for unit in [*available, *evidence["units"]]}.values()
                    )
                    context = [unit for unit in available if unit["unit_id"] not in topic.unit_ids]
                    previous = prior_card.get("text_check") if prior_card else None
                    card = CardDraft.model_validate(prior_card["candidate"]) if prior_card else None
                    if card:
                        chosen.update(
                            selection.unit_id for item in card.items for selection in item.selections
                        )
                        chosen.update(identity for item in card.items for identity in item.source_unit_ids)
                    for round_number in range(3):
                        if round_number == 1 and previous and previous["conclusion"] != "pass":
                            supplement = await self.evidence.research(
                                self.models,
                                run_id,
                                group + ":supplement",
                                corpus,
                                topic,
                                synthesis_records,
                                all_units,
                                branch,
                                previous=previous,
                            )
                            evidence = {**supplement, "initial": evidence}
                            if not supplement["assessment"]["sufficient"]:
                                raise ApplicationError(
                                    "补证后仍有未解决问题，保留候选等待修订。",
                                    supplement["assessment"],
                                    type="card_evidence_insufficient",
                                    non_retryable=True,
                                )
                            chosen.update(unit["unit_id"] for unit in supplement["units"])
                            available = list(
                                {
                                    unit["unit_id"]: unit for unit in [*available, *supplement["units"]]
                                }.values()
                            )
                            context = [unit for unit in available if unit["unit_id"] not in topic.unit_ids]
                        repair_sources(previous, available, chosen)
                        synthesis_units = [unit for unit in available if unit["unit_id"] in chosen]
                        original_card = card
                        output_type = CardDraft if original_card is None else CardRepair
                        instructions = prompts.SYNTHESIZE
                        if original_card is not None:
                            instructions += (
                                "\n本轮输出局部修订：items 只返回新增或修改的完整条目，"
                                "未改条目由程序保留。删除条目列在 remove_item_ids；"
                                "卡片级字段有变化时 metadata 返回完整元数据，否则为 null。"
                                "修复错误及其影响的日期、关系、推论和限定，保留未受影响条目及 item_id。"
                                "合并后的完整卡片仍接受独立语义和全文覆盖核验。"
                            )
                        result = await card_model(
                            self.models,
                            run_id,
                            f"{group}:制卡:{round_number}",
                            instructions,
                            {
                                "reading_records": synthesis_records,
                                "source_units": synthesis_units,
                                "evidence_research": evidence["assessment"],
                                "context_units": [u for u in context if u["unit_id"] not in chosen],
                                "research_state": research_state,
                                "previous_check": previous,
                                "previous_visual_checks": prior_card.get("repair_visual_checks", [])
                                if prior_card
                                else [],
                                "previous_processing_error": prior_card.get("processing_error")
                                if prior_card
                                else None,
                                "objective": topic.objective,
                                "previous_candidate": card.model_dump(mode="json") if card else None,
                            },
                            output_type,
                            model=self.settings.reasoning_model,
                            parent=branch,
                            validate=lambda value, units=[*synthesis_units, *context], original=original_card: validate_candidate(
                                apply_card_repair(original, value) if original is not None else value, units
                            ),
                        )
                        card = apply_card_repair(original_card, result) if original_card is not None else result
                        referenced = {
                            selection.unit_id for item in card.items for selection in item.selections
                        }
                        referenced.update(unit for item in card.items for unit in item.source_unit_ids)
                        check_payload = {
                            "candidate": card.model_dump(mode="json"),
                            "reading_records": synthesis_records,
                            "source_units": [unit for unit in available if unit["unit_id"] in referenced],
                            "context_units": [u for u in context if u["unit_id"] not in referenced],
                            "evidence_research": evidence["assessment"],
                            "research_state": research_state,
                        }
                        check = await card_model(
                            self.models,
                            run_id,
                            f"{group}:独立核验:{fingerprint(check_payload)}",
                            prompts.CHECK_CARD,
                            check_payload,
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
                                f"{group}:完整来源核验",
                                card,
                                units,
                                branch,
                                topic.objective,
                                context,
                                research_state,
                            )
                            previous["source_checks"] = [
                                result.model_dump(mode="json") for result in source_checks
                            ]
                            if all(passed(result) for result in source_checks):
                                break
                            previous["conclusion"] = "needs_revision"
                    source.update(units=available, evidence_research=evidence)
                    source_step = f"{base_group}:sources:{fingerprint(source)}"
                    self.outputs.put(
                        run_id,
                        source_step,
                        source,
                        {"topic": topic.model_dump(), "units": available, "corpus": corpus},
                    )
                    produced.append(
                        {
                            "id": identity,
                            "topic_index": number,
                            "topic_path": path,
                            "candidate": card.model_dump(mode="json"),
                            "text_check": previous,
                            "source_step": source_step,
                            "visual_revision": fingerprint(
                                {
                                    "candidate": card.model_dump(mode="json"),
                                    "source": run.get("source"),
                                    "conversion": run.get("conversion"),
                                    "revision": revision,
                                }
                            ),
                            "parent_node": branch,
                        }
                    )
                    self.outputs.put(
                        run_id, checkpoint, produced[-1], {"topic": topic.model_dump(), "revision": revision}
                    )
            except ApplicationError as error:
                if error.type not in {
                    "model_output_invalid",
                    "model_output_limit",
                    "card_input_budget",
                    "card_evidence_insufficient",
                    "model_request_exhausted",
                }:
                    raise
                depth = len(path.split(".")) if path else 0
                children = (
                    split_topic(topic, all_units)
                    if error.type in {"model_output_limit", "card_input_budget"}
                    and total_topics < MAX_CARD_TOPICS
                    else []
                )
                if children:
                    division = {
                        "topics": [child.model_dump() for child in children],
                        "reason": error.type,
                        "source_unit_ids": topic.unit_ids,
                        "depth": depth,
                    }
                    self.outputs.put(
                        run_id, f"{base_group}:division", division, {"topic": topic.model_dump()}
                    )
                    split_parent = self.outputs.node(
                        run_id,
                        base_group,
                        kind="group",
                        label=topic.name,
                        objective="原主题超限，已拆分，子主题尚须独立核验。",
                        parent=parent,
                        state="completed",
                        details=division,
                    )
                    if prior_card:
                        self._supersede(identity)
                    queue.extend(
                        (number, f"{path}.{i}" if path else str(i), child, split_parent)
                        for i, child in enumerate(children)
                    )
                    total_topics += len(children) - 1
                    continue
                pending.append(
                    {
                        "topic_index": number,
                        "topic_path": path,
                        "name": topic.name,
                        "error_type": error.type,
                        "message": str(error)[:500],
                        "evidence_questions": list(error.details),
                        "unit_ids": topic.unit_ids,
                    }
                )
                self.outputs.put(
                    run_id,
                    f"{base_group}:pending:{revision}:{fingerprint(pending[-1])}",
                    pending[-1],
                    {"topic": topic.model_dump()},
                )
                self.outputs.put(run_id, f"{base_group}:pending-result:{revision}", pending[-1], {"topic": topic.model_dump()})
        batch = {"cards": produced, "pending_topics": pending, "complete": not queue}
        batch_key = generated_key if not queue else generated_key + ":batch:" + fingerprint(batch)
        self.outputs.put(
            run_id,
            batch_key,
            batch,
            {"reading_plan": plan.model_dump(), "card_plan": card_plan.model_dump()},
        )
        Activities(self.settings, self.engine).transition(run_id, "processing", "vision")
        return {"run_id": run_id, "candidates": len(produced), "batch_key": batch_key, "more": bool(queue)}

    def check_images(self, run_id, batch_key=None):
        from .visual_review import VisualReview

        run = get_run(self.engine, run_id)
        generated = self.outputs.get(run_id, batch_key or card_stage(run, "generated-cards"))
        bundle = self.review.read_json(run["conversion"])
        reviewer = VisualReview(self.settings, self.engine)
        for card in generated["cards"]:
            if card.get("retained"):
                continue
            source = self.outputs.get(run_id, card["source_step"])
            units = {row["unit_id"]: row for row in source["units"]}
            for group in visual_groups(card["candidate"], units):
                try:
                    reviewer.check(run, bundle, card, group)
                except ApplicationError as error:
                    if error.type not in {"visual_output_invalid", "original_missing"}:
                        raise
                    self.outputs.put(
                        run_id,
                        f"visual-error:{card['id']}:{card.get('visual_revision', 'original')}:{group['key']}",
                        {"error_type": error.type, "message": str(error)[:500]},
                        {"card": card},
                    )
        return {"run_id": run_id}

    async def finalize(self, run_id, batch_key=None):
        """Reconcile narrative with original checks; keep quotations, anchors and readings immutable."""
        from .visual_review import result_key

        final_stage = batch_key + ":finalized" if batch_key else card_stage(get_run(self.engine, run_id), "finalized-cards")
        if self.outputs.get(run_id, final_stage):
            return {"run_id": run_id}
        Activities(self.settings, self.engine).transition(run_id, "processing", "finalization")
        generated = self.outputs.get(run_id, batch_key or card_stage(get_run(self.engine, run_id), "generated-cards"))
        sources = {card["id"]: self.outputs.get(run_id, card["source_step"]) for card in generated["cards"]}
        snapshot = self.outputs.get(run_id, "card-reading-sources:v2")
        all_assigned = (
            {unit["unit_id"] for unit in snapshot["units"]}
            if snapshot
            else {identity for source in sources.values() for identity in source["assigned_unit_ids"]}
        )
        finalized = []
        for card in generated["cards"]:
            if card.get("retained"):
                finalized.append(card)
                continue
            source = sources[card["id"]]
            units = {row["unit_id"]: row for row in source["units"]}
            checks = [
                self.outputs.get(run_id, result_key(card["id"], group["key"], card.get("visual_revision")))
                for group in visual_groups(card["candidate"], units)
            ]
            if (
                card["text_check"]["conclusion"] != "pass"
                or not checks
                or not all(
                    check and all(item["result"] == "verified" for item in check["items"]) for check in checks
                )
            ):
                failures = [
                    self.outputs.get(
                        run_id,
                        f"visual-error:{card['id']}:{card.get('visual_revision', 'original')}:{group['key']}",
                    )
                    for group in visual_groups(card["candidate"], units)
                ]
                finalized.append(
                    {**card, "processing_error": next((error for error in failures if error), None)}
                )
                continue
            final_key = f"final-card:{card['id']}:{fingerprint({'card': card, 'checks': checks})}"
            saved = self.outputs.get(run_id, final_key)
            if saved:
                finalized.append(saved)
                continue
            try:
                original = CardDraft.model_validate(card["candidate"])
                candidate = original
                referenced = {identity for item in original.items for identity in item.source_unit_ids}
                referenced.update(
                    selection.unit_id for item in original.items for selection in item.selections
                )
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
                final_receipt = None
                for revision in range(3):
                    key = f"原图后定稿v3:{card['id']}:{revision}:{card.get('visual_revision', 'original')}"
                    payload = {
                        "candidate": candidate.model_dump(mode="json"),
                        "source_units": review_units,
                        "original_checks": checks,
                        "research_state": research_state,
                        "evidence_research": source.get("evidence_research", {}).get("assessment"),
                    }
                    final_check = await card_model(
                        self.models,
                        run_id,
                        key,
                        prompts.FINAL_CHECK,
                        payload,
                        scoped_check([item.item_id for item in candidate.items]),
                        model=self.settings.reasoning_model,
                        parent=card["parent_node"],
                        validate=lambda value, candidate=candidate: validate_check(value, candidate),
                    )
                    final_receipt = final_check.model_dump(mode="json")
                    # Revised narrative can drop an unquoted limit. The initial source
                    # receipt cannot approve changed claims, even with frozen quotations.
                    if passed(final_check) and candidate != original:
                        source_checks = await check_candidate_coverage(
                            self.models,
                            run_id,
                            f"定稿完整来源:{card['id']}",
                            candidate,
                            [
                                unit
                                for unit in source["units"]
                                if unit["unit_id"] in source["assigned_unit_ids"]
                            ],
                            card["parent_node"],
                            source.get("topic", {}).get("objective", candidate.title),
                            [
                                unit
                                for unit in source["units"]
                                if unit["unit_id"] not in source["assigned_unit_ids"]
                            ],
                            research_state,
                        )
                        final_receipt["source_checks"] = [
                            result.model_dump(mode="json") for result in source_checks
                        ]
                        if not source_checks or not all(passed(result) for result in source_checks):
                            final_receipt["conclusion"] = "needs_revision"
                    if (final_receipt["conclusion"] == "pass" and passed(final_check)) or revision == 2:
                        break
                    chosen = {unit["unit_id"] for unit in review_units}
                    repair_sources(final_receipt, source["units"], chosen)
                    review_units = [unit for unit in source["units"] if unit["unit_id"] in chosen]
                    payload["source_units"] = review_units
                    candidate = await card_model(
                        self.models,
                        run_id,
                        key + ":修订",
                        prompts.FINALIZE,
                        {**payload, "previous_check": final_receipt},
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
                        "text_check": final_receipt,
                        "initial_text_check": card["text_check"],
                        "original_candidate": card["candidate"],
                    }
                )
            except ApplicationError as error:
                if error.type not in {"model_output_invalid", "model_output_limit", "card_input_budget"}:
                    raise
                finalized.append(
                    {**card, "processing_error": {"error_type": error.type, "message": str(error)[:500]}}
                )
            self.outputs.put(run_id, final_key, finalized[-1], {"card": card, "checks": checks})
        self.outputs.put(
            run_id,
            final_stage,
            {"cards": finalized, "pending_topics": generated.get("pending_topics", []), "complete": generated.get("complete", True)},
            {"generated": generated},
        )
        return {"run_id": run_id}

    def adopt(self, run_id, batch_key=None):
        from .visual_review import result_key

        run = get_run(self.engine, run_id)
        generated = self.outputs.get(run_id, batch_key + ":finalized" if batch_key else card_stage(run, "finalized-cards"))
        if generated is None:
            raise ValueError(
                "Final text review must incorporate original-image observations before adoption."
            )
        rows = []
        for card in generated["cards"]:
            source = self.outputs.get(run_id, card["source_step"])
            units = {row["unit_id"]: row for row in source["units"]}
            wanted = visual_groups(card["candidate"], units)
            checks = [
                self.outputs.get(run_id, result_key(card["id"], group["key"], card.get("visual_revision")))
                for group in wanted
            ]
            semantic = card["text_check"]
            initial = card.get("initial_text_check", semantic)
            passed = (
                bool(wanted)
                and not card.get("processing_error")
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
                ).encode(),
                run_id=run_id,
            )
            verdict = self.review.objects.put_bytes(
                json.dumps(
                    {
                        "text": semantic,
                        "initial_text": card.get("initial_text_check"),
                        "original_candidate": card.get("original_candidate"),
                        "visual": checks,
                        "processing_error": card.get("processing_error"),
                        "machine_approval": passed,
                        "human_approval": False,
                    },
                    ensure_ascii=False,
                ).encode(),
                run_id=run_id,
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
                connection.execute(
                    pg_insert(db.cards)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["id"],
                        set_={key: row[key] for key in ("title", "state", "content", "checks")},
                        where=db.cards.c.state != "adopted",
                    )
                )
        state = (
            "completed"
            if rows and not generated.get("pending_topics") and all(row["state"] == "adopted" for row in rows)
            else "needs_revision"
        )
        if not generated.get("complete", True):
            state = "processing"
        elif batch_key:
            self.outputs.put(run_id, card_stage(run, "finalized-cards"), generated, {"batch_key": batch_key})
        Activities(self.settings, self.engine).transition(
            run_id,
            state,
            "complete" if state == "completed" else "planning" if state == "processing" else "machine_review",
            result={
                **(run.get("result") or {}),
                "pending_topics": generated.get("pending_topics", []),
                "cards": len(rows),
                "adopted": sum(row["state"] == "adopted" for row in rows),
            },
        )
        return {
            "run_id": run_id,
            "state": state,
            "cards": len(rows),
            "adopted": sum(row["state"] == "adopted" for row in rows),
        }

    def schedule_repair(self, run_id, revision):
        """Advance only actionable failures, with durable idempotency and no-progress bounds."""
        run = get_run(self.engine, run_id)
        current = (run.get("result") or {}).get("card_revision", 0)
        if current > revision:
            return {"retry": True}
        if current != revision or run["state"] != "needs_revision":
            return {"retry": False}
        generated = self.outputs.get(run_id, card_stage(run, "finalized-cards"))
        actionable = []
        for topic in generated.get("pending_topics", []):
            if topic["error_type"] in {"model_output_invalid", "model_output_limit", "card_input_budget", "model_request_exhausted", "card_evidence_insufficient"}:
                actionable.append({k: topic[k] for k in ("unit_ids", "error_type")})
        with self.engine.connect() as connection:
            rows = list(connection.execute(select(db.cards).where(db.cards.c.run_id == run_id, db.cards.c.state == "needs_revision")).mappings())
        for row in rows:
            checks = self.review.read_json(row["checks"])
            if any(item["result"] in {"unreadable", "not_located", "not_checked"}
                   for check in checks.get("visual", []) if check for item in check["items"]):
                continue
            actionable.append({"id": str(row["id"]), "content": row["content"]["sha256"],
                               "processing_error": checks.get("processing_error"),
                               "findings": [(f.get("object_id"), f.get("code")) for f in checks["text"].get("findings", [])]})
        signature = fingerprint(actionable)
        with self.engine.begin() as connection:
            fresh = connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update()).mappings().one()
            result = dict(fresh["result"] or {})
            if result.get("card_revision", 0) > revision:
                return {"retry": True}
            if fresh["state"] != "needs_revision":
                return {"retry": False}
            history = result.get("auto_repair_signatures", [])
            retry = bool(actionable) and len(history) < 2 and signature not in history
            result["auto_repair_status"] = "scheduled" if retry else "manual_required"
            if retry:
                result.update(card_revision=revision + 1, auto_repair_signatures=[*history, signature])
            values = {"result": result, "revision": fresh["revision"] + 1}
            if retry:
                values.update(state="processing", stage="planning")
            connection.execute(update(db.runs).where(db.runs.c.id == run_id).values(**values))
            connection.execute(insert(db.events).values(book_id=fresh["book_id"], run_id=run_id,
                kind="run.changed", payload={"run_kind": "cards", "state": values.get("state", fresh["state"]),
                "stage": values.get("stage", fresh["stage"]), "revision": values["revision"]}))
        return {"retry": retry}

    def _supersede(self, identity):
        with self.engine.begin() as connection:
            connection.execute(
                update(db.cards)
                .where(db.cards.c.id == identity, db.cards.c.state != "adopted")
                .values(state="superseded")
            )

    def list(self, book_id=None, offset=0, limit=100):
        query = (
            select(db.cards)
            .where(db.cards.c.state != "superseded")
            .order_by(db.cards.c.created_at.desc(), db.cards.c.id)
            .offset(offset)
            .limit(limit)
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
        detail = {
            **row,
            **self.review.read_json(row["content"]),
            "verdict": self.review.read_json(row["checks"]),
        }
        units = {unit["unit_id"]: unit for unit in detail["units"]}
        locations = []
        for item in detail["candidate"]["items"]:
            # Older candidates remain exportable without inventing item identities.
            if not item.get("item_id"):
                continue
            for index, selection in enumerate(item.get("selections", [])):
                start, end, pages, issue = None, None, [], None
                unit = units.get(selection["unit_id"])
                if unit is None:
                    issue = "未找到此引文的来源正文。"
                else:
                    try:
                        start, end = quote_range(unit, selection)
                    except ValueError:
                        issue = "引文未能在保存的来源正文中精确定位。"
                    else:
                        try:
                            pages = quote_pages(unit, selection)
                        except (ValueError, KeyError):
                            issue = "引文已定位，但缺少可核实的原件页码。"
                locations.append(
                    {
                        "item_id": item["item_id"],
                        "selection_index": index,
                        "unit_id": selection["unit_id"],
                        "start": start,
                        "end": end,
                        "pages": pages,
                        "issue": issue,
                    }
                )
        return {**detail, "quote_locations": locations}
