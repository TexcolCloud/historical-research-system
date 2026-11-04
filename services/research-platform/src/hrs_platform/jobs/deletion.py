import asyncio

from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from hrs_platform.jobs.errors import activity_errors
from hrs_platform.services.deletion_cleanup import BookCleanup
from hrs_platform.services.deletion_cleanup import remove_cache as remove_cache
from hrs_platform.services.retrieval.compute import search_client
from hrs_platform.services.runs.recovery import workflow_id


class DeletionActivities(BookCleanup):
    def __init__(self, settings, engine):
        super().__init__(
            settings,
            engine,
            heartbeat=lambda: activity.heartbeat() if activity.in_activity() else None,
            search_factory=lambda url: search_client(url),
        )

    @activity.defn
    @activity_errors
    async def stop_book(self, book_id: str) -> list[str]:
        client = await Client.connect(
            self.settings.temporal_address, namespace=self.settings.temporal_namespace
        )
        handles = []
        # Cancel every attempt, including manually restarted card tasks.
        for run in await asyncio.to_thread(self.runs, book_id):
            for attempt in range(run["recovery_attempt"] + 1):
                handle = client.get_workflow_handle(workflow_id(run["kind"], run["id"], attempt))
                try:
                    if (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                        await handle.cancel()
                        handles.append(handle)
                except RPCError as error:
                    if error.status != RPCStatusCode.NOT_FOUND:
                        raise
        # WAIT_CANCELLATION_COMPLETED in the producer workflows ensures subprocesses
        # and blocking image checks have actually exited before files can be erased.
        while handles:
            activity.heartbeat()
            handles = [
                handle
                for handle in handles
                if (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
            ]
            if handles:
                await asyncio.sleep(1)
        return [run["id"] for run in await asyncio.to_thread(self.runs, book_id)]

    @activity.defn
    @activity_errors
    def stop_gpu_requests(self, book_id: str) -> None:
        from hrs_runtime.local_vision import request

        request("/cancel-tasks", {"task_ids": [run["id"] for run in self.runs(book_id)]}, timeout=60)

    @activity.defn
    @activity_errors
    def clean_gpu_cache(self, run_ids: list[str]) -> None:
        remove_cache(self.settings.cache_root, run_ids)
        remove_cache(self.settings.project_root / "state/local-vision/receipts", run_ids)

    @activity.defn
    @activity_errors
    def erase_book(self, book_id: str) -> dict:
        return super().erase_book(book_id)

    @activity.defn
    @activity_errors
    def deletion_failed(self, book_id: str) -> None:
        return super().deletion_failed(book_id)
