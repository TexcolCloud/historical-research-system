"""Production entry: Compose services plus the isolated Windows GPU worker.

Run with the research-platform environment. Ctrl+C drains the owned GPU worker
before stopping its broker. Never stop a GPU process owned by another launcher.
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / ".cache/platform-diagnostics"
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / ".env"),
    "-f",
    str(ROOT / "deploy/platform/compose.yml"),
    "--profile",
    "application",
]


def compose(*args, **kwargs):
    return subprocess.run([*COMPOSE, *args], cwd=ROOT, check=True, **kwargs)


def available(port, wait=0):
    deadline = time.monotonic() + wait
    while True:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Port {port} already has an owner; stop that launcher before the V2 cutover."
                    ) from None
        time.sleep(.25)


def stop_child(process, timeout=60):
    import psutil

    if process.poll() is not None:
        return
    # Capture identities before the venv launcher exits. psutil protects against
    # PID reuse when terminating the interpreter and model descendants later.
    try:
        descendants = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return
    deadline = time.monotonic() + timeout
    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=30)
    _, alive = psutil.wait_procs(descendants, timeout=max(0, deadline - time.monotonic()))
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=10)
    if alive:
        raise RuntimeError("Owned child processes did not stop; refusing GPU handoff.")


def start(build=False):
    if os.name != "nt":
        raise RuntimeError("This delivery profile requires the validated Windows GPU host.")
    available(18160)
    available(18161)
    from dotenv import dotenv_values

    env = {
        **dotenv_values(ROOT / ".env"),
        **os.environ,
        "PLATFORM_TUS_HOOK_URL": "http://api:18170/internal/tus",
    }
    # Validate ports before changing the running application profile.
    # Docker Desktop may release published ports after `compose stop` returns.
    # Wait briefly for release; never evict another listener.
    available(int(env.get("PLATFORM_WEB_PORT", 18156)), wait=30)
    available(int(env.get("PLATFORM_API_PORT", 18170)), wait=30)
    logdir = ROOT / ".cache/platform-diagnostics" / time.strftime("%Y%m%d-%H%M%S")
    logdir.mkdir(parents=True, exist_ok=True)
    stop_file = CONTROL / (uuid4().hex + ".stop")
    receipt = CONTROL / "launcher.json"
    receipt.write_text(
        json.dumps({"pid": os.getpid(), "stop_file": stop_file.name, "log_directory": str(logdir)}),
        encoding="utf-8",
    )
    children, streams = [], []
    try:
        if build:
            compose("build", "api", "web", env=env)
        compose("run", "--rm", "--no-deps", "api", "migrate", env=env)
        compose("up", "-d", "--wait", "temporal", "tusd", "api", "cpu-worker", "web", env=env)
        for name, command in [
            ("broker", [sys.executable, str(ROOT / "scripts/local_vision.py")]),
            ("gpu-worker", [sys.executable, "-m", "hrs_platform.cli", "gpu-worker"]),
        ]:
            stream = (logdir / (name + ".log")).open("ab")
            streams.append(stream)
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                env=env,
            )
            children.append(child)
            if name == "broker":
                from urllib.request import urlopen

                for attempt in range(30):
                    try:
                        with urlopen("http://127.0.0.1:18160/health", timeout=2):
                            break
                    except OSError:
                        if child.poll() is not None or attempt == 29:
                            raise RuntimeError(
                                "GPU broker failed to start; inspect the launch log."
                            )
                        time.sleep(1)
        print(
            f"V2 is running. Logs: {logdir}. Ctrl+C stops owned processes gracefully.", flush=True
        )
        while all(process.poll() is None for process in children) and not stop_file.exists():
            time.sleep(1)
        if not stop_file.exists():
            raise RuntimeError("An owned GPU process exited; inspect the launch log.")
    except KeyboardInterrupt:
        print("Stopping GPU work before its broker…", flush=True)
    finally:
        for process in reversed(children):
            stop_child(process)
        for stream in streams:
            stream.close()
        compose("stop", "cpu-worker", "web", "api", env=env)
        stop_file.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "up", "status", "doctor", "stop"])
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        from hrs_platform.bootstrap import bootstrap

        bootstrap()
    elif args.command == "up":
        from filelock import FileLock

        CONTROL.mkdir(parents=True, exist_ok=True)
        with FileLock(CONTROL / "launcher.lock", timeout=0):
            start(args.build)
    elif args.command == "status":
        compose("ps")
    elif args.command == "doctor":
        from hrs_platform.doctor import run
        from hrs_platform.settings import Settings

        raise SystemExit(run(Settings.load(ROOT)))
    else:
        import re

        from filelock import FileLock, Timeout

        CONTROL.mkdir(parents=True, exist_ok=True)
        lock = FileLock(CONTROL / "launcher.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout:
            receipt = json.loads((CONTROL / "launcher.json").read_text("utf-8"))
            if not re.fullmatch(r"[0-9a-f]{32}\.stop", receipt["stop_file"]):
                raise ValueError("Invalid launcher control receipt")
            (CONTROL / receipt["stop_file"]).touch()
            print(
                "Shutdown requested; waiting for the owned GPU worker and broker to drain.",
                flush=True,
            )
            lock.acquire(timeout=180)
        try:
            available(18160)
            compose("stop")
        finally:
            lock.release()


if __name__ == "__main__":
    main()
