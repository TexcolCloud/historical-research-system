"""Durable conversion execution. No database, files or model calls in workflows."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class BookWorkflow:
    def __init__(self):
        self.review_revision = -1

    @workflow.signal
    def review_changed(self, revision: int):
        self.review_revision = max(self.review_revision, revision)

    @workflow.run
    async def run(self, request: dict) -> dict:
        run_id = request["run_id"]
        retry = RetryPolicy(maximum_attempts=3)

        async def step(name, *, gpu=False, long=False):
            return await workflow.execute_activity(
                name,
                run_id,
                task_queue=request["gpu_queue"] if gpu else workflow.info().task_queue,
                start_to_close_timeout=timedelta(hours=24) if long else timedelta(minutes=15),
                heartbeat_timeout=timedelta(seconds=60) if long else None,
                retry_policy=retry,
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )

        try:
            await step("verify_upload")
            await step("convert_document", gpu=True, long=True)
            status = await step("initialize_review")
            while status["pending_count"]:
                # Signals can arrive before initialization returns. The committed revision
                # prevents that race without polling or keeping a worker occupied.
                await workflow.wait_condition(
                    lambda revision=status["revision"]: self.review_revision > revision
                )
                status = await step("review_status")
            await step("organize_book", long=True)
            await step("publish_book")
            await step("index_book", long=True)
            card_request = await step("create_card_run")
            result = await workflow.execute_child_workflow(
                CardWorkflow.run,
                {**card_request, "gpu_queue": request["gpu_queue"]},
                id=card_request.get("workflow_id", f"cards/{card_request['run_id']}"),
            )
            await step("finish_book")
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            await step("record_pipeline_failure")
            raise


@workflow.defn
class CardWorkflow:
    @workflow.run
    async def run(self, request: dict) -> dict:
        run_id = request["run_id"]

        async def step(name, gpu=False):
            return await workflow.execute_activity(
                name,
                run_id,
                task_queue=request["gpu_queue"] if gpu else workflow.info().task_queue,
                start_to_close_timeout=timedelta(hours=24),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )

        try:
            await step("generate_cards")
            await step("check_card_images", gpu=True)
            return await step("adopt_cards")
        except asyncio.CancelledError:
            raise
        except Exception:
            await workflow.execute_activity(
                "record_pipeline_failure",
                run_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            raise


@workflow.defn
class ConversionWorkflow:
    @workflow.run
    async def run(self, request: dict) -> dict:
        run_id = request["run_id"]
        try:
            await workflow.execute_activity(
                "verify_upload",
                run_id,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            return await workflow.execute_activity(
                "convert_document",
                run_id,
                task_queue=request["gpu_queue"],
                start_to_close_timeout=timedelta(hours=24),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        except Exception:
            await workflow.execute_activity(
                "record_conversion_failure",
                run_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            raise


@workflow.defn
class DeleteBookWorkflow:
    @workflow.run
    async def run(self, request: dict) -> dict:
        async def step(name, value, *, host=False):
            return await workflow.execute_activity(
                name,
                value,
                task_queue=request["control_queue"] if host else workflow.info().task_queue + "-control",
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=None if host else timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )

        try:
            if request["host_required"]:
                await step("stop_gpu_requests", request["book_id"], host=True)
            run_ids = await step("stop_book", request["book_id"])
            if request["host_required"]:
                await step("clean_gpu_cache", run_ids, host=True)
            return await step("erase_book", request["book_id"])
        except Exception:
            await workflow.execute_activity(
                "deletion_failed",
                request["book_id"],
                start_to_close_timeout=timedelta(seconds=30),
                task_queue=workflow.info().task_queue + "-control",
            )
            raise
