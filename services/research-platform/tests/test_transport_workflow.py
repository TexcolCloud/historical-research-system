"""Deterministic bounds for repeated outages; no model or Temporal server calls."""

import asyncio
from datetime import UTC, datetime

import pytest
from temporalio.exceptions import ActivityError, ApplicationError

from hrs_platform import workflows as module


@pytest.mark.parametrize("kind", ["model_transport", "retrieval"])
@pytest.mark.parametrize("delay,expected_sleeps", [(60, 12), (1800, 2)])
def test_repeated_service_waits_have_both_count_and_time_bounds(monkeypatch, delay, expected_sleeps, kind):
    now, sleeps, calls = [1000.0], [], []
    monkeypatch.setattr(module.workflow, "now", lambda: datetime.fromtimestamp(now[0], UTC))

    async def execute(*args, **kwargs):
        calls.append(args)
        raise ActivityError(
            "activity",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test",
            activity_type="generate_cards",
            activity_id="one",
            retry_state=None,
        ) from ApplicationError(
            "offline", {"retry_at": now[0] + delay}, type=kind + "_wait", non_retryable=True
        )

    async def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "sleep", sleep)
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(module.execute_with_service_recovery("generate_cards", "synthetic"))
    assert failure.value.type == kind + "_exhausted" and failure.value.non_retryable
    assert len(sleeps) == expected_sleeps
    assert len(calls) == expected_sleeps + 1
