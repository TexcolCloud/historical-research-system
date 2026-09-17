"""Explicit user recovery requests, delivered once per durable workflow identity."""

from sqlalchemy import insert, select, update

from hrs_platform import models as db
from hrs_platform.domain.errors import ServiceError


def workflow_id(kind, run_id, attempt=0):
    base = f"{'cards' if kind == 'cards' else 'book'}/{run_id}"
    return base + f"/retry/{attempt}" if attempt else base


def retry_run(engine, run_id, request_id):
    run_id = str(run_id)
    key = f"retry:{run_id}:{request_id}"
    with engine.begin() as connection:
        run = (
            connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        if run is None:
            raise ServiceError(404, "任务不存在。")
        from hrs_platform.services.deletion import require_active

        require_active(connection, run["book_id"])
        if connection.scalar(select(db.outbox.c.id).where(db.outbox.c.dedup_key == key)):
            return dict(run)
        if run["state"] != "failed" and not (run["kind"] == "cards" and run["state"] == "needs_revision"):
            raise ServiceError(409, "仅未完成的失败任务需要重试，正在处理和已完成的任务不会重复执行。")
        attempt = run["recovery_attempt"] + 1
        values = {
            "state": "queued",
            "error": None,
            "recovery_attempt": attempt,
            "revision": run["revision"] + 1,
        }
        if run["kind"] == "cards" and run["state"] == "needs_revision":
            values["result"] = {
                **(run["result"] or {}),
                "card_revision": (run["result"] or {}).get("card_revision", 0) + 1,
            }
        connection.execute(update(db.runs).where(db.runs.c.id == run_id).values(**values))
        published = (run["result"] or {}).get("published")
        if run["kind"] == "book" and not published:
            connection.execute(update(db.books).where(db.books.c.id == run["book_id"]).values(state="queued"))
        connection.execute(
            insert(db.outbox).values(
                dedup_key=key,
                run_id=run_id,
                kind="retry_run",
                payload={"recovery_attempt": attempt, "run_kind": run["kind"]},
            )
        )
        connection.execute(
            insert(db.events).values(
                book_id=run["book_id"],
                run_id=run_id,
                kind="run.changed",
                payload={
                    "state": "queued",
                    "book_state": "ready" if published else "queued",
                    "stage": run["stage"],
                    "run_kind": run["kind"],
                    "revision": values["revision"],
                },
            )
        )
    return {**run, **values}
