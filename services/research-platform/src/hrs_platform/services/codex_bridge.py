"""Project the native Codex workspace into the existing upload, reader and search contracts.

Codex is the processing authority. SQL/S3 retain the old application's immutable
reading projections; neither Temporal nor the old recognition providers run here.
"""

import logging
import threading
import time
from uuid import UUID, uuid5

from filelock import FileLock, Timeout
from hrs_codex.application import prepare
from hrs_codex.progress import page_progress as document_progress
from hrs_codex.runner import Runner
from hrs_codex.store import Store
from hrs_codex.workspace import resolve_workspace
from sqlalchemy import func, insert, or_, select, update

from hrs_platform import models as db
from hrs_platform.services.books import get_run
from hrs_platform.services.documents.review import Review
from hrs_platform.services.native.artifacts import ProjectionArtifacts
from hrs_platform.services.native.changes import NativeChanges
from hrs_platform.services.native.commit import ProjectionCommit
from hrs_platform.services.native.facts import (
    NAVIGATION_REVIEW_POLICY,
    PROJECTION_POLICY,
    projection_input,
    projection_state,
)
from hrs_platform.services.native.publication import NativePublication
from hrs_platform.services.native.publication import article_groups as article_groups
from hrs_platform.services.runs.outputs import Outputs, fingerprint
from hrs_platform.services.storage import objects_for

logger = logging.getLogger(__name__)


