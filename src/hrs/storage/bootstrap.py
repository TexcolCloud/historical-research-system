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
    settings = Settings.load(root)
    targets = [make_url(settings.database_url.get_secret_value())]
    if env.get("PLATFORM_TEST_DATABASE_URL"):
        targets.append(make_url(env["PLATFORM_TEST_DATABASE_URL"]))
    local_names = {
        target.database
        for target in targets
        if target.database and (target.host, target.port or 5432) == (url.host, url.port or 5432)
    }
    with psycopg.connect(admin, autocommit=True) as connection:
        for name in sorted(local_names):
            if not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = engine_for(settings)
    try:
        migrate(engine)
    finally:
        engine.dispose()
