"""Frozen UTF-8 and compact JSON digests; no transport or storage dependencies."""

import hashlib
import json
from pathlib import Path


def digest(value: bytes | str | dict | list) -> str:
    if not isinstance(value, bytes):
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
