"""Real Temporal durable wait; source/model steps are explicit synthetic test activities."""

import asyncio
import os
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Worker

from hrs_platform.settings import Settings
from hrs_platform.worker import connect
from hrs_platform.workflows import BookWorkflow, CardWorkflow

pytestmark = pytest.mark.skipif(
    os.environ.get("PLATFORM_TEST_TEMPORAL") != "1", reason="Requires local Temporal"
)


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
            async def call(run_id: str) -> dict:
                executed.append(name)
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
