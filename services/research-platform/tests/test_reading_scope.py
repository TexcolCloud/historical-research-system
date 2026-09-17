import asyncio
import json
from types import SimpleNamespace

import pytest
from agents import AgentOutputSchema
from pydantic import ValidationError
from test_card_pipeline import MemoryOutputs, record, verdict

from hrs_platform.domain.generation_contracts import ReadingRecord
from hrs_platform.services.cards.reading import coverage
from hrs_platform.services.cards.reading import read_batch
from hrs_platform.services.cards.reading import scoped_check
from hrs_platform.domain.card_rules import source_payload


def test_reading_batch_checks_sources_once_and_repairs_only_missing_verdicts():
    batch = [{"unit_id": str(i), "text": f"来源{i}，数量不得混同。"} for i in range(5)]
    checks = []

    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        if output_type is ReadingRecord:
            value = output_type(readings=[record(u) for u in batch], themes=[], structure=[],
                                questions=[], boundary_observations=[])
        else:
            checks.append(payload["required_object_ids"])
            ids = payload["required_object_ids"]
            value = output_type.model_validate(verdict(ids[:-1] if len(ids) > 1 else ids))
        kwargs["validate"](value)
        return value

    result = asyncio.run(read_batch(SimpleNamespace(run=model, outputs=MemoryOutputs()),
                                   "run", "batch", batch, "scope", None))
    assert checks == [[str(i) for i in range(5)], ["4"]]
    assert result["readings"] == [record(u) for u in batch]


def test_source_packet_deduplicates_text_but_keeps_note_owner_links():
    from copy import deepcopy
    note = {"id": "note", "text": "译者指出：仅指本地区。" * 200,
            "role": "note_owner", "sources": [{"pages": [4]}]}
    units = [{"unit_id": str(i), "text": f"正文{i}①", "context": [note]} for i in range(5)]
    payload = {"source_units": units, "context_units": [{**note, "unit_id": "note"}, units[0]]}
    original = deepcopy(payload)
    result = source_payload(payload)
    assert payload == original
    assert [u["unit_id"] for u in result["source_units"]] == [str(i) for i in range(5)]
    assert result["context_units"] == [{**note, "unit_id": "note"}]
    assert all(u["context"] == [{"unit_id": "note", "role": "note_owner"}] for u in result["source_units"])
    assert len(json.dumps(result)) < len(json.dumps(payload)) * 0.3
    assert source_payload(result) == result
    payload["context_units"][0] = {**note, "unit_id": "note", "text": "伪造"}
    with pytest.raises(ValueError, match="different evidence"):
        source_payload(payload)


def test_unlocated_batch_failure_never_approves_individual_readings():
    batch = [{"unit_id": "a", "text": "合成原文"}, {"unit_id": "b", "text": "限定范围"}]

    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        if output_type is ReadingRecord:
            value = output_type(readings=[record(u) for u in batch], themes=[], structure=[],
                                questions=[], boundary_observations=[])
        else:
            value = output_type.model_validate({**verdict(payload["required_object_ids"]),
                                                "conclusion": "insufficient_evidence"})
        kwargs["validate"](value)
        return value

    result = asyncio.run(read_batch(SimpleNamespace(run=model, outputs=MemoryOutputs()),
                                   "run", "batch", batch, "scope", None))
    assert result["source_only_unit_ids"] == ["a", "b"]
    assert all(not row["main_records"] for row in result["readings"])


@pytest.mark.parametrize("fail_at", ["batch-verdict", "unit-fanout", "fallback-fanout"])
def test_completed_batch_is_replayed_after_storage_failure_without_new_model_scope(fail_at):
    from hrs_platform.domain.errors import TaskError
    batch = [{"unit_id": str(i), "text": f"合成原文{i}"}
             for i in range(2 if fail_at == "fallback-fanout" else 3)]
    outputs, cache, checks = MemoryOutputs(), {}, []
    store, broken, written = outputs.put, [True], []

    def write(run, key, value, dependency):
        if ":unit-check:" in key:
            written.append(key)
        if broken[0] and ((fail_at == "batch-verdict" and ":batch-check:" in key)
                          or (fail_at in {"unit-fanout", "fallback-fanout"} and len(written) == 2)):
            broken[0] = False
            raise OSError("synthetic receipt interruption")
        return store(run, key, value, dependency)

    outputs.put = write

    async def model(run, key, instructions, payload, output_type, **kwargs):
        if key in cache:
            return output_type.model_validate(cache[key])
        if output_type is ReadingRecord:
            value = output_type(readings=[record(u) for u in batch], themes=[], structure=[],
                                questions=[], boundary_observations=[])
        else:
            if fail_at == "fallback-fanout" and len(payload["required_object_ids"]) > 1:
                raise TaskError("Cached aggregate output exhausted", type="model_output_invalid",
                                       non_retryable=True)
            checks.append(payload["required_object_ids"])
            value = output_type.model_validate(verdict(payload["required_object_ids"]))
        kwargs["validate"](value)
        cache[key] = value.model_dump(mode="json")
        return value

    models = SimpleNamespace(run=model, outputs=outputs)
    with pytest.raises(OSError, match="receipt interruption"):
        asyncio.run(read_batch(models, "run", "batch", batch, "scope", None))
    result = asyncio.run(read_batch(models, "run", "batch", batch, "scope", None))
    assert checks == ([["0"], ["1"]] if fail_at == "fallback-fanout" else [["0", "1", "2"]])
    assert result["readings"] == [record(u) for u in batch]


def test_context_ids_are_excluded_from_both_model_schema_and_validated_receipts():
    model = scoped_check({"target-a", "target-b"})
    payload = {
        "checked_object_ids": ["target-a", "target-b"],
        "unverified_object_ids": [],
        "findings": [],
        "conclusion": "pass",
        "reasoning_summary": "The two targets were checked using separate context.",
    }
    value = model.model_validate(payload)
    coverage(value, {"target-a", "target-b"})
    assert value.model_dump(mode="json") == payload
    for field in ("checked_object_ids", "unverified_object_ids"):
        with pytest.raises(ValidationError):
            model.model_validate({**payload, field: ["context-only"]})
    schema = json.dumps(AgentOutputSchema(model).json_schema())
    assert '"enum": ["target-a", "target-b"]' in schema
    with pytest.raises(ValueError, match="Missing IDs"):
        coverage(
            model.model_validate({**payload, "checked_object_ids": ["target-a"]}), {"target-a", "target-b"}
        )


@pytest.mark.parametrize(
    "duplicate,overlap,bad_finding", [(True, False, False), (False, True, False), (False, False, True)]
)
def test_receipts_cannot_double_count_targets_or_attach_findings_to_another_scope(
    duplicate, overlap, bad_finding
):
    model = scoped_check(["a"])
    payload = {
        "checked_object_ids": ["a", "a"] if duplicate else ["a"],
        "unverified_object_ids": ["a"] if overlap else [],
        "conclusion": "pass",
        "reasoning_summary": "test",
        "findings": [
            {
                "object_id": "context-only",
                "severity": "minor",
                "code": "scope",
                "explanation": "test",
                "source_unit_ids": [],
                "required_change": "test",
            }
        ]
        if bad_finding
        else [],
    }
    with pytest.raises(ValueError):
        coverage(model.model_validate(payload), ["a"])
