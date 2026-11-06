"""Durable atomic JSON files and sharing-safe reads of retained snapshots."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4


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
