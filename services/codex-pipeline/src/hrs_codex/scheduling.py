"""Measured, bounded capacity recommendations; native Codex still runs the models."""

import statistics
import time
from collections import Counter

STAGES = ("ocr", "review", "verify", "navigation", "organization")


def scheduling_plan(doc, *, now=None, capacity=7):
    now = time.time() if now is None else now
    capacity = max(2, min(7, capacity))
    tasks = doc["tasks"]
    active = {
        s: {
            t["claim"]["worker_id"]
            for t in tasks
            if t["stage"] == s
            and t["state"] == "leased"
            and t.get("claim", {}).get("expires_at", 0) > now
        }
        for s in STAGES
    }
    queued = Counter(
        t["stage"]
        for t in tasks
        if t["state"] == "ready"
        or (t["state"] == "leased" and t.get("claim", {}).get("expires_at", 0) <= now)
    )
    metrics = {}
    for stage in STAGES:
        completed = [t for t in tasks if t["stage"] == stage and t.get("receipt")]
        durations = [
            t["receipt"]["duration_seconds"]
            for t in completed
            if t["receipt"].get("duration_seconds", 0) > 0
        ]
        seconds = (
            statistics.median(durations[-20:]) if durations else (60 if stage == "ocr" else 120)
        )
        recent = [t for t in completed if t["receipt"].get("created_at", 0) >= now - 300]
        metrics[stage] = {
            "completed": len(completed),
            "queued": queued[stage],
            "active": len(active[stage]),
            "median_seconds": round(seconds, 1) if durations else None,
            "pages_per_minute": round(len(recent) / 5, 2),
            "samples": len(durations),
        }
    workers = dict.fromkeys(STAGES, 0)
    eligible = Counter(
        t["stage"]
        for t in tasks
        if (
            t["state"] == "ready"
            or (t["state"] == "leased" and t.get("claim", {}).get("expires_at", 0) <= now)
        )
        and t.get("retry_after", 0) <= now
    )
    work = {s: eligible[s] + len(active[s]) for s in STAGES}
    if work["ocr"] and work["review"]:
        ocr = metrics["ocr"]["median_seconds"] or 60
        review = metrics["review"]["median_seconds"] or 120
        ocr_slots = max(1, min(capacity - 1, round(capacity * ocr / (ocr + review))))
        if work["review"] >= 8:
            ocr_slots = max(1, ocr_slots - 1)
        workers["review"] = min(work["review"], capacity - ocr_slots)
        workers["ocr"] = min(work["ocr"], capacity - workers["review"])
    else:
        for stage in ("review", "ocr"):
            workers[stage] = min(capacity, work[stage])
    for stage in ("verify", "navigation", "organization"):
        if not work[stage]:
            continue
        if sum(workers.values()) >= capacity:
            donor = max(
                (s for s in ("ocr", "review") if workers[s] > 1),
                key=lambda s: workers[s],
                default=None,
            )
            if donor:
                workers[donor] -= 1
        if sum(workers.values()) < capacity:
            workers[stage] = 1
    scheduler = doc.get("scheduler", {})
    previous = scheduler.get("allocation", {})
    if (
        previous
        and sum(previous.values()) <= capacity
        and now - scheduler.get("rebalanced_at", 0) < 30
        and all(previous.get(s, 0) > 0 for s in STAGES if workers[s])
    ):
        desired = workers
        workers = {s: min(previous.get(s, 0), work[s]) for s in STAGES}
        for stage in ("verify", "navigation", "organization", "review", "ocr"):
            workers[stage] += min(
                max(0, desired[stage] - workers[stage]), max(0, capacity - sum(workers.values()))
            )
    if scheduler.get("cooldown_until", 0) > now:
        # Cooldown reduces new admissions; existing workers finish their current page.
        workers = dict.fromkeys(STAGES, 0)
    paused = doc.get("run_request", {}).get("pause_requested", False)
    if paused:
        workers = dict.fromkeys(STAGES, 0)
    latest = max((t.get("receipt", {}).get("created_at", 0) for t in tasks), default=0)
    return {
        "capacity": capacity,
        "workers": workers,
        "active": {s: len(active[s]) for s in STAGES},
        "metrics": metrics,
        "batch_size": 2,
        "rebalance_seconds": 30,
        "human_pending": sum(p["state"] == "pending" for p in doc["pages"]),
        "automatic_work_remaining": bool(sum(queued.values()) or any(active.values())),
        "paused": paused,
        "last_completion_at": latest or None,
        "cooldown_until": doc.get("scheduler", {}).get("cooldown_until", 0),
        "next_ready_at": min(
            (
                t["retry_after"]
                for t in tasks
                if t["state"] == "ready" and t.get("retry_after", 0) > now
            ),
            default=0,
        ),
        "policy": "measured-small-batches-v1",
    }


def task_priority(doc, task):
    page = task.get("page") or 0
    priority = 0 if page and page <= 8 else 2
    if page and doc["pages"][page - 1].get("page_kind") == "toc":
        priority = -1
    targets = (doc.get("navigation") or {}).get("priority_pages", [])
    if page in targets:
        priority = 0
    return priority, page
