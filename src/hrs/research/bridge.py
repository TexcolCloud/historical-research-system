"""Durably enqueue post-index research without changing source publication state."""

import logging
import time

from sqlalchemy import insert, select, update
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
        self.index_worker = None
        self.notebook_versions = {}

    def enqueue_links(self, book_id):
        from hrs.library.errors import ServiceError

        raise ServiceError(410, "自动研究补链已停用；新资料通过自动制卡整理实体和原文关系。")

    def refresh_links(self):
        """Acknowledge retired work without starting a second research workflow."""
        with self.engine.begin() as connection:
            events = (
                connection.execute(
                    select(db.runs, db.outbox.c.id.label("outbox_id"))
                    .join(db.runs)
                    .where(
                        db.outbox.c.kind == "link_research",
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
                    require_workspace(self.engine, event, self.store, connection=connection)
                except WrongWorkspace:
                    continue
                retired = connection.execute(
                    update(db.outbox)
                    .where(db.outbox.c.id == event["outbox_id"], db.outbox.c.delivered.is_(False))
                    .values(delivered=True)
                    .returning(db.outbox.c.id)
                ).scalar_one_or_none()
                if retired is not None:
                    connection.execute(
                        insert(db.events).values(
                            run_id=event["id"],
                            book_id=event["book_id"],
                            kind="cards.legacy_link_retired",
                            payload={
                                "outbox_id": event["outbox_id"],
                                "reason": "source_cards_only",
                            },
                        )
                    )

    def start_indexing(self):
        from hrs.research.discovery import DiscoveryWorker, ResearchDiscovery

        self.index_worker = DiscoveryWorker(
            ResearchDiscovery(self.settings, self.engine, self.objects, self.store)
        )
        self.index_worker.start()

    def stop_indexing(self):
        if self.index_worker:
            self.index_worker.stop()

    def rebuild_discovery(self, source_run_id):
        """Rebuild only the current source's derived read index, preserving native evidence."""
        from hrs.processing.workspace import identity
        from hrs.research.discovery import enqueue_discovery
        from hrs.research.sources import current_card_ids

        require_workspace(self.engine, get_run(self.engine, source_run_id), self.store)
        workspace_id = identity(self.store)["workspace_id"]
        with self.engine.begin() as connection:
            rows = (
                connection.execute(
                    select(db.research_runs).where(
                        db.research_runs.c.source_run_id == source_run_id,
                        db.research_runs.c.workspace_id == workspace_id,
                        db.research_runs.c.id.in_(
                            select(db.research_cards.c.run_id).where(
                                db.research_cards.c.id.in_(current_card_ids(workspace_id))
                            )
                        ),
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                enqueue_discovery(
                    connection,
                    source_run_id,
                    row["id"],
                    row["summary"]["discovery_input_sha256"],
                    rebuild=True,
                )
        return {
            "queued": len(rows),
            "research_run_ids": [row["id"] for row in rows],
            "source_run_id": source_run_id,
        }

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

    def start(
        self,
        book_id,
        *,
        revision_of=None,
        reason=None,
        question_id=None,
        article_ids=None,
        purpose="book",
        research_scope=None,
    ):
        from hrs.library.errors import ServiceError

        if question_id is not None or purpose not in {"book", "card_revision"}:
            raise ServiceError(410, "研究问题及自动论证已停用；原文索引完成后会自动制卡。")
        run = self.source_run(book_id)
        snapshot = publication_snapshot(
            self.settings, self.engine, self.store, self.objects, run, article_ids=article_ids
        )
        snapshot["research_questions"] = []
        snapshot["research_questions_omitted"] = 0
        if not revision_of and purpose == "book":
            retained = self._retained_source(snapshot)
            if retained is not None:
                return retained
        if research_scope:
            snapshot["research_scope"] = research_scope
        status = self.workflow.enqueue(
            snapshot, revision_of=revision_of, reason=reason, purpose=purpose
        )
        self.projection.sync(status)
        return status

    def _retained_source(self, snapshot):
        """Policy upgrades or index rebuilds never reprocess an existing source version."""
        for status in self.workflow.list_runs(str(snapshot["book_id"])):
            if (
                status.get("purpose", "book") == "book"
                and status["source_run_id"] == snapshot["source_run_id"]
                and status["publication_sha256"] == snapshot["publication_sha256"]
                and self.workflow.snapshot(status["run_id"])["chapters_sha256"]
                == snapshot["chapters_sha256"]
            ):
                return status
        return None

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
                    snapshot["research_questions"] = []
                    snapshot["research_questions_omitted"] = 0
                    if self._retained_source(snapshot) is None:
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
        from hrs.research.notebook import ResearchNotebook

        notebook = ResearchNotebook(self.store)
        for status in self.workflow.list_runs():
            if status["source_run_id"] not in active:
                continue
            try:
                if versions.get(status["run_id"]) != status["version"]:
                    self.projection.sync(status)
                # HTTP decisions and controls may already have updated SQL. The
                # notebook independently replays each native authority version.
                if (
                    not status.get("policy", {}).get("card_only")
                    and self.notebook_versions.get(status["run_id"]) != status["version"]
                ):
                    notebook.sync_run(status)
                    snapshot = self.workflow.snapshot(status["run_id"])
                    external = self.workflow.sources(status["run_id"])
                    from hrs.research.projection import located_card

                    for card in self.workflow.list_cards(run_id=status["run_id"]):
                        notebook.sync_card(located_card(card, snapshot, external))
                    self.notebook_versions[status["run_id"]] = status["version"]
            except Exception:  # noqa: BLE001 - independent projection/notebook retries
                logger.exception("Research projections retained for retry: %s", status["run_id"])
        self.refresh_links()

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
