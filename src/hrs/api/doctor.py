"""Read-only dependency checks; no model inference or content approval."""

import asyncio
import json
import shutil
import subprocess

from sqlalchemy import text

from hrs.processing.workspace import resolve_workspace
from hrs.search.artifacts import inspect_artifacts
from hrs.search.compute import search_client
from hrs.search.service import broker_health
from hrs.storage.db import engine_for
from hrs.storage.service import objects_for


async def inspect(settings, *, gpu=True):
    results = []

    async def check(name, operation):
        try:
            detail = await asyncio.to_thread(operation)
            results.append({"component": name, "ready": True, "detail": detail})
        except Exception as error:  # noqa: BLE001 -- collect all failures without leaking connection values
            results.append({"component": name, "ready": False, "error_type": type(error).__name__})

    def database():
        engine = engine_for(settings)
        try:
            with engine.connect() as connection:
                return {
                    "migration": connection.scalar(text("SELECT version_num FROM alembic_version"))
                }
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

    def native():
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("Codex executable unavailable")
        result = subprocess.run(
            [executable, "login", "status"], capture_output=True, timeout=10, check=False
        )
        if result.returncode:
            raise RuntimeError("Codex login unavailable")
        root = resolve_workspace(settings.codex_root)
        if not root.is_dir():
            raise RuntimeError("Configured native workspace is missing")
        return {"logged_in": True, "workspace_exists": True, "runtime": "native-subagents"}

    def models():
        report = inspect_artifacts(settings.models_root)
        if not all(row["present"] for row in report.values()):
            raise RuntimeError("Registered retrieval model artifacts are missing or incomplete")
        return {"artifacts_present": True, "inference_checked": False}

    def broker():
        result = broker_health(settings.retrieval_endpoint, timeout=5)
        if result.get("gpu_available") is False:
            raise RuntimeError("The configured retrieval GPU is unavailable")
        return {"service": result["service"], "inference_checked": False}

    checks = [
        check("PostgreSQL", database),
        check("S3", storage),
        check("OpenSearch", index),
        check("Codex executor", native),
        check("BGE artifacts", models),
    ]
    if gpu and settings.retrieval_device == "cuda":
        checks.append(check("Retrieval broker", broker))
    await asyncio.gather(*checks)
    return {"ready": all(row["ready"] for row in results), "checks": results}


def run(settings):
    report = asyncio.run(inspect(settings))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1
