"""Use a disposable process to check broker ownership; never start or stop real models."""

import importlib.util
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import pytest


def test_cancel_only_owns_matching_task_and_rejects_late_requests(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / "scripts/local_vision.py"
    spec = importlib.util.spec_from_file_location("task_cancel_broker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    broker = module.Vision()
    broker.folder = tmp_path
    broker.active_task = str(uuid4())
    broker.last_task = broker.active_task
    broker.log = (tmp_path / "dummy.log").open("wb")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=broker.log,
        stderr=broker.log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    broker.process = process
    try:
        other = str(uuid4())
        broker.cancel_tasks([other])
        assert process.poll() is None
        broker.cancel_tasks([broker.active_task])
        assert process.poll() is not None
        assert broker.process is None
        monkeypatch.setattr(module, "gpu_lease", nullcontext)
        with pytest.raises(RuntimeError, match="book_task_cancelled"):
            broker.complete({"model": module.MODEL, "hrs_task_id": other, "messages": []})
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        broker.log.close() if broker.log else None
