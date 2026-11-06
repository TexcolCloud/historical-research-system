"""Host-native Codex processing behind the original React workbench and upload API."""

import json
import os
import re
import shutil
import socket
import subprocess
import threading
from pathlib import Path

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / ".cache/platform-diagnostics"


def listening(port):
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def infrastructure():
    from hrs_platform.core.config import load_environment

    env = load_environment(ROOT)
    for compose in (
        "deploy/infrastructure/compose.storage.yml",
        "deploy/infrastructure/compose.search.yml",
    ):
        subprocess.run(
            ["docker", "compose", "--env-file", ".env", "-f", compose, "up", "-d", "--wait"],
            cwd=ROOT,
            check=True,
            env=env,
        )


def command(action, *, build=False):
    from hrs_codex.store import Store, atomic_json
    from hrs_codex.workspace import identity, resolve_workspace, running_workbench
    from hrs_platform.core.config import Settings
    from hrs_platform.core.db import engine_for, migrate

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
        infrastructure()
        from hrs_platform.bootstrap import bootstrap

        bootstrap()
        return
    settings = Settings.load(ROOT)
    if settings.processing_backend != "codex":
        raise ValueError(
            "Native launcher requires PLATFORM_PROCESSING_BACKEND=codex; use --legacy for legacy execution"
        )
    store = Store(resolve_workspace(settings.codex_root))
    if action in {"status", "doctor"}:
        status = {
            "backend": "codex",
            "workspace_id": identity(store)["workspace_id"],
            "ui": listening(18170),
            "executor": bool(running_workbench(store)),
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
        infrastructure()
        from hrs_platform.bootstrap import bootstrap

        bootstrap()
        if build:
            subprocess.run(
                ["npm.cmd" if os.name == "nt" else "npm", "run", "build"],
                cwd=ROOT / "services/review-workbench",
                check=True,
            )
        if not (ROOT / "services/review-workbench/dist/index.html").is_file():
            raise RuntimeError("Build services/review-workbench before starting the UI (--build)")
        broker = None
        if not listening(18160) and settings.retrieval_device == "cuda":
            python = (
                ROOT
                / ".cache/engineering-envs/research-platform"
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            if not python.is_file():
                raise RuntimeError(
                    "Install the documented retrieval runtime before starting GPU search"
                )
            with (CONTROL / "codex-retrieval.log").open("ab") as log:
                broker = subprocess.Popen(
                    [str(python), str(ROOT / "scripts/local_vision.py")],
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
        from uuid import uuid4

        import uvicorn
        from hrs_platform.main import create_app

        engine = engine_for(settings)
        migrate(engine)
        engine.dispose()
        stop_file = CONTROL / (uuid4().hex + ".stop")
        server = uvicorn.Server(
            uvicorn.Config(create_app(settings=settings), host="127.0.0.1", port=18170, timeout_graceful_shutdown=5)
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
