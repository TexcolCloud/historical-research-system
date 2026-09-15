"""Synthetic tool and recovery checks; no paid text/vision requests or historical verdicts."""

import asyncio
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext
from temporalio.exceptions import ApplicationError
from test_card_pipeline import MemoryOutputs, chapter

from hrs_platform import card_evidence as module
from hrs_platform.card_evidence import CardEvidence
from hrs_platform.cards import CardTopic, quote_pages
from hrs_platform.reading import compact_readings, coverage_batches, reading_units
from hrs_platform.search import recover_retrieval


@pytest.fixture
def research(monkeypatch):
    original = chapter("# 运输\n\n运输120吨，不包括水运①。\n\n# 注释\n\n① 仓库登记110吨，口径不同。")
    units = reading_units(original, size=30)
    outputs, calls = MemoryOutputs(), []
    settings = SimpleNamespace(reasoning_model="synthetic", opensearch_index="index")
    search = SimpleNamespace(client=SimpleNamespace(indices=SimpleNamespace(exists=lambda **_: True)))
    hits = [{**u, "generation": "generation", "score": 1} for u in units]

    def query(text, book_id, **kwargs):
        assert book_id == original["book_id"]
        assert kwargs["decompose"] is False
        calls.append((text, kwargs))
        return deepcopy(hits)

    search.search = query
    monkeypatch.setattr(module, "Search", lambda *_: search)
    evidence = CardEvidence(settings, None, outputs, None)
    parent = {"result": {"published": True, "retrieval_generation": "generation"}}
    monkeypatch.setattr(module, "get_run", lambda *_: parent)
    monkeypatch.setattr("hrs_platform.search.get_run", lambda *_: parent)
    corpus = {
        "book_id": original["book_id"],
        "parent_run_id": "parent",
        "generation": "generation",
        "index": "index",
        "retrieval_policy": None,
    }
    topic = CardTopic(name="运输", objective="比较运输与仓库记录", unit_ids=[units[0]["unit_id"]])
    return SimpleNamespace(
        evidence=evidence,
        corpus=corpus,
        topic=topic,
        units=units,
        hits=hits,
        calls=calls,
        outputs=outputs,
        search=search,
        parent=parent,
    )


async def invoke(tool, **arguments):
    encoded = json.dumps(arguments)
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="synthetic", tool_arguments=encoded)
    return json.loads(await tool.on_invoke_tool(context, encoded))


def execute(fixture, run):
    return asyncio.run(
        fixture.evidence.research(
            SimpleNamespace(run=run),
            "run",
            "topic",
            fixture.corpus,
            fixture.topic,
            [],
            fixture.units,
            "parent",
        )
    )


async def use_tools(output_type, kwargs):
    search, read = kwargs["tools"]
    hits = await invoke(
        search,
        queries=[{"query": purpose, "purpose": purpose} for purpose in ["support", "counter", "qualify"]],
    )
    identities = list(dict.fromkeys(u for row in hits for hit in row["hits"] for u in hit["unit_ids"]))
    return await invoke(read, unit_ids=identities)


def test_real_tools_map_search_ranges_to_frozen_originals_and_reuse_receipts(research):
    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        receipt = await use_tools(output_type, kwargs)
        unit = next(u for u in receipt["units"] if "120" in u["text"])
        assert quote_pages(unit, {"quote": "120吨"}) == [1]
        assert any("口径不同" in u["text"] for u in receipt["units"])
        value = output_type(
            source_unit_ids=[unit["unit_id"]],
            findings=[dict(category="limitation", text="统计口径不同", source_unit_ids=[unit["unit_id"]])],
            unresolved_questions=[],
            sufficient=True,
        )
        kwargs["validate"](value)
        return value

    result = execute(research, model)
    assert len(research.calls) == 3 and len(result["searches"]) == 3
    assert research.calls[0][1]["chapter_ids"]
    assert research.calls[1][1]["chapter_ids"] is None
    assert all(hit["preview_only"] for r in result["searches"] for hit in r["hits"])
    assert execute(research, lambda *a, **k: pytest.fail("Repeated model call")) == result
    assert len(research.calls) == 3


