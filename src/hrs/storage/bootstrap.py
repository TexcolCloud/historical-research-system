"""Create dedicated databases and apply the retained migration chain."""

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from hrs.config import Settings, load_environment, project_root
from hrs.storage.db import engine_for, migrate


def bootstrap(root=None):
    root = project_root(root)
    env = load_environment(root)
    if not env.get("INGEST_DATABASE_URL"):
        raise ValueError("Set INGEST_DEV_DB_PASSWORD before initialization.")
    url = make_url(env["INGEST_DATABASE_URL"])
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        for name in ("historical_research_v2", "historical_research_v2_test"):
            if not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = engine_for(Settings.load(root))
    try:
        migrate(engine)
    finally:
        engine.dispose()
