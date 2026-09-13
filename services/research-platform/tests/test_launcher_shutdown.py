import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest


@pytest.fixture
def launcher():
    spec = importlib.util.spec_from_file_location(
        "hrs_v2_launcher", Path(__file__).resolve().parents[3] / "scripts/hrs_v2.py"
    )
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    return launcher


def test_port_guard_waits_for_release_but_never_takes_an_owned_listener(launcher):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match="already has an owner"):
            launcher.available(port, wait=.05)
        assert listener.fileno() != -1
        release = threading.Timer(.1, listener.close)
        release.start()
        try:
            launcher.available(port, wait=2)
        finally:
            release.join()


@pytest.mark.skipif(os.name != "nt", reason="Windows venv launcher and CTRL_BREAK ownership")
def test_stop_cleans_owned_descendants_even_when_launcher_exits_first(tmp_path, launcher):
    pid_file = tmp_path / "descendant.json"
    child_code = "import signal,time; signal.signal(signal.SIGBREAK, signal.SIG_IGN); time.sleep(120)"
    parent_code = (
        "import subprocess,signal,sys,json,time; from pathlib import Path; "
        "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"Path({str(pid_file)!r}).write_text(json.dumps(child.pid)); time.sleep(120)"
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    captured = []
    try:
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pid_file.exists()
        captured = psutil.Process(parent.pid).children(recursive=True)
        assert captured
        launcher.stop_child(parent, timeout=0.3)
        assert parent.poll() is not None
        assert all(not child.is_running() for child in captured)
        assert unrelated.poll() is None
    finally:
        for child in captured:
            if child.is_running():
                child.kill()
        for owned in (parent, unrelated):
            if owned.poll() is None:
                owned.kill()
            owned.wait()