def test_planned_queries_supply_originals_without_model_tool_round_trips(research):
    research.topic.queries = [module.EvidenceQuery(query=p, purpose=p)
                             for p in ("support", "counter", "qualify")]
    model_calls = []

    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        model_calls.append(payload)
        initial = payload["initial_evidence"]
        assert len(initial["searches"]) == 3
        assert all("metrics" not in row for row in initial["searches"])
        originals = initial["originals"]["units"]
        assert any("口径不同" in u["text"] for u in originals)
        uid = originals[0]["unit_id"]
        result = output_type(source_unit_ids=[uid], sufficient=True, unresolved_questions=[],
                             findings=[dict(category="limitation", text="有口径限定", source_unit_ids=[uid])])
        kwargs["validate"](result)
        return result

    first = execute(research, model)
    assert len(model_calls) == 1 and len(research.calls) == 3
    assert execute(research, model) == first
    assert len(model_calls) == 1


def test_planned_query_outage_recovers_before_first_paid_call(research):
    research.topic.queries = [module.EvidenceQuery(query=p, purpose=p)
                             for p in ("support", "counter", "qualify")]
    query, broken = research.search.search, [True]

    def flaky(text, *args, **kw):
        if text == "counter" and broken[0]:
            raise OSError("synthetic outage")
        return query(text, *args, **kw)

    research.search.search = flaky
    with pytest.raises(OSError):
        execute(research, lambda *a, **k: pytest.fail("Model called before retrieval recovered"))
    broken[0] = False

    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        uid = payload["initial_evidence"]["originals"]["units"][0]["unit_id"]
        result = output_type(source_unit_ids=[uid], sufficient=True, unresolved_questions=[],
                             findings=[dict(category="event", text="恢复", source_unit_ids=[uid])])
        kwargs["validate"](result)
        return result

    assert execute(research, model)["assessment"]["sufficient"]
    assert [q for q, _ in research.calls] == ["support", "counter", "qualify"]


def test_planned_evidence_over_budget_keeps_tools_without_truncation(research, monkeypatch):
    research.topic.queries = [module.EvidenceQuery(query=p, purpose=p)
                             for p in ("support", "counter", "qualify")]
    original = deepcopy(research.units)
    monkeypatch.setattr(module, "READ_TOKENS", 1)

    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        assert payload["initial_evidence"]["originals"]["selection_required"]
        assert len(kwargs["tools"]) == 2
        result = output_type(source_unit_ids=[], findings=[], sufficient=False,
                             unresolved_questions=["需要缩小取证范围"])
        kwargs["validate"](result)
        return result

    assert not execute(research, model)["assessment"]["sufficient"]
    assert original == research.units


@pytest.mark.parametrize(
    "failure", ["wrong_book", "wrong_generation", "wrong_text", "changed_during_search", "deleted_index"]
)
def test_foreign_stale_or_missing_index_never_becomes_empty_evidence(research, failure):
    if failure == "wrong_book":
        research.hits[0]["book_id"] = "other"
    elif failure == "wrong_generation":
        research.hits[0]["generation"] = "old"
    elif failure == "wrong_text":
        research.hits[0]["text"] = "invented"
    elif failure == "deleted_index":
        research.search.client.indices.exists = lambda **_: False
    else:
        search = research.search.search

        def change(*args, **kwargs):
            hits = search(*args, **kwargs)
            research.parent["result"]["retrieval_generation"] = "new"
            return hits

        research.search.search = change

    async def model(*args, **kwargs):
        await invoke(kwargs["tools"][0], queries=[{"query": "运输", "purpose": "support"}])
        pytest.fail("Invalid corpus reached synthesis")

    with pytest.raises(ApplicationError) as error:
        execute(research, model)
    assert error.value.type == ("retrieval_wait" if failure == "deleted_index" else "card_corpus_changed")
    assert not any(":search:" in key and "retrieval-recovery" not in key or ":read:" in key
                   for key in research.outputs.values)


def test_mid_research_disconnect_reuses_completed_queries_and_reads(research):
    search = research.search.search
    broken = True

    def flaky(query, *args, **kwargs):
        if query == "counter" and broken:
            raise OSError("synthetic disconnect")
        return search(query, *args, **kwargs)

    research.search.search = flaky

    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        receipt = await use_tools(output_type, kwargs)
        value = output_type(
            source_unit_ids=[receipt["units"][0]["unit_id"]],
            findings=[dict(category="event", text="恢复", source_unit_ids=[receipt["units"][0]["unit_id"]])],
            unresolved_questions=[],
            sufficient=True,
        )
        kwargs["validate"](value)
        return value

    with pytest.raises(OSError, match="disconnect"):
        execute(research, model)
    assert len(research.calls) == 1
    broken = False
    assert execute(research, model)["assessment"]["sufficient"]
    assert [query for query, _ in research.calls] == ["support", "counter", "qualify"]


