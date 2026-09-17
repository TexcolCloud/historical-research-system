"""Synthesize one topic from retrieved originals and independently check its claims."""

from hrs_platform.domain import prompts
from hrs_platform.domain.card_rules import (
    TOPIC_PREFLIGHT_TOKENS,
    apply_card_repair,
    repair_sources,
    validate_candidate,
    validate_check,
)
from hrs_platform.domain.errors import TaskError
from hrs_platform.domain.generation_contracts import CardDraft, CardRepair, SemanticCheck
from hrs_platform.domain.tokens import estimate_request
from hrs_platform.services.cards.reading import (
    card_model,
    check_candidate_coverage,
    compact_readings,
    neighbor_context,
    passed,
    synthesis_readings,
)
from hrs_platform.services.runs.outputs import fingerprint


async def synthesize_topic(services, batch, *, topic, group, branch, prior_card, number, path):
    units = [row for row in batch.all_units if row["unit_id"] in topic.unit_ids]
    context = neighbor_context(batch.all_units, units)
    available = [*units, *context]
    research_state = batch.state(units)
    # Shared batch summaries are explicitly context, never evidence for another topic.
    topic_readings = [
        {
            **record,
            **(
                {
                    "source_only_unit_ids": [
                        identity for identity in record["source_only_unit_ids"] if identity in topic.unit_ids
                    ]
                }
                if "source_only_unit_ids" in record
                else {}
            ),
            "readings": [row for row in record["readings"] if row["unit_id"] in topic.unit_ids],
            "questions": [
                question
                for question in record["questions"]
                if set(question["source_unit_ids"]) <= set(topic.unit_ids)
            ],
            "batch_context_unit_ids": [row["unit_id"] for row in record["readings"]],
        }
        for record in batch.readings
        if any(row["unit_id"] in topic.unit_ids for row in record["readings"])
    ]
    source = {
        "units": available,
        "assigned_unit_ids": [row["unit_id"] for row in units],
        "readings": topic_readings,
        "topic": topic.model_dump(),
        "reading_assignment_indexes": [
            i
            for i, a in enumerate(batch.plan.assignments)
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
        raise TaskError("主题需要缩小输入与输出范围。", type="card_input_budget", non_retryable=True)
    synthesis_records = await synthesis_readings(
        services.models, batch.run_id, group, compact_readings(topic_readings), units, branch
    )
    evidence = await services.evidence.research(
        services.models,
        batch.run_id,
        group,
        batch.corpus,
        topic,
        synthesis_records,
        batch.all_units,
        branch,
        previous=batch.prior_pending.get((number, path)),
    )
    if not evidence["assessment"]["sufficient"]:
        raise TaskError(
            "主题证据不足，已保存待补证问题。",
            evidence["assessment"],
            type="card_evidence_insufficient",
            non_retryable=True,
        )
    chosen = {unit["unit_id"] for unit in evidence["units"]}
    available = list({unit["unit_id"]: unit for unit in [*available, *evidence["units"]]}.values())
    context = [unit for unit in available if unit["unit_id"] not in topic.unit_ids]
    previous = prior_card.get("text_check") if prior_card else None
    card = CardDraft.model_validate(prior_card["candidate"]) if prior_card else None
    if card:
        chosen.update(selection.unit_id for item in card.items for selection in item.selections)
        chosen.update(identity for item in card.items for identity in item.source_unit_ids)
    for round_number in range(3):
        if round_number == 1 and previous and previous["conclusion"] != "pass":
            supplement = await services.evidence.research(
                services.models,
                batch.run_id,
                group + ":supplement",
                batch.corpus,
                topic,
                synthesis_records,
                batch.all_units,
                branch,
                previous=previous,
            )
            evidence = {**supplement, "initial": evidence}
            if not supplement["assessment"]["sufficient"]:
                raise TaskError(
                    "补证后仍有未解决问题，保留候选等待修订。",
                    supplement["assessment"],
                    type="card_evidence_insufficient",
                    non_retryable=True,
                )
            chosen.update(unit["unit_id"] for unit in supplement["units"])
            available = list({unit["unit_id"]: unit for unit in [*available, *supplement["units"]]}.values())
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
            services.models,
            batch.run_id,
            f"{group}:制卡:{round_number}",
            instructions,
            {
                "reading_records": synthesis_records,
                "source_units": synthesis_units,
                "evidence_research": evidence["assessment"],
                "context_units": [u for u in context if u["unit_id"] not in chosen],
                "research_state": research_state,
                "previous_check": previous,
                "previous_visual_checks": prior_card.get("repair_visual_checks", []) if prior_card else [],
                "previous_processing_error": prior_card.get("processing_error") if prior_card else None,
                "objective": topic.objective,
                "previous_candidate": card.model_dump(mode="json") if card else None,
            },
            output_type,
            model=services.settings.reasoning_model,
            parent=branch,
            validate=lambda value, units=[*synthesis_units, *context], original=original_card: (
                validate_candidate(
                    apply_card_repair(original, value) if original is not None else value, units
                )
            ),
        )
        card = apply_card_repair(original_card, result) if original_card is not None else result
        referenced = {selection.unit_id for item in card.items for selection in item.selections}
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
            services.models,
            batch.run_id,
            f"{group}:独立核验:{fingerprint(check_payload)}",
            prompts.CHECK_CARD,
            check_payload,
            SemanticCheck,
            model=services.settings.reasoning_model,
            parent=branch,
            validate=lambda value, card=card: validate_check(value, card),
        )
        previous = check.model_dump(mode="json")
        if passed(check):
            source_checks = await check_candidate_coverage(
                services.models,
                batch.run_id,
                f"{group}:完整来源核验",
                card,
                units,
                branch,
                topic.objective,
                context,
                research_state,
            )
            previous["source_checks"] = [result.model_dump(mode="json") for result in source_checks]
            if all(passed(result) for result in source_checks):
                break
            previous["conclusion"] = "needs_revision"
    source.update(units=available, evidence_research=evidence)
    return card, previous, source
