"""Real SDK / fake HTTP: outages must defer, retain evidence and bound paid sends."""

import asyncio

import httpx2 as httpx
import pytest
from agents import function_tool
from temporalio.exceptions import ApplicationError
from test_model_lifecycle import Receipt, setup

from hrs_platform import agents as module


def test_tool_history_budget_stops_before_sending_oversized_followup(monkeypatch):
    calls = []

    def provider(request):
        calls.append(request)
        value = success().json()
        value["output"] = [{"id": "tool", "type": "function_call", "call_id": "read-1",
                            "name": "read_evidence", "arguments": "{}", "status": "completed"}]
        return httpx.Response(200, json=value)

    @function_tool
    async def read_evidence() -> str:
        """Return synthetic source text."""
        return "仅为合成测试来源，不可截断。" * 10000

    model, sends = transport_model(monkeypatch, provider)
    monkeypatch.setattr("hrs_platform.reading.CARD_INPUT_TOKENS", 10000)
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "tool-budget", "test", {}, Receipt, tools=[read_evidence]))
    assert failure.value.type == "card_input_budget"
    assert len(calls) == len(sends) == 1
    assert not any("transport-error" in key for key in model.outputs.values)


def transport_model(monkeypatch, handler):
    runner, client = module.Runner.run, module.DefaultAsyncHttpxClient
    model, _ = setup(monkeypatch)
    model.settings.model_max_calls = 10
    reservations = []

    def reserve(run, key, body, maximum):
        if len(reservations) >= maximum:
            raise ApplicationError("budget", type="model_request_budget", non_retryable=True)
        reservations.append(key)
        model.outputs.put(run, key, body, body)

    model.outputs.reserve_request = reserve
    monkeypatch.setattr(module.Runner, "run", runner)
    monkeypatch.setattr(
        module, "DefaultAsyncHttpxClient", lambda **kw: client(**kw, transport=httpx.MockTransport(handler))
    )
    return model, reservations


def test_disconnect_defers_without_sdk_retry_and_records_safe_diagnostics(monkeypatch):
    def offline(request):
        raise httpx.ConnectError("private proxy password and URL must not escape", request=request)

    model, sends = transport_model(monkeypatch, offline)
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "step", "test", {"source": "fixed"}, Receipt))
    assert failure.value.type == "model_transport_wait"
    assert failure.value.non_retryable
    assert len(sends) == 1
    receipt = model.outputs.get("run", "step:transport-error:1")
    assert receipt["phase"] == "request"
    assert receipt["cause_types"] == ["APIConnectionError", "ConnectError"]
    assert receipt["retry_at"] > receipt["failed_at"]
    assert "private" not in str(receipt)
    assert ("step", "waiting") in model.outputs.events


def restart(model):
    resumed = object.__new__(module.Models)
    resumed.outputs, resumed.settings = model.outputs, model.settings
    return resumed


def success():
    return httpx.Response(
        200,
        json={
            "id": "synthetic-response",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "mock",
            "output": [
                {
                    "id": "message",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": '{"acknowledgement":"ready"}', "annotations": []}
                    ],
                }
            ],
        },
    )


@pytest.mark.parametrize("recover", [True, False])
def test_continuous_outage_keeps_cooldown_and_send_limit_across_restarts(monkeypatch, recover):
    now, calls = [1000.0], []
    monkeypatch.setattr(module.time, "time", lambda: now[0])

    def provider(request):
        calls.append(request)
        if recover and len(calls) == 3:
            return success()
        raise httpx.ReadTimeout("synthetic outage", request=request)

    model, sends = transport_model(monkeypatch, provider)
    for attempt in range(1, 4):
        model = restart(model)
        if recover and attempt == 3:
            assert asyncio.run(model.run("run", "step", "test", {}, Receipt)).acknowledgement == "ready"
            # Successful cached output remains usable even while another step is offline.
            model._transport_failure = ("run", {"attempt": 3})
            assert asyncio.run(model.run("run", "step", "test", {}, Receipt)).acknowledgement == "ready"
            break
        with pytest.raises(ApplicationError) as failure:
            asyncio.run(model.run("run", "step", "test", {}, Receipt))
        assert failure.value.type == ("model_transport_wait" if attempt < 3 else "model_transport_exhausted")
        assert len(sends) == len(calls) == attempt
        # A fresh worker arriving before the persisted deadline cannot send early.
        with pytest.raises(ApplicationError):
            asyncio.run(restart(model).run("run", "step", "test", {}, Receipt))
        assert len(calls) == attempt
        if attempt < 3:
            now[0] = failure.value.details[0]["retry_at"]
    assert len(calls) == 3


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503])
def test_retryable_http_status_honors_retry_after_without_sdk_retry(monkeypatch, status):
    model, sends = transport_model(
        monkeypatch,
        lambda _: httpx.Response(status, headers={"Retry-After": "120"}, json={"error": "offline"}),
    )
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "step", "test", {}, Receipt))
    assert failure.value.type == "model_transport_wait" and len(sends) == 1
    receipt = failure.value.details[0]
    assert receipt["status_code"] == status
    assert receipt["retry_at"] - receipt["failed_at"] == 120


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_configuration_and_auth_errors_never_retry_as_outage(monkeypatch, status):
    model, sends = transport_model(monkeypatch, lambda _: httpx.Response(status, json={"error": "invalid"}))
    for _ in range(2):
        with pytest.raises(ApplicationError) as failure:
            asyncio.run(restart(model).run("run", "step", "test", {}, Receipt))
        assert failure.value.type == "model_request_rejected" and failure.value.non_retryable
    assert len(sends) == 1


