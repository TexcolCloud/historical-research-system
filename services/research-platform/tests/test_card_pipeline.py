"""Offline orchestration contracts only; no historical accuracy or model approval claim."""

import asyncio
import json
from contextlib import contextmanager
from copy import deepcopy
from types import MappingProxyType, SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError

from hrs_platform import cards as module
from hrs_platform.cards import CardPlan, Cards, ResearchPlan, neighbor_context, validate_topics
from hrs_platform.domain.generation_contracts import CardDraft, ReadingRecord
from hrs_platform.outputs import StageInputMismatch, fingerprint
from hrs_platform.reading import (
    card_model,
    check_candidate_coverage,
    read_batch,
    reading_units,
    scoped_check,
    synthesis_readings,
)


def chapter(text):
    return {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "run_id": str(uuid4()),
        "title": "合成材料",
        "text": text,
        "kind": "chapter",
        "pages": [1],
        "codepoints": len(text),
        "parts": [{"span_id": "p1", "start": 17, "text": text, "source": {"pages": [1]}}],
    }


def record(unit):
    return {
        "unit_id": unit["unit_id"],
        "main_records": [unit["text"]],
        "attribution": "原文",
        "dates_quantities_actors": [],
        "negations_and_limits": [],
        "note_table_dependencies": [],
        "candidate_quotes": [unit["text"]],
        "uncertainties": [],
    }


def verdict(ids, failed=False):
    return {
        "checked_object_ids": list(ids),
        "unverified_object_ids": [],
        "findings": [
            {
                "object_id": ids[0],
                "severity": "material",
                "code": "scope",
                "explanation": "合成失败",
                "source_unit_ids": [],
                "required_change": "修正归属",
            }
        ]
        if failed
        else [],
        "conclusion": "needs_revision" if failed else "pass",
        "reasoning_summary": "合成测试回执",
    }


class MemoryOutputs:
    def __init__(self):
        self.values, self.dependencies, self.events = {}, {}, []

    def node(self, run, key, **properties):
        self.events.append((key, properties.get("state", "running")))
        return key

    def get(self, run, key, dependency=None):
        if key in self.values and dependency is not None:
            if self.dependencies[key] != fingerprint(dependency):
                raise StageInputMismatch("Different fixed input")
        return deepcopy(self.values.get(key))

    def put(self, run, key, value, dependency):
        if key in self.values:
            assert self.values[key] == value and self.dependencies[key] == fingerprint(dependency)
        self.values[key], self.dependencies[key] = (
            json.loads(json.dumps(value, default=str)),
            fingerprint(dependency),
        )
        return value

    @contextmanager
    def operation(self, run, key, label, **kwargs):
        self.events.append((key, "running"))
        try:
            yield key
        except BaseException:
            self.events.append((key, "failed"))
            raise
        else:
            self.events.append((key, "completed"))


def test_reading_keeps_whole_source_and_nonadjacent_note_ownership():
    text = "# 来源\n\n原文记载①。\n\n" + "无关段落。" * 30 + "\n\n① 此处为译者更正。\n\n——译者\n"
    original = chapter(text)
    units = reading_units(original, size=80)
    assert "".join(unit["text"] for unit in units) == text
    body = next(unit for unit in units if "原文记载" in unit["text"])
    note = next(unit for unit in units if unit["kind"] == "note")
    assert "——译者" in note["text"]
    assert any("——译者" in unit["text"] for unit in neighbor_context(units, [body]))
    for unit in units:
        assert unit["sources"][0]["start"] == 17 + unit["start"]
        assert unit["text"] == text[unit["start"] : unit["end"]]
        assert "retrieval_text" not in unit


def test_reading_carries_section_introductions_and_shared_table_scope():
    text = "# 总题\n\n本月条件如下：\n\n## 一、甲\n\n条件甲。\n\n## 二、乙\n\n条件乙。\n\n"
    text += '<table><tr><th>地区</th><th>合计</th></tr><tr><td>甲</td><td rowspan="2">120</td></tr><tr><td>乙</td></tr></table>'
    units = reading_units(chapter(text), size=80)
    second = next(unit for unit in units if "条件乙" in unit["text"])
    assert any(extra["role"] == "section_intro" and "本月" in extra["text"] for extra in second["context"])
    tables = [unit for unit in units if unit["kind"] == "html_table"]
    assert any(
        scope["value"] == "120" and scope["row_numbers"] == [2, 3]
        for unit in tables
        for scope in unit["table_scopes"]
    )
    assert any('rowspan="2"' in unit["text"] and "<td>乙</td>" in unit["text"] for unit in tables)


