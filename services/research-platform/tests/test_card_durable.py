"""Offline long-running card control: durable yields, partial delivery and recovery."""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.error import URLError

import pytest
from temporalio.exceptions import ActivityError, ApplicationError
from test_card_pipeline import MemoryOutputs

from hrs_platform import workflows as workflows
from hrs_platform import visual_review as visual


class Continued(BaseException):
    def __init__(self, request):
        self.request = request


def continue_run(request):
    raise Continued(request)


@pytest.mark.parametrize("kind", ["model_transport_wait", "retrieval_wait", "vision_service_wait"])
def test_durable_outage_continues_same_phase_after_timer(monkeypatch, kind):
    now, calls, sleeps = [1000.0], [], []
    request = {"run_id": "run", "phase": "adopt", "batch": {"batch_key": "saved"}}
    monkeypatch.setattr(workflows.workflow, "now", lambda: datetime.fromtimestamp(now[0], UTC))
    monkeypatch.setattr(workflows.workflow, "continue_as_new", continue_run)
    async def execute(*args, **kwargs):
        calls.append(args)
        raise ActivityError("offline", scheduled_event_id=1, started_event_id=2, identity="test",
                            activity_type="adopt_cards", activity_id="one", retry_state=None) from ApplicationError(
            "wait", {"retry_at": now[0] + 1800}, type=kind, non_retryable=True)
    async def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    monkeypatch.setattr(workflows.workflow, "sleep", sleep)
    with pytest.raises(Continued) as result:
        asyncio.run(workflows.execute_with_service_recovery("adopt_cards", "run", resume=request))
    assert result.value.request == request
    assert len(sleeps) == len(calls) == 3


@pytest.mark.parametrize("phase,more", [("generate", True), ("vision", True), ("adopt", False)])
def test_incremental_workflow_adopts_committed_batch_before_continuing(monkeypatch, phase, more):
    calls = []
    batch = {"run_id": "run", "batch_key": "saved", "more": more}
    monkeypatch.setattr(workflows.workflow, "patched", lambda _: True)
    monkeypatch.setattr(workflows.workflow, "continue_as_new", continue_run)
    monkeypatch.setattr(workflows.workflow, "info", lambda: SimpleNamespace(task_queue="cpu"))
    async def execute(name, value, **kwargs):
        calls.append((name, value))
        if name == "generate_cards":
            return batch
        if name == "adopt_cards":
            return {"run_id": "run", "state": "processing" if more else "completed", "revision": 0}
        return {}
    monkeypatch.setattr(workflows, "execute_with_service_recovery", execute)
    task = workflows.CardWorkflow().run({"run_id": "run", "gpu_queue": "gpu", "phase": phase, "batch": batch})
    if more:
        with pytest.raises(Continued):
            asyncio.run(task)
    else:
        assert asyncio.run(task)["state"] == "completed"
    names = [name for name, _ in calls]
    assert names[-1] == "adopt_cards"
    assert ("generate_cards" in names) == (phase == "generate")
    assert ("check_card_images" in names) == (phase != "adopt")
    assert calls[-1][1] == batch


def test_local_vision_outage_uses_service_epochs_and_saved_response(monkeypatch):
    reviewer = object.__new__(visual.VisualReview)
    reviewer.outputs = MemoryOutputs()
    reviewer.settings = SimpleNamespace(vision_max_calls=20)
    sends, now = [], [1000.0]
    monkeypatch.setattr(visual.time, "time", lambda: now[0])
    reviewer.outputs.reserve_request = lambda r, k, v, m: reviewer.outputs.put(r, k, v, v)
    def chat(*args, **kwargs):
        sends.append(True)
        if len(sends) < 5:
            raise URLError("offline")
        return {"choices": ["saved"]}
    monkeypatch.setattr(visual, "chat", chat)
    for _ in range(4):
        with pytest.raises(ApplicationError) as failure:
            reviewer._response("run", "http-request:0", "response:0", {}, [])
        assert failure.value.type == "vision_service_wait"
        count = len(sends)
        with pytest.raises(ApplicationError):
            reviewer._response("run", "http-request:0", "response:0", {}, [])
        assert len(sends) == count
        now[0] = failure.value.details[0]["retry_at"]
    expected = reviewer._response("run", "http-request:0", "response:0", {}, [])
    assert reviewer._response("run", "http-request:0", "response:0", {}, []) == expected
    assert len(sends) == 5


def test_business_stall_is_not_hidden_by_live_worker_heartbeats(monkeypatch):
    from hrs_platform import pipeline_activities as module
    pipeline = module.PipelineActivities(SimpleNamespace(model_timeout_seconds=10), None)
    values = iter([0, 1900])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(values, 1900)))
    monkeypatch.setattr(module.activity, "info", lambda: SimpleNamespace(activity_type="generate_cards"))
    beats = []
    monkeypatch.setattr(module.activity, "heartbeat", lambda value: beats.append(value))
    async def observe(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(pipeline, "_observe", observe)
    async def operation():
        return {}
    op = operation()
    try:
        with pytest.raises(ApplicationError) as failure:
            asyncio.run(pipeline.observe("run", op))
        assert failure.value.type == "card_progress_stalled"
        assert beats[0]["idle_seconds"] == 1900
    finally:
        op.close()
