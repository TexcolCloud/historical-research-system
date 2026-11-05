"""Workspace execution limits, independent of frozen content acceptance policy."""

import json
import time
from pathlib import Path

from hrs_codex.checkpoints import atomic_json, read_snapshot

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


def execution_policy(root):
    path = Path(root) / "execution-policy.json"
    values = json.loads(read_snapshot(path)) if path.exists() else {}
    result = validate(values)
    pressure = Path(root) / "execution-pressure.json"
    if pressure.exists():
        state = json.loads(read_snapshot(pressure))
        if state["until"] > time.time():
            result["capacity"] = min(result["capacity"], state["capacity"])
    return result


def configure_execution(root, *, capacity, batch_pages=8, overlap_reading=True):
    values = validate(
        {"capacity": capacity, "batch_pages": batch_pages, "overlap_reading": overlap_reading}
    )
    atomic_json(Path(root) / "execution-policy.json", values)
    return values


def record_pressure(root, code):
    """Called under the workspace command lock; reduce new admissions across books."""
    current = execution_policy(root)
    if code in {"rate_limit", "429", "service_unavailable"}:
        atomic_json(
            Path(root) / "execution-pressure.json",
            {
                "capacity": max(2, current["capacity"] // 2),
                "until": time.time() + 300,
                "code": code,
            },
        )
