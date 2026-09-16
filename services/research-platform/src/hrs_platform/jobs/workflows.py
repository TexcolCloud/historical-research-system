"""Durable conversion execution. No database, files or model calls in workflows."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError


async def execute_with_service_recovery(name, run_id, *, resume=None, **options):
    waited, deferrals = 0.0, 0
    while True:
        try:
            return await workflow.execute_activity(name, run_id, **options)
        except ActivityError as error:
            cause = error.cause
            if not isinstance(cause, ApplicationError) or cause.type not in {"model_transport_wait", "retrieval_wait", "vision_service_wait"}:
                raise
            # The activity error is non-retryable: only this timer owns network
            # recovery. Completed steps replay receipts, not paid requests.
            delay = max(1.0, cause.details[0]["retry_at"] - workflow.now().timestamp())
            deferrals += 1
            waited += delay
            if deferrals > 12 or waited > 3600:
                if resume is not None:
                    await workflow.sleep(delay)
                    workflow.continue_as_new(resume)
                raise ApplicationError(
                    "服务恢复等待已达上限，进度保留，请稍后手动重试。",
                    type="retrieval_exhausted" if cause.type == "retrieval_wait" else "model_transport_exhausted",
                    non_retryable=True,
                ) from None
            await workflow.sleep(delay)


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
            return await execute_with_service_recovery(
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
            if card_request.get("skipped"):
                return {**await step("finish_book"), "auto_cards": "disabled"}
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
        incremental = workflow.patched("card-incremental-v1")

        async def step(name, gpu=False, value=None):
            return await execute_with_service_recovery(
                name,
                value if value is not None else run_id,
                resume=request if incremental else None,
                task_queue=request["gpu_queue"] if gpu else workflow.info().task_queue,
                start_to_close_timeout=timedelta(hours=24),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )

        try:
            if incremental:
                # A continuation carries the committed batch through service outages;
                # it cannot accidentally generate the next batch before adopting this one.
                phase = request.get("phase", "generate")
                batch = request.get("batch")
                if phase == "generate":
                    batch = await step("generate_cards", value={"run_id": run_id})
                    if "batch_key" not in batch:
                        workflow.continue_as_new({"run_id": run_id, "gpu_queue": request["gpu_queue"]})
                request = {**request, "phase": "vision", "batch": batch}
                if phase in {"generate", "vision"}:
                    await step("check_card_images", gpu=True, value=batch)
                request = {**request, "phase": "adopt"}
                result = await step("adopt_cards", value=batch)
                next_request = {"run_id": run_id, "gpu_queue": request["gpu_queue"]}
                if batch["more"]:
                    workflow.continue_as_new(next_request)
                if result["state"] == "needs_revision":
                    repair = await step("schedule_card_repair", value={"run_id": run_id, "revision": result["revision"]})
                    if repair["retry"]:
                        workflow.continue_as_new(next_request)
                return result
            # Compatibility for histories which scheduled the old string activities.
            # Remove after no pre-card-incremental-v1 execution remains open.
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
