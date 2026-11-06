"""Coordinate stopped services, PostgreSQL, native evidence and an independent S3 snapshot."""

import json
import re
import socket
import subprocess
import tempfile
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from hrs.config import load_environment, resolve_path
from hrs.processing.backup import restore_workspace, snapshot_workspace, verify_workspace_snapshot
from hrs.processing.snapshots import atomic_json
from hrs.processing.store import Store
from hrs.processing.workspace import identity, resolve_workspace, running_workbench
from hrs.storage.backups import dump_database, restore_database
from hrs.storage.object_backup import (
    require_empty_target,
    restore_snapshot,
    verify_snapshot,
    write_snapshot,
)
from hrs.storage.objects import file_digest
from hrs.storage.service import objects_for

LEGACY_DATABASES = {"historical_research_v2", "hrs_temporal_v2", "hrs_temporal_visibility_v2"}


def require_quiesced(settings, store):
    from hrs.runtime import discover_servers, live_servers
    from hrs.search.service import live_brokers

    def belongs_here(row):
        if not row["available"]:
            return False
        root = Path(row["runtime"]["root"]).resolve()
        return root == store.root or root.is_relative_to(settings.project_root.resolve())

    if live_servers(settings.project_root) or any(
        belongs_here(row) for row in discover_servers(settings.project_root)
    ):
        raise RuntimeError("Stop all project API instances, including custom ports, before backup.")
    if live_brokers(settings.project_root):
        raise RuntimeError("Stop the project's retrieval brokers before backup.")
    if running_workbench(store):
        raise RuntimeError("Stop this workspace's independent workbench/executor before backup.")
    env = load_environment(settings.project_root)
    project = env.get("COMPOSE_PROJECT_NAME") or "hrs-platform"
    containers = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=True
    )
    if f"{project}-tusd-1" in set(containers.stdout.splitlines()):
        raise RuntimeError("Stop uploads before backup.")
    from urllib.parse import urlparse

    upload = urlparse(settings.tus_control_url)
    if upload.scheme not in {"http", "https"}:
        raise RuntimeError("Use an HTTP(S) upload endpoint for coordinated backup.")
    if upload.hostname in {"127.0.0.1", "localhost"}:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            port = upload.port or (443 if upload.scheme == "https" else 80)
            if probe.connect_ex((upload.hostname, port)) == 0:
                raise RuntimeError(
                    "The configured upload service is still listening; stop it before backup."
                )
    else:
        raise RuntimeError("Use a stopped loopback upload endpoint for coordinated backup.")
    if store.checkpoints.ledger:
        now = time.time()
        if any(
            row["state"] in {"starting", "idle", "busy", "cooldown"} and row["expires_at"] > now
            for row in store.checkpoints.ledger.sessions()
        ):
            raise RuntimeError("Native worker sessions are still active; drain them before backup.")


def _reference(path):
    digest, size = file_digest(path)
    return {"sha256": digest, "byte_length": size}


def _native_runs(settings):
    """Read the SQL dependencies without adopting or rewriting historical mappings."""
    from sqlalchemy import or_, select

    from hrs.storage import models as db
    from hrs.storage.db import engine_for

    engine = engine_for(settings)
    try:
        with engine.connect().execution_options(postgresql_readonly=True) as connection:
            return list(
                connection.execute(
                    select(db.runs.c.id, db.runs.c.source, db.runs.c.result)
                    .join(db.books)
                    .where(
                        db.books.c.state != "deleted",
                        or_(
                            db.runs.c.result["processing_backend"].astext == "codex",
                            db.runs.c.result["codex_document_id"].astext.is_not(None),
                            db.runs.c.result["native_attach_document_id"].astext.is_not(None),
                        ),
                    )
                    .order_by(db.runs.c.id)
                ).mappings()
            )
    finally:
        engine.dispose()


def _native_stores(settings, native_roots):
    primary = resolve_workspace(settings.codex_root)
    roots = {primary}
    for selected in native_roots or ():
        root = resolve_path(settings.project_root, selected)
        if not root.is_dir() or not (root / ".workspace.json").is_file():
            raise ValueError(f"Additional native root has no retained workspace identity: {root}")
        roots.add(root)
    return primary, [Store(root) for root in sorted(roots, key=lambda root: str(root).casefold())]


