"""Offline replay and cancellation checks; never send a provider request."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from agents import AgentOutputSchema
from httpx import MockTransport
from pydantic import BaseModel, SecretStr
from temporalio.exceptions import ApplicationError
from test_card_pipeline import MemoryOutputs, record, verdict

from hrs_platform import agents as module
from hrs_platform.outputs import Outputs, execution_parent
from hrs_platform.reading import read_batch
from hrs_platform.settings import Settings


class Receipt(BaseModel):
    acknowledgement: str


def test_invalid_committed_output_is_repaired_once_without_overwriting_evidence(monkeypatch):
    model, dependency = setup(monkeypatch)
    model.outputs.put("run", "step", {"output": {"acknowledgement": "invalid"}}, dependency)
    calls = []

    def validate(value):
        if value.acknowledgement != "ready":
            raise ValueError("acknowledgement must be ready")

    async def respond(*args, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(final_output={"acknowledgement": "ready"})

    monkeypatch.setattr(module.Runner, "run", respond)
    for _ in range(2):
        value = asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt, validate=validate))
        assert value.acknowledgement == "ready"
    assert len(calls) == 1
    assert "acknowledgement must be ready" in str(calls[0])
    assert model.outputs.get("run", "step")["output"]["acknowledgement"] == "invalid"


def test_tool_enabled_terminal_receipt_is_replayed_without_provider_call(monkeypatch):
    model, dependency = setup(monkeypatch)
    tool = SimpleNamespace(name="lookup", params_json_schema={})
    dependency["tools"] = [{"name": "lookup", "schema": {}}]
    model.outputs.put("run", "step:request:1", dependency, dependency)
    model.outputs.put(
        "run",
        "step:response:1:1",
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"acknowledgement":"ready"}'}],
                }
            ],
        },
        dependency,
    )
    value = asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt, tools=[tool]))
    assert value.acknowledgement == "ready"


class TrackedOutputs(MemoryOutputs):
    operation = Outputs.operation
    engine = None

    def node(self, run, key, **properties):
        self.events.append((key, properties.get("state", "running")))
        return key


def setup(monkeypatch):
    model = object.__new__(module.Models)
    model.outputs = TrackedOutputs()
    model.settings = SimpleNamespace(
        reading_model="mock",
        reasoning_model="mock",
        reasoning_max_output=100,
        reading_max_output=100,
        model_max_output_ceiling=400,
        deepseek_api_key=SecretStr("synthetic"),
        deepseek_base_url="http://unused.invalid",
        model_timeout_seconds=10,
    )
    monkeypatch.setattr(module, "get_run", lambda *_: {"recovery_attempt": 0})
    monkeypatch.setattr(module.Runner, "run", lambda *a, **k: pytest.fail("Provider called"))
    dependency = {
        "model": "mock",
        "max_output_tokens": 100,
        "reasoning_effort": "low",
        "instructions": "test",
        "input": {"source": "fixed"},
        "schema": AgentOutputSchema(Receipt).json_schema(),
        "tools": [],
    }
    return model, dependency


@pytest.mark.parametrize("failure", ["exhausted", "request-save", "client-init", "cancelled"])
def test_every_exit_after_start_closes_agent_and_restores_parent(monkeypatch, failure):
    model, dependency = setup(monkeypatch)
    if failure == "exhausted":
        for n in range(1, 4):
            model.outputs.put("run", f"step:request:{n}", dependency, dependency)
        expected = ApplicationError
    elif failure == "request-save":

        def fail(*args):
            raise OSError("storage unavailable")

        model.outputs.put = fail
        expected = OSError
    elif failure == "client-init":

        def fail(*args, **kwargs):
            raise RuntimeError("client unavailable")

        monkeypatch.setattr(module, "AsyncOpenAI", fail)
        expected = RuntimeError
    else:

        async def cancel(*args, **kwargs):
            raise asyncio.CancelledError()

        monkeypatch.setattr(module.Runner, "run", cancel)
        expected = asyncio.CancelledError
    with pytest.raises(expected) as error:
        asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt))
    if failure == "exhausted":
        assert error.value.type == "model_request_exhausted"
    assert model.outputs.events[-1] == ("step", "failed")
    assert execution_parent.get() is None


@pytest.mark.parametrize("committed", [False, True])
def test_changed_input_never_reuses_old_receipt_and_preserves_it(monkeypatch, committed):
    model, dependency = setup(monkeypatch)
    model.outputs.put("run", "step:request:1", dependency, dependency)
    model.outputs.put(
        "run",
        "step:response:1:1",
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"acknowledgement":"old source"}'}],
                }
            ],
        },
        dependency,
    )
    if committed:
        model.outputs.put("run", "step", {"output": {"acknowledgement": "old source"}}, dependency)
    calls = []

    async def respond(*args, **kwargs):
        calls.append(kwargs["input"])
        return SimpleNamespace(final_output={"acknowledgement": "new source"})

    monkeypatch.setattr(module.Runner, "run", respond)

    async def exercise():
        value = await model.run("run", "step", "test", {"source": "different"}, Receipt)
        assert value.acknowledgement == "new source"
        assert await model.run("run", "step", "test", {"source": "different"}, Receipt) == value

    asyncio.run(exercise())
    assert len(calls) == 1 and "different" in calls[0]
    assert model.outputs.get("run", "step:request:1") == dependency
    assert model.outputs.get("run", "step") == (
        {"output": {"acknowledgement": "old source"}} if committed else None
    )
    assert model.outputs.events[-1] == ("step", "completed")


def test_shared_model_keeps_reading_and_reasoning_budgets_separate(monkeypatch):
    model, _ = setup(monkeypatch)
    model.settings.reading_max_output = 50
    seen = []

    async def respond(agent, **kwargs):
        seen.append((agent.model_settings.reasoning.effort, agent.model_settings.max_tokens))
        return SimpleNamespace(final_output={"acknowledgement": "ready"})

    monkeypatch.setattr(module.Runner, "run", respond)

    async def exercise():
        await model.run("run", "read", "test", {}, Receipt)
        await model.run("run", "reason", "test", {}, Receipt, model="mock")

    asyncio.run(exercise())
    assert seen == [("low", 50), ("high", 100)]


def test_sdk_request_hook_preserves_nonretryable_global_budget(monkeypatch):
    runner = module.Runner.run
    client = module.DefaultAsyncHttpxClient
    model, dependency = setup(monkeypatch)
    model.settings.model_max_calls = 0

    def exhausted(*args):
        raise ApplicationError("Budget exhausted", type="model_request_budget", non_retryable=True)

    model.outputs.reserve_request = exhausted
    monkeypatch.setattr(module.Runner, "run", runner)
    monkeypatch.setattr(
        module,
        "DefaultAsyncHttpxClient",
        lambda **kwargs: client(
            **kwargs, transport=MockTransport(lambda _: pytest.fail("Request escaped budget guard"))
        ),
    )
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt))
    assert failure.value.type == "model_request_budget" and failure.value.non_retryable
    assert model.outputs.get("run", "step:request:2") is None


def test_truncated_receipts_raise_budget_without_replaying_partial_json(monkeypatch):
    model, dependency = setup(monkeypatch)
    seen = []

    async def respond(agent, **kwargs):
        n = len(seen) + 1
        seen.append((agent.model_settings.max_tokens, kwargs["input"]))
        if n < 3:
            model.outputs.put(
                "run",
                f"step:response:{n}:1",
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "max_output_tokens": agent.model_settings.max_tokens,
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"acknowledgement":"partial"}'}],
                        }
                    ],
                },
                dependency,
            )
            raise module.ModelBehaviorError("response.incomplete")
        return SimpleNamespace(final_output={"acknowledgement": "complete"})

    monkeypatch.setattr(module.Runner, "run", respond)
    value = asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt))
    assert value.acknowledgement == "complete"
    assert [budget for budget, _ in seen] == [100, 200, 400]
    assert "max_output_tokens" in seen[1][1]


def test_truncation_at_ceiling_stops_without_another_provider_call(monkeypatch):
    model, dependency = setup(monkeypatch)
    model.settings.model_max_output_ceiling = 100
    model.outputs.put("run", "step:request:1", dependency, dependency)
    model.outputs.put(
        "run",
        "step:response:1:1",
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "max_output_tokens": 100,
        },
        dependency,
    )
    with pytest.raises(ApplicationError, match="输出.*上限") as error:
        asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt))
    assert error.value.non_retryable


def test_validation_feedback_survives_restart_and_exhaustion_is_terminal(monkeypatch):
    model, dependency = setup(monkeypatch)
    # SDK/schema validation can fail without a completed provider receipt.
    seen = []

    async def respond(agent, **kwargs):
        seen.append(json.loads(kwargs["input"]))
        raise module.ModelBehaviorError("invalid quotation at unit-2 candidate_quotes[0]")

    monkeypatch.setattr(module.Runner, "run", respond)
    with pytest.raises(ApplicationError, match="三次") as error:
        asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt))
    assert error.value.non_retryable
    assert len(seen) == 3
    assert "unit-2" in seen[1]["validation_feedback"][0]["problem"]
    monkeypatch.setattr(module.Runner, "run", lambda *a, **k: pytest.fail("Repeated exhausted step"))
    with pytest.raises(ApplicationError):
        asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt))


def test_reading_retry_gets_exact_quote_location_and_still_requires_review(monkeypatch):
    model, _ = setup(monkeypatch)
    batch = [{"unit_id": "a", "text": "合成原文甲。"}, {"unit_id": "b", "text": "数字 7，\n没有改变。"}]
    reads, reviews = [], []

    async def respond(agent, **kwargs):
        incoming = json.loads(kwargs["input"])
        payload = incoming.get("task", incoming)
        if ":reading:" in agent.name:
            reads.append(incoming)
            rows = [record(unit) for unit in batch]
            if len(reads) == 1:
                rows[1]["candidate_quotes"] = ["数字7，没有改变。"]
            output = dict(readings=rows, themes=[], structure=[], questions=[], boundary_observations=[])
        else:
            reviews.extend(payload["required_object_ids"])
            output = verdict(payload["required_object_ids"])
        return SimpleNamespace(final_output=output)

    monkeypatch.setattr(module.Runner, "run", respond)
    value = asyncio.run(read_batch(model, "run", "read", batch, "synthetic", None))
    assert len(reads) == 2 and reviews == ["a", "b"]
    problem = reads[1]["validation_feedback"][0]["problem"]
    assert '"unit_id": "b"' in problem and '"quote_index": 0' in problem
    assert "数字7，没有改变。" in problem
    assert value["readings"][0] == record(batch[0])
    assert value["readings"][1]["candidate_quotes"] == [batch[1]["text"]]


def test_tool_retry_uses_last_response_and_persisted_budget(monkeypatch):
    model, dependency = setup(monkeypatch)
    tool = SimpleNamespace(name="lookup", params_json_schema={})
    dependency["tools"] = [{"name": "lookup", "schema": {}}]
    model.outputs.put("run", "step:request:1", dependency, dependency)
    model.outputs.put("run", "step:response:1:1", {"status": "completed"}, dependency)
    model.outputs.put(
        "run",
        "step:response:1:2",
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "max_output_tokens": 100,
        },
        dependency,
    )
    seen = []

    async def respond(agent, **kwargs):
        seen.append(agent.model_settings.max_tokens)
        return SimpleNamespace(final_output={"acknowledgement": "ready"})

    monkeypatch.setattr(module.Runner, "run", respond)
    asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt, tools=[tool]))
    assert seen == [200]


@pytest.mark.parametrize(
    "overrides",
    [
        {"reading_max_output": 0},
        {"reasoning_max_output": 384001},
        {"reading_max_output": 65000},
        {"model_max_output_ceiling": 384001},
    ],
)
def test_invalid_output_budgets_fail_before_runtime(overrides):
    base = dict(
        database_url="postgresql://unused",
        s3_endpoint="http://unused",
        s3_bucket="test",
        s3_access_key="synthetic",
        s3_secret_key="synthetic",
        project_root=".",
        cache_root=".",
    )
    with pytest.raises(ValueError):
        Settings(**base, **overrides)
    maximum = Settings(
        **base, reading_max_output=384000, reasoning_max_output=384000, model_max_output_ceiling=384000
    )
    assert maximum.reading_max_output == 384000


def test_tool_exhaustion_closes_without_tools_and_replays_saved_history(monkeypatch):
    model, dependency = setup(monkeypatch)
    from agents import MaxTurnsExceeded

    tool = SimpleNamespace(name="lookup", params_json_schema={})
    calls = []

    async def respond(agent, **kwargs):
        calls.append((len(agent.tools), kwargs["input"]))
        if agent.tools:
            error = MaxTurnsExceeded("turn limit")
            error.run_data = SimpleNamespace(
                new_items=[
                    SimpleNamespace(
                        to_input_item=lambda: {
                            "type": "function_call_output",
                            "call_id": "one",
                            "output": "retained opening",
                        }
                    )
                ]
            )
            raise error
        assert "retained opening" in kwargs["input"]
        return SimpleNamespace(final_output={"acknowledgement": "ready"})

    monkeypatch.setattr(module.Runner, "run", respond)
    for _ in range(2):
        assert (
            asyncio.run(
                model.run("run", "step", "test", dependency["input"], Receipt, tools=[tool])
            ).acknowledgement
            == "ready"
        )
    assert [count for count, _ in calls] == [1, 0]
