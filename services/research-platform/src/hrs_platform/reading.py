"""Reuse retained research prompts and bounded digests without the retired job queue."""

import asyncio
import json
from enum import Enum

from pydantic import create_model
from temporalio.exceptions import ApplicationError

from .domain import prompts
from .domain.digests import DIGEST_INSTRUCTIONS, partition
from .domain.generation_contracts import ReadingDigest, ReadingRecord, SemanticCheck
from .domain.tokens import estimate_request
from .outputs import fingerprint
from .retrieval_chunks import (
    blocks,
    retrieval_chunks,
    section_introductions,
    shared_table_scopes,
    source_excerpt,
)

CARD_INPUT_TOKENS = 48000


async def card_model(models, run_id, key, instructions, payload, output_type, **kwargs):
    """Fail before sending an oversized card request; never truncate source evidence."""
    request = {
        "instructions": prompts.COMMON + instructions,
        "input": payload,
        "schema": output_type.model_json_schema(),
    }
    if estimate_request(request)["input_tokens"] > CARD_INPUT_TOKENS:
        raise ApplicationError(
            f"制卡步骤 {key} 超过输入预算，请缩小研究主题；已保存证据不截断、不放行。",
            type="card_input_budget",
            non_retryable=True,
        )
    return await models.run(run_id, key, instructions, payload, output_type, **kwargs)


def reading_units(chapter, size=5000):
    """Full, non-overlapping book coverage using the shared evidence parser, not Top-K."""
    text = chapter["text"]
    structure = blocks(text)
    introductions = section_introductions(text, structure)
    units, cursor = [], 0
    for row in retrieval_chunks(chapter, chapter["title"], size=size, overlap=0):
        if row["start"] != cursor or row["text"] != text[row["start"] : row["end"]]:
            raise ValueError("Card reading must preserve complete, contiguous source ranges.")
        cursor = row["end"]
        row.pop("retrieval_text")  # The embedding projection is not original evidence.
        extras = {
            (a, b, role)
            for block in structure
            if block["start"] < row["end"] and block["end"] > row["start"]
            for a, b, role in introductions.get(block["section"], [])
            if not row["start"] <= a < b <= row["end"]
        }
        row["context"].extend(dict(source_excerpt(chapter, a, b), role=role) for a, b, role in sorted(extras))
        units.append(
            {
                **row,
                "unit_id": row["id"],
                "physical_page": row["pages"][0] if row["pages"] else None,
                "table_scopes": shared_table_scopes(text, structure, row["start"], row["end"]),
            }
        )
    if cursor != len(text):
        raise ValueError("Card reading did not cover the entire reviewed chapter.")
    return units


def neighbor_context(all_units, selected):
    """Preserve adjoining text and book-opening provenance across agent assignments."""
    selected_ids = {unit["unit_id"] for unit in selected}
    indexes = {i for i, row in enumerate(all_units) if row["unit_id"] in selected_ids}
    wanted = {0}
    for index in indexes:
        wanted.update((index - 1, index + 1))
    neighbors = [
        row for index, row in enumerate(all_units) if index in wanted and row["unit_id"] not in selected_ids
    ]
    # Linked notes/owners can be far from the selected unit or across reading assignments.
    supplements = [
        dict(extra, unit_id=extra["id"])
        for unit in [*selected, *neighbors]
        for extra in unit.get("context", [])
    ]
    return list(
        {
            row["unit_id"]: row for row in [*supplements, *neighbors] if row["unit_id"] not in selected_ids
        }.values()
    )


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
    checked, unverified = check.checked_object_ids, check.unverified_object_ids
    if (
        len(checked) != len(set(checked))
        or len(unverified) != len(set(unverified))
        or set(checked) & set(unverified)
    ):
        raise ValueError("Review targets must be unique and cannot be both checked and unverified.")
    if any(finding.object_id not in expected for finding in check.findings):
        raise ValueError("Review findings must identify a supplied review target.")