@pytest.mark.parametrize("assigned", [["a"], ["a", "a", "b"], ["a", "b", "unknown"]])
def test_topic_plan_rejects_uncovered_duplicate_or_unknown_units(assigned):
    plan = CardPlan(rationale="测试", topics=[{"name": "主题", "objective": "测试", "unit_ids": assigned}])
    with pytest.raises(ValueError, match="exactly one"):
        validate_topics(plan, [{"unit_id": "a"}, {"unit_id": "b"}])


def test_oversized_requests_stop_before_model_and_preserve_atomic_sources(monkeypatch):
    monkeypatch.setattr("hrs_platform.reading.CARD_INPUT_TOKENS", 1)
    models = SimpleNamespace(run=lambda *a, **k: pytest.fail("Oversize input reached a model"))
    with pytest.raises(ValueError, match="超过输入预算"):
        asyncio.run(
            card_model(models, "run", "test", "review", {"source": "不允许截断"}, scoped_check(["u"]))
        )


@pytest.mark.parametrize("outside_batch", [False, True])
def test_long_readings_use_token_bounded_digests_without_borrowing_unseen_sources(outside_batch):
    units = [{"unit_id": "a", "text": "原文甲"}, {"unit_id": "b", "text": "原文乙"}]
    row = record(units[0])
    row["main_records"] = ["数量口径与原文归属。" * 1200]
    readings = [
        {"readings": [row], "themes": [], "structure": [], "questions": [], "boundary_observations": []}
    ]
    calls = []

    async def run(run_id, key, instructions, payload, output_type, **kwargs):
        calls.append(payload)
        assert "译者" in instructions
        chosen = units[1] if outside_batch else units[0]
        value = output_type(
            covered_record_indexes=[0],
            themes=[],
            structure=[],
            facts=[],
            quotation_candidates=[{"unit_id": chosen["unit_id"], "quote": chosen["text"]}],
            questions=[],
            boundary_observations=[],
        )
        kwargs["validate"](value)
        return value

    operation = synthesis_readings(SimpleNamespace(run=run), "run", "digest", readings, units, None)
    if outside_batch:
        with pytest.raises(ValueError, match="fixed source"):
            asyncio.run(operation)
    else:
        result = asyncio.run(operation)
        assert result[0]["quotation_candidates"][0]["unit_id"] == "a"
    assert len(calls) == 1
    assert calls[0]["records"][0]["record"] == readings[0]


@pytest.mark.parametrize("change_summary", [False, True])
def test_local_repair_reuses_only_unchanged_checks_and_preserves_verified_records(change_summary):
    batch = [{"unit_id": "a", "text": "原文甲"}, {"unit_id": "b", "text": "原文乙"}]
    calls, cache = [], {}

    async def run(run_id, key, instructions, payload, output_type, **kwargs):
        if key in cache:
            value = output_type.model_validate(cache[key])
        else:
            calls.append((key, deepcopy(payload)))
            if output_type is ReadingRecord:
                revision = int(key.rsplit(":", 1)[-1])
                rows = [record(unit) for unit in batch]
                if revision:
                    assert payload["fixed_unit_ids"] == ["a"]
                    assert payload["previous_record"]["readings"][0] == rows[0]
                    rows[1]["attribution"] = "译注"
                    # A model may gratuitously rewrite an already accepted record.
                    rows[0]["attribution"] = "模型误改"
                value = output_type(
                    readings=rows,
                    themes=["修正概括"] if change_summary and revision else [],
                    structure=[],
                    questions=[],
                    boundary_observations=[],
                )
            else:
                assert [u["unit_id"] for u in payload["source_units"]] == ["a", "b"]
                assert payload["context_units"] == []
                row = payload["reading_record"]["readings"][0]
                value = output_type.model_validate(
                    verdict(
                        payload["required_object_ids"],
                        failed=row["unit_id"] == "b" and row["attribution"] != "译注",
                    )
                )
            cache[key] = value.model_dump(mode="json")
        kwargs["validate"](value)
        return value

    result = asyncio.run(
        read_batch(SimpleNamespace(run=run, outputs=MemoryOutputs()), "run", "read", batch, "scope", None)
    )
    assert result["readings"][1]["attribution"] == "译注"
    assert result["readings"][0] == record(batch[0])
    assert len([p for k, p in calls if ":check:" in k and p["required_object_ids"] == ["a"]]) == (
        2 if change_summary else 1
    )
    assert len([k for k, _ in calls if ":check:" in k]) == (4 if change_summary else 3)


