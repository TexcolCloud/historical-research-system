"""Use a disposable process to check broker ownership; never start or stop real models."""

import importlib.util
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import pytest


def test_cancel_only_owns_matching_task_and_rejects_late_requests(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / "scripts/local_vision.py"
    spec = importlib.util.spec_from_file_location("task_cancel_broker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    broker = module.Vision()
    broker.folder = tmp_path
    broker.active_task = str(uuid4())
    broker.last_task = broker.active_task
    broker.log = (tmp_path / "dummy.log").open("wb")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=broker.log,
        stderr=broker.log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    broker.process = process
    try:
        other = str(uuid4())
        broker.cancel_tasks([other])
        assert process.poll() is None
        broker.cancel_tasks([broker.active_task])
        assert process.poll() is not None
        assert broker.process is None
        monkeypatch.setattr(module, "gpu_lease", nullcontext)
        with pytest.raises(RuntimeError, match="book_task_cancelled"):
            broker.complete({"model": module.MODEL, "hrs_task_id": other, "messages": []})
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        broker.log.close() if broker.log else None


@pytest.mark.parametrize("cancel_transport_fails", [False, True])
def test_cancel_search_drains_thread_and_prevents_later_gpu_calls(monkeypatch, cancel_transport_fails):
    import asyncio
    import threading

    import httpx

    from hrs_platform.services.retrieval.compute import remote_compute
    from hrs_platform.services.retrieval.compute import retrieval_owner
    from hrs_platform.services.retrieval.compute import task_search
    entered, release, notified = threading.Event(), threading.Event(), threading.Event()
    requests = []
    def post(url, **kw):
        requests.append((url, kw["json"]))
        notified.set()
        if cancel_transport_fails:
            raise httpx.ConnectError("synthetic cancel endpoint offline")
        return httpx.Response(200)
    monkeypatch.setattr(httpx, "post", post)
    def blocked():
        assert retrieval_owner.get()["task_id"] == task_id
        entered.set()
        assert release.wait(5)
        # A cancelled search must not submit a subsequent embedding/rerank batch.
        with pytest.raises(RuntimeError, match="retrieval_cancelled"):
            remote_compute("http://broker/retrieval", "embed", ["synthetic"])
    task_id = str(uuid4())
    async def run():
        task = asyncio.create_task(task_search(blocked, "http://broker/retrieval", task_id))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            assert await asyncio.to_thread(notified.wait, 5)
            assert not task.done()
            task.cancel()  # A second shutdown signal must not release the slot either.
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert retrieval_owner.get() is None
    asyncio.run(run())
    assert len(requests) == 1 and requests[0][0].endswith("/cancel-retrieval")
    assert len(requests[0][1]["request_ids"]) == 1


@pytest.mark.parametrize("by_task", [False, True])
def test_broker_cancels_only_owned_retrieval_and_rejects_queued_request(tmp_path, monkeypatch, by_task):
    path = Path(__file__).resolve().parents[3] / "scripts/local_vision.py"
    spec = importlib.util.spec_from_file_location("retrieval_cancel_broker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    broker = module.Vision()
    broker.folder = tmp_path
    task_id, request_id, other = str(uuid4()), str(uuid4()), str(uuid4())
    stopped = []
    broker.active_retrieval = (task_id, request_id)
    monkeypatch.setattr(broker, "unload_retrieval", lambda: stopped.append(True))
    if by_task:
        broker.cancel_tasks([other])
        assert not stopped
        broker.cancel_tasks([task_id])
    else:
        broker.cancel_retrieval([other])
        assert not stopped
        broker.cancel_retrieval([request_id])
    assert stopped == [True]
    from contextlib import contextmanager
    @contextmanager
    def queue(*, timeout, check_cancelled):
        assert timeout == 60
        check_cancelled()
        yield
    monkeypatch.setattr(module, "gpu_lease", queue)
    with pytest.raises(RuntimeError, match="retrieval_cancelled"):
        broker.retrieve({"operation": "embed", "texts": ["synthetic"], "task_id": task_id, "request_id": request_id})
    assert stopped == [True]  # No process loaded or touched by a cancelled queued call.


def test_remote_compute_propagates_task_and_request_ownership(monkeypatch):
    import asyncio

    import httpx

    from hrs_platform.services.retrieval.compute import remote_compute
    from hrs_platform.services.retrieval.compute import task_search
    bodies = []
    def post(url, **kw):
        bodies.append(kw["json"])
        return httpx.Response(200, json={"vectors": [[1]]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    identity = str(uuid4())
    result = asyncio.run(task_search(lambda: remote_compute("http://broker/retrieval", "embed", ["synthetic"]),
                                    "http://broker/retrieval", identity))
    assert result["vectors"] == [[1]]
    assert bodies[0]["task_id"] == identity and bodies[0]["request_id"]


@pytest.mark.parametrize("status,retryable", [(400, False), (401, False), (403, False), (429, True), (503, True)])
def test_gpu_recovery_does_not_retry_invalid_requests(monkeypatch, status, retryable):
    import httpx

    from hrs_platform.domain.errors import Problem
    from hrs_platform.services.retrieval.compute import remote_compute
    monkeypatch.setattr(httpx, "post", lambda url, **_: httpx.Response(status, request=httpx.Request("POST", url)))
    with pytest.raises(Problem) as error:
        remote_compute("http://broker/retrieval", "embed", ["synthetic"])
    assert error.value.retryable is retryable
