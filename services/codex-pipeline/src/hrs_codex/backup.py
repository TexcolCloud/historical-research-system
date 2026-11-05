"""Quiesced native snapshots; restore only into an absent, isolated directory."""

import json
import time
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from hrs_codex.hashing import digest, file_digest


def snapshot_workspace(store, destination: Path) -> dict:
    with store.lock:
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
            for path in (store.root / "documents").rglob("*")
            if path.is_file() and not path.name.startswith(".") and not path.name.endswith(".lock")
        )
        paths += [
            path
            for name in (".workspace.json", ".workflow-test-authorization.json")
            if (path := store.root / name).is_file()
        ]
        manifest = {
            "schema_version": 1,
            "kind": "quiesced-codex-workspace",
            "documents": [document["id"] for document in documents],
            "files": {path.relative_to(store.root).as_posix(): file_digest(path) for path in paths},
        }
        # Exclusive creation prevents accidentally replacing an existing backup.
        with ZipFile(destination, "x", compression=ZIP_STORED) as archive:
            for path in paths:
                archive.write(path, path.relative_to(store.root).as_posix())
            archive.writestr("backup-manifest.json", json.dumps(manifest, ensure_ascii=False))
        return manifest


def restore_workspace(source: Path, destination: Path) -> Path:
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Native restore destination must not exist")
    with ZipFile(source) as archive:
        manifest = json.loads(archive.read("backup-manifest.json"))
        if (
            manifest.get("kind") != "quiesced-codex-workspace"
            or manifest.get("schema_version") != 1
        ):
            raise ValueError("Unsupported native backup manifest")
        if len(archive.namelist()) != len(manifest["files"]) + 1 or set(archive.namelist()) != {
            "backup-manifest.json",
            *manifest["files"],
        }:
            raise ValueError("Native backup members differ from the manifest")
        for name, expected in manifest["files"].items():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination) or target == destination:
                raise ValueError("Native backup path escapes restore directory")
            if digest(archive.read(name)) != expected:
                raise ValueError("Native backup evidence hash mismatch")
        destination.mkdir(parents=True)
        for name in manifest["files"]:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    return destination
