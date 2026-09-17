"""Own local retrieval requests, cancellation and bounded model/client caches."""

import asyncio
import logging
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from threading import Event
from time import perf_counter
from uuid import uuid4

from opensearchpy import OpenSearch

from hrs_platform.domain.settings import MODEL_REVISIONS

logger = logging.getLogger(__name__)
EMBEDDING_IDENTITY = {"revision": MODEL_REVISIONS["BAAI/bge-m3"], "adapter": "bge-cls-l2-float32-v1"}
retrieval_owner = ContextVar("retrieval_owner", default=None)


async def task_search(search, endpoint, run_id, *args, **kwargs):
    """Keep the activity slot until its owned retrieval request actually exits."""
    owner = {"task_id": str(run_id), "request_id": str(uuid4()), "cancelled": Event()}
    token = retrieval_owner.set(owner)
    running = asyncio.create_task(asyncio.to_thread(search, *args, **kwargs))
    try:
        return await asyncio.shield(running)
    except asyncio.CancelledError:
        owner["cancelled"].set()
        try:
            if endpoint:
                import httpx

                with suppress(httpx.HTTPError):
                    await asyncio.to_thread(
                        httpx.post,
                        endpoint.rsplit("/", 1)[0] + "/cancel-retrieval",
                        json={"request_ids": [owner["request_id"]]},
                        timeout=30,
                    )
        finally:
            # Even an unreachable broker must not release the slot while the
            # original bounded HTTP/CPU call is still alive.
            while not running.done():
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(running)
            with suppress(Exception, asyncio.CancelledError):
                running.result()
        raise
    finally:
        retrieval_owner.reset(token)


@contextmanager
def measured(metrics, name):
    started = perf_counter()
    try:
        yield
    finally:
        metrics[name] = metrics.get(name, 0) + round((perf_counter() - started) * 1000, 3)


@lru_cache(maxsize=1)
def normalizer():
    from opencc import OpenCC

    return OpenCC("t2s")


@lru_cache(maxsize=4)
def search_client(url):
    return OpenSearch(url, timeout=30, max_retries=2, retry_on_timeout=True)


@lru_cache(maxsize=1)
def local_models(root):
    from hrs_platform.domain.retrieval_models import LocalModels
    from hrs_platform.domain.settings import RetrievalSettings

    return LocalModels(RetrievalSettings(models_root=Path(root), device="cpu"))


@lru_cache(maxsize=256)
def query_vector(root, device, endpoint, identity, query):
    if device == "cuda":
        return remote_compute(endpoint, "embed", [query], query=True)["vectors"][0]
    return local_models(root).embed([query], query=True)[0]


def remote_compute(endpoint, operation, texts, **options):
    import httpx

    from hrs_platform.domain.errors import Problem

    result = {"vectors": [], "scores": []}
    for offset in range(0, len(texts), 32):
        owner = retrieval_owner.get()
        if owner and owner["cancelled"].is_set():
            raise RuntimeError("retrieval_cancelled")
        identity = {key: owner[key] for key in ("task_id", "request_id")} if owner else {}
        try:
            response = httpx.post(
                endpoint,
                json={"operation": operation, "texts": texts[offset : offset + 32], **options, **identity},
                timeout=1800,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            retryable = (
                not isinstance(error, httpx.HTTPStatusError)
                or error.response.status_code in {408, 409, 429}
                or error.response.status_code >= 500
            )
            raise Problem(
                "retrieval_unavailable",
                "本地 GPU 检索请求失败，请检查主机运行服务及请求配置。",
                status=503,
                retryable=retryable,
            ) from error
        value = response.json()
        key = "vectors" if operation == "embed" else "scores"
        if len(value[key]) != len(texts[offset : offset + 32]):
            raise ValueError("Retrieval response count differs from request")
        result[key].extend(value[key])
        if "identity" in value:
            result["identity"] = value["identity"]
    return result
