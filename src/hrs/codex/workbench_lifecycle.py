"""Own the loopback workbench process without coupling processing to a transport."""

import os
import subprocess
import sys
import time

from filelock import FileLock

from hrs.config import project_root
from hrs.processing.store import Store
from hrs.processing.workspace import running_workbench

PROJECT = project_root()

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
                    "hrs",
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
