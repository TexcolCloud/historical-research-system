"""Temporal adapters invoke the same domain use cases as the API."""

import asyncio

from temporalio import activity
from temporalio.exceptions import ApplicationError

from .activities import Activities
from .books import get_run
from .cards import Cards
from .library import Library
from .outputs import Outputs
from .review import Review
from .search import Search


class PipelineActivities:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine

    async def observe(self, run_id, operation, *, blocking=False):
        async def heartbeat():
            while True:
                activity.heartbeat({"stage": activity.info().activity_type})
                await asyncio.sleep(10)

        observer = asyncio.create_task(heartbeat())
        try:
            run = get_run(self.engine, run_id)
            if (run["error"] or {}).get("code") == "model_transport_wait":
                Activities(self.settings, self.engine).transition(
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
                except ApplicationError as error:
                    if error.type in {
                        "model_transport_wait",
                        "model_transport_exhausted",
                        "model_request_rejected",
                        "model_execution_timeout",
                    }:
                        run = get_run(self.engine, run_id)
                        Activities(self.settings, self.engine).transition(
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
            observer.cancel()
            try:
                await observer
            except asyncio.CancelledError:
                pass

    @activity.defn
    def initialize_review(self, run_id: str) -> dict:
        return Review(self.settings, self.engine).initialize(run_id)

    @activity.defn
    def review_status(self, run_id: str) -> dict:
        return Review(self.settings, self.engine).status(run_id)

    @activity.defn
    async def organize_book(self, run_id: str) -> dict:
        Activities(self.settings, self.engine).transition(run_id, "processing", "organization")
        result = await self.observe(run_id, Library(self.settings, self.engine).organize(run_id))
        return {"run_id": run_id, "chapters": len(result["groups"])}

    @activity.defn
    def publish_book(self, run_id: str) -> dict:
        return Library(self.settings, self.engine).publish(run_id)

    @activity.defn
    async def index_book(self, run_id: str) -> dict:
        return await self.observe(
            run_id, asyncio.to_thread(Search(self.settings, self.engine).index, run_id), blocking=True
        )

    @activity.defn
    def create_card_run(self, run_id: str) -> dict:
        if not self.settings.auto_cards_enabled:
            return {"run_id": run_id, "skipped": True, "reason": "auto_cards_disabled"}
        return Cards(self.settings, self.engine).create_run(run_id)

    @activity.defn
    def finish_book(self, run_id: str) -> dict:
        Activities(self.settings, self.engine).transition(run_id, "completed", "complete")
        return {"run_id": run_id, "state": "completed"}

    @activity.defn
    async def generate_cards(self, run_id: str) -> dict:
        return await self.observe(run_id, Cards(self.settings, self.engine).generate(run_id))

    @activity.defn
    async def check_card_images(self, run_id: str) -> dict:
        from hrs_runtime.local_vision import task_scope

        token = task_scope.set(run_id)
        try:
            return await self.observe(
                run_id,
                asyncio.to_thread(Cards(self.settings, self.engine).check_images, run_id),
                blocking=True,
            )
        finally:
            task_scope.reset(token)

    @activity.defn
    async def adopt_cards(self, run_id: str) -> dict:
        async def finalize_and_adopt():
            cards = Cards(self.settings, self.engine)
            await cards.finalize(run_id)
            return await asyncio.to_thread(cards.adopt, run_id)

        return await self.observe(run_id, finalize_and_adopt())

    @activity.defn
    def record_pipeline_failure(self, run_id: str) -> dict:
        run = get_run(self.engine, run_id)
        previous = run["error"] or {}
        if previous.get("code") == "model_transport_wait":
            error = {
                "code": "model_transport_exhausted",
                "stage": run["stage"],
                "message": "文本服务恢复等待已达上限，已提交结果保留，请稍后手动重试。",
                "last_failure": previous,
            }
        elif previous.get("code") in {
            "model_transport_exhausted",
            "model_request_rejected",
            "model_execution_timeout",
        }:
            error = previous
        else:
            error = {
                "code": "pipeline_failed",
                "stage": run["stage"],
                "message": "本阶段未完成，已提交结果及原始证据保留，可手动重试。",
            }
        Activities(self.settings, self.engine).transition(
            run_id,
            "failed",
            run["stage"],
            error=error,
        )
        return {"run_id": run_id, "stage": run["stage"]}
