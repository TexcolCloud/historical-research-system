"""Coordinate frozen sources, complete reading and resumable topic generation."""

import asyncio
from math import ceil
from uuid import UUID, uuid5

from sqlalchemy import select

from hrs_platform import models as db
from hrs_platform.domain.card_rules import CardPlan, card_stage
from hrs_platform.services.books import get_run
from hrs_platform.services.cards.assignments import read_assignments
from hrs_platform.services.cards.planning import plan_reading, plan_topics
from hrs_platform.services.cards.reading import (
    reading_units,
)
from hrs_platform.services.cards.topics import TopicBatch, generate_topics
from hrs_platform.services.documents.review import Review
from hrs_platform.services.runs.lifecycle import RunLifecycle


async def generate(services, run_id, *, incremental=False):

    run = get_run(services.engine, run_id)
    generated_key = card_stage(run, "generated-cards")
    cached = services.outputs.get(run_id, generated_key)
    if cached:
        return {
            "run_id": run_id,
            "candidates": len(cached["cards"]),
            "batch_key": generated_key,
            "more": False,
        }
    snapshot = services.outputs.get(run_id, "card-reading-sources:v2")
    if snapshot is None:
        chapters = [
            {key: row[key] for key in ("id", "title", "kind", "pages", "codepoints")}
            for row in services.library.chapters(run["book_id"])
        ]
        originals = [await asyncio.to_thread(services.library.chapter, row["id"]) for row in chapters]
        all_units = [unit for chapter in originals for unit in reading_units(chapter)]
        snapshot = services.outputs.put(
            run_id,
            "card-reading-sources:v2",
            {"chapters": chapters, "units": all_units},
            {"chapters": originals, "reading_rule": "structured-full-coverage-v2"},
        )
    chapters, all_units = snapshot["chapters"], snapshot["units"]
    corpus = await services.evidence.prepare(run, snapshot)
    if not all_units:
        raise ValueError("No reviewed source text is available for card reading.")
    revision = (run.get("result") or {}).get("card_revision", 0)
    saved_plan = services.outputs.get(run_id, "card-reading-plan")
    policy = services.outputs.get(run_id, "card-reading-policy")
    if policy is None:
        policy = services.outputs.put(run_id, "card-reading-policy", {"legacy_pairs": bool(saved_plan)}, {})
    # Preflight is a lower-bound estimate, not permission to exceed the hard cap.
    minimum = 2 * ceil(len(all_units) / (2 if policy["legacy_pairs"] else 8)) + ceil(len(all_units) / 8) + 4
    budget = services.outputs.preflight(run_id, minimum, services.settings.model_max_calls)
    services.outputs.node(
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
            services.outputs.get(run_id, card_stage(prior_run, "finalized-cards"))
            or services.outputs.get(run_id, card_stage(prior_run, "generated-cards"))
            or {"cards": []}
        )
        prior_cards = {row["id"]: row for row in prior["cards"]}
        prior_pending = {
            (row["topic_index"], row.get("topic_path", "")): row for row in prior.get("pending_topics", [])
        }
        with services.engine.connect() as connection:
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
    bundle = Review(services.settings, services.engine).read_json(run["conversion"])
    from hrs_platform.services.models.vision import VisualReview

    required_pages = {page for unit in all_units for source in unit["sources"] for page in source["pages"]}
    prepared = await asyncio.to_thread(
        VisualReview(services.settings, services.engine).prepare, run, bundle, required_pages
    )
    image_pages = [row["page"] for row in prepared["pages"]]
    services.outputs.node(
        run_id,
        "original-preflight",
        kind="component",
        label="原图来源预检",
        objective="检查原图并按固定 PDF 恢复缺失物理页",
        state="completed" if not prepared["issues"] else "failed",
        details=prepared,
    )
    RunLifecycle(services.engine).transition(run_id, "processing", "planning")
    main = str(uuid5(UUID(run_id), "主研究 Agent:v2"))

    research = services.outputs.get(run_id, "card-research-package")
    plan = await plan_reading(services, run_id, chapters, all_units, main, research, saved_plan)
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
        readings = await read_assignments(services, run_id, plan, all_units, main, policy, incremental, state)
        if readings is None:
            return {"run_id": run_id, "more": True, "stage": "reading"}
        card_plan = await plan_topics(services, run_id, readings, all_units, chapters, main)
        services.outputs.put(
            run_id,
            "card-research-package",
            {
                "plan": plan.model_dump(mode="json"),
                "readings": readings,
                "card_plan": card_plan.model_dump(mode="json"),
            },
            {"snapshot": snapshot},
        )
    batch = TopicBatch(
        run_id=run_id,
        run=run,
        generated_key=generated_key,
        card_plan=card_plan,
        main=main,
        revision=revision,
        prior_cards=prior_cards,
        prior_pending=prior_pending,
        adopted_ids=adopted_ids,
        all_units=all_units,
        state=state,
        readings=readings,
        plan=plan,
        corpus=corpus,
    )
    return await generate_topics(services, batch, incremental=incremental)
