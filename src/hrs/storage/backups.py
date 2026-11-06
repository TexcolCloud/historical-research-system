"""PostgreSQL dump/restore transport; destinations never overwrite an existing database."""

import os
import re
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from hrs.config import load_environment


def connection_details(settings):
    url = make_url(settings.database_url.get_secret_value())
    env = load_environment(settings.project_root)
    project = env.get("COMPOSE_PROJECT_NAME") or "historical-ingestion-acceptance"
    container = env.get("PLATFORM_POSTGRES_CONTAINER") or f"{project}-postgres-1"
    environment = {**os.environ, "PGPASSWORD": url.password or ""}
    command = ["docker", "exec", "-i", "-e", "PGPASSWORD", container]
    return url, environment, command


def dump_database(settings, name, destination):
    url, environment, command = connection_details(settings)
    with Path(destination).open("wb") as stream:
        result = subprocess.run(
            [
                *command,
                "pg_dump",
                "-U",
                url.username,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                name,
            ],
            stdout=stream,
            check=False,
            stderr=subprocess.PIPE,
            env=environment,
        )
    if result.returncode:
        raise RuntimeError("PostgreSQL dump failed; no backup receipt was committed.")


def restore_database(settings, name, source):
    if not re.fullmatch(r"hrs_restore_[a-z0-9_]{1,45}", name):
        raise ValueError("Restore destinations must be new hrs_restore_* databases.")
    url, environment, command = connection_details(settings)
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        # CREATE fails if the target exists. Never clean or overwrite a live database.
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with Path(source).open("rb") as stream:
        result = subprocess.run(
            [
                *command,
                "pg_restore",
                "-U",
                url.username,
                "--dbname",
                name,
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--single-transaction",
            ],
            stdin=stream,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=environment,
        )
    if result.returncode:
        raise RuntimeError(
            f"Restore failed. The new database {name} is retained for inspection; existing databases are untouched."
        )
