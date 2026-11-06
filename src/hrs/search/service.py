"""Own a managed retrieval broker, or connect to an explicitly external service."""

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4

import psutil

from hrs.config import load_environment

SERVICE = "hrs-retrieval"
PROTOCOL = 1


def health_url(endpoint):
    parsed = urlparse(endpoint)
    return parsed._replace(
        path=parsed.path.rsplit("/", 1)[0] + "/health", query="", fragment=""
    ).geturl()


def broker_health(endpoint, *, timeout=1.5):
    with urlopen(health_url(endpoint), timeout=timeout) as response:
        value = json.load(response)
    if (
        not isinstance(value, dict)
        or value.get("service") != SERVICE
        or value.get("protocol") != PROTOCOL
    ):
        raise ValueError("The retrieval endpoint is occupied by an incompatible service")
    if value.get("closing"):
        raise RuntimeError("The retrieval service is stopping")
    return value


def live_brokers(project):
    """Read only receipts whose exact process identity is still alive."""
    project = Path(project).resolve()
    result = []
    for path in sorted((project / ".cache/platform-diagnostics/brokers").glob("*.json")):
        try:
            row = json.loads(path.read_text("utf-8"))
            if Path(row["project_root"]).resolve() != project:
                continue
            process = psutil.Process(int(row["pid"]))
            if (
                process.status() == psutil.STATUS_ZOMBIE
                or abs(process.create_time() - float(row["process_create_time"])) > 0.01
            ):
                continue
        except (OSError, ValueError, KeyError, TypeError, psutil.Error):
            continue
        result.append({**row, "record": str(path)})
    return result


def _kill_owned_tree(pid, created):
    """Escalation stays tied to one child identity; PID reuse never grants ownership."""
    try:
        parent = psutil.Process(pid)
        if abs(parent.create_time() - created) > 0.01:
            return
        children = parent.children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
        _, alive = psutil.wait_procs([*children, parent], timeout=10)
        if alive:
            raise RuntimeError("Owned retrieval processes did not stop")
    except psutil.NoSuchProcess:
        return


def _belongs_to_launcher(pid, launcher, created):
    try:
        parent = psutil.Process(launcher)
        return abs(parent.create_time() - created) <= 0.01 and (
            pid == launcher or any(child.pid == pid for child in parent.children(recursive=True))
        )
    except psutil.Error:
        return False


def _remove_owned_receipt(project, pid, created):
    path = project / ".cache/platform-diagnostics/brokers" / (str(pid) + ".json")
    try:
        row = json.loads(path.read_text("utf-8"))
        if row.get("pid") == pid and abs(row["process_create_time"] - created) <= 0.01:
            path.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, TypeError):
        pass


@contextmanager
def retrieval_service(settings):
    """The complete spawn/assemble/serve interval has one resource owner."""
    if settings.retrieval_device != "cuda":
        yield None
        return
    try:
        running = broker_health(settings.retrieval_endpoint)
    except OSError:
        if settings.retrieval_mode == "external":
            raise RuntimeError("The configured external retrieval service is unavailable") from None
    else:
        if (
            settings.retrieval_mode == "managed"
            and Path(running.get("models_root", "")).resolve() != settings.models_root
        ):
            raise RuntimeError("The managed retrieval endpoint uses a different model directory")
        yield {"owned": False, "health": running}
        return
    folder = settings.project_root / ".cache/platform-diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    stop_file = folder / ("retrieval-" + uuid4().hex + ".stop")
    owner = psutil.Process(os.getpid())
    environment = {
        **load_environment(settings.project_root),
        "HRS_PROJECT_ROOT": str(settings.project_root),
        "PLATFORM_MODELS_ROOT": str(settings.models_root),
        "PLATFORM_RETRIEVAL_ENDPOINT": settings.retrieval_endpoint,
        "PYTHONUTF8": "1",
    }
    process = None
    created = None
    actual = None
    try:
        with (folder / "codex-retrieval.log").open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "hrs.search.broker",
                    "--endpoint",
                    settings.retrieval_endpoint,
                    "--stop-file",
                    str(stop_file),
                    "--owner-pid",
                    str(owner.pid),
                    "--owner-created",
                    str(owner.create_time()),
                ],
                cwd=settings.project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        created = psutil.Process(process.pid).create_time()
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None:
                raise RuntimeError("Managed retrieval service exited during startup")
            try:
                running = broker_health(settings.retrieval_endpoint)
                if not _belongs_to_launcher(running.get("pid"), process.pid, created):
                    raise RuntimeError("Another process took the configured retrieval endpoint")
                actual = psutil.Process(running["pid"])
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Managed retrieval service startup timed out") from None
                time.sleep(0.1)
        yield {"owned": True, "pid": actual.pid, "launcher_pid": process.pid, "health": running}
    finally:
        if process is not None:
            stop_file.touch()
            if actual is not None:
                try:
                    actual.wait(timeout=30)
                except psutil.TimeoutExpired:
                    _kill_owned_tree(actual.pid, actual.create_time())
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                if created is not None:
                    _kill_owned_tree(process.pid, created)
                process.wait(timeout=10)
            if actual is not None:
                _remove_owned_receipt(settings.project_root, actual.pid, actual.create_time())
        stop_file.unlink(missing_ok=True)
