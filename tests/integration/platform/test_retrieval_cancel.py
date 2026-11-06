"""Use a disposable process to check broker ownership; never start or stop real models."""

from uuid import uuid4

import pytest


@pytest.mark.parametrize("cancel_transport_fails", [False, True])
def test_cancel_search_drains_thread_and_prevents_later_gpu_calls(
    monkeypatch, cancel_transport_fails
):
    import asyncio
    import threading

    import httpx

    from hrs.search.compute import remote_compute, retrieval_owner, task_search

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


def test_remote_compute_propagates_task_and_request_ownership(monkeypatch):
    import asyncio

    import httpx

    from hrs.search.compute import remote_compute, task_search

    bodies = []

    def post(url, **kw):
        bodies.append(kw["json"])
        return httpx.Response(200, json={"vectors": [[1]]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    identity = str(uuid4())
    result = asyncio.run(
        task_search(
            lambda: remote_compute("http://broker/retrieval", "embed", ["synthetic"]),
            "http://broker/retrieval",
            identity,
        )
    )
    assert result["vectors"] == [[1]]
    assert bodies[0]["task_id"] == identity and bodies[0]["request_id"]


@pytest.mark.parametrize(
    "status,retryable", [(400, False), (401, False), (403, False), (429, True), (503, True)]
)
def test_gpu_recovery_does_not_retry_invalid_requests(monkeypatch, status, retryable):
    import httpx

    from hrs.library.errors import Problem
    from hrs.search.compute import remote_compute

    monkeypatch.setattr(
        httpx, "post", lambda url, **_: httpx.Response(status, request=httpx.Request("POST", url))
    )
    with pytest.raises(Problem) as error:
        remote_compute("http://broker/retrieval", "embed", ["synthetic"])
    assert error.value.retryable is retryable
