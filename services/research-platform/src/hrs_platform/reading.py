"""Reuse retained research prompts and bounded digests without the retired job queue."""

import json
from enum import Enum

from pydantic import create_model

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
        raise ValueError(f"制卡步骤 {key} 超过输入预算，请缩小研究主题；已保存证据不截断、不放行。")
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

    def validate(value):
        if len(value.readings) != len(batch) or {row.unit_id for row in value.readings} != identities:
            raise ValueError("Every supplied unit must be read exactly once.")
        by_id = {row["unit_id"]: row["text"] for row in batch}
        if any(quote not in by_id[row.unit_id] for row in value.readings for quote in row.candidate_quotes):
            raise ValueError("Reading quotations must be exact continuous source text.")

    problems, prior, accepted = None, None, {}
    for revision in range(3):

        def validate_repair(value, accepted=accepted):
            validate(value)
            if any(
                row.unit_id in accepted and row.model_dump(mode="json") != accepted[row.unit_id]
                for row in value.readings
            ):
                raise ValueError("A local reading repair must preserve already verified unit records.")

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
        prior = reading.model_dump(mode="json")
        problems, accepted = [], {}
        for unit, record in (
            (unit, next(row for row in prior["readings"] if row["unit_id"] == unit["unit_id"]))
            for unit in batch
        ):
            payload = {
                "source_units": [unit],
                "context_units": [
                    *(context or []),
                    *(other for other in batch if other["unit_id"] != unit["unit_id"]),
                ],
                "research_state": research_state or {},
                "reading_record": {**prior, "readings": [record]},
                "required_object_ids": [unit["unit_id"]],
            }
            check = await card_model(
                models,
                run_id,
                f"{key}:check:{fingerprint(payload)}",
                prompts.CHECK_READING
                + "\n只核验 source_units 及其在批次概括中的表达；其他概括不属本单元时不判遗漏。上下文不是核验对象。",
                payload,
                scoped_check([unit["unit_id"]]),
                parent=parent,
                validate=lambda value, identity=unit["unit_id"]: coverage(value, [identity]),
            )
            if passed(check):
                accepted[unit["unit_id"]] = record
            else:
                problems.append(check.model_dump(mode="json"))
        if not problems:
            return prior
    raise ValueError("逐段阅读仍有实质核验问题，阅读记录和原始证据已保留。")


async def synthesis_readings(models, run_id, key, readings, units, parent):
    current = readings
    known = {row["unit_id"]: row for row in units}
    for level in range(4):
        if estimate_request({"records": current})["input_tokens"] <= 6000:
            return current
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
    raise ValueError("阅读提要仍超过单次综合范围，需要拆分本次研究分工；完整阅读记录已保存。")


async def check_candidate_coverage(
    models, run_id, key, candidate, units, parent, objective, context, research_state
):
    """Check every assigned source again after synthesis, including unquoted limits."""
    checks = []
    for offset in range(0, len(units), 2):
        batch = units[offset : offset + 2]
        identities = {unit["unit_id"] for unit in batch}
        payload = {
            "candidate": candidate.model_dump(mode="json"),
            "required_object_ids": sorted(identities),
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
            "checked_object_ids/unverified_object_ids 使用本批 source_units 的 unit_id；不要使用卡片 item_id。",
            payload,
            scoped_check(identities),
            parent=parent,
            validate=lambda value, identities=identities: coverage(value, identities),
        )
        checks.append(check)
    return checks
