"""Measured, bounded capacity recommendations; native Codex still runs the models."""

import statistics
import time
from collections import Counter

STAGES = ("ocr", "review", "verify", "navigation", "organization")


def scheduling_plan(doc, *, now=None, capacity=7, batch_pages=2, overlap_reading=False):
    now = time.time() if now is None else now
    capacity = max(1, min(64, capacity))
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
        sample_end = (
            now
            if queued[stage] or active[stage]
            else max((t["receipt"].get("created_at", now) for t in completed), default=now)
        )
        recent = [t for t in completed if t["receipt"].get("created_at", 0) >= sample_end - 300]
        starts = [
            min(
                t.get("claim", {}).get(
                    "started_at",
                    t["receipt"]["created_at"] - t["receipt"].get("duration_seconds", 0),
                ),
                t.get("source_reading", {})
                .get("image_access", {})
                .get("opened_at", t["receipt"]["created_at"]),
            )
            for t in completed
            if t["receipt"].get("created_at")
        ]
        window = min(300, max(1, sample_end - min(starts))) if starts else 0
        phases = {}
        for field in (
            "queue_seconds",
            "startup_seconds",
            "original_reading_seconds",
            "candidate_comparison_seconds",
        ):
            samples = [t["receipt"][field] for t in completed[-20:] if field in t["receipt"]]
            if samples:
                phases[field] = round(statistics.median(samples), 1)
        metrics[stage] = {
            "completed": len(completed),
            "queued": queued[stage],
            "active": len(active[stage]),
            "median_seconds": round(seconds, 1) if durations else None,
            "pages_per_minute": round(len(recent) * 60 / window, 2) if window else 0,
            "sample_window_seconds": round(window, 1),
            "phase_medians": phases,
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
        and not t.get("technical_blocked")
    )
    work = {s: eligible[s] + len(active[s]) for s in STAGES}
    if overlap_reading and work["ocr"]:
        # Blind original reading can run before the candidate exists.
        work["review"] += work["ocr"]
    if work["ocr"] and work["review"]:
        ocr = metrics["ocr"]["median_seconds"] or 60
        review = metrics["review"]["median_seconds"] or (60 if overlap_reading else 120)
        if overlap_reading and min(metrics["ocr"]["samples"], metrics["review"]["samples"]) < 3:
            # A measured OCR time versus an unmeasured review prior is not a ratio.
            # Hold balanced warm lanes until both populations have real samples.
            ocr = review = 60
        ocr_slots = max(1, min(capacity - 1, round(capacity * ocr / (ocr + review))))
        if (queued["review"] if overlap_reading else work["review"]) >= 8:
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
            workers[stage] = (
                min(work[stage], capacity - sum(workers.values())) if stage == "verify" else 1
            )
    if (
        overlap_reading
        and not work["organization"]
        and not doc.get("organization")
        and 0 < sum(p["state"] not in {"approved", "pending"} for p in doc["pages"]) <= 8
        and sum(workers.values()) < capacity
    ):
        workers["organization"] = 1
    if capacity == 1:
        # Recovery probes must perform ready work, rather than await a future candidate.
        selected = next(
            (
                s
                for s in ("verify", "review", "ocr", "navigation", "organization")
                if eligible[s] or active[s]
            ),
            None,
        )
        workers = {stage: int(stage == selected) for stage in STAGES}
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
        "batch_size": batch_pages,
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
        "policy": "overlapped-original-reading-v2"
        if overlap_reading
        else "measured-small-batches-v1",
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