async def read_batch(
    models, run_id, key, batch, objective, parent, previous=None, context=None, research_state=None
):
    identities = {row["unit_id"] for row in batch}
    dependency = {
        "batch": batch,
        "objective": objective,
        "previous": previous,
        "context": context,
        "research_state": research_state,
        "reading_instructions": prompts.READ,
        "check_instructions": prompts.CHECK_READING,
        "policy": "source-only-fallback-v1",
    }
    checkpoint = f"{key}:resolved:{fingerprint(dependency)}"
    saved = await asyncio.to_thread(models.outputs.get, run_id, checkpoint, dependency)
    if saved is not None:
        return saved["output"]

    async def save(result, fallback=None):
        await asyncio.to_thread(
            models.outputs.put, run_id, checkpoint, {"output": result, "fallback": fallback}, dependency
        )
        return result

    async def source_fallback(accepted, checks, reason):
        # Only reviewed source survives; failed interpretations and shared summaries
        # are discarded, never promoted to semantic or human approval.
        affected = [unit["unit_id"] for unit in batch if unit["unit_id"] not in accepted]
        records = [
            accepted[unit["unit_id"]]
            if unit["unit_id"] in accepted
            else {
                "unit_id": unit["unit_id"],
                "main_records": [],
                "attribution": "来源正文原文，陈述归属以原文为准。",
                "dates_quantities_actors": [],
                "negations_and_limits": [],
                "note_table_dependencies": [],
                "candidate_quotes": [unit["text"]] if unit["text"].strip() else [],
                "uncertainties": [],
            }
            for unit in batch
        ]
        value = ReadingRecord(
            readings=records, themes=[], structure=[], questions=[], boundary_observations=[]
        )
        validate(value)
        result = {**value.model_dump(mode="json"), "source_only_unit_ids": affected}
        details = {
            "strategy": "source_only",
            "unit_ids": affected,
            "reason": reason,
            "checks": checks,
            "semantic_approval": False,
            "source_sha256": fingerprint(batch),
        }
        # The visible component completes its fallback, not an approval of the failed reading.
        await asyncio.to_thread(
            models.outputs.node,
            run_id,
            checkpoint + ":fallback",
            kind="component",
            label="原文回退（未采用模型解读）",
            parent=parent,
            objective="保留来源全文继续制卡，最终内容仍须语义与原图核验。",
            state="completed",
            details=details,
        )
        return await save(result, details)

    def validate(value):
        if len(value.readings) != len(batch) or {row.unit_id for row in value.readings} != identities:
            raise ValueError("Every supplied unit must be read exactly once.")
        by_id = {row["unit_id"]: row["text"] for row in batch}
        invalid = [
            {"unit_id": row.unit_id, "quote_index": index, "invalid_quote": quote[:500]}
            for row in value.readings
            for index, quote in enumerate(row.candidate_quotes)
            if not quote.strip() or quote not in by_id[row.unit_id]
        ]
        if invalid:
            raise ValueError(
                "Reading quotations must be exact continuous source text. "
                "以下引文未命中对应 source_units[].text，请按 unit_id 和 quote_index 重新摘录连续原文，"
                "保留换行、空格、标点和原字形；不得从 context_units 或其他单元引用，也不能为了通过校验"
                "删除有研究价值的引文。其他正确记录保持不变。错误位置（最多 5 项）："
                + json.dumps(invalid[:5], ensure_ascii=False)
            )

    problems, prior, accepted = None, None, {}
    for revision in range(3):

        def validate_repair(value, accepted=accepted):
            # Restore fixed records deterministically; model rewrites are not repairs.
            value.readings = [
                type(row).model_validate(accepted[row.unit_id]) if row.unit_id in accepted else row
                for row in value.readings
            ]
            validate(value)

        try:
            reading = await card_model(
                models,
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
                    "previous_record": prior,
                    "fixed_unit_ids": sorted(accepted),
                },
                ReadingRecord,
                parent=parent,
                validate=validate_repair,
            )
        except ApplicationError as error:
            if error.type not in {"model_output_invalid", "model_output_limit"}:
                raise
            return await source_fallback(accepted, problems, error.type)
        prior = reading.model_dump(mode="json")
        problems, accepted = [], {}
        failed_output = False
        for unit, record in (
            (unit, next(row for row in prior["readings"] if row["unit_id"] == unit["unit_id"]))
            for unit in batch
        ):
            payload = {
                "source_units": batch,
                "context_units": context or [],
                "research_state": research_state or {},
                "reading_record": {**prior, "readings": [record]},
                "required_object_ids": [unit["unit_id"]],
            }
            try:
                check = await card_model(
                    models,
                    run_id,
                    f"{key}:check:{fingerprint(payload)}",
                    prompts.CHECK_READING
                    + "\nsource_units 保留完整阅读批次，required_object_ids 才是本轮核验目标。"
                    "reading_record 的概括、结构、问题及边界描述属于完整批次，readings 仅保留目标单元。"
                    "只对目标单元及概括中涉及它的实质问题出具核验，不把其他批次成员误判为上下文或漏读。",
                    payload,
                    scoped_check([unit["unit_id"]]),
                    parent=parent,
                    validate=lambda value, identity=unit["unit_id"]: coverage(value, [identity]),
                )
            except ApplicationError as error:
                if error.type not in {"model_output_invalid", "model_output_limit"}:
                    raise
                problems.append({"unit_id": unit["unit_id"], "error_type": error.type})
                failed_output = True
                continue
            if passed(check):
                accepted[unit["unit_id"]] = record
            else:
                problems.append(check.model_dump(mode="json"))
        if not problems:
            return await save(prior)
        if failed_output:
            return await source_fallback(accepted, problems, "review_output_failed")
    return await source_fallback(accepted, problems, "content_repairs_exhausted")


