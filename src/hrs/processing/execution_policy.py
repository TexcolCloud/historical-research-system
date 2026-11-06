"""Workspace execution limits, independent of frozen content acceptance policy."""

import json
from pathlib import Path

from hrs.processing.checkpoints import atomic_json, read_snapshot

DEFAULTS = {"capacity": 7, "batch_pages": 2, "overlap_reading": False}
MAX_CAPACITY = 64


def validate(values):
    result = {**DEFAULTS, **values}
    if set(result) != set(DEFAULTS):
        raise ValueError("Unknown execution-policy field")
    if type(result["capacity"]) is not int or not 2 <= result["capacity"] <= MAX_CAPACITY:
        raise ValueError("Execution capacity must be an integer between 2 and 64")
    if type(result["batch_pages"]) is not int or not 1 <= result["batch_pages"] <= 8:
        raise ValueError("Warm worker batch must be between 1 and 8 pages")
    if type(result["overlap_reading"]) is not bool:
        raise ValueError("overlap_reading must be boolean")
    return result


def configured_policy(root):
    path = Path(root) / "execution-policy.json"
    values = json.loads(read_snapshot(path)) if path.exists() else {}
    return validate(values)


def execution_policy(root):
    result = configured_policy(root)
    if (Path(root) / "ledger.sqlite3").exists():
        from hrs.processing.runtime_control import RuntimeControl

        account = RuntimeControl(root).snapshot()["account"]
        result["capacity"] = min(result["capacity"], account["limit"])
    return result


def configure_execution(root, *, capacity, batch_pages=8, overlap_reading=True):
    values = validate(
        {"capacity": capacity, "batch_pages": batch_pages, "overlap_reading": overlap_reading}
    )
    atomic_json(Path(root) / "execution-policy.json", values)
    return values


def record_pressure(root, code):
    """Called under the workspace command lock; reduce new admissions across books."""
    if (Path(root) / "ledger.sqlite3").exists():
        from uuid import uuid4

        from hrs.processing.runtime_control import RuntimeControl

        return RuntimeControl(root).failure(code, "legacy:" + uuid4().hex, scope="account")
    raise ValueError("Runtime control requires a writable ledger workspace")
