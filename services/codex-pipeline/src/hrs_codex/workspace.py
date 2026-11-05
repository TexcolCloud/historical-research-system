"""Shared workspace selection and the lifecycle of its local workbench."""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4

from filelock import FileLock

from hrs_codex.checkpoints import atomic_json
from hrs_codex.store import Store

PROJECT = Path(__file__).resolve().parents[4]
CONFIG = PROJECT / ".codex/historical-workspace.json"


def resolve_workspace(explicit: Path | str | None = None) -> Path:
    selected = explicit or os.environ.get("HRS_CODEX_ROOT")
    if selected:
        return Path(selected).expanduser().resolve()
    config = (
        json.loads(CONFIG.read_text(encoding="utf-8"))
        if CONFIG.exists()
        else {"root": "state/codex"}
    )
    return (PROJECT / config["root"]).resolve()


def identity(store: Store) -> dict:
    with store.lock:
        path = store.root / ".workspace.json"
        if not path.exists():
            atomic_json(path, {"schema_version": 1, "workspace_id": uuid4().hex})
        record = json.loads(path.read_text(encoding="utf-8"))
    return {
        **record,
        "root": str(store.root),
        "configured_root": str(resolve_workspace()),
        "is_default": store.root == resolve_workspace(),
    }


def change_token(store: Store) -> str:
    """Cheap UI change detection; content approval still uses evidence hashes."""
    with store.lock:
        rows = []
        for path in sorted((store.root / "documents").glob("*/document.json")):
            stat = path.stat()
            rows.append((path.parent.name, stat.st_mtime_ns, stat.st_size))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def running_workbench(store: Store) -> dict | None:
    path = store.root / ".workbench.json"
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        parsed = urlparse(record["url"])
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            return None
        expected = identity(store)["workspace_id"]
        if record.get("workbench_protocol", 1) < 2:
            # Reuse an older live executor until it is idle and can be upgraded;
            # starting a second dashboard must not fork its active work.
            with urlopen(record["url"], timeout=2) as response:
                body = response.read().decode("utf-8")
            return record if record["workspace_id"] == expected and expected in body else None
        # The session token stays in this workspace; never forward it remotely.
        health_url = parsed._replace(path="/health").geturl()
        with urlopen(health_url, timeout=2) as response:
            if response.status != 200:
                return None
            health = json.load(response)
        return (
            record
            if (
                record["workspace_id"] == health["workspace_id"] == expected
                and health["executor_running"]
            )
            else None
        )
    except (OSError, ValueError, KeyError):
        return None


def ensure_workbench(store: Store) -> dict:
    """Start one owned loopback workbench/runner for this exact workspace."""
    with FileLock(store.root / ".workbench-start.lock", timeout=30):
        existing = running_workbench(store)
        if existing:
            return existing
        logs = store.root / "workbench.log"
        with logs.open("ab") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "hrs_codex.cli",
                    "--root",
                    str(store.root),
                    "workbench",
                    "--port",
                    "0",
                ],
                cwd=PROJECT,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            record = running_workbench(store)
            if record:
                return record
            if process.poll() is not None:
                raise RuntimeError(f"Workbench exited {process.returncode}; inspect {logs}")
            time.sleep(0.1)
        process.terminate()
        raise RuntimeError(f"Workbench startup timed out; inspect {logs}")
