"""Commit a prepared projection only if its SQL input revision is still current."""

import time

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs.library.projection.facts import (
    NAVIGATION_REVIEW_POLICY,
    PROJECTION_POLICY,
    REVIEW_AUTHORITY_POLICY,
    runtime_control_for_document,
)
from hrs.processing.acceptance import publication_acceptance
from hrs.processing.navigation import navigation_requires_confirmation
from hrs.storage import models as db


class ProjectionCommit:
    def __init__(self, engine):
        self.engine = engine

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
        publication_projection=None,
    ):
        request = doc.get("run_request", {})
        if "runtime_control" in scheduling:
            scheduling = {
                **scheduling,
                "runtime_control": runtime_control_for_document(doc, scheduling["runtime_control"]),
            }
        with self.engine.begin() as connection:
            from hrs.library.deletion import require_active

            require_active(connection, run["book_id"])
            current_revision = connection.scalar(
                select(db.runs.c.revision).where(db.runs.c.id == run["id"]).with_for_update()
            )
            if current_revision != run["revision"]:
                # A human decision arrived while objects were prepared. Retry from it next tick.
                return
            if publication_projection and publication_projection["chapter_rows"] is not None:
                connection.execute(delete(db.chapters).where(db.chapters.c.run_id == run["id"]))
                connection.execute(
                    pg_insert(db.chapters)
                    .values(publication_projection["chapter_rows"])
                    .on_conflict_do_nothing()
                )
                connection.execute(
                    insert(db.events).values(
                        book_id=run["book_id"],
                        run_id=run["id"],
                        kind="library.revised"
                        if run["result"].get("codex_publication_sha256")
                        else "book.published",
                        payload={
                            "state": "ready",
                            "stage": "indexing",
                            "revision": run["revision"] + 1,
                        },
                    )
                )
            for issue in prepared_issues:
                current = (
                    connection.execute(
                        select(db.review_issues)
                        .where(db.review_issues.c.id == issue["id"])
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    connection.execute(insert(db.review_issues).values(**issue))
                    continue
                if current["state"] == "pending" and current["content"] == issue["content"]:
                    continue
                # A SQL draft can arrive while source artifacts are being prepared.
                # Preserve the locked current draft, not the earlier snapshot.
                draft = current["draft"]
                if draft:
                    draft = {
                        "base_text_sha256": current["text_sha256"],
                        "base_content": current["content"],
                        **draft,
                    }
                connection.execute(
                    update(db.review_issues)
                    .where(db.review_issues.c.id == issue["id"])
                    .values(
                        content=issue["content"],
                        text_sha256=issue["text_sha256"],
                        state="pending",
                        draft=draft,
                        replacement=None,
                        revision=current["revision"] + 1,
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
                "codex_navigation_review_policy": NAVIGATION_REVIEW_POLICY,
                "codex_navigation_pending_count": sum(
                    navigation_requires_confirmation(entry)
                    for entry in doc.get("navigation", {}).get("entries", [])
                ),
                "codex_navigation_missing_page_count": len(
                    set(
                        doc.get("navigation", {}).get("missing_pages", [])
                        + doc.get("navigation", {}).get("unresolved_pages", [])
                    )
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
                        if t["state"] == "leased"
                        and t.get("claim", {}).get("expires_at", 0) > time.time()
                    ],
                },
                "codex_projection_policy": PROJECTION_POLICY,
                "codex_review_authority_policy": REVIEW_AUTHORITY_POLICY,
                "review_initialized": True,
                "published": bool(doc.get("publication") or doc.get("active_publication")),
                "codex_revision_pending": bool(
                    doc.get("active_publication") and doc["state"] != "published"
                ),
                **({"codex_publication_sha256": publication_sha} if publication_sha else {}),
                **(
                    {"codex_organization_sha256": doc["publication"].get("organization_sha256")}
                    if publication_projection
                    else {}
                ),
                **(
                    {"codex_reading_projection": publication_projection["reading_projection"]}
                    if publication_projection
                    else {}
                ),
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
                "content_acceptance": publication_acceptance(doc),
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
                        "navigation_missing_page_count": result[
                            "codex_navigation_missing_page_count"
                        ],
                        "revision_pending": result["codex_revision_pending"],
                    },
                )
            )