def compact_readings(readings):
    """Keep source-linked clues, without copying quotations already in frozen sources."""
    return [
        {
            **record,
            "readings": [
                {
                    **{key: value for key, value in row.items() if key != "candidate_quotes"},
                    "has_candidate_quotes": bool(row.get("candidate_quotes")),
                }
                for row in record.get("readings", [])
            ],
        }
        for record in readings
    ]


async def synthesis_readings(models, run_id, key, readings, units, parent):
    current = readings
    known = {row["unit_id"]: row for row in units}
    for level in range(5):
        if estimate_request({"records": current})["input_tokens"] <= 6000:
            return current
        if level == 4:
            break
        next_level = []
        groups = partition(list(range(len(current))), current.__getitem__, 6000)
        for index, group in enumerate(groups):
            supplied = {row["unit_id"] for number in group for row in current[number].get("readings", [])}
            supplied.update(
                quote["unit_id"]
                for number in group
                for quote in current[number].get("quotation_candidates", [])
            )
            supplied.update(
                identity
                for number in group
                for fact in current[number].get("facts", [])
                for identity in fact["source_unit_ids"]
            )

            def validate(value, group=group, supplied=supplied):
                if value.covered_record_indexes != list(range(len(group))):
                    raise ValueError("Digest coverage must retain every supplied reading record.")
                for quote in value.quotation_candidates:
                    if (
                        quote.unit_id not in known
                        or quote.unit_id not in supplied
                        or known[quote.unit_id]["text"].count(quote.quote) <= quote.occurrence
                    ):
                        raise ValueError("Digest quotation does not match its fixed source.")
                if any(set(fact.source_unit_ids) - (known.keys() & supplied) for fact in value.facts):
                    raise ValueError("Digest facts refer to unavailable sources.")

            result = await card_model(
                models,
                run_id,
                f"{key}:digest:{level}:{index}",
                DIGEST_INSTRUCTIONS + prompts.PERSPECTIVES,
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
    raise ApplicationError(
        "阅读提要仍超过单次综合范围，需要拆分本次研究分工；完整阅读记录已保存。",
        type="card_input_budget",
        non_retryable=True,
    )


def coverage_batches(units):
    # Batch small structural units without merging identities or truncating tables.
    for offset in range(0, len(units), 8):
        for indexes in partition(list(range(offset, min(offset + 8, len(units)))), lambda i: units[i], 6000):
            yield [units[i] for i in indexes]


async def check_candidate_coverage(
    models, run_id, key, candidate, units, parent, objective, context, research_state
):
    """Check every assigned source again after synthesis, including unquoted limits."""
    checks = []
    for offset, batch in enumerate(coverage_batches(units)):
        identities = {unit["unit_id"] for unit in batch}
        payload = {
            "candidate": candidate.model_dump(mode="json"),
            "required_object_ids": sorted(identities),
            "candidate_source_unit_ids": [unit["unit_id"] for unit in units],
            "source_units": batch,
            "context_units": context,
            "research_state": research_state,
            "objective": objective,
        }
        check = await card_model(
            models,
            run_id,
            f"{key}:source-coverage:{offset}:{fingerprint(payload)}",
            prompts.CHECK_READING + "\n本轮核验对象是最终 candidate 与完整原文的对应关系。"
            "逐个 source_units 检查候选是否遗漏或改变影响研究结论的事实、数量口径、否定、归属和限制。"
            "卡片无需抄录所有句子，但不得因综合提要而丢失重要反证或边界。"
            "candidate_source_unit_ids 是候选的完整研究范围，本批 source_units 只是核验窗口；"
            "候选中涉及其他单元的范围表述不构成本批越界，不因窗口不含它们而判来源缺失。"
            "checked_object_ids/unverified_object_ids 使用本批 source_units 的 unit_id；不要使用卡片 item_id。",
            payload,
            scoped_check(identities),
            parent=parent,
            validate=lambda value, identities=identities: coverage(value, identities),
        )
        checks.append(check)
    return checks
