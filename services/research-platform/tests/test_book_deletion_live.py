"""Live Temporal cancellation and queue deletion; no OCR, model or historical book is used."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Worker
from test_book_deletion import seed

from hrs_platform.books import list_books
from hrs_platform.deletion import DeletionActivities, request_deletion
from hrs_platform.worker import connect, dispatch_once
from hrs_platform.workflows import BookWorkflow, DeleteBookWorkflow

pytestmark = pytest.mark.skipif(
    os.environ.get("PLATFORM_TEST_TEMPORAL") != "1", reason="Local Temporal integration opt-in"
)


@pytest.mark.parametrize("running", [False, True])
def test_delete_waits_for_real_activity_exit_before_removing_cache(platform, tmp_path, running):
    settings, engine = platform
    suffix = uuid4().hex
    settings = settings.model_copy(
        update={
            "task_queue": "delete-test-cpu-" + suffix,
            "gpu_queue": "delete-test-gpu-" + suffix,
            "cache_root": tmp_path,
            "project_root": tmp_path,
            "opensearch_index": "delete-test-" + suffix,
        }
    )
    book, run = seed(engine, stage="conversion" if running else "verify_upload")
    deletion = DeletionActivities(settings, engine)

    async def exercise():
        entered, exited = asyncio.Event(), asyncio.Event()

        @activity.defn(name="verify_upload")
        async def verify(run_id: str) -> dict:
            return {"run_id": run_id}

        @activity.defn(name="convert_document")
        async def compute(run_id: str) -> dict:
            folder = tmp_path / run_id
            folder.mkdir()
            (folder / "owned-cache").write_text("task bytes")
            entered.set()
            try:
                while True:
                    activity.heartbeat()
                    await asyncio.sleep(0.1)
            finally:
                await asyncio.sleep(0.2)
                assert folder.exists(), "Cleanup ran before the producer exited"
                exited.set()

        @activity.defn(name="stop_gpu_requests")
        async def stop_requests(book_id: str) -> None:
            assert book_id == book  # This fixture has no model requests to stop.

        client = await connect(settings)
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[BookWorkflow, DeleteBookWorkflow],
                    activities=[verify],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-control",
                    activities=[deletion.stop_book, deletion.erase_book, deletion.deletion_failed],
                    activity_executor=executor,
                ),
            ):
                if running:
                    from datetime import timedelta

                    async with (
                        Worker(
                            client,
                            task_queue=settings.gpu_queue,
                            activities=[compute],
                            max_heartbeat_throttle_interval=timedelta(seconds=1),
                        ),
                        Worker(
                            client,
                            task_queue=settings.gpu_queue + "-control",
                            activities=[stop_requests, deletion.clean_gpu_cache],
                            activity_executor=executor,
                        ),
                    ):
                        await dispatch_once(engine, client, settings)
                        await asyncio.wait_for(entered.wait(), 30)
                        await asyncio.to_thread(request_deletion, engine, book)
                        await dispatch_once(engine, client, settings)
                        result = await asyncio.wait_for(
                            client.get_workflow_handle(f"delete-book/{book}/0").result(), 45
                        )
                        assert exited.is_set()
                else:
                    # No GPU/control worker exists: a queued task must delete directly.
                    await asyncio.to_thread(request_deletion, engine, book)
                    await dispatch_once(engine, client, settings)
                    result = await asyncio.wait_for(
                        client.get_workflow_handle(f"delete-book/{book}/0").result(), 45
                    )
                assert result["state"] == "completed"
                assert not (tmp_path / run).exists()
                assert list_books(engine) == []

    asyncio.run(exercise())
