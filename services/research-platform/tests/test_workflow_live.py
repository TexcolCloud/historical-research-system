"""Real Temporal/Postgres/S3 contract; substitutes only the expensive OCR activity."""

import asyncio
import io
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select, update
from temporalio import activity
from temporalio.worker import Worker

from hrs_platform import models as schema
from hrs_platform.jobs.conversion import Activities
from hrs_platform.main import create_app
from hrs_platform.jobs.worker import connect
from hrs_platform.jobs.worker import dispatch_once
from hrs_platform.jobs.workflows import ConversionWorkflow

pytestmark = pytest.mark.skipif(
    os.environ.get("PLATFORM_TEST_TEMPORAL") != "1", reason="Opt in to the local Temporal/S3 integration test"
)


def test_waits_for_gpu_worker_and_recovers_without_local_source(platform, tmp_path):
    settings, engine = platform
    suffix = uuid4().hex
    settings = settings.model_copy(
        update={"task_queue": f"test-cpu-{suffix}", "gpu_queue": f"test-gpu-{suffix}", "cache_root": tmp_path}
    )
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    content = stream.getvalue()
    activities = Activities(settings, engine)
    with TestClient(create_app(settings, engine)) as api:
        session = api.post(
            "/api/v2/uploads", json={"filename": "workflow-test.pdf", "byte_length": len(content)}
        ).json()
        key = f"{settings.upload_prefix}{session['session_id']}/{uuid4()}"
        activities.objects.client.put_object(Bucket=settings.s3_bucket, Key=key, Body=content)
        hook = {
            "Type": "post-finish",
            "Event": {
                "Upload": {
                    "Size": len(content),
                    "Offset": len(content),
                    "MetaData": {"session_id": session["session_id"]},
                    "Storage": {"Type": "s3store", "Bucket": settings.s3_bucket, "Key": key},
                }
            },
        }
        assert (
            api.post(
                "/internal/tus", json=hook, headers={"Authorization": "Bearer " + session["token"]}
            ).status_code
            == 200
        )
        run_id = api.get("/api/v2/books").json()[0]["run_id"]

    @activity.defn(name="convert_document")
    def test_conversion(run: str) -> dict:
        with engine.connect() as connection:
            source = connection.scalar(select(schema.runs.c.source).where(schema.runs.c.id == run))
        # Prove a restarted worker can recover from S3 after its local source is removed.
        assert not (tmp_path / run / "source.pdf").exists()
        assert activities.objects.read_bytes(source) == content
        return {"scope": "test activity, no OCR or content approval", "run_id": run}

    async def exercise():
        client = await connect(settings)
        with ThreadPoolExecutor(max_workers=2) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ConversionWorkflow],
                activities=[activities.verify_upload, activities.record_conversion_failure],
                activity_executor=executor,
            ):
                await dispatch_once(engine, client, settings, conversion_only=True)
                handle = client.get_workflow_handle(f"book-conversion/{run_id}")
                for _ in range(100):
                    with engine.connect() as connection:
                        source = connection.scalar(
                            select(schema.runs.c.source).where(schema.runs.c.id == run_id)
                        )
                    if source.get("sha256"):
                        break
                    await asyncio.sleep(0.1)
                assert source.get("sha256")
            (tmp_path / run_id / "source.pdf").unlink()
            if os.environ.get("PLATFORM_TEST_RESTART_TEMPORAL") == "1":
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        ".env",
                        "-f",
                        "deploy/platform/compose.yml",
                        "restart",
                        "temporal",
                    ],
                    cwd=settings.project_root,
                    capture_output=True,
                    check=True,
                )
                for attempt in range(45):
                    try:
                        client = await connect(settings)
                        break
                    except RuntimeError:
                        if attempt == 44:
                            raise
                        await asyncio.sleep(1)
            # Both worker processes have stopped; the pending GPU activity survives in Temporal.
            async with (
                Worker(client, task_queue=settings.task_queue, workflows=[ConversionWorkflow]),
                Worker(
                    client,
                    task_queue=settings.gpu_queue,
                    activities=[test_conversion],
                    activity_executor=executor,
                ),
            ):
                result = await asyncio.wait_for(handle.result(), timeout=30)
                assert result["run_id"] == run_id
            with engine.begin() as connection:
                connection.execute(update(schema.outbox).values(delivered=False))
            await dispatch_once(engine, client, settings, conversion_only=True)
            with engine.connect() as connection:
                assert connection.scalar(select(schema.outbox.c.delivered)) is True

    asyncio.run(exercise())