def test_candidate_windows_declare_whole_scope_but_only_review_window_targets():
    units = [{"unit_id": key, "text": key} for key in ("a", "b", "c")]
    targets = []

    async def run(run_id, key, instructions, payload, output_type, **kwargs):
        assert payload["candidate_source_unit_ids"] == ["a", "b", "c"]
        assert payload["required_object_ids"] == [u["unit_id"] for u in payload["source_units"]]
        targets.extend(payload["required_object_ids"])
        value = output_type.model_validate(verdict(payload["required_object_ids"]))
        kwargs["validate"](value)
        return value

    candidate = SimpleNamespace(model_dump=lambda **kwargs: {"scope": "a, b, c"})
    result = asyncio.run(
        check_candidate_coverage(
            SimpleNamespace(run=run),
            "run",
            "test",
            candidate,
            units,
            None,
            "scope",
            [],
            {},
        )
    )
    assert len(result) == 2 and targets == ["a", "b", "c"]


@pytest.mark.parametrize("severity", ["minor", "material"])
def test_unresolved_reading_falls_back_to_source_without_approving_failed_interpretation(severity):
    batch = [{"unit_id": "a", "text": "已核验原文"}, {"unit_id": "b", "text": "原文乙，\n数量 7。"}]
    revisions = []
    outputs = MemoryOutputs()

    async def run(run_id, key, instructions, payload, output_type, **kwargs):
        if output_type is ReadingRecord:
            revisions.append(key)
            rows = [record(unit) for unit in batch]
            rows[1]["attribution"] = "错误归属"
            value = ReadingRecord(
                readings=rows, themes=["错误概括"], structure=[], questions=[], boundary_observations=[]
            )
        else:
            identity = payload["required_object_ids"][0]
            check = verdict([identity], failed=identity == "b")
            if check["findings"]:
                check["findings"][0]["severity"] = severity
            value = output_type.model_validate(check)
        kwargs["validate"](value)
        return value

    models = SimpleNamespace(run=run, outputs=outputs)
    result = asyncio.run(read_batch(models, "run", "read", batch, "scope", None))
    assert len(revisions) == 3
    assert result["readings"][0] == record(batch[0])
    assert result["readings"][1]["candidate_quotes"] == [batch[1]["text"]]
    assert result["readings"][1]["main_records"] == []
    assert "错误" not in json.dumps(result, ensure_ascii=False)
    assert result["themes"] == []
    assert result["source_only_unit_ids"] == ["b"]
    assert any("fallback" in key for key, _ in outputs.events)
    models.run = lambda *a, **k: pytest.fail("Resolved batch reran a model")
    assert asyncio.run(read_batch(models, "run", "read", batch, "scope", None)) == result


@pytest.mark.parametrize("error_type", ["model_output_limit", "model_output_invalid"])
@pytest.mark.parametrize("stage", ["reading", "check"])
def test_local_model_failure_falls_back_without_more_calls(error_type, stage):
    async def run(run_id, key, instructions, payload, output_type, **kwargs):
        if stage == "check" and output_type is ReadingRecord:
            value = ReadingRecord(
                readings=[record(unit) for unit in payload["source_units"]],
                themes=[],
                structure=[],
                questions=[],
                boundary_observations=[],
            )
            kwargs["validate"](value)
            return value
        raise ApplicationError("model output exhausted", type=error_type, non_retryable=True)

    models = SimpleNamespace(run=run, outputs=MemoryOutputs())
    result = asyncio.run(read_batch(models, "run", "read", [{"unit_id": "a", "text": "来源"}], "scope", None))
    assert result["source_only_unit_ids"] == ["a"]
    assert result["readings"][0]["candidate_quotes"] == ["来源"]
    changed = asyncio.run(
        read_batch(models, "run", "read", [{"unit_id": "a", "text": "新来源"}], "scope", None)
    )
    assert changed["readings"][0]["candidate_quotes"] == ["新来源"]
    assert len(models.outputs.values) == 2


@pytest.mark.parametrize(
    "error",
    [
        ApplicationError("budget", type="model_request_budget", non_retryable=True),
        ApplicationError("credentials", non_retryable=True),
        OSError("S3 unavailable"),
        asyncio.CancelledError(),
    ],
)
def test_global_failures_and_cancellation_do_not_become_source_fallback(error):
    async def run(*args, **kwargs):
        raise error

    models = SimpleNamespace(run=run, outputs=MemoryOutputs())
    with pytest.raises(type(error)):
        asyncio.run(read_batch(models, "run", "read", [{"unit_id": "a", "text": "来源"}], "scope", None))
    assert not models.outputs.values


