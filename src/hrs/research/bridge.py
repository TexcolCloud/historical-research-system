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
from hrs.research.sources import ResearchEvidence, indexed_version, publication_snapshot
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

    def _link_questions(self, card, book_ids):
        """Prioritize an existing card's question links within a fixed context budget."""
        from hrs.research.notebook import ResearchNotebook

        notebook, limit = ResearchNotebook(self.store), 10
        context = notebook.research_context(book_ids=book_ids)
        previous = list(
            dict.fromkeys(finding["question_id"] for finding in card.get("question_findings", []))
        )
        priority, extra, uncovered = {}, 0, max(0, len(previous) - limit)
        for question_id in previous[:limit]:
            try:
                question = notebook.question(question_id)
                if question["status"] == "archived":
                    continue
                frozen = notebook.research_context(book_ids=book_ids, question_id=question_id)
            except (FileNotFoundError, ValueError):
                uncovered += 1
                continue
            priority[question_id] = frozen["questions"][0]
            extra += question["status"] == "resolved"
        questions = {**priority}
        for question in context["questions"]:
            if len(questions) >= limit:
                break
            questions.setdefault(question["id"], question)
        return {
            "questions": list(questions.values()),
            "omitted": max(0, context["total"] + extra - len(questions)),
            "previous_omitted": len(previous) - len(priority),
            "preserve_source_card": bool(uncovered),
        }

    def enqueue_links(self, book_id):
        """Freeze bounded cross-book leads; native author/enrich/verify decide their meaning."""
        evidence = ResearchEvidence(self.settings, self.engine)
        cards, queued = {}, []

        def current(card_id):
            if card_id not in cards:
                try:
                    evidence.require_card(card_id)
                    cards[card_id] = self.workflow.card(card_id)
                except (FileNotFoundError, ValueError):
                    cards[card_id] = None
                except Exception as error:
                    from hrs.library.errors import ServiceError

                    if not isinstance(error, ServiceError) or error.status_code not in {404, 409}:
                        raise
                    cards[card_id] = None
            return cards[card_id]

        for group in self.workflow.link_refresh_candidates(book_id, limit=100):
            targets = [card for target in group["target_card_ids"] if (card := current(target))]
            retained_targets = targets[:10]
            descriptors = [
                {
                    "card_id": card["id"],
                    "version": card["version"],
                    "title": card["title"],
                    "publication_sha256": card["publication_sha256"],
                }
                for card in retained_targets
            ]
            if not descriptors:
                continue
            for card_id in group["card_ids"]:
                card = current(card_id)
                if card is None:
                    continue
                source = self.source_run(card["book_id"])
                snapshot = publication_snapshot(
                    self.settings,
                    self.engine,
                    self.store,
                    self.objects,
                    source,
                    article_ids=[card["article_id"]],
                )
                snapshot["research_scope"] = {
                    "kind": "link_refresh",
                    "source_card_id": card_id,
                    "source_card_version": card["version"],
                    "book_ids": sorted(
                        {card["book_id"], *[t["book_id"] for t in retained_targets]}
                    ),
                    "targets_omitted": max(0, len(targets) - len(descriptors)),
                }
                context = self._link_questions(card, snapshot["research_scope"]["book_ids"])
                snapshot["research_questions"] = context["questions"]
                snapshot["research_questions_omitted"] = context["omitted"]
                snapshot["research_scope"].update(
                    source_findings_omitted=context["previous_omitted"],
                    preserve_source_card=context["preserve_source_card"],
                )
                snapshot["link_target_cards"] = descriptors
                status = self.workflow.enqueue(snapshot, purpose="link_refresh")
                self.projection.sync(status)
                queued.append(status["run_id"])
        return queued

    def refresh_links(self):
        with self.engine.connect() as connection:
            events = (
                connection.execute(
                    select(db.outbox)
                    .join(db.runs)
                    .where(
                        db.outbox.c.kind == "link_research",
                        db.outbox.c.delivered.is_(False),
                        workspace_runs(self.store),
                    )
                    .order_by(db.outbox.c.id)
                    .limit(10)
                )
                .mappings()
                .all()
            )
        for event in events:
            try:
                self.enqueue_links(event["payload"]["book_id"])
                with self.engine.begin() as connection:
                    connection.execute(
                        update(db.outbox)
                        .where(db.outbox.c.id == event["id"])
                        .values(delivered=True)
                    )
            except Exception:  # noqa: BLE001 - checkpoint retained; retry never blocks source publication
                logger.exception("Incremental research linking retained for retry: %s", event["id"])

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
        from hrs.research.notebook import ResearchNotebook

        run = self.source_run(book_id)
        snapshot = publication_snapshot(
            self.settings, self.engine, self.store, self.objects, run, article_ids=article_ids
        )
        context_books = (research_scope or {}).get("book_ids", [str(book_id)])
        if question_id and (research_scope or {}).get("selection") == "workspace_all":
            # The user's unrestricted definition is distinct from an external
            # notebook request selecting at most 200 books.
            context_books = None
        context = ResearchNotebook(self.store).research_context(
            book_ids=context_books, question_id=question_id
        )
        expected_definition = (research_scope or {}).get("definition_version")
        if (
            question_id
            and expected_definition is not None
            and context["questions"][0]["definition_version"] != expected_definition
        ):
            from hrs.library.errors import ServiceError

            raise ServiceError(409, "研究问题已变化，请重新读取后启动。")
        snapshot["research_questions"] = context["questions"]
        snapshot["research_questions_omitted"] = context["omitted"]
        if research_scope:
            snapshot["research_scope"] = research_scope
        status = self.workflow.enqueue(
            snapshot, revision_of=revision_of, reason=reason, purpose=purpose
        )
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
                    from hrs.research.notebook import ResearchNotebook

                    context = ResearchNotebook(self.store).research_context(
                        book_ids=[str(run["book_id"])]
                    )
                    snapshot["research_questions"] = context["questions"]
                    snapshot["research_questions_omitted"] = context["omitted"]
                    retained = any(
                        status.get("purpose", "book") == "book"
                        and status["source_run_id"] == str(run["id"])
                        and status["publication_sha256"] == snapshot["publication_sha256"]
                        and self.workflow.snapshot(status["run_id"])["chapters_sha256"]
                        == snapshot["chapters_sha256"]
                        for status in self.workflow.list_runs(str(run["book_id"]))
                    )
                    if not retained:
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
                if self.notebook_versions.get(status["run_id"]) != status["version"]:
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
