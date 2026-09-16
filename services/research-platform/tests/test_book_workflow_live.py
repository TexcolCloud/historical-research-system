"""Real Temporal durable wait; source/model steps are explicit synthetic test activities."""

import asyncio
import os
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from hrs_platform.core.config import Settings
from hrs_platform.jobs.worker import connect
from hrs_platform.jobs.workflows import BookWorkflow
from hrs_platform.jobs.workflows import CardWorkflow
from hrs_platform.jobs.workflows import execute_with_service_recovery

pytestmark = pytest.mark.skipif(
    os.environ.get("PLATFORM_TEST_TEMPORAL") != "1", reason="Requires local Temporal"
)


@workflow.defn(name="CardWorkflow")
class LegacyCardWorkflow:
    """Pre-patch wire commands, retained only to prove old history compatibility."""
    @workflow.run
    async def run(self, request: dict) -> dict:
        for name in ("generate_cards", "check_card_images", "adopt_cards"):
            result = await execute_with_service_recovery(
                name, request["run_id"], task_queue=request["gpu_queue"],
                start_to_close_timeout=timedelta(hours=24), heartbeat_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        return result


def test_pre_incremental_card_history_replays_with_new_workflow():
    async def exercise():
        client = await connect(Settings.load())
        identity = str(uuid4())
        queue = "test-card-legacy-" + identity
        def operation(name):
            @activity.defn(name=name)
            async def call(run_id: str) -> dict:
                assert run_id == identity
                return {"state": "completed"}
            return call
        async with Worker(client, task_queue=queue, workflows=[LegacyCardWorkflow],
                          workflow_runner=UnsandboxedWorkflowRunner(),
                          activities=[operation(n) for n in ("generate_cards", "check_card_images", "adopt_cards")]):
            handle = await client.start_workflow(LegacyCardWorkflow.run,
                {"run_id": identity, "gpu_queue": queue}, id=queue, task_queue=queue)
            assert (await asyncio.wait_for(handle.result(), 30))["state"] == "completed"
        await Replayer(workflows=[CardWorkflow]).replay_workflow(await handle.fetch_history())

    asyncio.run(exercise())


def test_incremental_card_delivery_continues_history_and_replays():
    async def exercise():
        client = await connect(Settings.load())
        identity = str(uuid4())
        queue = "test-card-batches-" + identity
        calls, runs = [], []

        @activity.defn(name="generate_cards")
        async def generate(request: dict) -> dict:
            runs.append(activity.info().workflow_run_id)
            batch = len(runs)
            calls.append(("generate", batch))
            return {"run_id": identity, "batch_key": str(batch), "more": batch < 3}

        @activity.defn(name="check_card_images")
        async def check(request: dict) -> dict:
            calls.append(("vision", int(request["batch_key"])))
            return {}

        @activity.defn(name="adopt_cards")
        async def adopt(request: dict) -> dict:
            calls.append(("adopt", int(request["batch_key"])))
            return {"state": "processing" if request["more"] else "completed", "revision": 0}

        async with Worker(client, task_queue=queue, workflows=[CardWorkflow], activities=[generate, check, adopt]):
            handle = await client.start_workflow(CardWorkflow.run,
                {"run_id": identity, "gpu_queue": queue}, id=queue, task_queue=queue)
            assert (await asyncio.wait_for(handle.result(), 30))["state"] == "completed"
        assert calls == [(phase, batch) for batch in range(1, 4) for phase in ("generate", "vision", "adopt")]
        assert len(set(runs)) == 3
        for run in runs:
            history = await client.get_workflow_handle(queue, run_id=run).fetch_history()
            await Replayer(workflows=[CardWorkflow]).replay_workflow(history)

    asyncio.run(exercise())


@pytest.mark.parametrize("auto_cards", [True, False])
def test_review_wait_survives_worker_restart_and_resumes_only_after_committed_revision(auto_cards):
    async def exercise():
        client = await connect(Settings.load())
        identity = str(uuid4())
        queue = "test-book-" + identity
        executed = []
        committed = {"pending_count": 1, "revision": 1}
        waiting = asyncio.Event()

        def operation(name):
            @activity.defn(name=name)
            async def call(request: str | dict) -> dict:
                run_id = request["run_id"] if isinstance(request, dict) else request
                executed.append(name)
                if name == "generate_cards":
                    return {"run_id": run_id, "batch_key": "synthetic", "more": False}
                if name == "initialize_review":
                    waiting.set()
                    return dict(committed)
                if name == "review_status":
                    return dict(committed)
                if name == "create_card_run" and not auto_cards:
                    return {"run_id": identity, "skipped": True, "reason": "auto_cards_disabled"}
                return {"run_id": identity, "state": "completed"}

            return call

        operations = [
            operation(name)
            for name in (
                "verify_upload",
                "convert_document",
                "initialize_review",
                "review_status",
                "organize_book",
                "publish_book",
                "index_book",
                "create_card_run",
                "generate_cards",
                "check_card_images",
                "adopt_cards",
                "record_pipeline_failure",
                "finish_book",
            )
        ]
        async with Worker(
            client, task_queue=queue, workflows=[BookWorkflow, CardWorkflow], activities=operations
        ):
            handle = await client.start_workflow(
                BookWorkflow.run,
                {"run_id": identity, "gpu_queue": queue},
                id="test-book/" + identity,
                task_queue=queue,
            )
            await asyncio.wait_for(waiting.wait(), 30)
            assert "publish_book" not in executed
        # No process is waiting in a custom polling loop. The history owns the pause.
        committed.update(pending_count=0, revision=2)
        await handle.signal(BookWorkflow.review_changed, 2)
        async with Worker(
            client, task_queue=queue, workflows=[BookWorkflow, CardWorkflow], activities=operations
        ):
            result = await asyncio.wait_for(handle.result(), 30)
        assert result["state"] == "completed"
        assert executed.count("convert_document") == 1
        assert executed.index("review_status") < executed.index("publish_book") < executed.index("index_book")
        if auto_cards:
            assert executed.index("index_book") < executed.index("generate_cards")
        else:
            assert result["auto_cards"] == "disabled"
            assert "generate_cards" not in executed
            assert "check_card_images" not in executed
            assert "adopt_cards" not in executed
            assert executed[-1] == "finish_book"

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_card_outage_timer_survives_worker_restart_and_can_be_cancelled(cancel):
    async def exercise():
        client = await connect(Settings.load())
        identity = str(uuid4())
        queue = "test-card-outage-" + identity
        calls, downstream = [], []

        @activity.defn(name="generate_cards")
        async def generate(request: dict) -> dict:
            calls.append(time.monotonic())
            if len(calls) < 3:
                raise ApplicationError(
                    "synthetic disconnect",
                    {"retry_at": time.time() + 2},
                    type="model_transport_wait",
                    non_retryable=True,
                )
            return {"run_id": request["run_id"], "batch_key": "synthetic", "more": False}

        def operation(name):
            @activity.defn(name=name)
            async def call(request: str | dict) -> dict:
                downstream.append(name)
                return {"state": "completed"}

            return call

        activities = [generate] + [
            operation(name) for name in ("check_card_images", "adopt_cards", "record_pipeline_failure")
        ]
        async with Worker(client, task_queue=queue, workflows=[CardWorkflow], activities=activities):
            handle = await client.start_workflow(
                CardWorkflow.run,
                {"run_id": identity, "gpu_queue": queue},
                id="test-card-outage/" + identity,
                task_queue=queue,
            )

            async def timer_started():
                while True:
                    history = await handle.fetch_history()
                    if any(event.HasField("timer_started_event_attributes") for event in history.events):
                        return
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(timer_started(), 30)
            assert len(calls) == 1 and downstream == []
        # The first worker is gone. Temporal owns the timer, not an asyncio sleeper.
        if cancel:
            await handle.cancel()
        async with Worker(client, task_queue=queue, workflows=[CardWorkflow], activities=activities):
            if cancel:
                from temporalio.client import WorkflowFailureError
                from temporalio.exceptions import CancelledError

                with pytest.raises(WorkflowFailureError) as failure:
                    await asyncio.wait_for(handle.result(), 30)
                assert isinstance(failure.value.cause, CancelledError)
                assert len(calls) == 1 and downstream == []
            else:
                assert (await asyncio.wait_for(handle.result(), 30))["state"] == "completed"
                assert len(calls) == 3
                assert all(b - a >= 2 for a, b in zip(calls, calls[1:]))
                assert downstream == ["check_card_images", "adopt_cards"]

    asyncio.run(exercise())
