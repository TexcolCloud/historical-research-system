"""Offline replay and cancellation checks; never send a provider request."""

import asyncio
from types import SimpleNamespace

import pytest
from agents import AgentOutputSchema
from pydantic import BaseModel, SecretStr
from temporalio.exceptions import ApplicationError
from test_card_pipeline import MemoryOutputs

from hrs_platform import agents as module
from hrs_platform.outputs import Outputs, execution_parent


class Receipt(BaseModel):
    acknowledgement: str


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
        deepseek_api_key=SecretStr("synthetic"),
        deepseek_base_url="http://unused.invalid",
        model_timeout_seconds=10,
    )
    monkeypatch.setattr(module, "get_run", lambda *_: {"recovery_attempt": 0})
    monkeypatch.setattr(module.Runner, "run", lambda *a, **k: pytest.fail("Provider called"))
    dependency = {
        "model": "mock",
        "max_output_tokens": 100,
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
    with pytest.raises(expected):
        asyncio.run(model.run("run", "step", "test", dependency["input"], Receipt))
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
