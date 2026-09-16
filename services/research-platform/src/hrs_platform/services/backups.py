"""Official PostgreSQL dumps stored in S3; restore only into new database names.

This protects database recovery while the immutable S3 bucket is retained. It is
not a substitute for an independent backup of the S3 storage volume or bucket.
"""

import json
import os
import re
import socket
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from hrs_platform.services.storage import objects_for

DATABASES = ("historical_research_v2", "hrs_temporal_v2", "hrs_temporal_visibility_v2")


def connection_details(settings):
    url = make_url(settings.database_url.get_secret_value())
    container = os.environ.get("PLATFORM_POSTGRES_CONTAINER", "historical-ingestion-acceptance-postgres-1")
    environment = {**os.environ, "PGPASSWORD": url.password or ""}
    command = ["docker", "exec", "-i", "-e", "PGPASSWORD", container]
    return url, environment, command


def dump_database(settings, name, destination):
    url, environment, command = connection_details(settings)
    with Path(destination).open("wb") as stream:
        result = subprocess.run(
            [*command, "pg_dump", "-U", url.username, "--format=custom", "--no-owner", "--no-acl", name],
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
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
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


def require_quiesced():
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=True
    )
    active = set(result.stdout.splitlines())
    if active & {f"hrs-platform-{name}-1" for name in ("api", "cpu-worker", "temporal", "tusd")}:
        raise RuntimeError("Stop the V2 application, uploads and Temporal before a coordinated backup.")
    for port in (18160, 18170):
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Drain the host API/GPU launcher before a coordinated backup.")


def backup(settings):
    require_quiesced()
    storage = objects_for(settings)
    settings.cache_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="database-backup-", dir=settings.cache_root) as folder:
        references = {}
        for name in DATABASES:
            path = Path(folder) / (name + ".dump")
            dump_database(settings, name, path)
            references[name] = storage.put_file(path, "application/vnd.postgresql.dump")
        manifest = {
            "schema_version": 1,
            "kind": "quiesced-platform-databases",
            "created_at": datetime.now(UTC).isoformat(),
            "databases": references,
            "required_object_store": {
                "bucket": settings.s3_bucket,
                "prefix": "hrs/v2/",
                "must_be_retained": True,
            },
            "scope": "Database recovery only; independent S3 volume/bucket backup is required for storage disaster recovery.",
        }
        return storage.put_bytes(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))


def restore(settings, manifest_reference, suffix):
    if not re.fullmatch(r"[a-z0-9_]{1,20}", suffix):
        raise ValueError("Use a short lowercase restore suffix.")
    storage = objects_for(settings)
    manifest = json.loads(storage.read_bytes(manifest_reference))
    if manifest.get("kind") != "quiesced-platform-databases" or set(manifest["databases"]) != set(DATABASES):
        raise ValueError("The manifest is not a complete coordinated platform database backup.")
    settings.cache_root.mkdir(parents=True, exist_ok=True)
    restored = {}
    with tempfile.TemporaryDirectory(prefix="database-restore-", dir=settings.cache_root) as folder:
        for number, (original, reference) in enumerate(manifest["databases"].items()):
            path = storage.materialize(reference, Path(folder) / "database.dump")
            destination = f"hrs_restore_{suffix}_{number}"
            restore_database(settings, destination, path)
            restored[original] = destination
    return restored
