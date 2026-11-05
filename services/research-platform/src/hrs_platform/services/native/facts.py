"""Native-to-platform facts; run and book state remain separate contracts."""

import time

from hrs_codex.scheduling import scheduling_plan

from hrs_platform.services.runs.outputs import fingerprint

PROJECTION_POLICY = "codex-platform-v9-page-fragments"


def projection_input(doc):
    # Heartbeat extensions alone do not change the reading or execution projection.
    return fingerprint(
        {
            "policy": PROJECTION_POLICY,
            "coordinator_active": doc.get("run_request", {}).get("state") == "running"
            and doc.get("run_request", {}).get("lease_expires_at", 0) > time.time(),
            "state": doc["state"],
            "revision": doc.get("revision"),
            "pages": doc["pages"],
            "organization": doc.get("organization"),
            "publication": doc.get("publication"),
            "active_publication": doc.get("active_publication"),
            "navigation": doc.get("navigation", {}).get("sha256"),
            "request": {
                k: v for k, v in doc.get("run_request", {}).items() if k not in {"claim", "updated_at"}
            },
            "tasks": [
                {k: t[k] for k in ("task_id", "stage", "page", "state", "retry_after", "receipt") if k in t}
                | {
                    "started_at": t.get("claim", {}).get("started_at"),
                    "active": t["state"] == "leased"
                    and t.get("claim", {}).get("expires_at", 0) > time.time(),
                }
                for t in doc["tasks"]
            ],
            "scheduling": scheduling_plan(doc),
        }
    )


def projection_state(run, doc, scheduling, pending, publication_sha, navigation_sha):
    request = doc.get("run_request", {})
    state = (
        "paused"
        if request.get("pause_requested") or request.get("state") == "paused"
        else "completed"
        if doc["state"] == "published"
        and run["result"].get("codex_indexed_publication_sha256") == publication_sha
        and run["result"].get("codex_indexed_projection_policy") == PROJECTION_POLICY
        and run["result"].get("codex_indexed_navigation_sha256") == navigation_sha
        else "ready"
        if doc["state"] == "published"
        else "awaiting_review"
        if pending and not scheduling["automatic_work_remaining"]
        else "failed"
        if request.get("state") == "retryable_error"
        else "queued"
        if request.get("state") == "pending"
        else "running"
    )
    stage = (
        "paused"
        if state == "paused"
        else "complete"
        if state == "completed"
        else "indexing"
        if state == "ready"
        else "review"
        if pending and not scheduling["automatic_work_remaining"]
        else "organization"
        if all(p["state"] == "approved" for p in doc["pages"])
        else "vision"
        if any(t["stage"] == "review" for t in doc["tasks"])
        else "ocr"
    )
    return state, stage
