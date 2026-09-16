"""Synthetic failure injection through real orchestration, SQL and S3; no model calls."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import insert
from temporalio.exceptions import ApplicationError
from test_card_pipeline import StubEvidence, chapter, record, verdict

from hrs_platform import cards as module
from hrs_platform import schema as db
from hrs_platform.books import get_run
from hrs_platform.cards import CardPlan, Cards, ResearchPlan
from hrs_platform.domain.generation_contracts import CardDraft, CardRepair, ReadingRecord
from hrs_platform.outputs import fingerprint
from hrs_platform.reading import reading_units
from hrs_platform.recovery import retry_run
from hrs_platform.visual_review import result_key


@pytest.fixture
def harness(platform, monkeypatch, request):
    monkeypatch.setattr(module, "CardEvidence", StubEvidence)
    settings, engine = platform
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="Synthetic recovery", state="ready"))
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                kind="cards",
                state="processing",
                stage="planning",
                conversion={"synthetic": True},
                source={"sha256": "synthetic"},
            )
        )
    cards = Cards(settings.model_copy(update={"model_max_calls": 4096}), engine)
    originals = [chapter(f"合成原文{i}，运送{i}吨。") for i in range(getattr(request, "param", 3))]
    units = [unit for original in originals for unit in reading_units(original)]
    cards.library = SimpleNamespace(
        chapters=lambda _: originals,
        chapter=lambda identity: next(row for row in originals if row["id"] == identity),
    )
    bundle = {"manifest": {"pages": [], "page_count": 1}, "files": {}}
    monkeypatch.setattr(module, "Review", lambda *_: SimpleNamespace(read_json=lambda _: bundle))
    read_json = cards.review.read_json
    monkeypatch.setattr(
        cards.review, "read_json", lambda ref: bundle if ref == {"synthetic": True} else read_json(ref)
    )
    scenario, calls = {}, []

    async def respond(run_id, key, instructions, payload, output_type, **kwargs):
        cache = f"synthetic:{key}:{fingerprint(payload)}"
        saved = cards.outputs.get(run_id, cache)
        if saved:
            return output_type.model_validate(saved)
        calls.append((key, deepcopy(payload)))
        if output_type is ResearchPlan:
            value = output_type(
                rationale="test",
                assignments=[
                    dict(name="read", objective="read", chapter_ids=[row["id"] for row in originals])
                ],
            )
        elif output_type is CardPlan:
            groups = [units] if scenario.get("combined") else [[unit] for unit in units]
            value = output_type(
                rationale="test",
                topics=[
                    dict(name=f"topic-{i}", objective=f"topic-{i}", unit_ids=[u["unit_id"] for u in group])
                    for i, group in enumerate(groups)
                ],
            )
        elif output_type is ReadingRecord:
            rows = [record(unit) for unit in payload["source_units"]]
            if scenario.get("omission"):
                for row in rows:
                    if row["unit_id"] != units[0]["unit_id"]:
                        row["candidate_quotes"] = []
            value = output_type(
                readings=rows, themes=[], structure=[], questions=[], boundary_observations=[]
            )
        elif output_type in {CardDraft, CardRepair}:
            if scenario.get("fail_topic") == payload.get("objective"):
                raise ApplicationError(
                    "synthetic exhausted output", type="model_output_invalid", non_retryable=True
                )
            value = CardDraft(
                title=payload["objective"],
                document_type="synthetic",
                source_layer="synthetic",
                formation_date={},
                event_date={},
                tags=[],
                entities=[],
                evidence_relations=[],
                no_argument_reason="test",
                items=[
                    dict(
                        item_id=f"e{i}",
                        kind="evidence",
                        title="evidence",
                        text=unit["text"],
                        source_unit_ids=[unit["unit_id"]],
                        selections=[dict(unit_id=unit["unit_id"], quote=unit["text"])],
                    )
                    for i, unit in enumerate(payload["source_units"])
                ],
            )
            if output_type is CardRepair:
                value = CardRepair(items=value.items, remove_item_ids=[],
                                   metadata=value.model_dump(exclude={"items"}))
        else:
            if (
                scenario.get("final_failure")
                and key.startswith("原图后定稿")
                and payload["candidate"]["title"] == "topic-1"
            ):
                raise ApplicationError(
                    "synthetic final invalid", type="model_output_invalid", non_retryable=True
                )
            ids = payload.get("required_object_ids") or [
                item["item_id"] for item in payload["candidate"]["items"]
            ]
            receipt = verdict(ids)
            if scenario.get("omission") and ":source-coverage:" in key:
                missing = units[-1]["unit_id"]
                if not any(missing in item["source_unit_ids"] for item in payload["candidate"]["items"]):
                    receipt = verdict(ids, failed=True)
                    receipt["findings"][0].update(object_id=missing, source_unit_ids=[missing])
            value = output_type.model_validate(receipt)
        kwargs["validate"](value)
        cards.outputs.put(run_id, cache, value.model_dump(mode="json"), payload)
        return value

    def vision(run_row, bundle, card, group):
        if scenario.get("visual_failure") and card["candidate"]["title"] == "topic-1":
            raise ApplicationError(
                "synthetic visual invalid", type="visual_output_invalid", non_retryable=True
            )
        return cards.outputs.put(
            run,
            result_key(card["id"], group["key"], card.get("visual_revision")),
            {"items": [dict(item_id=item["item_id"], result="verified") for item in group["items"]]},
            {},
        )

    monkeypatch.setattr("hrs_platform.visual_review.VisualReview.check", lambda self, *args: vision(*args))
    monkeypatch.setattr(
        "hrs_platform.visual_review.VisualReview.prepare",
        lambda self, run, bundle, numbers: {"pages": [], "issues": []},
    )
    monkeypatch.setattr("agents.Runner.run", lambda *a, **k: pytest.fail("Real model API called"))
    cards.models = SimpleNamespace(run=respond, outputs=cards.outputs)
    return cards, run, units, scenario, calls


def finish(cards, run):
    asyncio.run(cards.generate(run))
    cards.check_images(run)
    asyncio.run(cards.finalize(run))
    return cards.adopt(run)


def test_failed_topic_is_isolated_and_explicit_retry_keeps_adopted_cards(harness):
    cards, run, _, scenario, calls = harness
    scenario["fail_topic"] = "topic-1"
    result = finish(cards, run)
    assert result["state"] == "needs_revision" and result["adopted"] == 2
    before = {str(row["id"]): row["content"] for row in cards.list()}
    run_row = get_run(cards.engine, run)
    assert run_row["result"]["pending_topics"][0]["topic_index"] == 1
    request_id = str(uuid4())
    retry_run(cards.engine, run, request_id)
    retry_run(cards.engine, run, request_id)
    assert get_run(cards.engine, run)["result"]["card_revision"] == 1
    scenario.clear()
    calls.clear()
    result = finish(cards, run)
    assert result["state"] == "completed" and result["adopted"] == 3
    assert [payload["objective"] for key, payload in calls if ":制卡:" in key] == ["topic-1"]
    assert all(row["content"] == before[str(row["id"])] for row in cards.list() if str(row["id"]) in before)


def test_coverage_feedback_supplies_missing_original_before_next_revision(harness):
    cards, run, units, scenario, calls = harness
    scenario.update(combined=True, omission=True)
    research = cards.evidence.research
    passes = []

    async def partial(*args, **kwargs):
        receipt = await research(*args, **kwargs)
        passes.append(kwargs.get("previous"))
        if kwargs.get("previous") is None:
            receipt["units"] = receipt["units"][:1]
            receipt["assessment"]["source_unit_ids"] = [receipt["units"][0]["unit_id"]]
        return receipt

    cards.evidence.research = partial
    asyncio.run(cards.generate(run))
    drafts = [payload for key, payload in calls if ":制卡:" in key]
    assert len(drafts) == 2
    missing = units[-1]["unit_id"]
    assert missing not in {unit["unit_id"] for unit in drafts[0]["source_units"]}
    assert missing in {unit["unit_id"] for unit in drafts[1]["source_units"]}
    generated = cards.outputs.get(run, "generated-cards")
    assert generated["cards"][0]["text_check"]["conclusion"] == "pass"
    assert len(passes) == 2 and passes[1]["source_checks"]


def test_budget_preflight_and_local_vision_have_separate_allowances(harness):
    cards, run, _, _, calls = harness
    with pytest.raises(ApplicationError, match="预算不足"):
        cards.outputs.preflight(run, 1348, 512)
    assert not calls
    cards.outputs.reserve_request(run, "视觉核对:card:http-request:0:0", {}, 1)
    cards.outputs.reserve_request(run, "read:http-request:1:1", {}, 1)
    with pytest.raises(ApplicationError):
        cards.outputs.reserve_request(run, "read:recovery:1:http-request:1:1", {}, 1)
    assert cards.outputs.request_count(run) == cards.outputs.request_count(run, vision=True) == 1


def test_empty_partial_run_is_not_marked_completed(harness):
    cards, run, _, _, _ = harness
    cards.outputs.put(run, "finalized-cards", {"cards": [], "pending_topics": [{"topic_index": 0}]}, {})
    assert cards.adopt(run)["state"] == "needs_revision"


@pytest.mark.parametrize("stage", ["visual_failure", "final_failure"])
def test_machine_review_failure_can_update_existing_card_without_rerunning_passed_cards(harness, stage):
    cards, run, _, scenario, calls = harness
    scenario[stage] = True
    assert finish(cards, run)["adopted"] == 2
    failed = next(row for row in cards.list() if row["state"] == "needs_revision")
    prior = cards.get(failed["id"])
    retry_run(cards.engine, run, str(uuid4()))
    scenario.clear()
    calls.clear()
    assert finish(cards, run)["adopted"] == 3
    repaired = cards.get(failed["id"])
    assert repaired["state"] == "adopted"
    assert repaired["checks"] != prior["checks"]
    assert not any(key.startswith("主研究") or ":reading:" in key for key, _ in calls)
    assert [payload["objective"] for key, payload in calls if ":制卡:" in key] == ["topic-1"]


def test_worker_failure_keeps_completed_topic_checkpoint(harness):
    cards, run, _, _, calls = harness
    original = cards.models.run
    failed = False

    async def interrupted(run_id, key, instructions, payload, output_type, **kwargs):
        nonlocal failed
        if output_type is CardDraft and payload.get("objective") == "topic-1" and not failed:
            failed = True
            raise OSError("synthetic storage failure")
        return await original(run_id, key, instructions, payload, output_type, **kwargs)

    cards.models.run = interrupted
    with pytest.raises(OSError):
        asyncio.run(cards.generate(run))
    assert cards.outputs.get(run, "卡片主题:v2:0:result:0")
    assert cards.outputs.get(run, "generated-cards") is None
    calls.clear()
    assert finish(cards, run)["adopted"] == 3
    assert [payload["objective"] for key, payload in calls if ":制卡:" in key] == ["topic-1", "topic-2"]


def test_incremental_delivery_survives_later_topic_failure_and_auto_repairs_once(harness):
    cards, run, _, scenario, calls = harness
    first = asyncio.run(cards.generate(run, incremental=True))
    assert first["more"] and first["candidates"] == 1
    cards.check_images(run, first["batch_key"])
    asyncio.run(cards.finalize(run, first["batch_key"]))
    result = cards.adopt(run, first["batch_key"])
    assert result["state"] == "processing" and result["adopted"] == 1
    adopted = cards.list()[0]
    scenario["fail_topic"] = "topic-1"
    while True:
        batch = asyncio.run(cards.generate(run, incremental=True))
        cards.check_images(run, batch["batch_key"])
        asyncio.run(cards.finalize(run, batch["batch_key"]))
        result = cards.adopt(run, batch["batch_key"])
        if not batch["more"]:
            break
    assert result["state"] == "needs_revision" and result["adopted"] == 2
    assert cards.schedule_repair(run, 0) == {"retry": True}
    assert cards.schedule_repair(run, 0) == {"retry": True}
    assert get_run(cards.engine, run)["result"]["card_revision"] == 1
    # Same deterministic failure cannot launch infinite automatic revisions.
    result = finish(cards, run)
    assert result["adopted"] == 2
    assert cards.schedule_repair(run, 1) == {"retry": False}
    assert next(row for row in cards.list() if row["id"] == adopted["id"])["content"] == adopted["content"]


@pytest.mark.parametrize("harness", [80], indirect=True)
def test_reading_yields_and_resumes_committed_batches_without_duplicate_models(harness, monkeypatch):
    cards, run, units, _, calls = harness
    async def plan(*args):
        return CardPlan(rationale="synthetic", topics=[dict(name=f"topic-{i}", objective=f"topic-{i}", unit_ids=[u["unit_id"]]) for i, u in enumerate(units)])
    monkeypatch.setattr(cards, "_plan_topics", plan)
    first = asyncio.run(cards.generate(run, incremental=True))
    assert first == {"run_id": run, "more": True, "stage": "reading"}
    reading_calls = [(key, payload) for key, payload in calls if ":reading:" in key]
    assert len(reading_calls) == 8
    calls.clear()
    second = asyncio.run(cards.generate(run, incremental=True))
    assert second["batch_key"] and second["more"]
    assert len([key for key, _ in calls if ":reading:" in key]) == 2


def test_generation_refuses_insufficient_budget_before_any_model(harness):
    cards, run, _, _, calls = harness
    cards.settings.model_max_calls = 1
    with pytest.raises(ApplicationError, match="预算不足"):
        asyncio.run(cards.generate(run))
    assert calls == []


def test_tool_closeout_failure_uses_durable_catalogue_reading_plan(harness):
    cards, run, _, _, calls = harness
    original = cards.models.run

    async def fail_plan(*args, **kwargs):
        if args[4] is ResearchPlan:
            raise ApplicationError("plan invalid", type="model_output_invalid", non_retryable=True)
        return await original(*args, **kwargs)

    cards.models.run = fail_plan
    assert finish(cards, run)["state"] == "completed"
    plan = cards.outputs.get(run, "card-reading-plan")
    assert len({identity for row in plan["assignments"] for identity in row["chapter_ids"]}) == 3
    assert "目录" in plan["rationale"]


@pytest.mark.parametrize("stage", ["draft", "evidence"])
def test_budget_limit_splits_only_failed_topic_and_reuses_children_on_restart(harness, stage):
    cards, run, units, scenario, calls = harness
    scenario["combined"] = True
    original = cards.models.run
    failed_sizes = []

    async def bounded(*args, **kwargs):
        if args[4] is CardDraft and len(args[3]["source_units"]) > 1:
            failed_sizes.append(len(args[3]["source_units"]))
            raise ApplicationError("output too long", type="model_output_limit", non_retryable=True)
        return await original(*args, **kwargs)

    if stage == "draft":
        cards.models.run = bounded
    else:
        research = cards.evidence.research
        async def bounded_evidence(*args, **kwargs):
            size = len(args[4].unit_ids)
            if size > 1:
                failed_sizes.append(size)
                raise ApplicationError("evidence too large", type="card_input_budget", non_retryable=True)
            return await research(*args, **kwargs)
        cards.evidence.research = bounded_evidence
    result = finish(cards, run)
    assert result["state"] == "completed" and result["adopted"] == 3
    generated = cards.outputs.get(run, "generated-cards")
    assigned = [
        uid
        for card in generated["cards"]
        for uid in cards.outputs.get(run, card["source_step"])["assigned_unit_ids"]
    ]
    assert sorted(assigned) == sorted(unit["unit_id"] for unit in units)
    assert len(set(card["id"] for card in generated["cards"])) == 3
    before = len(calls)
    assert finish(cards, run)["adopted"] == 3
    assert len(calls) == before and failed_sizes == [3, 2]


def test_atomic_overflow_stays_pending_without_recursive_retries(harness):
    cards, run, _, scenario, calls = harness
    original = cards.models.run

    async def overflow(*args, **kwargs):
        if args[4] is CardDraft and args[3]["objective"] == "topic-1":
            raise ApplicationError("atomic overflow", type="model_output_limit", non_retryable=True)
        return await original(*args, **kwargs)

    cards.models.run = overflow
    result = finish(cards, run)
    assert result["state"] == "needs_revision" and result["adopted"] == 2
    pending = get_run(cards.engine, run)["result"]["pending_topics"]
    assert len(pending) == 1 and pending[0]["topic_path"] == ""


def test_topic_preflight_splits_before_draft_calls_and_total_is_bounded(harness, monkeypatch):
    cards, run, units, scenario, calls = harness
    scenario["combined"] = True
    monkeypatch.setattr(module, "TOPIC_PREFLIGHT_TOKENS", 1)
    monkeypatch.setattr(module, "MAX_CARD_TOPICS", 2)
    result = finish(cards, run)
    assert result["state"] == "needs_revision" and result["cards"] == 0
    pending = get_run(cards.engine, run)["result"]["pending_topics"]
    assert len(pending) == 2
    assert sorted(uid for row in pending for uid in row["unit_ids"]) == sorted(
        unit["unit_id"] for unit in units
    )
    assert not any(":制卡:" in key for key, _ in calls)


def test_split_recovery_retires_failed_parent_even_after_worker_interruption(harness, monkeypatch):
    cards, run, _, scenario, _ = harness
    scenario["combined"] = True
    original = cards.models.run

    async def limited_final(*args, **kwargs):
        if args[1].startswith("原图后定稿"):
            raise ApplicationError("final output too long", type="model_output_limit", non_retryable=True)
        return await original(*args, **kwargs)

    cards.models.run = limited_final
    assert finish(cards, run)["state"] == "needs_revision"
    parent = str(cards.list()[0]["id"])
    retry_run(cards.engine, run, str(uuid4()))
    cards.models.run = original
    retire = cards._supersede

    def interrupted(*args):
        raise OSError("worker interrupted after split checkpoint")

    monkeypatch.setattr(cards, "_supersede", interrupted)
    with pytest.raises(OSError):
        asyncio.run(cards.generate(run))
    monkeypatch.setattr(cards, "_supersede", retire)
    assert finish(cards, run)["state"] == "completed"
    assert cards.get(parent)["state"] == "superseded"
    assert parent not in {str(row["id"]) for row in cards.list()}


@pytest.mark.parametrize("harness", [6], indirect=True)
def test_replayed_split_tree_reserves_all_leaf_slots_before_new_splits(harness, monkeypatch):
    cards, run, units, scenario, calls = harness
    scenario["combined"] = True
    monkeypatch.setattr(module, "MAX_CARD_TOPICS", 3)
    root = module.CardTopic(name="topic-0", objective="topic-0", unit_ids=[unit["unit_id"] for unit in units])
    children = module.split_topic(root, units)
    grandchildren = module.split_topic(children[1], units)
    for key, rows in [
        ("卡片主题:v2:0:division", children),
        ("卡片主题:v2:0:split:1:division", grandchildren),
    ]:
        cards.outputs.put(run, key, {"topics": [row.model_dump() for row in rows]}, {})
    original = cards.models.run

    async def limited(*args, **kwargs):
        if args[4] is CardDraft and len(args[3]["source_units"]) > 1:
            raise ApplicationError("limit", type="model_output_limit", non_retryable=True)
        return await original(*args, **kwargs)

    cards.models.run = limited
    result = finish(cards, run)
    assert result["adopted"] == 1 and result["state"] == "needs_revision"
    pending = get_run(cards.engine, run)["result"]["pending_topics"]
    assert len(pending) == 2
    assert cards.outputs.get(run, "卡片主题:v2:0:split:0:division") is None
