"""Durably enqueue post-index research without changing source publication state."""

import logging
import time

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs.library.books import get_run
from hrs.library.native_workspace import WrongWorkspace, require_workspace, workspace_runs
from hrs.library.object_ownership import owned_objects
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.processing.workspace import resolve_workspace
from hrs.research.projection import ResearchProjection
from hrs.research.sources import indexed_version, publication_snapshot
from hrs.storage import models as db

logger = logging.getLogger(__name__)


def enqueue_research_event(connection, run_id, version):
    key = f"research:{run_id}:{version['publication_sha256']}:{version['chapters_sha256']}"
    connection.execute(
        pg_insert(db.outbox)
        .values(
            dedup_key=key,
            run_id=str(run_id),
            kind="start_research",
            payload=version,
        )
        .on_conflict_do_nothing(index_elements=[db.outbox.c.dedup_key])
    )


class ResearchBridge:
    def __init__(self, settings, engine, *, store=None, objects=None):
        self.settings, self.engine = settings, engine
        self.store = store or Store(resolve_workspace(settings.codex_root))
        self.objects = objects or owned_objects(settings, engine)
        self.workflow = ResearchWorkflow(self.store)
        self.projection = ResearchProjection(engine, self.objects, self.workflow)
        self.reconcile_at = 0

    def source_run(self, book_id):
        with self.engine.connect() as connection:
            runs = (
                connection.execute(
                    select(db.runs)
                    .join(db.books)
                    .where(
                        db.runs.c.book_id == str(book_id),
                        db.runs.c.kind == "book",
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                    )
                    .order_by(db.runs.c.created_at.desc())
                )
                .mappings()
                .all()
            )
        for run in runs:
            if indexed_version(run):
                return require_workspace(self.engine, run, self.store)
        from hrs.library.errors import ServiceError

        raise ServiceError(409, "本书尚无已完成索引的原文版本。")

    def start(self, book_id, *, revision_of=None, reason=None):
        run = self.source_run(book_id)
        snapshot = publication_snapshot(self.settings, self.engine, self.store, self.objects, run)
        status = self.workflow.enqueue(snapshot, revision_of=revision_of, reason=reason)
        self.projection.sync(status)
        return status

    def reconcile(self):
        with self.engine.connect() as connection:
            runs = (
                connection.execute(
                    select(db.runs)
                    .join(db.books)
                    .where(
                        db.runs.c.kind == "book",
                        workspace_runs(self.store),
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                    )
                )
                .mappings()
                .all()
            )
        with self.engine.begin() as connection:
            for run in runs:
                if version := indexed_version(run):
                    enqueue_research_event(connection, run["id"], version)
        self.reconcile_at = time.monotonic() + 60

    def tick(self):
        active = self.cancel_inactive()
        if time.monotonic() >= self.reconcile_at:
            self.reconcile()
        with self.engine.connect() as connection:
            events = (
                connection.execute(
                    select(db.outbox)
                    .join(db.runs)
                    .where(
                        db.outbox.c.kind == "start_research",
                        db.outbox.c.delivered.is_(False),
                        workspace_runs(self.store),
                    )
                    .order_by(db.outbox.c.id)
                    .limit(50)
                )
                .mappings()
                .all()
            )
        for event in events:
            try:
                run = require_workspace(
                    self.engine, get_run(self.engine, event["run_id"]), self.store
                )
                # A newer visible version supersedes an unconsumed request, never binds it to wrong text.
                if indexed_version(run) == {
                    key: event["payload"][key] for key in ("publication_sha256", "chapters_sha256")
                }:
                    snapshot = publication_snapshot(
                        self.settings, self.engine, self.store, self.objects, run
                    )
                    self.workflow.enqueue(snapshot)
                with self.engine.begin() as connection:
                    connection.execute(
                        update(db.outbox)
                        .where(db.outbox.c.id == event["id"])
                        .values(delivered=True)
                    )
            except WrongWorkspace:
                continue
            except Exception:  # noqa: BLE001 - research failures never fail a source run
                logger.exception("Research enqueue retained for retry: %s", event["id"])
        versions = self.projection.versions()
        for status in self.workflow.list_runs():
            if status["source_run_id"] not in active:
                continue
            if versions.get(status["run_id"]) != status["version"]:
                try:
                    self.projection.sync(status)
                except Exception:  # noqa: BLE001 - independent read projection retries
                    logger.exception("Research projection retained for retry: %s", status["run_id"])

    def cancel_inactive(self):
        """Stop native producers before deleted-book objects and SQL rows disappear."""
        with self.engine.connect() as connection:
            active = set(
                map(
                    str,
                    connection.scalars(
                        select(db.runs.c.id)
                        .join(db.books)
                        .where(
                            db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                            workspace_runs(self.store),
                        )
                    ),
                )
            )
        for status in self.workflow.list_runs():
            if status["source_run_id"] not in active and status["state"] != "cancelled":
                self.workflow.cancel_book(status["book_id"])
        return active
