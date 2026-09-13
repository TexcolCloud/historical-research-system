"""Create only test-owned databases and verify Linux API/worker plus persistent volumes."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evaluation/v1-20260907"))
    args = parser.parse_args()
    module = Path(__file__).resolve().parents[2]
    configured = dotenv_values(module.parents[1] / ".env")
    url = make_url(configured["INGEST_TEST_DATABASE_URL"])
    assert url.database == "ingestion_test"
    environment = {
        **os.environ,
        **{key: value for key, value in configured.items() if value is not None},
    }
    compose = [
        "docker",
        "compose",
        "-f",
        "config/compose.acceptance.yml",
        "-f",
        "config/compose.linux.yml",
    ]
    admin = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    origin = "http://127.0.0.1:58766"

    def run(*arguments, check=True):
        result = subprocess.run(
            [*compose, *arguments],
            cwd=module,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    def ready():
        deadline = time.monotonic() + 60
        with httpx.Client(base_url=origin, timeout=3, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    result = client.get("/api/v1/health/ready")
                    if result.status_code == 200:
                        return result.json()
                except httpx.TransportError:
                    pass
                time.sleep(0.5)
        raise RuntimeError("Linux readiness deadline exceeded")

    try:
        for backend in ("local", "s3"):
            name = "ingestion_test_linux_" + uuid4().hex
            assert re.fullmatch(r"ingestion_test_linux_[0-9a-f]{32}", name)
            output = module / args.output / ("linux-" + backend)
            output.mkdir(parents=True, exist_ok=True)
            work = module / ".cache/linux-acceptance" / name
            with admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
            environment.update(
                INGEST_LINUX_DATABASE_URL=url.set(
                    host="postgres", port=5432, database=name
                ).render_as_string(hide_password=False),
                INGEST_LINUX_STORAGE_BACKEND=backend,
                INGEST_LINUX_STORAGE_PROFILE="linux-" + backend,
            )
            try:
                migration = run("run", "--rm", "--no-deps", "linux-api", "db-upgrade")
                repeated = run("run", "--rm", "--no-deps", "linux-api", "db-upgrade")
                run("up", "-d", "--no-deps", "linux-api", "linux-worker")
                before = ready()
                environment["INGEST_SMOKE_CONTAINER"] = run("ps", "-q", "linux-api").stdout.strip()
                smoke = [
                    sys.executable,
                    str(module / "evaluation/tools/pipeline_smoke.py"),
                    "--url",
                    origin,
                    "--output",
                    str(work),
                ]
                subprocess.run(smoke, cwd=module, env=environment, check=True)
                run("restart", "linux-api", "linux-worker")
                after = ready()
                subprocess.run([*smoke, "--resume"], cwd=module, check=True)
                shutil.copyfile(work / "pipeline.json", output / "pipeline.json")
                identity = subprocess.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "historical-document-ingestion:acceptance-v1",
                        "--format",
                        "{{.Id}}",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                report = {
                    "backend": backend,
                    "outcome": "passed",
                    "image_id": identity,
                    "first_migration": migration.stdout,
                    "idempotent_migration": repeated.stdout,
                    "before_restart": before,
                    "after_restart": after,
                    "database_disposition": "isolated test-owned database removed after restart readback",
                }
                (output / "runtime.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps({"linux_backend": backend, "outcome": "passed"}), flush=True)
            finally:
                run("stop", "linux-api", "linux-worker", check=False)
                with admin.connect() as connection:
                    connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


if __name__ == "__main__":
    main()
