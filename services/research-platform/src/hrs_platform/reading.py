"""Reuse retained research prompts and bounded digests without the retired job queue."""

import json
from enum import Enum

from pydantic import create_model

from .domain import prompts
from .domain.digests import DIGEST_INSTRUCTIONS, partition
from .domain.generation_contracts import ReadingDigest, ReadingRecord, SemanticCheck


def scoped_check(identities):
    """Constrain the provider contract to the same source scope we validate."""
    if not identities:
        raise ValueError("A source review requires at least one target.")
    target = Enum(
        "ReviewTarget", {f"target_{index}": value for index, value in enumerate(sorted(identities))}, type=str
    )
    return create_model(
        "ScopedSemanticCheck",
        __base__=SemanticCheck,
        checked_object_ids=(list[target], ...),
        unverified_object_ids=(list[target], ...),
    )


def passed(check):
    return (
        check.conclusion == "pass"
        and not check.unverified_object_ids
        and not any(row.severity in {"serious", "material"} for row in check.findings)
    )


def coverage(check, identities):
    expected = set(identities)
    actual = set(check.checked_object_ids) | set(check.unverified_object_ids)
    if actual != expected:
        raise ValueError(
            "Review scope must contain only these source unit IDs: "
            + json.dumps(sorted(expected))
            + ". Missing IDs: "
            + json.dumps(sorted(expected - actual))
            + ". Extraneous IDs (context is not a review target): "
            + json.dumps(sorted(actual - expected))
        )


async def read_batch(
    models, run_id, key, batch, objective, parent, previous=None, context=None, research_state=None
):
    identities = {row["unit_id"] for row in batch}

    def validate(value):
        if len(value.readings) != len(batch) or {row.unit_id for row in value.readings} != identities:
            raise ValueError("Every supplied unit must be read exactly once.")
        by_id = {row["unit_id"]: row["text"] for row in batch}
        if any(quote not in by_id[row.unit_id] for row in value.readings for quote in row.candidate_quotes):
            raise ValueError("Reading quotations must be exact continuous source text.")

    problems = None
    for revision in range(3):
        reading = await models.run(
            run_id,
            f"{key}:reading:{revision}",
            prompts.READ,
            {
                "objective": objective,
                "source_units": batch,
                "previous_reading": previous,
                "context_units": context or [],
                "research_state": research_state or {},
                "repair_findings": problems,
            },
            ReadingRecord,
            parent=parent,
            validate=validate,
        )
        check = await models.run(
            run_id,
            f"{key}:check:{revision}",
            prompts.CHECK_READING
            + "\n只核验 source_units；context_units 是上下文，不列入 checked_object_ids 或 unverified_object_ids。required_object_ids 明确列出本批全部核验对象。",
            {
                "source_units": batch,
                "context_units": context or [],
                "research_state": research_state or {},
                "reading_record": reading.model_dump(mode="json"),
                "required_object_ids": sorted(identities),
            },
            scoped_check(identities),
            parent=parent,
            validate=lambda value: coverage(value, identities),
        )
        if passed(check):
            return reading.model_dump(mode="json")
        problems = check.model_dump(mode="json")
    raise ValueError("逐段阅读仍有实质核验问题，阅读记录和原始证据已保留。")


async def synthesis_readings(models, run_id, key, readings, units, parent):
    current = readings
    known = {row["unit_id"]: row for row in units}
    for level in range(4):
        if len(json.dumps(current, ensure_ascii=False)) <= 20000:
            return current
        next_level = []
        groups = partition(list(range(len(current))), current.__getitem__, 6000)
        for index, group in enumerate(groups):

            def validate(value, group=group):
                if value.covered_record_indexes != list(range(len(group))):
                    raise ValueError("Digest coverage must retain every supplied reading record.")
                for quote in value.quotation_candidates:
                    if (
                        quote.unit_id not in known
                        or known[quote.unit_id]["text"].count(quote.quote) <= quote.occurrence
                    ):
                        raise ValueError("Digest quotation does not match its fixed source.")
                if any(set(fact.source_unit_ids) - known.keys() for fact in value.facts):
                    raise ValueError("Digest facts refer to unavailable sources.")

            result = await models.run(
                run_id,
                f"{key}:digest:{level}:{index}",
                DIGEST_INSTRUCTIONS,
                {
                    "records": [
                        {"record_index": i, "record": current[number]} for i, number in enumerate(group)
                    ],
                    "complete_original_readings_preserved": True,
                },
                ReadingDigest,
                parent=parent,
                validate=validate,
            )
            next_level.append(result.model_dump(mode="json"))
        current = next_level
    raise ValueError("阅读提要仍超过单次综合范围，需要拆分本次研究分工；完整阅读记录已保存。")


async def check_candidate_coverage(
    models, run_id, key, candidate, units, parent, objective, context, research_state
):
    """Check every assigned source again after synthesis, including unquoted limits."""
    checks = []
    for offset in range(0, len(units), 2):
        batch = units[offset : offset + 2]
        identities = {unit["unit_id"] for unit in batch}
        check = await models.run(
            run_id,
            f"{key}:source-coverage:{offset}",
            prompts.CHECK_READING + "\n本轮核验对象是最终 candidate 与完整原文的对应关系。"
            "逐个 source_units 检查候选是否遗漏或改变影响研究结论的事实、数量口径、否定、归属和限制。"
            "卡片无需抄录所有句子，但不得因综合提要而丢失重要反证或边界。"
            "checked_object_ids/unverified_object_ids 使用本批 source_units 的 unit_id；不要使用卡片 item_id。",
            {
                "candidate": candidate.model_dump(mode="json"),
                "required_object_ids": sorted(identities),
                "source_units": batch,
                "context_units": context,
                "research_state": research_state,
                "objective": objective,
            },
            scoped_check(identities),
            parent=parent,
            validate=lambda value, identities=identities: coverage(value, identities),
        )
        checks.append(check)
    return checks
