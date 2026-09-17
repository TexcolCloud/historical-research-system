"""Read-only deployment checks; never load a model or approve content."""

import asyncio
import json
from datetime import UTC, datetime
from urllib.request import urlopen

from sqlalchemy import text
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

from hrs_platform.core.db import engine_for
from hrs_platform.jobs.worker import connect
from hrs_platform.services.retrieval.compute import search_client
from hrs_platform.services.storage import objects_for


async def inspect(settings, *, gpu=True):
    results = []

    async def check(name, operation):
        try:
            detail = await operation()
            results.append({"component": name, "ready": True, "detail": detail})
        except Exception as error:  # noqa: BLE001 -- one unavailable dependency must not hide the other diagnostic results
            # Connection exceptions can contain credentials; only report the type.
            results.append({"component": name, "ready": False, "error_type": type(error).__name__})

    def database():
        engine = engine_for(settings)
        try:
            with engine.connect() as connection:
                return {"migration": connection.scalar(text("SELECT version_num FROM alembic_version"))}
        finally:
            engine.dispose()

    def storage():
        objects = objects_for(settings)
        objects.client.head_bucket(Bucket=objects.bucket)
        return {"bucket_accessible": True}

    def index():
        health = search_client(settings.opensearch_url).cluster.health(request_timeout=5)
        if health["status"] == "red":
            raise RuntimeError("Index cluster unavailable")
        return {"status": health["status"]}

    async def queue(name):
        client = await connect(settings)
        response = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=settings.temporal_namespace,
                task_queue=TaskQueue(name=name),
                task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
            )
        )
        if not response.pollers:
            # A saturated single-slot GPU worker stops polling while its long OCR
            # activity runs. A recent Temporal heartbeat is stronger evidence.
            seen = 0
            async for execution in client.list_workflows("ExecutionStatus = 'Running'"):
                if execution.task_queue != settings.task_queue:
                    continue
                detail = await client.get_workflow_handle(execution.id).describe()
                for pending in detail.raw_description.pending_activities:
                    is_gpu = pending.activity_type.name in {"convert_document", "check_card_images"}
                    if is_gpu != (name == settings.gpu_queue) or not pending.HasField("last_heartbeat_time"):
                        continue
                    age = (
                        datetime.now(UTC) - pending.last_heartbeat_time.ToDatetime(tzinfo=UTC)
                    ).total_seconds()
                    if age < 120:
                        return {"pollers": 0, "state": "busy", "heartbeat_age_seconds": round(age)}
                seen += 1
                if seen >= 50:
                    break
            raise RuntimeError("No recent worker poll or active heartbeat")
        return {"pollers": len(response.pollers)}

    def broker():
        with urlopen("http://127.0.0.1:18160/health", timeout=5) as response:
            result = json.load(response)
        return {key: result[key] for key in ("model", "resident", "free_gib")}

    checks = [
        check("PostgreSQL", lambda: asyncio.to_thread(database)),
        check("S3", lambda: asyncio.to_thread(storage)),
        check("OpenSearch", lambda: asyncio.to_thread(index)),
        check("CPU worker", lambda: queue(settings.task_queue)),
    ]
    if gpu:
        checks += [
            check("GPU worker", lambda: queue(settings.gpu_queue)),
            check("GPU broker", lambda: asyncio.to_thread(broker)),
        ]
    await asyncio.gather(*checks)
    return {"ready": all(row["ready"] for row in results), "checks": results}


def run(settings):
    report = asyncio.run(inspect(settings))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1
