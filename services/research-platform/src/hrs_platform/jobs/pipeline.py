"""Temporal adapters invoke the same domain use cases as the API."""
import asyncio
import time
from contextlib import suppress
from threading import Event

from temporalio import activity
from temporalio.exceptions import ApplicationError

from hrs_platform.domain.errors import TaskError
from hrs_platform.jobs.errors import activity_errors
from hrs_platform.services.books import get_run
from hrs_platform.services.cards import Cards
from hrs_platform.services.documents.library import Library
from hrs_platform.services.documents.review import Review
from hrs_platform.services.retrieval.compute import task_search
from hrs_platform.services.retrieval.indexing import BookIndexer
from hrs_platform.services.retrieval.recovery import recover_retrieval
from hrs_platform.services.runs.lifecycle import RunLifecycle
from hrs_platform.services.runs.outputs import Outputs, execution_progress


async def isolated(operation):
    """Keep synchronous domain I/O off the worker loop, drain it on cancellation."""
    cancelled, control = Event(), {}

    def execute():
        from hrs_platform.services.storage import storage_heartbeat

        async def run():
            control.update(loop=asyncio.get_running_loop(), task=asyncio.current_task())
            if cancelled.is_set():
                control["task"].cancel()
            return await operation

        token = storage_heartbeat.set(None)
        try:
            return asyncio.run(run())
        finally:
            storage_heartbeat.reset(token)

    running = asyncio.create_task(asyncio.to_thread(execute))
    try:
        return await asyncio.shield(running)
    except asyncio.CancelledError:
        cancelled.set()
        if "loop" in control:
            with suppress(RuntimeError):
                control["loop"].call_soon_threadsafe(control["task"].cancel)
        while not running.done():
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(running)
        with suppress(Exception, asyncio.CancelledError):
            running.result()
        raise


