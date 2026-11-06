"""Durable dispatch of native Codex runs, independent of the browser/chat lifetime."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from filelock import FileLock

from hrs.processing.checkpoints import atomic_json
from hrs.processing.store import Store


class Runner:
    def __init__(
        self, store: Store, execute, *, poll_seconds: float = 1, lease_seconds: int = 900
    ):
        self.store = store
        self.execute = execute
        self.poll_seconds, self.lease_seconds = poll_seconds, lease_seconds
        self.owner = "local-runner:" + uuid4().hex
        self.stop_event = threading.Event()
        self.thread = None
        self.active_stops = {}
        self.active_lock = threading.Lock()

    def tick(self, document_id: str | None = None) -> bool:
        if self.stop_event.is_set() or self.store.checkpoints.ledger is None:
            return False
        request = self.store.claim_run(self.owner, self.lease_seconds, document_id=document_id)
        if request is None:
            return self.recover_exhausted(document_id)
        with FileLock(
            self.store.document_directory(request["document_id"]) / ".executor.lock", timeout=10
        ):
            return self.execute_request(request)

    def recover_exhausted(self, document_id=None):
        from hrs.processing.recovery import RecoveryWait

        documents = [dict(id=document_id)] if document_id else self.store.list_documents()
        for item in documents:
            doc = self.store.status(item["id"])
            request = doc.get("run_request", {})
            if (
                not request.get("checkpoint_recovery_enabled")
                or request.get("automatic_checkpoint_used")
                or request.get("pause_requested")
                or request.get("cancel_requested")
                or request.get("state") != "retryable_error"
                or request.get("technical_failures", 0) < request.get("retry_limit", 3)
                or request.get("not_before", 0) > time.time()
            ):
                continue
            try:
                self.store.recover_run(
                    doc["id"],
                    "auto:"
                    + str(request.get("recovery_id", "initial"))
                    + ":"
                    + str(request["attempts"]),
                    automatic=True,
                )
                return True
            except RecoveryWait:
                continue
            except (ValueError, OSError) as error:
                self.store.record_recovery_failure(doc["id"], request, str(error))
        return False

    def execute_request(self, request):
        done = threading.Event()
        run_stop = threading.Event()
        with self.active_lock:
            self.active_stops[request["document_id"]] = run_stop
        if self.stop_event.is_set():
            run_stop.set()
        heartbeat_error = []

        def heartbeat():
            while not done.wait(max(0.1, self.lease_seconds / 3)):
                try:
                    self.store.renew_run(
                        request["document_id"], request["token"], self.lease_seconds
                    )
                except (ValueError, OSError) as error:
                    heartbeat_error.append(str(error))
                    run_stop.set()
                    break

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        failure = None
        try:
            self.execute(self.store, request["document_id"], stop_event=run_stop)
        except Exception as error:  # noqa: BLE001 - persist failures from the execution adapter
            failure = getattr(error, "failure", None) or {
                "type": type(error).__name__,
                "message": str(error),
            }
        finally:
            done.set()
            thread.join(timeout=5)
            with self.active_lock:
                self.active_stops.pop(request["document_id"], None)
        if heartbeat_error:
            raise RuntimeError("Run lease lost: " + heartbeat_error[-1])
        self.store.finish_run(request["document_id"], request["token"], error=failure)
        return True

    def _loop(self):
        # Threads supervise two CLI coordinators, never perform model inference.
        # Every native worker still passes the workspace-wide durable admission gate.
        futures, sequence, sweep_at = set(), -1, 0
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="codex-coordinator") as pool:
            while not self.stop_event.is_set():
                completed = False
                for future in list(futures):
                    if not future.done():
                        continue
                    futures.remove(future)
                    try:
                        completed = future.result() or completed
                    except Exception as error:  # noqa: BLE001 - isolate each owned coordinator failure
                        atomic_json(
                            self.store.root / "runner-error.json",
                            {
                                "at": time.time(),
                                "type": type(error).__name__,
                                "message": str(error),
                            },
                        )
                ledger = self.store.checkpoints.ledger
                current = ledger.sequence() if ledger else 0
                if completed or current != sequence or time.monotonic() >= sweep_at:
                    sequence, sweep_at = current, time.monotonic() + 30
                    for _ in range(2 - len(futures)):
                        futures.add(pool.submit(self.tick))
                self.stop_event.wait(self.poll_seconds)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="historical-runner", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.active_lock:
            for active in self.active_stops.values():
                active.set()
        if self.thread:
            self.thread.join(timeout=20)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()
