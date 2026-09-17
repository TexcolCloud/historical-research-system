"""Plan complete reading assignments and bounded topics with durable fallbacks."""

import json
from collections import deque
from itertools import groupby
from math import ceil

from hrs_platform.domain.card_rules import (
    MAX_CARD_TOPICS,
    Assignment,
    CardPlan,
    CardTopic,
    ResearchPlan,
    split_topic,
    validate_plan,
    validate_topics,
)
from hrs_platform.domain.errors import TaskError
from hrs_platform.domain.tokens import estimate_request
from hrs_platform.services.cards.reading import (
    card_model,
    compact_readings,
    synthesis_readings,
)


async def plan_topics(services, run_id, readings, units, chapters, parent):
    dependency = {"readings": readings, "units": units, "chapters": chapters}
    receipt = services.outputs.get(run_id, "card-topic-plan", dependency)
    if receipt is None:
        stage = "digest"
        reason = None
        try:
            overview = await synthesis_readings(
                services.models, run_id, "全书主题:v2", compact_readings(readings), units, parent
            )
            stage = "topic"
            plan = await card_model(
                services.models,
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
                model=services.settings.reasoning_model,
                parent=parent,
                validate=lambda value: validate_topics(value, units),
            )
        except TaskError as error:
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
                            unit["unit_id"] for _, rows in groups[offset : offset + width] for unit in rows
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
                children = (
                    split_topic(topic, units)
                    if (len(selected) > 8 or estimate_request(selected)["input_tokens"] > 6000)
                    else []
                )
                if children and len(packed) + len(bounded) + len(children) <= MAX_CARD_TOPICS:
                    bounded.extendleft(reversed(children))
                else:
                    packed.append(topic)
            plan.topics = packed
        validate_topics(plan, units)
        receipt = services.outputs.put(
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
        services.outputs.node(
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


async def plan_reading(services, run_id, chapters, all_units, main, research, saved_plan):
    from agents import function_tool

    @function_tool
    async def read_chapter_opening(chapter_id: str) -> str:
        """Read the opening of a chapter in this book to inform research assignments."""
        if chapter_id not in {row["id"] for row in chapters}:
            raise ValueError("Chapter is outside this book.")
        key = f"章节预览:{chapter_id}"
        services.outputs.node(
            run_id, key, kind="tool", label="读取章节开头", objective=chapter_id, parent=main
        )
        text = "".join(unit["text"] for unit in all_units if unit["chapter_id"] == chapter_id)
        result = {
            "chapter_id": chapter_id,
            "text": text[:3000],
            "complete": len(text) <= 3000,
        }
        services.outputs.node(
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

    try:
        plan = (
            ResearchPlan.model_validate(research["plan"])
            if research
            else ResearchPlan.model_validate(saved_plan)
            if saved_plan
            else await card_model(
                services.models,
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
                model=services.settings.reasoning_model,
                tools=[read_chapter_opening],
                validate=lambda value: validate_plan(value, [row["id"] for row in chapters]),
            )
        )
    except TaskError as error:
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
        services.outputs.node(
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
    services.outputs.put(run_id, "card-reading-plan", plan.model_dump(mode="json"), {"chapters": chapters})
    return plan