def test_empty_search_uses_known_sources_and_never_claims_absence(research):
    research.hits.clear()

    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        search, read = kwargs["tools"]
        results = await invoke(search, queries=[{"query": p, "purpose": p} for p in module.PURPOSES])
        assert all(not row["hits"] for row in results)
        assert "不证明" in instructions
        receipt = await invoke(read, unit_ids=research.topic.unit_ids)
        assert receipt["units"]
        value = output_type(
            source_unit_ids=research.topic.unit_ids,
            findings=[
                dict(category="limitation", text="仅当前原文", source_unit_ids=research.topic.unit_ids)
            ],
            unresolved_questions=["尚不能确定两组数据是否同口径"],
            sufficient=False,
        )
        kwargs["validate"](value)
        return value

    result = execute(research, model)
    assert not result["assessment"]["sufficient"]
    assert len(research.calls) == 4  # one bounded whole-book retry for empty chapter support


def test_preview_only_citations_and_foreign_read_ids_are_rejected(research):
    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        search, read = kwargs["tools"]
        await invoke(search, queries=[{"query": p, "purpose": p} for p in module.PURPOSES])
        assert "error" in await invoke(read, unit_ids=["other-book-unit"])
        value = output_type(
            source_unit_ids=research.topic.unit_ids,
            findings=[dict(category="event", text="preview", source_unit_ids=research.topic.unit_ids)],
            unresolved_questions=[],
            sufficient=True,
        )
        kwargs["validate"](value)

    with pytest.raises(ValueError, match="actually read"):
        execute(research, model)


@pytest.mark.parametrize("multiple", [False, True])
def test_budgets_survive_model_retry_without_truncating_source(research, monkeypatch, multiple):
    original = deepcopy(research.units)
    monkeypatch.setattr(module, "READ_TOKENS", 1)

    async def model(run_id, key, instructions, payload, output_type, **kwargs):
        search, read = kwargs["tools"]
        await invoke(search, queries=[{"query": p, "purpose": p} for p in module.PURPOSES])
        cached = await invoke(search, queries=[{"query": "extra", "purpose": "support"}])
        assert cached[0]["request"]["query"] == "support"
        await invoke(read, unit_ids=[u["unit_id"] for u in research.units] if multiple else research.topic.unit_ids)

    for _ in range(2):
        with pytest.raises(ApplicationError) as failure:
            execute(research, model)
        assert failure.value.type == "card_input_budget"
    assert len(research.calls) == 3
    assert research.units == original
    assert not any(":read:" in key for key in research.outputs.values)


def test_token_packing_and_compact_clues_keep_every_source_and_original_reading():
    units = [{"unit_id": str(i), "text": "正文"} for i in range(19)]
    batches = list(coverage_batches(units))
    assert [u for batch in batches for u in batch] == units
    assert [len(b) for b in batches] == [8, 8, 3]
    record = {
        "readings": [{"unit_id": "1", "candidate_quotes": ["原文"], "negations_and_limits": ["不含水运"]}]
    }
    clues = compact_readings([record])
    assert clues[0]["readings"][0]["negations_and_limits"] == ["不含水运"]
    assert "candidate_quotes" not in clues[0]["readings"][0]
    assert record["readings"][0]["candidate_quotes"] == ["原文"]


def test_duplicate_purposes_cannot_poison_reserved_slots(research):
    async def model(run, key, instructions, payload, output_type, **kw):
        search = kw["tools"][0]
        invalid = await invoke(search, queries=[{"query": str(i), "purpose": "support"} for i in range(3)])
        assert "error" in invalid and not research.calls
        # Rewriting support replays its receipt without consuming counter/qualify.
        for q in ("first", "rewritten", "again"):
            rows = await invoke(search, queries=[{"query": q, "purpose": "support"}])
            assert rows[0]["request"]["query"] == "first"
        await use_tools(output_type, kw)
        assert len(research.calls) == 3
        raise RuntimeError("probe complete")
    with pytest.raises(RuntimeError, match="probe complete"):
        execute(research, model)