def _native_coverage(settings, stores, primary):
    workspaces, documents = {}, {}
    for store in stores:
        workspace_id = identity(store)["workspace_id"]
        if not isinstance(workspace_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", workspace_id
        ):
            raise ValueError("Native workspace identity is not a portable directory identifier.")
        if workspace_id.casefold() in {saved.casefold() for saved in workspaces}:
            raise ValueError(
                f"Different native roots have duplicate workspace identity: {workspace_id}"
            )
        workspaces[workspace_id] = {
            "workspace_id": workspace_id,
            "document_ids": store.checkpoints.ids(),
            "primary": store.root == primary,
            "store": store,
        }
        documents[workspace_id] = {
            document_id: store.status(document_id) for document_id in store.checkpoints.ids()
        }
    missing = []
    for run in _native_runs(settings):
        result = run["result"] or {}
        workspace_id = result.get("codex_workspace_id")
        document_id = result.get("codex_document_id") or result.get("native_attach_document_id")
        source_sha = (run["source"] or {}).get("sha256")
        if workspace_id:
            found = documents.get(workspace_id)
            document = found.get(document_id) if found is not None and document_id else None
            if found is None or (
                document_id
                and (
                    document is None
                    or (source_sha and document.get("source", {}).get("sha256") != source_sha)
                )
            ):
                missing.append(
                    f"workspace {workspace_id}, document {document_id or '(not attached)'}"
                )
        elif document_id:
            matches = [
                saved_id
                for saved_id, saved in documents.items()
                if document_id in saved
                and source_sha
                and saved[document_id].get("source", {}).get("sha256") == source_sha
            ]
            if len(matches) != 1:
                missing.append(
                    f"legacy run {run['id']}, document {document_id}: "
                    f"{len(matches)} matching supplied workspaces "
                    f"({', '.join(matches) or 'none'}); exactly one is required"
                )
    if missing:
        raise ValueError(
            "Backup native workspace coverage is incomplete or ambiguous. Supply every required "
            "root with --additional-native-root: " + "; ".join(missing)
        )
    return sorted(workspaces.values(), key=lambda item: (not item["primary"], item["workspace_id"]))


def backup(settings, *, destination=None, native_roots=None):
    """Create a new local backup that does not depend on the source S3 bucket surviving."""
    from sqlalchemy.engine import make_url

    from hrs.runtime import lifecycle_lock

    primary, stores = _native_stores(settings, native_roots)
    destination = (
        Path(destination).expanduser().resolve()
        if destination
        else (
            settings.project_root
            / "backups"
            / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
        )
    )
    with lifecycle_lock(settings.project_root), ExitStack() as locks:
        for store in stores:
            locks.enter_context(store.lock)
        for store in stores:
            require_quiesced(settings, store)
        workspaces = _native_coverage(settings, stores, primary)
        destination.mkdir(parents=True, exist_ok=False)
        incomplete = destination / ".incomplete.json"
        atomic_json(incomplete, {"state": "incomplete", "created_at": time.time()})
        try:
            natives = []
            for number, workspace in enumerate(workspaces):
                name = "native-workspace.zip" if number == 0 else f"native-workspace-{number}.zip"
                snapshot_workspace(workspace["store"], destination / name)
                verify_workspace_snapshot(destination / name)
                natives.append(
                    {k: workspace[k] for k in ("workspace_id", "document_ids", "primary")}
                    | {"archive": name}
                )
            dump_database(
                settings,
                make_url(settings.database_url.get_secret_value()).database,
                destination / "database.dump",
            )
            objects = write_snapshot(objects_for(settings), destination / "objects.zip")
            verify_snapshot(destination / "objects.zip")
            _native_coverage(settings, stores, primary)
            manifest = {
                "schema_version": 3,
                "kind": "hrs-complete-backup",
                "complete": True,
                "created_at": datetime.now(UTC).isoformat(),
                "database": make_url(settings.database_url.get_secret_value()).database,
                "workspace_id": natives[0]["workspace_id"],
                "native_document_ids": natives[0]["document_ids"],
                "native_workspaces": natives,
                "object_store": objects,
                "files": {
                    name: _reference(destination / name)
                    for name in (
                        "database.dump",
                        "objects.zip",
                        *(item["archive"] for item in natives),
                    )
                },
                "scope": "SQL, native originals/checkpoints and independent complete hrs/v2/ object bytes.",
                "private_configuration_included": False,
            }
            atomic_json(destination / "manifest.json", manifest)
            incomplete.unlink()
        except BaseException as error:
            atomic_json(incomplete, {"state": "incomplete", "error_type": type(error).__name__})
            raise
    return {
        "manifest": str(destination / "manifest.json"),
        "complete": True,
        "objects": objects["objects"],
        "native_documents": sum(len(item["document_ids"]) for item in natives),
        "native_workspaces": len(natives),
    }


