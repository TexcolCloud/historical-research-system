"""Isolated ingestion HTTP fixture, run with the unchanged ingestion environment."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
from document_ingestion.api import create_app
from document_ingestion.database import upgrade_database
from document_ingestion.errors import Problem
from document_ingestion.settings import Settings
from document_ingestion.worker import run_worker
from dotenv import dotenv_values
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18125)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    directory = args.state.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "private-connection.json"
    if not config_path.exists():
        values = dotenv_values(root / ".env")
        url = make_url(values["INGEST_TEST_DATABASE_URL"])
        if (
            url.get_backend_name() != "postgresql"
            or url.database != "ingestion_test"
            or url.host not in ("127.0.0.1", "localhost")
        ):
            raise ValueError(
                "The evaluation requires the explicit local ingestion_test administrator"
            )
        name = "ingestion_test_retrieval_" + uuid4().hex
        with create_engine(
            url, isolation_level="AUTOCOMMIT", hide_parameters=True
        ).connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        config_path.write_text(
            json.dumps(
                {"database_url": url.set(database=name).render_as_string(hide_password=False)}
            ),
            encoding="utf-8",
        )
        (directory / "identity.json").write_text(
            json.dumps(
                {
                    "database_name": name,
                    "url": f"http://127.0.0.1:{args.port}",
                    "module_sources_modified": False,
                }
            ),
            encoding="utf-8",
        )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    settings = Settings(
        database_url=SecretStr(config["database_url"]),
        storage_root=directory / "assets",
        receive_root=directory / "receiving",
        deepseek_max_calls_per_package=0,
    )
    settings.receive_root.mkdir(parents=True, exist_ok=True)
    if args.worker:
        while True:
            try:
                run_worker(settings)
                break
            except Problem as error:
                if not error.retryable:
                    raise
                print(json.dumps({"fixture_worker_retry": error.code}), flush=True)
                time.sleep(1)
        return
    upgrade_database(settings)
    worker_log = (directory / "worker.log").open("ab")
    worker = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--state", str(directory), "--worker"],
        stdout=worker_log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUTF8": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, access_log=False)
    finally:
        worker.terminate()
        worker.wait(timeout=30)
        worker_log.close()


if __name__ == "__main__":
    main()
