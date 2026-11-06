"""Quiesced native snapshots; restore only into an absent, isolated directory."""

import hashlib
import json
import shutil
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, ZipFile

from hrs.processing.hashing import file_digest


def snapshot_workspace(store, destination: Path) -> dict:
    with store.lock, TemporaryDirectory(prefix="hrs-ledger-backup-") as temporary:
        documents = [store.checkpoints.read(row["id"]) for row in store.list_documents()]
        for document in documents:
            if document.get("run_request", {}).get("state") == "running" or any(
                task["state"] == "leased"
                and task.get("claim", {}).get("expires_at", 0) > time.time()
                for task in document["tasks"]
            ):
                raise ValueError("Stop the native executor before creating a workspace snapshot")
            store.verify_evidence(document)
        paths = sorted(
            path
            for document in documents
            for path in store.document_directory(document["id"]).rglob("*")
            if path.is_file() and not path.name.startswith(".") and not path.name.endswith(".lock")
        )
        paths += [
            path
            for name in (
                ".workspace.json",
                ".workflow-test-authorization.json",
                "execution-policy.json",
            )
            if (path := store.root / name).is_file()
        ]
        files = {path.relative_to(store.root).as_posix(): path for path in paths}
        if store.checkpoints.ledger:
            database = Path(temporary) / "ledger.sqlite3"
            store.checkpoints.ledger.backup(database)
            files["ledger.sqlite3"] = database
        manifest = {
            "schema_version": 2 if store.checkpoints.ledger else 1,
            "kind": "quiesced-codex-workspace",
            "documents": [document["id"] for document in documents],
            "files": {name: file_digest(path) for name, path in files.items()},
        }
        # Exclusive creation prevents accidentally replacing an existing backup.
        with ZipFile(destination, "x", compression=ZIP_STORED) as archive:
            for name, path in files.items():
                archive.write(path, name)
            archive.writestr("backup-manifest.json", json.dumps(manifest, ensure_ascii=False))
        return manifest


def verify_workspace_snapshot(source: Path) -> dict:
    """Validate every retained byte before a coordinated restore creates targets."""
    with ZipFile(source) as archive:
        manifest = json.loads(archive.read("backup-manifest.json"))
        if manifest.get("kind") != "quiesced-codex-workspace" or manifest.get(
            "schema_version"
        ) not in {1, 2}:
            raise ValueError("Unsupported native backup manifest")
        if len(archive.namelist()) != len(manifest["files"]) + 1 or set(archive.namelist()) != {
            "backup-manifest.json",
            *manifest["files"],
        }:
            raise ValueError("Native backup members differ from the manifest")
        for name, expected in manifest["files"].items():
            member = Path(name)
            if member.is_absolute() or ".." in member.parts or "\\" in name or ":" in name:
                raise ValueError("Native backup path escapes restore directory")
            measured = hashlib.sha256()
            with archive.open(name) as stream:
                while chunk := stream.read(1024 * 1024):
                    measured.update(chunk)
            if measured.hexdigest() != expected:
                raise ValueError("Native backup evidence hash mismatch")
        return manifest


def restore_workspace(source: Path, destination: Path) -> Path:
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Native restore destination must not exist")
    manifest = verify_workspace_snapshot(source)
    destination.mkdir(parents=True)
    with ZipFile(source) as archive:
        for name in manifest["files"]:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as stream, target.open("xb") as output:
                shutil.copyfileobj(stream, output, length=1024 * 1024)
    return destination
