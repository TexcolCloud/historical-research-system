"""Bounded research generation budgets shared by the watchdog and lease renewal."""

from copy import deepcopy
from math import ceil

COMPLETE_SUPERVISION = {
    "version": 1,
    "unobserved_idle_seconds": 600,
    "coordinator_idle_seconds": 600,
    "source_characters_per_unit": 1000,
    "seconds_per_source_unit": 120,
    "max_generation_seconds": 3600,
    "stage_generation_seconds": {
        "author": 1200,
        "enrich": 1200,
        "verify": 900,
        "repair": 1200,
        "reverify": 900,
    },
    "stage_absolute_seconds": {
        "author": 5400,
        "enrich": 5400,
        "verify": 3600,
        "repair": 5400,
        "reverify": 3600,
    },
}


def supervision_policy(policy):
    """Old v5 snapshots get the explicit runtime fallback; v1–v4 retain their limit."""
    if policy.get("version", 1) < 5 or not policy.get("complete_research"):
        return None
    return deepcopy(policy.get("supervision") or COMPLETE_SUPERVISION)


def task_limits(policy, task, source_characters):
    budget = supervision_policy(policy)
    if budget is None:
        return {"unobserved_idle_seconds": 600, "generation_seconds": 600, "absolute_seconds": None}
    stage = task["stage"]
    units = ceil(max(0, source_characters) / max(1, int(budget["source_characters_per_unit"])))
    generation = min(
        3600,
        int(budget["max_generation_seconds"]),
        int(budget["stage_generation_seconds"][stage])
        + units * int(budget["seconds_per_source_unit"]),
    )
    return {
        "unobserved_idle_seconds": min(600, int(budget["unobserved_idle_seconds"])),
        "generation_seconds": max(600, generation),
        "absolute_seconds": min(
            7200, max(generation, int(budget["stage_absolute_seconds"][stage]))
        ),
    }


def task_timeout(policy, task, *, now, active, source_characters=0, max_idle_seconds=None):
    limits = task_limits(policy, task, source_characters)
    claimed = task.get("claimed_at", task["created_at"])
    if limits["absolute_seconds"] is not None and now - claimed > limits["absolute_seconds"]:
        return f"Research {task['stage']} exceeded its absolute task deadline of {limits['absolute_seconds']} seconds"
    idle = limits["generation_seconds"] if active else limits["unobserved_idle_seconds"]
    if max_idle_seconds is not None:
        idle = min(idle, max_idle_seconds)
    if now - task.get("last_progress_at", claimed) > idle:
        return f"Research worker made no source/tool progress for {idle} seconds"
    return None
