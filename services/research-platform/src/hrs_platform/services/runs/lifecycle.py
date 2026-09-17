"""Transactional run state and progress shared by API and workers."""

from sqlalchemy import func, insert, select, update

from hrs_platform import models as db


class RunLifecycle:
    def __init__(self, engine):
        self.engine = engine

    def report_progress(self, run_id, completed, total):
        # Observability only: never change the content revision or release state.
        progress = {"stage": "vision", "completed": min(completed, total), "total": total}
        with self.engine.begin() as connection:
            run = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            result = run["result"] or {}
            if run["stage"] != "vision" or run["state"] != "processing" or result.get("progress") == progress:
                return
            connection.execute(
                update(db.runs).where(db.runs.c.id == run_id).values(result={**result, "progress": progress})
            )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"], run_id=run_id, kind="run.progress", payload={"progress": progress}
                )
            )

    def transition(self, run_id, state, stage, **values):
        with self.engine.begin() as connection:
            from hrs_platform.services.deletion import require_run_active

            require_run_active(connection, run_id)
            run = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(
                    state=state, stage=stage, revision=run["revision"] + 1, updated_at=func.now(), **values
                )
            )
            book_state = "ready" if (run["result"] or {}).get("published") else state
            if run["kind"] == "book":
                connection.execute(
                    update(db.books).where(db.books.c.id == run["book_id"]).values(state=book_state)
                )
            if state == "failed":
                # The workflow has drained its activities before recording terminal failure.
                # Close nodes left by worker loss or an exception before local cleanup.
                connection.execute(
                    update(db.execution_nodes)
                    .where(
                        db.execution_nodes.c.run_id == run_id,
                        db.execution_nodes.c.state.in_(["running", "waiting"]),
                    )
                    .values(state="failed", finished_at=func.now())
                )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run_id,
                    kind="run.changed",
                    payload={
                        "state": state,
                        "stage": stage,
                        "revision": run["revision"] + 1,
                        "run_kind": run["kind"],
                        "book_state": book_state if run["kind"] == "book" else None,
                    },
                )
            )