def test_large_neighbor_units_are_explicit_reads_not_mandatory_overflow(research):
    research.units = reading_units(chapter("# 合成章节\n\n" + "该地运输物资数量发生变化，但不包括仓库转运部分。\n\n" * 1200))
    research.topic.unit_ids = [research.units[2]["unit_id"]]
    async def model(*args, **kw):
        receipt = await invoke(kw["tools"][1], unit_ids=research.topic.unit_ids)
        assert "error" not in receipt
        assert receipt["units"][0]["text"] == research.units[2]["text"]
        assert research.units[1]["unit_id"] in receipt["related_unit_ids"]
        assert research.units[3]["unit_id"] in receipt["related_unit_ids"]
        raise RuntimeError("probe complete")
    with pytest.raises(RuntimeError, match="probe complete"):
        execute(research, model)


@pytest.mark.parametrize("outage", ["gpu", "opensearch"])
def test_retrieval_outage_restores_intents_before_any_new_model_call(research, monkeypatch, outage):
    from opensearchpy.exceptions import ConnectionError

    from hrs_platform.domain.errors import Problem
    now, attempts, model_calls = [1000.0], [], []
    monkeypatch.setattr(time, "time", lambda: now[0])
    original = research.search.search
    def flaky(query, *args, **kw):
        attempts.append(query)
        if query == "counter" and now[0] < 1360:
            if outage == "gpu":
                raise Problem("retrieval_unavailable", "offline", retryable=True)
            raise ConnectionError("N/A", "offline")
        return original(query, *args, **kw)
    research.search.search = flaky
    async def model(run, key, instructions, payload, output_type, **kw):
        model_calls.append(payload)
        receipt = await use_tools(output_type, kw)
        uid = receipt["units"][0]["unit_id"]
        value = output_type(source_unit_ids=[uid], findings=[dict(category="event", text="合成", source_unit_ids=[uid])],
                            unresolved_questions=[], sufficient=True)
        kw["validate"](value)
        return value
    for at in (1000, 1001, 1060, 1180):
        now[0] = at
        with pytest.raises(ApplicationError) as error:
            execute(research, model)
        assert error.value.type == "retrieval_wait"
        assert len(model_calls) == 1
    # The first successful query is retained across three failures and restarts.
    assert [q for q, _ in research.calls] == ["support"]
    now[0] = 1420
    assert execute(research, model)["assessment"]["sufficient"]
    assert model_calls[0] == model_calls[1]
    assert [q for q, _ in research.calls] == ["support", "counter", "qualify"]
    assert attempts.count("support") == 1


def test_terminal_model_result_is_reused_after_evidence_receipt_write_failure(research, monkeypatch):
    from test_model_lifecycle import Receipt
    from test_model_transport import success, transport_model
    model, sends = transport_model(monkeypatch, lambda request: success())
    original, fail = research.outputs.put, [True]
    def write(run, key, value, dependency):
        if "assessment" in value and fail[0]:
            fail[0] = False
            raise OSError("synthetic final write failure")
        return original(run, key, value, dependency)
    research.outputs.put = write
    async def run(run_id, key, instructions, payload, output_type, **kw):
        receipt = await use_tools(output_type, kw)
        await model.run(run_id, key, "test", payload, Receipt)
        uid = receipt["units"][0]["unit_id"]
        value = output_type(source_unit_ids=[uid], findings=[dict(category="event", text="合成", source_unit_ids=[uid])],
                            unresolved_questions=[], sufficient=True)
        kw["validate"](value)
        return value
    with pytest.raises(OSError, match="final write"):
        execute(research, run)
    assert execute(research, run)["assessment"]["sufficient"]
    assert len(sends) == 1 and len(research.calls) == 3


def test_persistent_retrieval_failures_exhaust_and_explicit_retry_gets_new_epoch(research, monkeypatch):
    from hrs_platform.domain.errors import Problem
    now, calls = [1000.0], []
    monkeypatch.setattr(time, "time", lambda: now[0])
    async def unavailable():
        calls.append(True)
        raise Problem("retrieval_unavailable", "offline", retryable=True)
    for attempt in range(1, 13):
        with pytest.raises(ApplicationError) as error:
            asyncio.run(recover_retrieval(research.evidence.engine, research.outputs, "run", "probe", unavailable))
        assert error.value.type == ("retrieval_exhausted" if attempt == 12 else "retrieval_wait")
        now[0] = error.value.details[0]["retry_at"]
    with pytest.raises(ApplicationError) as error:
        asyncio.run(recover_retrieval(research.evidence.engine, research.outputs, "run", "probe", unavailable))
    assert error.value.type == "retrieval_exhausted" and len(calls) == 12
    research.parent["recovery_attempt"] = 1
    async def restored():
        return "ready"
    assert asyncio.run(recover_retrieval(research.evidence.engine, research.outputs, "run", "probe", restored)) == "ready"
