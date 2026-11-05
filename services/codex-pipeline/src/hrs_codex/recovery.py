"""Recovery reuses validated receipts, never rolls back content or human decisions."""

import time

from filelock import FileLock, Timeout

from hrs_codex.store import digest


class RecoveryWait(ValueError):
    def __init__(self, retry_at, message="上一次处理尚未结束，结束后将自动继续恢复检查。"):
        super().__init__(message)
        self.retry_at = retry_at


def plan(doc):
    now = time.time()
    request = doc.get("run_request", {})
    leases = [
        t.get("claim", {}).get("expires_at", 0) for t in doc["tasks"] if t["state"] == "leased"
    ]
    if request.get("state") == "running":
        leases.append(request.get("lease_expires_at", 0))
    live = [value for value in leases if value > now]
    remaining = [t for t in doc["tasks"] if t["state"] in {"ready", "leased"}]
    next_task = min(
        remaining,
        key=lambda t: (
            {"verify": 0, "review": 1, "ocr": 2, "navigation": 3, "organization": 4}.get(
                t["stage"], 5
            ),
            t.get("page") or 0,
        ),
        default={},
    )
    return dict(
        state="waiting" if live else "available",
        retry_at=max(live) if live else None,
        stage=next_task.get("stage"),
        page=next_task.get("page"),
        unfinished_tasks=len(remaining),
        preserved_ocr=sum(bool(p.get("ocr")) for p in doc["pages"]),
        preserved_approved=sum(bool(p.get("approved")) for p in doc["pages"]),
        pending_human=sum(p["state"] == "pending" for p in doc["pages"]),
        automatic_checkpoint_used=bool(request.get("automatic_checkpoint_used")),
        retry_limit=request.get("retry_limit", 3),
        evidence_checked=False,
    )


def recover(store, identity, recovery_id, *, automatic=False):
    if not isinstance(recovery_id, str) or not 1 <= len(recovery_id) <= 128:
        raise ValueError("Recovery identity required")
    snapshot = store.status(identity)
    if receipt := snapshot.get("recoveries", {}).get(recovery_id):
        return receipt
    try:
        with FileLock(store._directory(identity) / ".executor.lock", timeout=0):
            with store._verified(identity) as doc:
                if receipt := doc.get("recoveries", {}).get(recovery_id):
                    return receipt
                request = doc.get("run_request", {})
                if request.get("cancel_requested") or doc["state"] == "cancelled":
                    raise ValueError("Document is stopping for deletion")
                if request.get("pause_requested"):
                    raise ValueError("Document is paused; explicitly resume before recovery")
                status = plan(doc)
                if status["retry_at"]:
                    raise RecoveryWait(status["retry_at"])
                if automatic and (
                    request.get("automatic_checkpoint_used") or request.get("pause_requested")
                ):
                    raise ValueError("Automatic checkpoint recovery already used or paused")
                # Different repeated clicks cannot grant more budget to an already queued batch.
                current = request.get("recovery_id")
                if current and request.get("state") in {"pending", "running"}:
                    return doc["recoveries"][current]
                reset = []
                for task in doc["tasks"]:
                    if task["state"] == "leased":
                        task.setdefault("recovery_history", []).append(dict(task["claim"]))
                        task["state"] = "ready"
                        reset.append(task["task_id"])
                request = store._request_run(doc, resume=not automatic)
                request.update(
                    technical_failures=0,
                    retry_limit=1 if automatic else 3,
                    automatic_checkpoint_used=automatic,
                    checkpoint_recovery_enabled=True,
                    recovery_id=recovery_id,
                    error=None,
                )
                if doc["state"] == "published":
                    request["state"] = "succeeded"
                elif not status["unfinished_tasks"]:
                    request["state"] = "waiting_human"
                else:
                    request["state"] = "pending"
                request["not_before"] = max(
                    0,
                    doc.get("scheduler", {}).get("cooldown_until", 0),
                    time.time() + 30 if automatic else 0,
                )
                request["ready_after"] = request["not_before"]
                receipt = {
                    **status,
                    "state": request["state"],
                    "evidence_checked": True,
                    "recovery_id": recovery_id,
                    "automatic": automatic,
                    "reset_tasks": reset,
                    "retry_limit": request["retry_limit"],
                    "checkpoint_sha256": digest(
                        [doc["source"], doc["pages"], [t.get("receipt") for t in doc["tasks"]]]
                    ),
                }
                doc.setdefault("recoveries", {})[recovery_id] = receipt
                request["history"].append(
                    {"event": "checkpoint_recovery", "at": time.time(), **receipt}
                )
                (store._directory(identity) / ".pause-requested").unlink(missing_ok=True)
                store._save(doc)
                return receipt
    except Timeout as error:
        raise RecoveryWait(time.time() + 5) from error