def _manifest_workspaces(manifest):
    entries = manifest.get("native_workspaces")
    if entries is None:
        entries = [
            {
                "workspace_id": manifest["workspace_id"],
                "document_ids": manifest["native_document_ids"],
                "primary": True,
                "archive": "native-workspace.zip",
            }
        ]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Backup has no native workspace manifest.")
    identities, archives = set(), set()
    for number, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("Backup native workspace mapping must contain objects.")
        expected = "native-workspace.zip" if number == 0 else f"native-workspace-{number}.zip"
        workspace_id = entry.get("workspace_id")
        if (
            not isinstance(workspace_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_id)
            or workspace_id.casefold() in identities
            or entry.get("archive") != expected
            or entry.get("primary") is not (number == 0)
            or not isinstance(entry.get("document_ids"), list)
        ):
            raise ValueError("Backup native workspace mapping is invalid or duplicated.")
        identities.add(workspace_id.casefold())
        archives.add(entry["archive"])
    if (
        entries[0]["workspace_id"] != manifest["workspace_id"]
        or entries[0]["document_ids"] != manifest["native_document_ids"]
    ):
        raise ValueError("Backup primary workspace differs from its compatibility fields.")
    return entries, archives


def _portable_manifest(reference):
    path = (
        Path(reference["manifest"] if isinstance(reference, dict) else reference)
        .expanduser()
        .resolve()
    )
    manifest = json.loads(path.read_text("utf-8-sig"))
    if (
        manifest.get("kind") != "hrs-complete-backup"
        or manifest.get("schema_version") != 3
        or manifest.get("complete") is not True
    ):
        raise ValueError("The source is not a complete independent backup.")
    workspaces, archives = _manifest_workspaces(manifest)
    if set(manifest["files"]) != {"database.dump", "objects.zip", *archives}:
        raise ValueError("Backup file set differs from the supported manifest.")
    for name, expected in manifest["files"].items():
        candidate = (path.parent / name).resolve()
        if not candidate.is_relative_to(path.parent) or _reference(candidate) != expected:
            raise ValueError("Backup file hash or length mismatch.")
    for workspace in workspaces:
        source = path.parent / workspace["archive"]
        native = verify_workspace_snapshot(source)
        with ZipFile(source) as archive:
            saved_identity = json.loads(archive.read(".workspace.json"))
        if (
            saved_identity.get("workspace_id") != workspace["workspace_id"]
            or native["documents"] != workspace["document_ids"]
        ):
            raise ValueError("Backup native archive differs from its workspace mapping.")
    verify_snapshot(path.parent / "objects.zip")
    return path.parent, manifest


