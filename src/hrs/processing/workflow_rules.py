"""Pure task, lease and continuation rules shared by workflow commands."""

import time
from uuid import uuid4

from hrs.processing.contracts import TaskRecord, TaskStage
from hrs.processing.errors import LeaseConflict, WorkflowStopped
from hrs.processing.review_contract import validate_issues

STAGES = {"ocr", "review", "verify", "navigation", "organization"}


def require_active(doc: dict, *, allow_paused: bool = False) -> None:
    if doc.get("run_request", {}).get("cancel_requested"):
        raise WorkflowStopped("Document cancellation requested; no further processing is allowed")
    if not allow_paused and doc.get("run_request", {}).get("pause_requested"):
        raise WorkflowStopped("Document is paused; explicitly resume before processing")


def new_task(stage: TaskStage, page: int | None = None) -> TaskRecord:
    return {
        "task_id": uuid4().hex,
        "stage": stage,
        "page": page,
        "state": "ready",
        "created_at": time.time(),
    }


def active_review(doc: dict, task_id: str, lease_token: str) -> dict:
    task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
    if (
        task["stage"] not in {"review", "verify"}
        or task["state"] != "leased"
        or task["claim"]["lease_token"] != lease_token
    ):
        raise LeaseConflict("Original reading requires this active review lease")
    if task["claim"]["expires_at"] <= time.time():
        raise LeaseConflict("Task lease expired; reclaim before reading")
    return task


def review_verdict(result: dict) -> str:
    verdict = result.get("verdict")
    if verdict not in {"approved", "pending", "corrections"}:
        raise ValueError("Review verdict must be approved, pending or corrections")
    validate_issues(result.get("issues", []), strict=result.get("issue_contract_version") == 1)
    if "disposition" in result and result["disposition"] not in {"disputed", "unreadable"}:
        raise ValueError("Pending review disposition must be disputed or unreadable")
    if not isinstance(result.get("normalizations", []), list):
        raise ValueError("Review normalizations must be a list")
    if verdict == "approved" and result.get("issues"):
        raise ValueError("Unresolved issues cannot be approved")
    if verdict == "pending" and not result.get("issues"):
        raise ValueError("Pending review requires an issue explaining the unresolved content")
    return verdict


def request_continuation(doc: dict, *, resume: bool = False, followup: bool = False) -> dict:
    request = doc.get("run_request")
    if request is None:
        request = {
            "document_id": doc["id"],
            "state": "pending",
            "attempts": 0,
            "technical_failures": 0,
            "checkpoint_recovery_enabled": True,
            "retry_limit": 3,
            "not_before": 0,
            "ready_after": 0,
            "history": [],
        }
        doc["run_request"] = request
    if doc["state"] == "published":
        request["state"] = "succeeded"
        return request
    if request.get("pause_requested"):
        if not resume:
            return request
        request.pop("pause_requested", None)
        if doc["state"] == "paused":
            doc["state"] = (
                "pending" if any(p["state"] == "pending" for p in doc["pages"]) else "processing"
            )
    if request["state"] == "running":
        if resume and request["lease_expires_at"] <= time.time():
            request["history"].append(
                {"event": "lease_expired", "at": time.time(), "owner": request["owner"]}
            )
            request["state"] = "retryable_error"
        else:
            if followup:
                request["continuation_requested"] = True
            return request
    if request.get("technical_failures", 0) >= request.get("retry_limit", 3) and not resume:
        return request
    if resume:
        request["history"].append({"event": "explicit_resume", "at": time.time()})
        request["technical_failures"] = 0
        request.update(
            retry_limit=3,
            automatic_checkpoint_used=False,
            checkpoint_recovery_enabled=True,
            error=None,
        )
    if request["state"] not in {"pending", "retryable_error"} or resume:
        request.update(state="pending", not_before=0)
    request["ready_after"] = request["not_before"]
    return request