@pytest.mark.parametrize("fallback", [False, True])
def test_generate_reads_all_sources_then_builds_cross_assignment_topics_and_resumes_without_api(
    monkeypatch, fallback
):
    originals = [
        chapter(
            f"# {name}一\n\n{name}材料一。\n\n# {name}二\n\n{name}材料二。\n\n# {name}三\n\n{name}材料三。"
        )
        for name in ["甲", "乙"]
    ]
    units = [unit for source in originals for unit in reading_units(source)]
    assert len(units) == 6
    cards = object.__new__(Cards)
    cards.settings = SimpleNamespace(reasoning_model="mock")
    cards.engine, cards.outputs = None, MemoryOutputs()
    cards.library = SimpleNamespace(
        chapters=lambda _: [MappingProxyType(row) for row in originals],
        chapter=lambda identity: next(row for row in originals if row["id"] == identity),
    )
    run_id = str(uuid4())
    monkeypatch.setattr(module, "get_run", lambda *_: {"book_id": "book", "conversion": {}})
    monkeypatch.setattr(
        module,
        "Review",
        lambda *_: SimpleNamespace(
            read_json=lambda _: {"manifest": {"pages": [], "page_count": 1}, "files": {}}
        ),
    )
    transitions = []
    monkeypatch.setattr(
        module, "Activities", lambda *_: SimpleNamespace(transition=lambda *args: transitions.append(args))
    )
    monkeypatch.setattr("agents.Runner.run", lambda *a, **k: pytest.fail("Real model API called"))
    monkeypatch.setattr(
        "hrs_platform.visual_review.VisualReview.check", lambda *a, **k: pytest.fail("Vision API called")
    )
    calls, cache, active, maximum, fail_topic = [], {}, 0, 0, True
    first_reads_ready = asyncio.Event()

    async def run(identity, key, instructions, payload, output_type, **kwargs):
        nonlocal active, maximum, fail_topic
        dependency = fingerprint(payload)
        if key in cache:
            saved, old_dependency = cache[key]
            assert dependency == old_dependency
            value = output_type.model_validate(saved)
        else:
            calls.append((key, deepcopy(payload)))
            if output_type is ResearchPlan:
                value = output_type(
                    rationale="两组阅读",
                    assignments=[
                        {"name": str(i), "objective": "通读", "chapter_ids": [row["id"]]}
                        for i, row in enumerate(originals)
                    ],
                )
            elif output_type is CardPlan:
                if fail_topic:
                    fail_topic = False
                    raise RuntimeError("temporary topic failure")
                value = output_type(
                    rationale="跨分工主题",
                    topics=[
                        {
                            "name": "合并",
                            "objective": "两章首段",
                            "unit_ids": [units[0]["unit_id"], units[3]["unit_id"]],
                        },
                        {
                            "name": "甲",
                            "objective": "甲次段",
                            "unit_ids": [units[1]["unit_id"], units[2]["unit_id"]],
                        },
                        {
                            "name": "乙",
                            "objective": "乙次段",
                            "unit_ids": [units[4]["unit_id"], units[5]["unit_id"]],
                        },
                    ],
                )
            elif output_type is ReadingRecord:
                if key.endswith(":阅读:2:reading:0"):
                    assert payload["previous_reading"]["readings"][-1]["unit_id"] in {
                        units[1]["unit_id"],
                        units[4]["unit_id"],
                    }
                else:
                    assert payload["previous_reading"] is None
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    first_reads_ready.set()
                await asyncio.wait_for(first_reads_ready.wait(), timeout=5)
                active -= 1
                value = output_type(
                    readings=[record(unit) for unit in payload["source_units"]],
                    themes=[],
                    structure=[],
                    questions=[],
                    boundary_observations=[],
                )
            elif output_type is CardDraft:
                value = output_type(
                    title=payload["objective"],
                    document_type="合成",
                    source_layer="原文",
                    formation_date={},
                    event_date={},
                    tags=[],
                    entities=[],
                    evidence_relations=[],
                    no_argument_reason="测试",
                    items=[
                        {
                            "item_id": f"e{i}",
                            "kind": "evidence",
                            "title": "原文",
                            "text": unit["text"],
                            "source_unit_ids": [unit["unit_id"]],
                            "selections": [{"unit_id": unit["unit_id"], "quote": unit["text"]}],
                        }
                        for i, unit in enumerate(payload["source_units"])
                    ],
                )
            else:
                ids = payload.get("required_object_ids") or [
                    row["item_id"] for row in payload["candidate"]["items"]
                ]
                value = output_type.model_validate(
                    verdict(
                        ids, failed=fallback and "reading_record" in payload and ids == [units[0]["unit_id"]]
                    )
                )
            cache[key] = (value.model_dump(mode="json"), dependency)
        kwargs["validate"](value)
        return value

    cards.models = SimpleNamespace(run=run, outputs=cards.outputs)
    with pytest.raises(RuntimeError, match="temporary topic"):
        asyncio.run(cards.generate(run_id))
    assert cards.outputs.get(run_id, "generated-cards") is None
    # Published library changes must not silently replace fixed evidence on retry.
    cards.library = SimpleNamespace(chapters=lambda _: pytest.fail("Snapshot was not reused"))
    result = asyncio.run(cards.generate(run_id))
    assert result["candidates"] == 3 and maximum == 2
    read_calls = [p for k, p in calls if k.endswith(":reading:0")]
    assert sorted(unit["unit_id"] for p in read_calls for unit in p["source_units"]) == sorted(
        unit["unit_id"] for unit in units
    )
    saved = cards.outputs.get(run_id, "generated-cards")["cards"]
    first_source = cards.outputs.get(run_id, saved[0]["source_step"])
    assert first_source["reading_assignment_indexes"] == [0, 1]
    assert all(row["text_check"]["source_checks"] for row in saved)
    if fallback:
        assert any("fallback" in key for key, _ in cards.outputs.events)
        assert any(record.get("source_only_unit_ids") for record in first_source["readings"])
        assert any(":独立核验:" in key for key, _ in calls)
        assert any(":source-coverage:" in key for key, _ in calls)
        for candidate in saved:
            source = cards.outputs.get(run_id, candidate["source_step"])
            assert all(
                set(record.get("source_only_unit_ids", [])) <= set(source["assigned_unit_ids"])
                for record in source["readings"]
            )
    before = len(calls)
    assert asyncio.run(cards.generate(run_id)) == result and len(calls) == before
    assert transitions[-1][1:] == ("processing", "vision")


