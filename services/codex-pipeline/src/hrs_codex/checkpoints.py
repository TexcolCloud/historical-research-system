"""Atomic document snapshots and verified compare-before-commit transactions."""

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from filelock import FileLock


def read_snapshot(path: Path) -> bytes:
    """Windows replacement can briefly deny an open; never return a stale snapshot."""
    for attempt in range(50):
        try:
            return path.read_bytes()
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.01)
    raise AssertionError("Unreachable snapshot retry state")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers may briefly hold a sharing handle to the old snapshot.
        for attempt in range(50):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.01)
    finally:
        temporary.unlink(missing_ok=True)


class Checkpoints:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # One process lock serializes local document mutations; split by
        # document only if measured local queue contention warrants it.
        self.lock = FileLock(self.root / ".store.lock", timeout=30)

    def directory(self, document_id: str) -> Path:
        if len(document_id) != 24 or any(c not in "0123456789abcdef" for c in document_id):
            raise ValueError("Invalid document ID")
        return self.root / "documents" / document_id

    def read(self, document_id: str) -> dict:
        return json.loads(read_snapshot(self.directory(document_id) / "document.json"))

    def write(self, doc: dict) -> None:
        atomic_json(self.directory(doc["id"]) / "document.json", doc)

    @contextmanager
    def validated(self, document_id, validate):
        """Validate an immutable snapshot without blocking readers or lease heartbeats."""
        path = self.directory(document_id) / "document.json"
        for _ in range(8):
            snapshot = read_snapshot(path)
            doc = json.loads(snapshot)
            validate(doc)
            with self.lock:
                if read_snapshot(path) != snapshot:
                    continue
                yield doc
                return
        raise ValueError(
            "Document changed during evidence validation; retry with the current snapshot"
        )

    def evidence_path(self, doc: dict, relative: str) -> Path:
        directory = self.directory(doc["id"]).resolve()
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Evidence path escapes document directory")
        return path
