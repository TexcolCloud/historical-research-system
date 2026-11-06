"""Read-only dependency checks; no model inference or content approval."""

import asyncio
import json
import shutil
import subprocess
from urllib.request import urlopen

from sqlalchemy import text

from hrs.processing.workspace import resolve_workspace
from hrs.search.compute import search_client
from hrs.search.settings import MODEL_REVISIONS
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
        for name, revision in MODEL_REVISIONS.items():
            directory = settings.models_root / name.split("/")[-1] / revision
            if (
                not (directory / "config.json").is_file()
                or not (directory / "tokenizer.json").is_file()
            ):
                raise RuntimeError("Registered retrieval model artifacts are missing")
            if not any(directory.glob("*.safetensors")) and not any(
                directory.glob("pytorch_model*.bin")
            ):
                raise RuntimeError("Registered retrieval model weights are missing")
        return {"artifacts_present": True, "inference_checked": False}

    def broker():
        endpoint = settings.retrieval_endpoint.rsplit("/", 1)[0] + "/health"
        with urlopen(endpoint, timeout=5) as response:
            result = json.load(response)
        return {"status": result.get("status", "ok"), "inference_checked": False}

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
