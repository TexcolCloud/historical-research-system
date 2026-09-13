"""Cancel an owned interpreter tree without starting OCR or touching GPU processes."""

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hrs_platform.activities import Activities


@pytest.mark.skipif(os.name != "nt", reason="Windows venv interpreter process ownership")
def test_conversion_cancellation_reaps_its_owned_interpreter_child(platform, tmp_path, monkeypatch):
    settings, engine = platform
    pid_file = tmp_path / "owned-child.json"
    code = (
        "import json,subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        f"Path({str(pid_file)!r}).write_text(json.dumps(child.pid)); time.sleep(120)"
    )
    real_popen = subprocess.Popen
    owned = []

    def launch(command, **kwargs):
        if Path(command[0]).stem.lower() == "taskkill":
            return real_popen(command, **kwargs)
        process = real_popen([sys.executable, "-c", code], **kwargs)
        owned.append(process)
        return process

    def heartbeat(*_):
        if pid_file.exists():
            raise InterruptedError("technical Temporal cancellation")

    monkeypatch.setattr("hrs_platform.activities.subprocess.Popen", launch)
    monkeypatch.setattr("hrs_platform.activities.activity.heartbeat", heartbeat)
    with pytest.raises(InterruptedError, match="technical Temporal cancellation"):
        Activities(settings, engine)._extract(
            "fixture", tmp_path / "source.pdf", tmp_path / "conversion", phase="review"
        )
    assert len(owned) == 1 and owned[0].poll() is not None
    child_pid = json.loads(pid_file.read_text())
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, child_pid)
    if handle:
        try:
            exit_code = ctypes.c_ulong()
            assert kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(exit_code))
            assert exit_code.value != 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