def test_failed_assignment_cancels_siblings_before_topic_generation(monkeypatch):
    originals = [chapter(name) for name in ["甲", "乙", "丙"]]
    cards = object.__new__(Cards)
    cards.settings, cards.engine, cards.outputs = (
        SimpleNamespace(reasoning_model="mock"),
        None,
        MemoryOutputs(),
    )
    cards.library = SimpleNamespace(
        chapters=lambda _: originals,
        chapter=lambda identity: next(row for row in originals if row["id"] == identity),
    )
    monkeypatch.setattr(module, "get_run", lambda *_: {"book_id": "book", "conversion": {}})
    monkeypatch.setattr(
        module,
        "Review",
        lambda *_: SimpleNamespace(
            read_json=lambda _: {"manifest": {"pages": [], "page_count": 1}, "files": {}}
        ),
    )
    monkeypatch.setattr(module, "Activities", lambda *_: SimpleNamespace(transition=lambda *args: None))

    async def plan(*args, **kwargs):
        assert args[4] is ResearchPlan
        return ResearchPlan(
            rationale="测试",
            assignments=[
                {"name": row["text"], "objective": row["text"], "chapter_ids": [row["id"]]}
                for row in originals
            ],
        )

    cards.models = SimpleNamespace(run=plan)
    started, stopped, running = [], [], []

    async def exercise():
        second = asyncio.Event()

        async def reading(models, run, key, batch, objective, *args, **kwargs):
            started.append(objective)
            running.append(objective)
            try:
                if objective == "甲":
                    await second.wait()
                    raise RuntimeError("reading failed")
                second.set()
                await asyncio.Future()
            finally:
                running.remove(objective)
                stopped.append(objective)

        monkeypatch.setattr(module, "read_batch", reading)
        with pytest.raises(RuntimeError, match="reading failed"):
            await cards.generate(str(uuid4()))
        assert not running and "乙" in stopped and len(started) >= 2
        assert "generated-cards" not in cards.outputs.values
        assert not any("卡片主题" in key for key, _ in cards.outputs.events)

    asyncio.run(exercise())