def restore(settings, reference, suffix, *, native_destination=None):
    """Restore an independent backup to a new database, absent workspace and empty bucket."""
    if not re.fullmatch(r"[a-z0-9_]{1,20}", suffix):
        raise ValueError("Use a short lowercase restore suffix.")
    if isinstance(reference, dict) and "key" in reference:
        return restore_legacy(settings, reference, suffix, native_destination=native_destination)
    folder, manifest = _portable_manifest(reference)
    if native_destination is None:
        raise ValueError("Supply an absent native restore directory.")
    selected = Path(native_destination).expanduser()
    native_parent = selected.resolve()
    if selected.is_symlink() or native_parent.exists():
        raise ValueError("Supply an absent native restore directory.")
    workspaces, _ = _manifest_workspaces(manifest)
    destinations = {
        item["workspace_id"]: (
            native_parent if len(workspaces) == 1 else native_parent / item["workspace_id"]
        )
        for item in workspaces
    }
    primary_destination = destinations[workspaces[0]["workspace_id"]]
    storage = objects_for(settings)
    require_empty_target(storage, create=True)
    settings.cache_root.mkdir(parents=True, exist_ok=True)
    target = f"hrs_restore_{suffix}_0"
    journal = settings.cache_root / f"restore-{target}-{uuid4().hex[:8]}.json"
    progress = {
        "state": "restoring",
        "phase": "database",
        "database": target,
        "bucket": storage.bucket,
        "native_workspace": str(primary_destination),
        "native_workspaces": {key: str(value) for key, value in destinations.items()},
        "restored_native_workspaces": [],
        "backup_manifest": str(folder / "manifest.json"),
        "created_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(journal, progress)
    try:
        restore_database(settings, target, folder / "database.dump")
        progress["phase"] = "objects"
        atomic_json(journal, progress)
        objects = restore_snapshot(
            storage, folder / "objects.zip", temporary_root=settings.cache_root
        )
        progress["phase"] = "native_workspace"
        atomic_json(journal, progress)
        if len(workspaces) > 1:
            native_parent.mkdir(parents=True, exist_ok=False)
        for workspace in workspaces:
            workspace_id = workspace["workspace_id"]
            progress["workspace_id"] = workspace_id
            atomic_json(journal, progress)
            restore_workspace(folder / workspace["archive"], destinations[workspace_id])
            progress["restored_native_workspaces"].append(workspace_id)
            atomic_json(journal, progress)
        progress["phase"] = "native_coverage"
        atomic_json(journal, progress)
        from pydantic import SecretStr
        from sqlalchemy.engine import make_url

        restored_settings = settings.model_copy(
            update={
                "database_url": SecretStr(
                    make_url(settings.database_url.get_secret_value())
                    .set(database=target)
                    .render_as_string(hide_password=False)
                )
            }
        )
        _native_coverage(
            restored_settings,
            [Store(destination) for destination in destinations.values()],
            primary_destination,
        )
    except BaseException as error:
        atomic_json(
            journal, {**progress, "state": "incomplete", "error_type": type(error).__name__}
        )
        raise RuntimeError(
            f"Restore stopped during {progress['phase']}. New targets are retained for inspection; "
            f"existing data is untouched. Recovery details: {journal}"
        ) from error
    atomic_json(journal, {**progress, "state": "complete"})
    return {
        manifest["database"]: target,
        "native_workspace": str(primary_destination),
        "native_workspaces": {key: str(value) for key, value in destinations.items()},
        "object_store": objects,
        "recovery_record": str(journal),
    }


def restore_legacy(settings, manifest_reference, suffix, *, native_destination=None):
    """Historical SQL/native receipts remain readable while their original object bucket is retained."""
    storage = objects_for(settings)
    manifest = json.loads(storage.read_bytes(manifest_reference))
    names = set(manifest.get("databases", {}))
    native = (
        manifest.get("schema_version") == 2 and manifest.get("native_workspace") and len(names) == 1
    )
    if manifest.get("kind") != "quiesced-platform-databases" or not (
        names == LEGACY_DATABASES or native
    ):
        raise ValueError("Unsupported historical coordinated backup.")
    if manifest.get("native_workspace") and (
        native_destination is None or Path(native_destination).exists()
    ):
        raise ValueError("Supply an absent native restore directory.")
    settings.cache_root.mkdir(parents=True, exist_ok=True)
    restored = {}
    with tempfile.TemporaryDirectory(
        prefix="database-restore-", dir=settings.cache_root
    ) as temporary:
        for number, (original, ref) in enumerate(manifest["databases"].items()):
            source = storage.materialize(ref, Path(temporary) / "database.dump")
            target = f"hrs_restore_{suffix}_{number}"
            restore_database(settings, target, source)
            restored[original] = target
        if manifest.get("native_workspace"):
            source = storage.materialize(
                manifest["native_workspace"], Path(temporary) / "native.zip"
            )
            restored["native_workspace"] = str(restore_workspace(source, native_destination))
    return restored