class CodexBridge:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.store = Store(resolve_workspace(settings.codex_root))
        self.executor = Runner(self.store)
        self.objects = objects_for(settings, engine)
        self.outputs, self.review = Outputs(settings, engine), Review(settings, engine)
        self.stop_event = threading.Event()
        self.thread = None
        self.verified_at = {}
        self.changes = NativeChanges(self.store.checkpoints)
        self.runtime_projection_sha = None
        self.artifacts = ProjectionArtifacts(self.store, self.objects, self.review, engine)
        self.commit = ProjectionCommit(
            engine, workflow_test=(self.store.root / ".workflow-test-authorization.json").exists()
        )
        self.publication = NativePublication(settings, engine, self.objects)

    def start(self):
        self.executor.start()
        self.thread = threading.Thread(target=self.loop, name="codex-platform-bridge", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.executor.stop()
        if self.thread:
            self.thread.join(timeout=10)

    def loop(self):
        # One local bridge owns projections; its executor consumes the shared durable queue.
        try:
            with FileLock(self.store.root / ".platform-bridge.lock", timeout=0):
                while not self.stop_event.is_set():
                    try:
                        self.tick()
                    except Exception:
                        logger.exception("Codex platform bridge will retry retained work")
                    self.stop_event.wait(1)
        except Timeout:
            logger.info("Another process already owns this workspace's platform bridge")

    def tick(self):
        with self.engine.connect() as connection:
            events = (
                connection.execute(
                    select(db.outbox)
                    .join(db.runs, db.runs.c.id == db.outbox.c.run_id)
                    .where(
                        db.outbox.c.delivered.is_(False),
                        db.runs.c.kind == "book",
                        db.outbox.c.kind.in_(["start_book", "review_changed", "retry_run"]),
                        or_(
                            db.runs.c.result["processing_backend"].astext == "codex",
                            db.runs.c.result["codex_document_id"].astext.is_not(None),
                        ),
                    )
                    .order_by(db.outbox.c.id)
                    .limit(50)
                )
                .mappings()
                .all()
            )
        for event in events:
            try:
                self.dispatch(event)
                run = get_run(self.engine, event["run_id"])
                if identity := (run["result"] or {}).get("codex_document_id"):
                    self.changes.pending.setdefault(identity, set())
            except Exception as error:  # noqa: BLE001 - retain each independent run's recoverable failure
                self.failure(event["run_id"], error)
        runtime_sha = fingerprint(self.store.runtime_control.snapshot())
        if runtime_sha != self.runtime_projection_sha:
            self.runtime_projection_sha = runtime_sha
            self.changes.reconcile_at = 0
        changed = self.changes.poll()
        if changed == []:
            self.delete_pending()
            return
        with self.engine.connect() as connection:
            query = (
                select(db.runs)
                .join(db.books)
                .where(
                    db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                    db.runs.c.result["codex_document_id"].astext.is_not(None),
                )
            )
            if changed is not None:
                query = query.where(db.runs.c.result["codex_document_id"].astext.in_(changed))
            runs = connection.execute(query).mappings().all()
        failed = set()
        for run in runs:
            identity = run["result"]["codex_document_id"]
            if not self.due(run):
                failed.add(identity)
                self.changes.retry_at[identity] = run["result"].get("bridge_not_before", time.time() + 30)
                continue
            try:
                self.sync(run)
            except Exception as error:  # noqa: BLE001 - a failed projection must not stop other books
                failed.add(identity)
                self.changes.retry_at[identity] = time.time() + 30
                self.failure(run["id"], error)
        for identity in {run["result"]["codex_document_id"] for run in runs} - failed:
            self.changes.acknowledge(identity)
        candidates = set(changed) if changed is not None else set(self.changes.pending)
        for identity in candidates - {run["result"]["codex_document_id"] for run in runs}:
            # A future intake/outbox event reintroduces newly linked documents.
            self.changes.acknowledge(identity)
        self.delete_pending()

    def delete_pending(self):
        from hrs_platform.services.deletion_cleanup import BookCleanup, remove_cache

        with self.engine.connect() as connection:
            books = connection.scalars(
                select(db.book_deletions.c.book_id).where(db.book_deletions.c.state == "pending")
            ).all()
        for book_id in books:
            deletion = BookCleanup(self.settings, self.engine)
            runs = deletion.runs(book_id)
            native_ids = {
                r["result"]["codex_document_id"] for r in runs if (r["result"] or {}).get("codex_document_id")
            }
            # Historical in-flight workflows retain their explicit legacy owner.
            if not native_ids and any((r["result"] or {}).get("processing_backend") != "codex" for r in runs):
                continue
            unshared, waiting = [], False
            for doc_id in native_ids:
                with self.engine.connect() as connection:
                    shared = connection.scalar(
                        select(func.count())
                        .select_from(db.runs)
                        .join(db.books)
                        .where(
                            db.runs.c.result["codex_document_id"].astext == doc_id,
                            db.runs.c.book_id != book_id,
                            db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                        )
                    )
                if not shared and self.store.document_directory(doc_id).exists():
                    result = self.store.cancel_document(doc_id)
                    waiting |= result["state"] == "running"
                    unshared.append(doc_id)
            if waiting:
                continue
            try:
                for doc_id in unshared:
                    self.store.forget_document(doc_id)
                remove_cache(self.store.root / "platform-intake", [r["id"] for r in runs])
                deletion.erase_book(book_id)
            except Exception:  # noqa: BLE001 - preserve deletion receipt for an explicit retry
                deletion.deletion_failed(book_id)

    def failure(self, run_id, error):
        logger.warning("Codex bridge run %s: %s", run_id, type(error).__name__)
        with self.engine.begin() as connection:
            result = (
                connection.scalar(select(db.runs.c.result).where(db.runs.c.id == run_id).with_for_update())
                or {}
            )
            failures = result.get("bridge_failures", 0) + 1
            result = {
                **result,
                "bridge_failures": failures,
                "bridge_not_before": time.time() + 30 * 2 ** min(failures - 1, 4),
            }
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(
                    state="failed",
                    revision=db.runs.c.revision + 1,
                    updated_at=func.now(),
                    result=result,
                    error={"code": "codex_bridge", "detail": str(error), "retryable": True},
                )
            )
            if not result.get("published"):
                book_id = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == run_id))
                connection.execute(update(db.books).where(db.books.c.id == book_id).values(state="failed"))
            changed = connection.execute(select(db.runs).where(db.runs.c.id == run_id)).mappings().one()
            connection.execute(
                insert(db.events).values(
                    book_id=changed["book_id"],
                    run_id=run_id,
                    kind="run.changed",
                    payload={
                        "state": "failed",
                        "stage": changed["stage"],
                        "revision": changed["revision"],
                        "error": changed["error"],
                    },
                )
            )

    def dispatch(self, event):
        run = get_run(self.engine, event["run_id"])
        if event["kind"] == "retry_run" and (run["result"] or {}).get("codex_document_id"):
            from hrs_platform.services.runs.recovery import resume_native

            return resume_native(self, event, run)
        if event["kind"] == "retry_run":
            with self.engine.begin() as connection:
                connection.execute(
                    update(db.runs)
                    .where(db.runs.c.id == run["id"])
                    .values(
                        result={**(run["result"] or {}), "bridge_failures": 0, "bridge_not_before": 0},
                        error=None,
                    )
                )
        elif not self.due(run):
            return
        if event["kind"] == "start_book" and not (run["result"] or {}).get("codex_document_id"):
            directory = self.store.root / "platform-intake" / run["id"]
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "original.pdf"
            source = run["source"]
            if source.get("upload_key"):
                response = self.objects.client.get_object(
                    Bucket=self.objects.bucket, Key=source["upload_key"]
                )
                body = response["Body"]
                try:
                    with path.open("wb") as stream:
                        while chunk := body.read(1024 * 1024):
                            stream.write(chunk)
                finally:
                    body.close()
                if path.stat().st_size != source["byte_length"]:
                    raise ValueError("Retained upload length differs from the upload receipt")
            else:
                path.write_bytes(self.objects.read_bytes(source))
            doc = prepare(self.store, str(path), self.title(run["book_id"]))
            retained = self.objects.put_file(path, "application/pdf", run_id=run["id"])
            self.store.request_run(doc["id"])
            with self.engine.begin() as connection:
                current = connection.scalar(select(db.runs.c.result).where(db.runs.c.id == run["id"])) or {}
                connection.execute(
                    update(db.runs)
                    .where(db.runs.c.id == run["id"])
                    .values(
                        source=retained,
                        state="running",
                        stage="ocr",
                        result={**current, "codex_document_id": doc["id"]},
                    )
                )
        elif event["kind"] not in {"review_changed", "start_book", "retry_run"}:
            # Legacy deletion has its own immutable ownership manifest and worker.
            return
        with self.engine.begin() as connection:
            connection.execute(update(db.outbox).where(db.outbox.c.id == event["id"]).values(delivered=True))

    def title(self, book_id):
        with self.engine.connect() as connection:
            return connection.scalar(select(db.books.c.title).where(db.books.c.id == book_id))

    def apply_decisions(self, run, doc):
        if not any(p["state"] == "pending" for p in doc["pages"]):
            return False
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(db.review_issues, db.review_decisions.c.receipt)
                    .join(db.review_decisions, db.review_decisions.c.issue_id == db.review_issues.c.id)
                    .where(db.review_issues.c.run_id == run["id"], db.review_issues.c.state == "approved")
                )
                .mappings()
                .all()
            )
        applied = False
        for row in rows:
            if doc["pages"][row["page"] - 1]["state"] != "pending":
                continue
            packet = self.store.human_packet(doc["id"], row["page"])
            body = self.review.read_json(row["content"])
            if body.get("codex_revision", 1) != doc.get("revision", 1):
                continue
            if body.get("codex_input_sha256") != packet["input_sha256"]:
                raise ValueError("Native review input changed; the old platform decision cannot replace it")
            text = (
                self.objects.read_bytes(row["replacement"]).decode("utf-8")
                if row["replacement"]
                else body["text"]
            )
            scope = (
                "workflow_test"
                if (self.store.root / ".workflow-test-authorization.json").exists()
                else "content"
            )
            self.store.record_human_review(
                doc["id"],
                row["page"],
                input_sha256=packet["input_sha256"],
                markdown=text,
                verdict="approved",
                decision_scope=scope,
                note="用户授权流程测试放行，未证明内容准确率" if scope == "workflow_test" else "本地确认",
            )
            applied = True
        return applied

    @staticmethod
    def projection_input(doc):
        return projection_input(doc)

    def sync(self, run):
        if not self.due(run) or run["result"].get("codex_recovery", {}).get("state") in {
            "checking",
            "waiting",
            "blocked",
        }:
            return
        doc_id = run["result"]["codex_document_id"]
        snapshot = self.store.status(doc_id)
        input_sha = projection_input(snapshot, self.store.runtime_control.snapshot())
        unchanged = (
            run["result"].get("codex_projection_input") == input_sha
            and run["result"].get("codex_projected_sql_revision") == run["revision"]
        )
        indexing = snapshot["state"] == "published" and (
            run["result"].get("codex_indexed_publication_sha256")
            != (snapshot.get("publication") or {}).get("markdown_sha256")
            or run["result"].get("codex_indexed_projection_policy") != PROJECTION_POLICY
            or run["result"].get("codex_indexed_navigation_sha256")
            != snapshot.get("navigation", {}).get("sha256")
        )
        if unchanged and not indexing and time.monotonic() - self.verified_at.get(doc_id, 0) < 60:
            return
        # A periodic full audit still detects external tampering. No acceptance uses this cache.
        if self.store.checkpoints.ledger:
            audit = indexing or time.monotonic() - self.verified_at.get(doc_id, 0) >= 60
            self.store.verify_evidence(snapshot, None if audit else self.changes.pages(snapshot))
            if audit:
                self.verified_at[doc_id] = time.monotonic()
            doc = snapshot
        else:
            doc = self.store.refresh_navigation(doc_id)
            self.verified_at[doc_id] = time.monotonic()
        publication_sha = (doc.get("publication") or {}).get("markdown_sha256")
        navigation_sha = doc.get("navigation", {}).get("sha256")
        run = self.publication.index_if_needed(run, doc)
        if self.apply_decisions(run, doc):
            doc = self.store.status(doc_id)
        input_sha = projection_input(doc, self.store.runtime_control.snapshot())
        navigation_sha = doc.get("navigation", {}).get("sha256")
        scheduling = self.store.scheduling_plan(doc_id)
        page_progress = document_progress(doc)
        key = fingerprint(
            {
                "projection_policy": PROJECTION_POLICY,
                "navigation_review_policy": NAVIGATION_REVIEW_POLICY,
                "page_progress": page_progress,
                "pages": doc["pages"],
                "tasks": [
                    (t["task_id"], t["state"], t.get("claim", {}).get("started_at")) for t in doc["tasks"]
                ],
                "organization": doc["organization"],
                "publication": doc["publication"],
                "navigation": navigation_sha,
                "scheduling": scheduling,
                "request": doc.get("run_request", {}).get("state"),
            }
        )
        if run["result"].get("codex_sync_sha256") == key and unchanged:
            return
        bundle, boundaries = self.artifacts.bundle(run, doc)
        pending = [p for p in doc["pages"] if p["state"] == "pending"]
        state, stage = projection_state(run, doc, scheduling, pending, publication_sha, navigation_sha)
        if doc["organization"]:
            self.publish_chapters(run, doc)
        prepared_issues = self.artifacts.issues(run, doc, pending, boundaries)
        self.project_nodes(run, doc, state)
        self.commit.apply(
            run,
            doc,
            prepared_issues=prepared_issues,
            pending=pending,
            bundle=bundle,
            state=state,
            stage=stage,
            key=key,
            input_sha=input_sha,
            page_progress=page_progress,
            scheduling=scheduling,
            publication_sha=publication_sha,
            navigation_sha=navigation_sha,
        )

    def project_nodes(self, run, doc, state):
        doc_id = doc["id"]
        coordinator = str(uuid5(UUID(run["id"]), "codex:coordinator"))
        records = [
            dict(
                key="codex:coordinator",
                kind="agent",
                label="Codex 协调器",
                objective="逐页并行识别与独立核验；不确定项交人工；整书通过后整理入库",
                state="completed"
                if state == "completed"
                else "paused"
                if state == "paused"
                else "queued"
                if state == "queued"
                else "waiting"
                if state == "awaiting_review"
                else "failed"
                if state == "failed"
                else "running",
                details={
                    "document_id": doc_id,
                    "processing_policy": doc.get("processing_policy"),
                    "publication": doc.get("publication"),
                },
            )
        ]
        for task in doc["tasks"]:
            label = {
                "ocr": "识别正文",
                "review": "原页核验",
                "verify": "修正复核",
                "navigation": "目录关联",
                "organization": "文章整理",
                "human_review": "人工核对",
                "machine_handoff": "人工兜底核对",
            }.get(task["stage"], task["stage"])
            records.append(
                dict(
                    key="codex:" + task["task_id"],
                    kind="agent",
                    parent=coordinator,
                    label=label,
                    objective=f"{label} · 原件第 {task['page']} 页"
                    if task["page"]
                    else f"{label} · 整书任务",
                    state=task["state"],
                    details={
                        k: task[k] for k in ("stage", "page", "state", "receipt", "result") if k in task
                    },
                )
            )
        self.outputs.nodes(run["id"], records)

    def publish_chapters(self, run, doc):
        return self.publication.publish_chapters(run, doc)

    @staticmethod
    def due(run):
        result = run.get("result") or {}
        return result.get("bridge_failures", 0) < 3 and result.get("bridge_not_before", 0) <= time.time()
