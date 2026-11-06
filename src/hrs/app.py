"""Host-native Codex processing behind the original React workbench and upload API."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading

from filelock import FileLock

from hrs.config import load_environment, project_root

ROOT = project_root()
CONTROL = ROOT / ".cache/platform-diagnostics"


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


def command(action, *, build=False, existing_services=False):
    from hrs.config import Settings
    from hrs.processing.store import Store, atomic_json
    from hrs.processing.workspace import identity, resolve_workspace, running_workbench

    CONTROL.mkdir(parents=True, exist_ok=True)
    record = CONTROL / "codex-platform.json"
    if action == "stop":
        if record.exists():
            saved = json.loads(record.read_text("utf8"))
            if not re.fullmatch(r"[0-9a-f]{32}\.stop", saved["stop_file"]):
                raise ValueError("Invalid native launcher control receipt")
            (CONTROL / saved["stop_file"]).touch()
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
    store = Store(resolve_workspace(settings.codex_root))
    if action in {"status", "doctor"}:
        status = {
            "backend": "codex",
            "workspace_id": identity(store)["workspace_id"],
            "ui": listening(18170),
            "standalone_workbench": bool(running_workbench(store)),
            "retrieval": listening(18160),
            "codex_cli": bool(shutil.which("codex")),
        }
        print(json.dumps(status))
        return
    if listening(18170):
        raise RuntimeError(
            "Port 18170 already has an owner; stop its launcher before starting this one"
        )
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
        broker = None
        if not listening(18160) and settings.retrieval_device == "cuda":
            with (CONTROL / "codex-retrieval.log").open("ab") as log:
                broker = subprocess.Popen(
                    [sys.executable, "-m", "hrs.search.broker"],
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
        from uuid import uuid4

        import uvicorn

        from hrs.api.main import create_app
        from hrs.api.server import Server

        stop_file = CONTROL / (uuid4().hex + ".stop")
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
                "stop_file": stop_file.name,
                "workspace_id": identity(store)["workspace_id"],
                "url": "http://127.0.0.1:18170",
            },
        )
        done = threading.Event()

        def shutdown_watch():
            while not done.wait(0.5):
                if stop_file.exists():
                    server.should_exit = True
                    return

        watcher = threading.Thread(target=shutdown_watch, daemon=True)
        watcher.start()
        print("Research workbench: http://127.0.0.1:18170", flush=True)
        try:
            server.run()
        finally:
            done.set()
            watcher.join(timeout=2)
            record.unlink(missing_ok=True)
            stop_file.unlink(missing_ok=True)
            if broker:
                broker.terminate()
                broker.wait(timeout=30)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "up", "stop", "status", "doctor"])
    parser.add_argument("--build", action="store_true")
    arguments = parser.parse_args()
    command(arguments.action, build=arguments.build)
