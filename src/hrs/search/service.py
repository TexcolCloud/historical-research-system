"""Own a managed retrieval broker, or connect to an explicitly external service."""

import json
import math
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

import psutil

from hrs.config import diagnostics_root, diagnostics_roots, load_environment

SERVICE = "hrs-retrieval"
PROTOCOL = 1
CLIENT_PROTOCOL = 1


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


def process_alive(pid, created):
    """A PID alone is never a lease or permission to stop a process."""
    if type(pid) is not int or pid <= 0 or type(created) not in (int, float):
        return False
    try:
        process = psutil.Process(pid)
        return (
            math.isfinite(created)
            and abs(process.create_time() - created) <= 0.01
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.Error, TypeError, ValueError):
        return False


def configured_broker_health(settings):
    """Validate the configured host before managed registration or readiness."""
    running = broker_health(settings.retrieval_endpoint)
    if settings.retrieval_mode == "managed":
        if (
            running.get("client_protocol") != CLIENT_PROTOCOL
            or running.get("managed") is not True
            or not isinstance(running.get("instance_id"), str)
            or not running["instance_id"]
            or Path(running.get("project_root", "")).resolve() != settings.project_root.resolve()
            or Path(running.get("models_root", "")).resolve() != settings.models_root.resolve()
            or running.get("endpoint") != settings.retrieval_endpoint
            or not process_alive(running.get("pid"), running.get("process_create_time"))
        ):
            raise RuntimeError(
                "The managed retrieval endpoint has incompatible ownership or models"
            )
    return running


def _client_request(settings, running, client, action):
    endpoint = health_url(settings.retrieval_endpoint).rsplit("/", 1)[0] + "/clients"
    request = Request(
        endpoint,
        data=json.dumps(
            {
                **client,
                "action": action,
                "instance_id": running["instance_id"],
                "project_root": str(settings.project_root.resolve()),
                "models_root": str(settings.models_root.resolve()),
                "endpoint": settings.retrieval_endpoint,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        result = json.load(response)
    if result.get("instance_id") != running["instance_id"]:
        raise RuntimeError("The retrieval host changed during client registration")
    return result


def live_brokers(project):
    """Read only receipts whose exact process identity is still alive."""
    project = Path(project).resolve()
    result = []
    paths = (
        path
        for folder in diagnostics_roots(project)
        for path in sorted((folder / "brokers").glob("*.json"))
    )
    seen = set()
    for path in paths:
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
        key = (row["pid"], row["process_create_time"])
        if key not in seen:
            result.append({**row, "record": str(path)})
            seen.add(key)
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
    for folder in diagnostics_roots(project):
        path = folder / "brokers" / (str(pid) + ".json")
        try:
            row = json.loads(path.read_text("utf-8"))
            if row.get("pid") == pid and abs(row["process_create_time"] - created) <= 0.01:
                path.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError):
            pass


@contextmanager
def retrieval_service(settings):
    """Every managed context holds a broker lease, including reused connections."""
    if settings.retrieval_device != "cuda":
        yield None
        return
    if settings.retrieval_mode == "external":
        try:
            running = broker_health(settings.retrieval_endpoint)
        except OSError:
            raise RuntimeError("The configured external retrieval service is unavailable") from None
        yield {"owned": False, "health": running}
        return

    owner = psutil.Process(os.getpid())
    client = {"client_id": uuid4().hex, "pid": owner.pid, "created": owner.create_time()}
    process = actual = running = None
    created = None
    registered = False
    attempted = False
    stop_file = None
    try:
        try:
            running = configured_broker_health(settings)
        except OSError:
            folder = diagnostics_root(settings.project_root)
            folder.mkdir(parents=True, exist_ok=True)
            stop_file = folder / ("retrieval-" + uuid4().hex + ".stop")
            environment = {
                **load_environment(settings.project_root),
                "HRS_PROJECT_ROOT": str(settings.project_root),
                "PLATFORM_MODELS_ROOT": str(settings.models_root),
                "PLATFORM_RETRIEVAL_ENDPOINT": settings.retrieval_endpoint,
                "PYTHONUTF8": "1",
            }
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
                try:
                    running = configured_broker_health(settings)
                    if _belongs_to_launcher(running["pid"], process.pid, created):
                        actual = psutil.Process(running["pid"])
                    # Another compatible managed launcher may win the port bind.
                    # Register with that host; never stop or take ownership of it.
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Managed retrieval service exited during startup"
                        ) from None
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Managed retrieval service startup timed out") from None
                    time.sleep(0.1)
        attempted = True
        _client_request(settings, running, client, "acquire")
        registered = True
        result = {"owned": actual is not None, "health": running}
        if actual is not None:
            result.update(pid=actual.pid, launcher_pid=process.pid)
        yield result
    finally:
        released = None
        if attempted:
            try:
                released = _client_request(settings, running, client, "release")
            except (OSError, ValueError, RuntimeError):
                # An ambiguous transport failure grants no permission to kill a
                # shared host. Its PID-identity watcher still reclaims dead clients.
                pass
        last = released is not None and released.get("closing") is True
        if (
            last
            and running is not None
            and process_alive(running["pid"], running["process_create_time"])
        ):
            try:
                psutil.Process(running["pid"]).wait(timeout=30)
            except psutil.NoSuchProcess:
                pass
            except psutil.TimeoutExpired:
                if actual is not None:
                    _kill_owned_tree(actual.pid, running["process_create_time"])
        if process is not None:
            if not registered and stop_file is not None:
                # Bootstrap abandonment is honored only before any client leases.
                stop_file.touch()
            if last or not process_alive(process.pid, created):
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    if last and actual is not None:
                        _kill_owned_tree(process.pid, created)
                        process.wait(timeout=10)
            else:
                # The host has outlived its launcher context. Reap its handle
                # asynchronously after the final borrower releases it.
                threading.Thread(target=process.wait, daemon=True).start()
            if running is not None and not process_alive(
                running["pid"], running["process_create_time"]
            ):
                _remove_owned_receipt(
                    settings.project_root, running["pid"], running["process_create_time"]
                )
        if stop_file is not None and (registered or process is None or process.poll() is not None):
            stop_file.unlink(missing_ok=True)