@pytest.mark.parametrize("phase", ["request_save", "response_save"])
def test_local_persistence_failure_is_not_misclassified_as_provider_outage(monkeypatch, phase):
    model, sends = transport_model(monkeypatch, lambda _: success())
    put = model.outputs.put

    def unavailable(*args):
        raise OSError("local object store unavailable")

    if phase == "request_save":
        model.outputs.reserve_request = unavailable
    else:
        model.outputs.put = lambda *args: unavailable() if ":response:" in args[1] else put(*args)
    with pytest.raises(OSError, match="local object store"):
        asyncio.run(model.run("run", "step", "test", {}, Receipt))
    assert not any(":transport-error:" in key for key in model.outputs.values)
    assert len(sends) == (1 if phase == "response_save" else 0)


def test_shared_circuit_stops_new_steps_without_cancelling_inflight_response(monkeypatch):
    async def exercise():
        second_started, offline = asyncio.Event(), asyncio.Event()

        async def provider(request):
            if b"slow" in request.content:
                second_started.set()
                await offline.wait()
                return success()
            await second_started.wait()
            raise httpx.ConnectError("offline", request=request)

        model, sends = transport_model(monkeypatch, provider)
        slow = asyncio.create_task(model.run("run", "slow", "test", {"case": "slow"}, Receipt))
        try:
            with pytest.raises(ApplicationError):
                await model.run("run", "broken", "test", {}, Receipt)
            offline.set()
            assert (await slow).acknowledgement == "ready"
            with pytest.raises(ApplicationError):
                await model.run("run", "queued", "test", {}, Receipt)
            assert len(sends) == 2 and "queued:request:1" not in model.outputs.values
        finally:
            offline.set()
            await slow

    asyncio.run(exercise())


def test_failure_is_compact_in_temporal_and_preserves_underlying_cause_types(monkeypatch):
    from temporalio.api.failure.v1 import Failure
    from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter

    def offline(request):
        raise httpx.ConnectError("private" * 50000, request=request)

    model, _ = transport_model(monkeypatch, offline)
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "step", "test", {}, Receipt))
    serialized = Failure()
    DefaultFailureConverter().to_failure(failure.value, DefaultPayloadConverter(), serialized)
    assert serialized.ByteSize() < 16000
    assert not serialized.HasField("cause")
    assert b"privateprivate" not in serialized.SerializeToString()


def test_timeout_during_tool_execution_is_not_provider_disconnect(monkeypatch):
    model, _ = transport_model(monkeypatch, lambda _: success())

    async def execute(agent, **kwargs):
        # Drive the response hook, then time out in a local tool rather than on HTTP.
        await agent.model._client.responses.create(model="mock", input="synthetic")
        raise TimeoutError()

    monkeypatch.setattr(module.Runner, "run", execute)
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "step", "test", {}, Receipt))
    assert failure.value.type == "model_execution_timeout"


def test_outer_request_deadline_enters_recovery_and_cancellation_stays_cancelled(monkeypatch):
    model, _ = transport_model(monkeypatch, lambda _: success())

    async def timeout(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(module.Runner, "run", timeout)
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "step", "test", {}, Receipt))
    assert failure.value.type == "model_transport_wait"

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(module.Runner, "run", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(restart(model).run("run", "other", "test", {}, Receipt))


def test_response_body_disconnect_is_distinguished_from_connection_failure(monkeypatch):
    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"status":'
            raise httpx.ReadError("synthetic broken stream")

    model, sends = transport_model(monkeypatch, lambda _: httpx.Response(200, stream=BrokenBody()))
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "step", "test", {}, Receipt))
    assert len(sends) == 1
    assert failure.value.details[0]["phase"] == "response_read"
    assert failure.value.details[0]["status_code"] == 200
    assert model.outputs.get("run", "step:response:1:1") is None


def test_budget_is_shared_with_outage_recovery_and_never_automatically_increased(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    model, sends = transport_model(monkeypatch, offline)
    model.settings.model_max_calls = 1
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(model.run("run", "step", "test", {}, Receipt))
    now[0] = failure.value.details[0]["retry_at"]
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(restart(model).run("run", "step", "test", {}, Receipt))
    assert failure.value.type == "model_request_budget" and len(sends) == 1


def test_circuit_opened_between_request_intent_and_send_reuses_unsent_slot(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    model, sends = transport_model(monkeypatch, lambda _: success())
    put = model.outputs.put
    receipt = {"step": "other", "attempt": 1, "retry_at": 1060.0}

    def open_circuit(run, key, value, dependency):
        result = put(run, key, value, dependency)
        if key == "step:request:1":
            model._transport_failure = (run, receipt)
        return result

    model.outputs.put = open_circuit
    with pytest.raises(ApplicationError):
        asyncio.run(model.run("run", "step", "test", {}, Receipt))
    assert sends == [] and model.outputs.get("run", "step:request-deferred:1") == receipt
    with pytest.raises(ApplicationError):
        asyncio.run(restart(model).run("run", "step", "test", {}, Receipt))
    assert sends == []
    now[0] = 1060.0
    assert asyncio.run(restart(model).run("run", "step", "test", {}, Receipt)).acknowledgement == "ready"
    assert sends == ["step:http-request:1:1"]