class PipelineActivities:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine

    async def observe(self, run_id, operation, *, blocking=False):
        progress = {"step": activity.info().activity_type, "last_commit": time.monotonic()}
        token = execution_progress.set(progress)
        async def heartbeat():
            while True:
                idle = time.monotonic() - progress["last_commit"]
                activity.heartbeat({"stage": activity.info().activity_type, "step": progress["step"], "idle_seconds": round(idle)})
                if activity.info().activity_type in {"generate_cards", "check_card_images", "adopt_cards"} and idle > max(1800, getattr(self.settings, "model_timeout_seconds", 600) * 2):
                    raise ApplicationError("制卡步骤长时间未提交新检查点，保留进度后重新调度。", type="card_progress_stalled")
                await asyncio.sleep(10)

        observer = asyncio.create_task(heartbeat())
        running = asyncio.create_task(isolated(self._observe(run_id, operation, blocking=blocking)))
        try:
            done, _ = await asyncio.wait((running, observer), return_when=asyncio.FIRST_COMPLETED)
            if observer in done:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
                await observer
            return await running
        except ApplicationError as error:
            if error.type == "card_progress_stalled":
                run = get_run(self.engine, run_id)
                RunLifecycle(self.engine).transition(
                    run_id, "processing", run["stage"],
                    error={"code": error.type, "stage": run["stage"], "message": error.message,
                           "step": progress["step"]},
                )
            raise
        except asyncio.CancelledError:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            raise
        finally:
            observer.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(observer, return_exceptions=True)
            execution_progress.reset(token)

    async def _observe(self, run_id, operation, *, blocking=False):
        try:
            run = get_run(self.engine, run_id)
            if (run["error"] or {}).get("code") in {"model_transport_wait", "retrieval_wait", "vision_service_wait", "card_progress_stalled"}:
                RunLifecycle(self.engine).transition(
                    run_id, "processing", run["stage"], error=None
                )
            name = activity.info().activity_type
            label = {
                "organize_book": "章节组织",
                "index_book": "建立检索索引",
                "generate_cards": "研究与制卡",
                "check_card_images": "卡片原件核验",
                "adopt_cards": "原图后定稿与采用",
            }[name]
            with Outputs(self.settings, self.engine).operation(run_id, "component:" + name, label):
                running = asyncio.ensure_future(operation)
                try:
                    return await asyncio.shield(running)
                except asyncio.CancelledError:
                    # Retain the activity slot while its real model thread drains.
                    if not blocking:
                        running.cancel()
                    try:
                        await running
                    finally:
                        raise
                except TaskError as error:
                    if error.type in {
                        "model_transport_wait",
                        "retrieval_wait",
                        "retrieval_exhausted",
                        "model_transport_exhausted",
                        "model_request_rejected",
                        "model_execution_timeout",
                        "vision_service_wait",
                        "vision_request_rejected",
                        "model_request_budget",
                    }:
                        run = get_run(self.engine, run_id)
                        RunLifecycle(self.engine).transition(
                            run_id,
                            "processing",
                            run["stage"],
                            error={
                                "code": error.type,
                                "stage": run["stage"],
                                "message": error.message,
                                "recovery": error.details[0] if error.details else {},
                            },
                        )
                    raise
        finally:
            # Closing an unstarted coroutine avoids leaked work if preflight I/O fails.
            operation.close()

    @activity.defn
    @activity_errors
    def initialize_review(self, run_id: str) -> dict:
        return Review(self.settings, self.engine).initialize(run_id)

    @activity.defn
    @activity_errors
    def review_status(self, run_id: str) -> dict:
        return Review(self.settings, self.engine).status(run_id)

    @activity.defn
    @activity_errors
    async def organize_book(self, run_id: str) -> dict:
        async def organize():
            RunLifecycle(self.engine).transition(run_id, "processing", "organization")
            return await Library(self.settings, self.engine).organize(run_id)

        result = await self.observe(run_id, organize())
        return {"run_id": run_id, "chapters": len(result["groups"])}

    @activity.defn
    @activity_errors
    def publish_book(self, run_id: str) -> dict:
        return Library(self.settings, self.engine).publish(run_id)

    @activity.defn
    @activity_errors
    async def index_book(self, run_id: str) -> dict:
        async def index():
            indexer = BookIndexer(self.settings, self.engine)
            return await recover_retrieval(
                self.engine,
                indexer.outputs,
                run_id,
                "book-index",
                lambda: task_search(indexer.index, self.settings.retrieval_endpoint, run_id, run_id),
            )

        return await self.observe(run_id, index())

    @activity.defn
    @activity_errors
    def create_card_run(self, run_id: str) -> dict:
        if not self.settings.auto_cards_enabled:
            return {"run_id": run_id, "skipped": True, "reason": "auto_cards_disabled"}
        return Cards(self.settings, self.engine).create_run(run_id)

    @activity.defn
    @activity_errors
    def finish_book(self, run_id: str) -> dict:
        RunLifecycle(self.engine).transition(run_id, "completed", "complete")
        return {"run_id": run_id, "state": "completed"}

    @activity.defn
    @activity_errors
    async def generate_cards(self, request: str | dict) -> dict:
        run_id = request["run_id"] if isinstance(request, dict) else request
        return await self.observe(run_id, Cards(self.settings, self.engine).generate(run_id, incremental=isinstance(request, dict)))

    @activity.defn
    @activity_errors
    async def check_card_images(self, request: str | dict) -> dict:
        from hrs_runtime.local_vision import task_scope

        run_id = request["run_id"] if isinstance(request, dict) else request
        batch_key = request.get("batch_key") if isinstance(request, dict) else None
        token = task_scope.set(run_id)
        try:
            return await self.observe(
                run_id,
                asyncio.to_thread(Cards(self.settings, self.engine).check_images, run_id, batch_key),
                blocking=True,
            )
        finally:
            task_scope.reset(token)

    @activity.defn
    @activity_errors
    async def adopt_cards(self, request: str | dict) -> dict:
        run_id = request["run_id"] if isinstance(request, dict) else request
        batch_key = request.get("batch_key") if isinstance(request, dict) else None
        async def finalize_and_adopt():
            cards = Cards(self.settings, self.engine)
            await cards.finalize(run_id, batch_key)
            result = await asyncio.to_thread(cards.adopt, run_id, batch_key)
            result["revision"] = (get_run(self.engine, run_id).get("result") or {}).get("card_revision", 0)
            return result

        return await self.observe(run_id, finalize_and_adopt())

    @activity.defn
    @activity_errors
    def schedule_card_repair(self, request: dict) -> dict:
        return Cards(self.settings, self.engine).schedule_repair(request["run_id"], request["revision"])

    @activity.defn
    @activity_errors
    def record_pipeline_failure(self, run_id: str) -> dict:
        run = get_run(self.engine, run_id)
        previous = run["error"] or {}
        if previous.get("code") in {"model_transport_wait", "retrieval_wait"}:
            error = {
                "code": "retrieval_exhausted"
                if previous["code"] == "retrieval_wait"
                else "model_transport_exhausted",
                "stage": run["stage"],
                "message": "服务恢复等待已达上限，已提交结果保留，请稍后手动重试。",
                "last_failure": previous,
            }
        elif previous.get("code") in {
            "retrieval_exhausted",
            "model_transport_exhausted",
            "model_request_rejected",
            "model_execution_timeout",
            "vision_request_rejected",
            "model_request_budget",
            "card_progress_stalled",
        }:
            error = previous
        else:
            error = {
                "code": "pipeline_failed",
                "stage": run["stage"],
                "message": "本阶段未完成，已提交结果及原始证据保留，可手动重试。",
            }
        RunLifecycle(self.engine).transition(
            run_id,
            "failed",
            run["stage"],
            error=error,
        )
        return {"run_id": run_id, "stage": run["stage"]}
