"""Create only dedicated V2 databases, then apply official versioned schemas."""

import asyncio
import os
import subprocess
from pathlib import Path

import psycopg
from dotenv import dotenv_values, set_key
from psycopg import sql
from sqlalchemy.engine import make_url

from .database import engine_for, migrate
from .settings import Settings
from .worker import connect, register_namespace


def bootstrap():
    root = Path(__file__).resolve().parents[4]
    env = {**dotenv_values(root / ".env"), **os.environ}
    ingest = make_url(env["INGEST_DATABASE_URL"])
    admin = ingest.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    names = [
        "historical_research_v2",
        "historical_research_v2_test",
        "hrs_temporal_v2",
        "hrs_temporal_visibility_v2",
    ]
    with psycopg.connect(admin, autocommit=True) as connection:
        for name in names:
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    for key, name in [("PLATFORM_DATABASE_URL", names[0]), ("PLATFORM_TEST_DATABASE_URL", names[1])]:
        if not env.get(key):
            set_key(root / ".env", key, ingest.set(database=name).render_as_string(hide_password=False))
    if not env.get("PLATFORM_DOCKER_DATABASE_URL"):
        set_key(
            root / ".env",
            "PLATFORM_DOCKER_DATABASE_URL",
            ingest.set(database=names[0], host="host.docker.internal").render_as_string(hide_password=False),
        )
    logdir = root / ".cache/platform-diagnostics"
    logdir.mkdir(parents=True, exist_ok=True)
    compose = ["docker", "compose", "--env-file", ".env", "-f", "deploy/platform/compose.yml"]
    with (logdir / "bootstrap.log").open("ab") as log:
        subprocess.run(
            [*compose, "--profile", "setup", "run", "--rm", "temporal-schema"],
            cwd=root,
            stdout=log,
            stderr=log,
            check=True,
        )
        subprocess.run(
            [*compose, "up", "-d", "--wait", "temporal", "tusd"], cwd=root, stdout=log, stderr=log, check=True
        )
    settings = Settings.load(root)
    engine = engine_for(settings)
    try:
        migrate(engine)
    finally:
        engine.dispose()

    async def namespace():
        for attempt in range(30):
            try:
                await connect(settings)
                break
            except RuntimeError:
                if attempt == 29:
                    raise
                await asyncio.sleep(1)
        await register_namespace(settings)

    asyncio.run(namespace())
    print("V2 databases, migrations, Temporal namespace and tusd are ready. No legacy data was migrated.")


if __name__ == "__main__":
    bootstrap()
