"""Persist retrieval cooldowns independently of paid model attempts."""

import asyncio
import time

from opensearchpy.exceptions import TransportError

from hrs_platform.domain.errors import Problem, TaskError
from hrs_platform.services.books import get_run


async def recover_retrieval(engine, outputs, run_id, key, operation):
    """Persist service cooldown independently of paid model request attempts."""
    run = await asyncio.to_thread(get_run, engine, run_id)
    epoch = run.get("recovery_attempt", 0)
    prefix = f"{key}:retrieval-recovery:{epoch}"
    previous, attempt = None, 1
    while receipt := await asyncio.to_thread(outputs.get, run_id, f"{prefix}:{attempt}"):
        previous, attempt = receipt, attempt + 1

    def defer(receipt):
        raise TaskError(
            "检索服务暂不可用，保留查询和证据，等待自动恢复。",
            receipt,
            type="retrieval_wait",
            non_retryable=True,
        ) from None

    if previous and previous["retry_at"] > time.time():
        defer(previous)
    try:
        return await operation()
    except (Problem, TransportError, TaskError) as error:
        retryable = (
            isinstance(error, Problem)
            and error.retryable
            or isinstance(error, TransportError)
            and (
                not isinstance(error.status_code, int)
                or error.status_code in {404, 408, 409, 429}
                or error.status_code >= 500
            )
            or isinstance(error, TaskError)
            and error.type == "card_index_missing"
        )
        if not retryable:
            raise
        receipt = {"attempt": attempt, "retry_at": time.time() + min(60 * 2 ** min(attempt - 1, 5), 1800)}
        await asyncio.to_thread(outputs.put, run_id, f"{prefix}:{attempt}", receipt, {"key": key})
        defer(receipt)
