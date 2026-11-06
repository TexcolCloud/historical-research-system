"""Loopback BGE compute service; owns only its retrieval worker process."""

import argparse
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

import psutil
from filelock import FileLock, Timeout

from hrs.config import (
    DEFAULT_RETRIEVAL_ENDPOINT,
    load_environment,
    project_root,
    resolve_path,
    retrieval_bind,
)
from hrs.processing.snapshots import atomic_json
from hrs.runtime import lifecycle_lock
from hrs.search.service import PROTOCOL, SERVICE


def free_gib():
    value = subprocess.check_output(
        ["nvidia-smi", "--id=0", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        text=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return float(value.strip().splitlines()[0]) / 1024


@contextmanager
def gpu_lease(path, check_cancelled):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(path)
    deadline = time.monotonic() + 60
    while True:
        check_cancelled()
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            if time.monotonic() >= deadline:
                raise TimeoutError("GPU queue wait exceeded") from None
            time.sleep(0.2)
    try:
        yield
    finally:
        lock.release()


def retrieval_worker(connection, models_root):
    finished = threading.Event()
    parent = multiprocessing.parent_process()

    def watch_parent():
        while not finished.wait(0.25):
            if parent is not None and not parent.is_alive():
                # A blocked inference cannot outlive the broker that owns it.
                # Exit this worker only; the OS releases its GPU context/handles.
                os._exit(1)

    watcher = threading.Thread(target=watch_parent, daemon=True)
    if parent is not None:
        watcher.start()
    models = None
    try:
        from hrs.search.models import LocalModels
        from hrs.search.settings import RetrievalSettings

        models = LocalModels(RetrievalSettings(models_root=models_root, device="cuda"))
        while True:
            body = connection.recv()
            if body.get("operation") == "shutdown":
                break
            started = time.monotonic()
            try:
                if body["operation"] == "embed":
                    result = {
                        "vectors": models.embed(body["texts"], query=body.get("query", False)),
                        "identity": models.identity(),
                    }
                else:
                    result = {"scores": models.rerank(body["query"], body["texts"])}
                connection.send({"result": result, "seconds": time.monotonic() - started})
            except Exception as error:  # noqa: BLE001 -- child protocol envelope
                connection.send({"error": type(error).__name__})
    except EOFError:
        pass
    finally:
        finished.set()
        if watcher.is_alive():
            watcher.join(timeout=1)
        if models is not None:
            models.unload()
        connection.close()


class RetrievalBroker:
    def __init__(self, root=None):
        self.root = project_root(root)
        self.env = load_environment(self.root)
        self.models_root = resolve_path(
            self.root, self.env.get("PLATFORM_MODELS_ROOT", "models/document-retrieval")
        )
        self.folder = self.root / "state/retrieval"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.closing = threading.Event()
        self.task_lock = threading.RLock()
        self.process = None
        self.pipe = None
        self.active = None
        self.cancelled_requests = {}
        self.cancelled_tasks = set()

    def event(self, value):
        with (self.folder / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": time.time(), **value}, ensure_ascii=False) + "\n")

    def unload(self):
        process, self.process = self.process, None
        pipe, self.pipe = self.pipe, None
        if process is not None:
            if process.pid is not None:
                if self.active is None and pipe is not None and process.is_alive():
                    try:
                        pipe.send({"operation": "shutdown"})
                        process.join(timeout=2)
                    except (OSError, EOFError):
                        pass
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=20)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=10)
                if process.is_alive():
                    raise RuntimeError("Retrieval worker did not release GPU")
            process.close()
        if pipe is not None:
            pipe.close()

    def retrieve(self, body):
        operation, texts = body.get("operation"), body.get("texts")
        if (
            operation not in {"embed", "rerank"}
            or not isinstance(texts, list)
            or not 1 <= len(texts) <= 64
        ):
            raise ValueError("Invalid retrieval request")
        if any(not isinstance(text, str) or len(text) > 40000 for text in texts):
            raise ValueError("Invalid retrieval text")
        if operation == "rerank" and (
            not isinstance(body.get("query"), str) or not 1 <= len(body["query"]) <= 1000
        ):
            raise ValueError("Invalid rerank query")
        task_id = str(UUID(body["task_id"])) if body.get("task_id") else None
        request_id = str(UUID(body["request_id"])) if body.get("request_id") else None

        def check_cancelled():
            with self.task_lock:
                if (
                    self.closing.is_set()
                    or task_id in self.cancelled_tasks
                    or request_id in self.cancelled_requests
                ):
                    raise RuntimeError("Retrieval cancelled")

        with gpu_lease(self.folder / "gpu.lock", check_cancelled):
            try:
                with self.task_lock:
                    check_cancelled()
                    self.active = (task_id, request_id)
                    if self.process is None or not self.process.is_alive():
                        self.unload()
                        required = float(self.env.get("HRS_RETRIEVAL_PEAK_GIB", "6"))
                        reserve = float(self.env.get("HRS_GPU_RESERVE_GIB", "1.5"))
                        if free_gib() < required + reserve:
                            raise RuntimeError("Insufficient GPU memory for retrieval")
                        context = multiprocessing.get_context("spawn")
                        self.pipe, child = context.Pipe()
                        self.process = context.Process(
                            target=retrieval_worker,
                            args=(child, self.models_root),
                            daemon=True,
                        )
                        try:
                            self.process.start()
                        finally:
                            child.close()
                    pipe = self.pipe
                    pipe.send(body)
                deadline = time.monotonic() + 900
                while not pipe.poll(0.2):
                    check_cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Retrieval timed out")
                check_cancelled()
                response = pipe.recv()
                if "error" in response:
                    raise RuntimeError("Retrieval failed: " + response["error"])
                self.event(
                    {
                        "event": "completed",
                        "operation": operation,
                        "items": len(texts),
                        "seconds": response["seconds"],
                    }
                )
                return response["result"]
            except Exception:
                with self.task_lock:
                    self.unload()
                raise
            finally:
                with self.task_lock:
                    self.active = None

    def cancel(self, values, *, tasks=False):
        identities = {str(UUID(value)) for value in values}
        with self.task_lock:
            if tasks:
                self.cancelled_tasks.update(identities)
            else:
                now = time.monotonic()
                self.cancelled_requests = {
                    key: at for key, at in self.cancelled_requests.items() if now - at < 3600
                }
                self.cancelled_requests.update({key: now for key in identities})
            if self.active and self.active[0 if tasks else 1] in identities:
                self.unload()
        return {"cancelled": sorted(identities)}


def main(argv=()):
    broker = RetrievalBroker()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default=broker.env.get("PLATFORM_RETRIEVAL_ENDPOINT", DEFAULT_RETRIEVAL_ENDPOINT),
    )
    parser.add_argument("--stop-file", type=lambda value: resolve_path(broker.root, value))
    parser.add_argument("--owner-pid", type=int)
    parser.add_argument("--owner-created", type=float)
    options = parser.parse_args(argv)
    if (options.owner_pid is None) != (options.owner_created is None):
        raise ValueError("Managed retrieval requires both owner PID and creation time")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path != "/health":
                return self.reply(404, {"error": "unknown_route"})
            try:
                free = free_gib()
            except (OSError, ValueError, subprocess.SubprocessError):
                free = None
            with broker.task_lock:
                worker_pid = broker.process.pid if broker.process is not None else None
            self.reply(
                200,
                {
                    "service": SERVICE,
                    "protocol": PROTOCOL,
                    "pid": os.getpid(),
                    "model": "BGE-M3",
                    "resident": worker_pid is not None,
                    "worker_pid": worker_pid,
                    "models_root": str(broker.models_root),
                    "closing": broker.closing.is_set(),
                    "free_gib": free,
                    "gpu_available": free is not None,
                },
            )

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 80 * 1024 * 1024:
                    raise ValueError("Invalid request length")
                body = json.loads(self.rfile.read(size))
                if self.path == "/retrieval":
                    result = broker.retrieve(body)
                elif self.path == "/cancel-retrieval":
                    result = broker.cancel(body["request_ids"])
                elif self.path == "/cancel-tasks":
                    result = broker.cancel(body["task_ids"], tasks=True)
                else:
                    return self.reply(404, {"error": "unknown_route"})
                self.reply(200, result)
            except Exception as error:  # noqa: BLE001 -- bounded HTTP error envelope
                broker.event({"event": "failed", "error_class": type(error).__name__})
                self.reply(
                    503, {"error": "retrieval_unavailable", "error_class": type(error).__name__}
                )

    with lifecycle_lock(broker.root):
        server = ThreadingHTTPServer(retrieval_bind(options.endpoint), Handler)
        server.daemon_threads = True
        process = psutil.Process(os.getpid())
        receipt = broker.root / ".cache/platform-diagnostics/brokers" / (str(os.getpid()) + ".json")
        atomic_json(
            receipt,
            {
                "pid": os.getpid(),
                "process_create_time": process.create_time(),
                "project_root": str(broker.root),
                "endpoint": options.endpoint,
                "owner_pid": options.owner_pid,
                "owner_created": options.owner_created,
            },
        )

    def stop(*_):
        broker.closing.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (
        signal.SIGINT,
        signal.SIGTERM,
        *([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else []),
    ):
        signal.signal(sig, stop)
    done = threading.Event()

    def watch_owner():
        while not done.wait(0.25):
            stop_requested = options.stop_file is not None and options.stop_file.exists()
            if options.owner_pid is not None:
                try:
                    parent = psutil.Process(options.owner_pid)
                    stop_requested |= abs(parent.create_time() - options.owner_created) > 0.01
                    stop_requested |= parent.status() == psutil.STATUS_ZOMBIE
                except psutil.Error:
                    stop_requested = True
            if stop_requested:
                stop()
                return

    watcher = threading.Thread(target=watch_owner, daemon=True)
    watcher.start()
    try:
        server.serve_forever()
    finally:
        broker.closing.set()
        done.set()
        watcher.join(timeout=2)
        try:
            with broker.task_lock:
                broker.unload()
        finally:
            server.server_close()
            receipt.unlink(missing_ok=True)


if __name__ == "__main__":
    main(sys.argv[1:])
