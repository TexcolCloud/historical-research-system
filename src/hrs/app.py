"""Host-native Codex processing behind the original React workbench and upload API."""

import json
import os
import re
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import psutil
from filelock import FileLock

from hrs.config import diagnostics_root, diagnostics_roots, load_environment, project_root
from hrs.search.service import broker_health, retrieval_service

ROOT = project_root()
CONTROL = diagnostics_root(ROOT)


def listening(port):
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def infrastructure():
    env = load_environment(ROOT)
    for compose in (
        "deploy/compose.storage.yml",
        "deploy/compose.search.yml",
        "deploy/compose.upload.yml",
    ):
        subprocess.run(
            ["docker", "compose", "--env-file", ".env", "-f", compose, "up", "-d", "--wait"],
            cwd=ROOT,
            check=True,
            env=env,
        )


def _running_launcher(saved):
    from hrs.runtime import live_servers

    try:
        process = psutil.Process(int(saved["pid"]))
        if process.status() == psutil.STATUS_ZOMBIE:
            return False
        if saved.get("process_create_time") is not None:
            return abs(process.create_time() - float(saved["process_create_time"])) <= 0.01
        # Older launchers have no creation timestamp; their live API receipt
        # supplies the independently checked process identity instead.
        return any(row["pid"] == process.pid for row in live_servers(ROOT))
    except (KeyError, TypeError, ValueError, psutil.Error):
        return False


def command(action, *, build=False, existing_services=False):
    from hrs.config import Settings
    from hrs.processing.snapshots import atomic_json
    from hrs.processing.store import Store
    from hrs.processing.workspace import identity, resolve_workspace, running_workbench
    from hrs.runtime import discover_servers

    record = CONTROL / "codex-platform.json"
    if action == "stop":
        records = dict.fromkeys(
            (record, *(folder / "codex-platform.json" for folder in diagnostics_roots(ROOT)))
        )
        for previous in records:
            if not previous.exists():
                continue
            saved = json.loads(previous.read_text("utf8"))
            if not _running_launcher(saved):
                continue
            if not re.fullmatch(r"[0-9a-f]{32}\.stop", saved["stop_file"]):
                raise ValueError("Invalid native launcher control receipt")
            (previous.parent / saved["stop_file"]).touch()
            print("UI/bridge shutdown requested; Codex checkpoints remain in its workspace.")
        return
    if action == "init":
        if not existing_services:
            infrastructure()
        from hrs.storage.bootstrap import bootstrap

        bootstrap(ROOT)
        return
    settings = Settings.load(ROOT)
    if settings.processing_backend != "codex":
        raise ValueError(
            "Native launcher requires PLATFORM_PROCESSING_BACKEND=codex; only the native Codex execution path is supported"
        )
    native_root = resolve_workspace(settings.codex_root)
    if action in {"status", "doctor"}:
        metadata = native_root / ".workspace.json"
        configured = json.loads(metadata.read_text("utf-8")) if metadata.is_file() else {}
        servers = discover_servers(ROOT)
        running = [row for row in servers if row["available"]]
        configured_id = configured.get("workspace_id")
        for row in running:
            row["workspace_matches"] = (
                row["runtime"]["workspace_id"] == configured_id
                and Path(row["runtime"]["root"]).resolve() == native_root
            )
        retrieval = {
            "mode": settings.retrieval_mode,
            "endpoint": settings.retrieval_endpoint,
            "broker_required": settings.retrieval_device == "cuda",
            "reachable": False,
        }
        if settings.retrieval_device == "cuda":
            try:
                broker_health(settings.retrieval_endpoint)
                retrieval["reachable"] = True
            except (OSError, ValueError, RuntimeError) as error:
                retrieval["error_type"] = type(error).__name__
        status = {
            "backend": "codex",
            "workspace_id": configured_id,
            "configured_root": str(native_root),
            "ui": any(row["available"] or row.get("record") for row in servers),
            "runtime_available": bool(running),
            "servers": servers,
            "workspace_matches": all(row["workspace_matches"] for row in running)
            if running
            else None,
            "standalone_workbench": bool(running_workbench(Store(native_root)))
            if metadata.is_file()
            else False,
            "retrieval": retrieval["reachable"],
            "retrieval_status": retrieval,
            "codex_cli": bool(shutil.which("codex")),
        }
        print(json.dumps(status))
        return
    store = Store(native_root)
    if listening(18170):
        raise RuntimeError(
            "Port 18170 already has an owner; stop its launcher before starting this one"
        )
    CONTROL.mkdir(parents=True, exist_ok=True)
    with FileLock(CONTROL / "codex-platform.lock", timeout=0):
        if not existing_services:
            infrastructure()
        from hrs.storage.bootstrap import bootstrap

        bootstrap(ROOT)
        if build:
            subprocess.run(
                ["npm.cmd" if os.name == "nt" else "npm", "run", "build"],
                cwd=ROOT / "web",
                check=True,
            )
        if not (settings.web_dist / "index.html").is_file():
            raise RuntimeError("Build web before starting the UI (--build)")
        with retrieval_service(settings):
            from uuid import uuid4

            import uvicorn

            from hrs.api.main import create_app
            from hrs.api.server import Server

            stop_file = CONTROL / (uuid4().hex + ".stop")
            done = threading.Event()
            watcher = None
            try:
                server = Server(
                    uvicorn.Config(
                        create_app(settings=settings),
                        host="127.0.0.1",
                        port=18170,
                        timeout_graceful_shutdown=5,
                    )
                )
                atomic_json(
                    record,
                    {
                        "pid": os.getpid(),
                        "process_create_time": psutil.Process(os.getpid()).create_time(),
                        "stop_file": stop_file.name,
                        "workspace_id": identity(store)["workspace_id"],
                        "root": str(store.root),
                        "url": "http://127.0.0.1:18170",
                    },
                )

                def shutdown_watch():
                    while not done.wait(0.5):
                        if stop_file.exists():
                            server.should_exit = True
                            return

                watcher = threading.Thread(target=shutdown_watch, daemon=True)
                watcher.start()
                print("Research workbench: http://127.0.0.1:18170", flush=True)
                server.run()
            finally:
                done.set()
                if watcher is not None:
                    watcher.join(timeout=2)
                record.unlink(missing_ok=True)
                stop_file.unlink(missing_ok=True)
