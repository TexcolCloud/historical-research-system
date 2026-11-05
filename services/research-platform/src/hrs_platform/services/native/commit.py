"""Commit a prepared projection only if its SQL input revision is still current."""

import time

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs_platform import models as db
from hrs_platform.services.native.facts import PROJECTION_POLICY, runtime_control_for_document


class ProjectionCommit:
    def __init__(self, engine, *, workflow_test=False):
        self.engine, self.workflow_test = engine, workflow_test

    def apply(
        self,
        run,
        doc,
        *,
        prepared_issues,
        pending,
        bundle,
        state,
        stage,
        key,
        input_sha,
        page_progress,
        scheduling,
        publication_sha,
        navigation_sha,
    ):
        request = doc.get("run_request", {})
        if "runtime_control" in scheduling:
            scheduling = {
                **scheduling,
                "runtime_control": runtime_control_for_document(doc, scheduling["runtime_control"]),
            }
        with self.engine.begin() as connection:
            from hrs_platform.services.deletion import require_active

            require_active(connection, run["book_id"])
            current_revision = connection.scalar(
                select(db.runs.c.revision).where(db.runs.c.id == run["id"]).with_for_update()
            )
            if current_revision != run["revision"]:
                # A human decision arrived while objects were prepared. Retry from it next tick.
                return
            if prepared_issues:
                statement = pg_insert(db.review_issues).values(prepared_issues)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "content": statement.excluded.content,
                            "text_sha256": statement.excluded.text_sha256,
                            "revision": db.review_issues.c.revision + 1,
                        },
                        where=(db.review_issues.c.state == "pending")
                        & db.review_issues.c.draft.is_(None)
                        & db.review_issues.c.content.is_distinct_from(statement.excluded.content),
                    )
                )
            connection.execute(
                update(db.review_issues)
                .where(
                    db.review_issues.c.run_id == run["id"],
                    db.review_issues.c.state == "pending",
                    db.review_issues.c.page.not_in([p["page"] for p in pending]),
                )
                .values(state="superseded", revision=db.review_issues.c.revision + 1)
            )
            revision = run["revision"] + 1
            result = {
                **run["result"],
                "bridge_failures": 0,
                "bridge_not_before": 0,
                "codex_sync_sha256": key,
                "codex_projection_input": input_sha,
                "codex_projected_sql_revision": revision,
                "codex_page_progress": page_progress,
                "codex_navigation_pending_count": sum(
                    e["status"] == "conflict" for e in doc.get("navigation", {}).get("entries", [])
                ),
                "codex_scheduling": scheduling,
                "codex_execution": {
                    "state": request.get("state"),
                    "paused": state == "paused",
                    "technical_failures": request.get("technical_failures", 0),
                    "retry_limit": request.get("retry_limit", 3),
                    "retry_at": request.get("not_before")
                    if request.get("technical_failures", 0) < request.get("retry_limit", 3)
                    else None,
                    "lease_expires_at": request.get("lease_expires_at"),
                    "coordinator_active": request.get("state") == "running"
                    and request.get("lease_expires_at", 0) > time.time(),
                    "stop_reason": request.get("error"),
                    "automatic_work_remaining": scheduling["automatic_work_remaining"],
                    "last_progress_at": scheduling.get("last_completion_at"),
                    "active_tasks": [
                        {
                            "stage": t["stage"],
                            "page": t["page"],
                            "started_at": t.get("claim", {}).get("started_at"),
                        }
                        for t in doc["tasks"]
                        if t["state"] == "leased" and t.get("claim", {}).get("expires_at", 0) > time.time()
                    ],
                },
                "codex_projection_policy": PROJECTION_POLICY,
                "review_initialized": True,
                "published": bool(doc.get("publication") or doc.get("active_publication")),
                "codex_revision_pending": bool(doc.get("active_publication") and doc["state"] != "published"),
                **({"codex_publication_sha256": publication_sha} if publication_sha else {}),
                **(
                    {"codex_projected_navigation_sha256": navigation_sha}
                    if doc["state"] == "published"
                    else {}
                ),
                "progress": {
                    "stage": "vision",
                    "completed": page_progress["reviewed"],
                    "total": len(doc["pages"]),
                },
                "content_acceptance": "workflow_test" if self.workflow_test else "machine_or_human",
            }
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run["id"])
                .values(
                    conversion=bundle,
                    result=result,
                    pending_count=len(pending),
                    state=state,
                    stage=stage,
                    revision=revision,
                    updated_at=func.now(),
                    error=request.get("error")
                    if isinstance(request.get("error"), dict) or not request.get("error")
                    else {"detail": str(request["error"]), "retryable": True},
                )
            )
            book_state = "ready" if state == "completed" else state
            connection.execute(
                update(db.books).where(db.books.c.id == run["book_id"]).values(state=book_state)
            )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run["id"],
                    kind="run.changed",
                    payload={
                        "state": state,
                        "book_state": book_state,
                        "stage": stage,
                        "revision": revision,
                        "pending_count": len(pending),
                        "navigation_pending_count": result["codex_navigation_pending_count"],
                        "revision_pending": result["codex_revision_pending"],
                    },
                )
            )
