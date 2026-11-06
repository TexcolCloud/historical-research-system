"""Create only dedicated V2 databases, then apply official versioned schemas."""

import asyncio
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from hrs_platform.core.config import Settings, load_environment
from hrs_platform.core.db import engine_for, migrate


def bootstrap(*, legacy=False):
    root = Path(__file__).resolve().parents[4]
    env = load_environment(root)
    if not env.get("INGEST_DATABASE_URL"):
        raise ValueError("Set INGEST_DEV_DB_PASSWORD or INGEST_DATABASE_URL before initialization.")
    ingest = make_url(env["INGEST_DATABASE_URL"])
    admin = ingest.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    names = [
        "historical_research_v2",
        "historical_research_v2_test",
    ]
    if legacy:
        names += ["hrs_temporal_v2", "hrs_temporal_visibility_v2"]
    with psycopg.connect(admin, autocommit=True) as connection:
        for name in names:
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    logdir = root / ".cache/platform-diagnostics"
    logdir.mkdir(parents=True, exist_ok=True)
    compose = ["docker", "compose", "--env-file", ".env", "-f", "deploy/platform/compose.yml"]
    with (logdir / "bootstrap.log").open("ab") as log:
        if legacy:
            subprocess.run(
                [*compose, "--profile", "setup", "run", "--rm", "temporal-schema"],
                cwd=root,
                stdout=log,
                stderr=log,
                check=True,
                env=env,
            )
        subprocess.run(
            [*compose, "up", "-d", "--wait", *(["temporal"] if legacy else []), "tusd"],
            cwd=root,
            stdout=log,
            stderr=log,
            check=True,
            env=env,
        )
    settings = Settings.load(root)
    engine = engine_for(settings)
    try:
        migrate(engine)
    finally:
        engine.dispose()

    async def namespace():
        from hrs_platform.jobs.worker import connect, register_namespace

        for attempt in range(30):
            try:
                await connect(settings)
                break
            except RuntimeError:
                if attempt == 29:
                    raise
                await asyncio.sleep(1)
        await register_namespace(settings)

    if legacy:
        asyncio.run(namespace())
    print("Databases, migrations and tusd ready; backend=" + ("legacy" if legacy else "codex"))


if __name__ == "__main__":
    bootstrap()
