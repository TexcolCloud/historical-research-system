"""Project the native Codex workspace into the existing upload, reader and search contracts.

Codex is the processing authority. SQL/S3 retain the old application's immutable
reading projections; neither Temporal nor the old recognition providers run here.
"""

import json
import logging
import re
import threading
import time
from uuid import UUID, uuid5

from filelock import FileLock, Timeout
from hrs_codex.scheduling import scheduling_plan
from hrs_codex.server import prepare
from hrs_codex.store import Store, digest
from hrs_codex.workspace import ensure_workbench, resolve_workspace
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs_platform import models as db
from hrs_platform.services.books import get_run
from hrs_platform.services.documents.review import Review
from hrs_platform.services.documents.structure import describe_structure, project_structure
from hrs_platform.services.runs.outputs import Outputs, fingerprint
from hrs_platform.services.storage import objects_for

logger = logging.getLogger(__name__)
PROJECTION_POLICY = "codex-platform-v6-navigation"


def article_groups(document, run_id):
    """Adapt validated native article ranges to the existing immutable chapter contract."""
    from hrs_codex.organization import article_parts

    articles = document["organization"]["articles"]
    groups = []
    for article, source_parts in zip(articles, article_parts(document["pages"], articles), strict=True):
        parts = []
        for part in source_parts:
            page = document["pages"][part["page"] - 1]
            parts.append(
                {
                    "span_id": str(
                        uuid5(UUID(run_id), f"codex:{part['page']}:{part['start']}:{part['end']}")
                    ),
                    "start": part["start"],
                    "end": part["end"],
                    "text": part["text"],
                    "source": {
                        "pages": [part["page"]],
                        "page": part["page"],
                        "original_start": part["original_start"],
                        "original_end": part["original_end"],
                        "image_sha256": page["image_sha256"],
                        "markdown_sha256": page["approved"]["sha256"],
                        "approval_type": page["approved"]["approval_type"],
                    },
                }
            )
        groups.append(
            {
                "title": article["title"],
                "kind": article.get("kind", "article"),
                "parts": parts,
                "bibliography": article.get("bibliography", {"status": "unclaimed"}),
            }
        )
    return groups


class CodexBridge:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.store = Store(resolve_workspace(settings.codex_root))
        self.objects = objects_for(settings, engine)
        self.outputs, self.review = Outputs(settings, engine), Review(settings, engine)
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        ensure_workbench(self.store)
        self.thread = threading.Thread(target=self.loop, name="codex-platform-bridge", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def loop(self):
        # One local bridge owns projections. The separate workspace runner owns Codex execution.
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
            except Exception as error:  # noqa: BLE001 - retain each independent run's recoverable failure
                self.failure(event["run_id"], error)
        with self.engine.connect() as connection:
            runs = (
                connection.execute(
                    select(db.runs)
                    .join(db.books)
                    .where(
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                        db.runs.c.result["codex_document_id"].astext.is_not(None),
                    )
                )
                .mappings()
                .all()
            )
        for run in runs:
            try:
                self.sync(run)
            except Exception as error:  # noqa: BLE001 - a failed projection must not stop other books
                self.failure(run["id"], error)
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
                if not shared and self.store._directory(doc_id).exists():
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
        elif event["kind"] == "retry_run" and (run["result"] or {}).get("codex_document_id"):
            self.store.request_run(run["result"]["codex_document_id"], resume=True)
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
            return
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

    def sync(self, run):
        if not self.due(run):
            return
        doc_id = run["result"]["codex_document_id"]
        doc = self.store.refresh_navigation(doc_id)
        publication_sha = (doc.get("publication") or {}).get("markdown_sha256")
        navigation_sha = doc.get("navigation", {}).get("sha256")
        if (
            doc["state"] == "published"
            and run["result"].get("published")
            and run["result"].get("codex_publication_sha256") == publication_sha
            and run["result"].get("codex_projection_policy") == PROJECTION_POLICY
            and (
                run["result"].get("codex_indexed_publication_sha256") != publication_sha
                or run["result"].get("codex_indexed_projection_policy") != PROJECTION_POLICY
                or run["result"].get("codex_indexed_navigation_sha256") != navigation_sha
            )
            and run["result"].get("codex_projected_navigation_sha256") == navigation_sha
        ):
            from hrs_platform.services.retrieval.indexing import BookIndexer

            BookIndexer(self.settings, self.engine).index(run["id"])
            indexed = get_run(self.engine, run["id"])["result"]
            with self.engine.begin() as connection:
                connection.execute(
                    update(db.runs)
                    .where(db.runs.c.id == run["id"])
                    .values(
                        state="completed",
                        stage="complete",
                        revision=db.runs.c.revision + 1,
                        updated_at=func.now(),
                        error=None,
                        result={
                            **indexed,
                            "codex_indexed_publication_sha256": publication_sha,
                            "codex_indexed_projection_policy": PROJECTION_POLICY,
                            "codex_indexed_navigation_sha256": navigation_sha,
                            "bridge_failures": 0,
                            "bridge_not_before": 0,
                        },
                    )
                )
                connection.execute(
                    insert(db.events).values(
                        book_id=run["book_id"],
                        run_id=run["id"],
                        kind="run.changed",
                        payload={"state": "completed", "stage": "complete", "revision": run["revision"] + 1},
                    )
                )
            run = get_run(self.engine, run["id"])
        self.apply_decisions(run, doc)
        doc = self.store.refresh_navigation(doc_id)
        navigation_sha = doc.get("navigation", {}).get("sha256")
        scheduling = scheduling_plan(doc)
        latest_reviews = {t["page"]: t for t in doc["tasks"] if t["stage"] == "review"}
        page_progress = {
            "total": len(doc["pages"]),
            "extracted": sum(bool(p.get("ocr")) for p in doc["pages"]),
            "reviewed": sum(bool(t.get("result")) for t in latest_reviews.values()),
            "approved": sum(p["state"] == "approved" for p in doc["pages"]),
            "pending": sum(p["state"] == "pending" for p in doc["pages"]),
        }
        key = fingerprint(
            {
                "projection_policy": PROJECTION_POLICY,
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
        if run["result"].get("codex_sync_sha256") == key:
            return
        previous_files = (
            self.review.read_json(run["conversion"]).get("files", {}) if run.get("conversion") else {}
        )
        files, boundaries, carrier, completion, texts = {}, [], [], [], []
        offset = 0
        for page in doc["pages"]:
            selected = page.get("approved") or page.get("candidate") or page.get("ocr")
            text = (selected or {}).get("markdown", "") + "\n\n"
            texts.append(text)
            boundaries.append({"page": page["page"], "start": offset, "end": offset + len(text)})
            offset += len(text)
            name = page["image"]
            previous = previous_files.get(name, {})
            files[name] = (
                previous
                if previous.get("sha256") == page["image_sha256"]
                else self.objects.put_file(self.store._path(doc, name), "image/png", run_id=run["id"])
            )
            status = (
                "release-accepted"
                if page["state"] == "approved"
                else "unreviewed-source"
                if selected
                else "awaiting-ocr"
            )
            carrier.append(
                {
                    "page": page["page"],
                    "image": name,
                    "status": status,
                    "extraction_method": page.get("route", "ocr"),
                    "acceptance_basis": (selected or {}).get("approval_type"),
                }
            )
            reviews = [
                t
                for t in doc["tasks"]
                if t["page"] == page["page"] and t["stage"] == "review" and t.get("result")
            ]
            completion.append(
                {
                    "page": page["page"],
                    "structure": reviews[-1]["result"].get("structure", {}) if reviews else {},
                }
            )

        def saved(name, value, media="application/json"):
            data = (
                value.encode("utf-8")
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False).encode("utf-8")
            )
            files[name] = self.objects.put_bytes(data, media, run_id=run["id"])

        saved("document.md", "".join(texts), "text/markdown; charset=utf-8")
        for relative in re.findall(
            r"!\[[^\]]*\]\(((?:pages|evidence/details)/[\w./-]+\.png)\)", "".join(texts)
        ):
            asset = self.store._path(doc, relative)
            if not asset.is_file():
                raise ValueError("Markdown references a missing retained original image")
            files[relative] = self.objects.put_file(asset, "image/png", run_id=run["id"])
        saved("pages.json", {"pages": boundaries})
        saved("navigation.json", doc.get("navigation", {}))
        saved("review-cards.json", {"cards": []})
        saved("completion.json", {"pages": completion})
        if (doc.get("organization") or {}).get("reading_structure"):
            saved("paddle-restructure.json", doc["organization"]["reading_structure"])
        saved(
            "codex-evidence.json",
            {
                "source": doc["source"],
                "policy": doc.get("processing_policy"),
                "publication": doc["publication"],
                "tasks": doc["tasks"],
            },
        )
        manifest = {
            "page_count": len(carrier),
            "pages": carrier,
            "candidate_markdown": "document.md",
            "page_boundaries": "pages.json",
            "review_cards": "review-cards.json",
        }
        bundle = self.objects.put_bytes(
            json.dumps({"files": files, "manifest": manifest}, ensure_ascii=False).encode(), run_id=run["id"]
        )
        pending = [p for p in doc["pages"] if p["state"] == "pending"]
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
        if doc["organization"]:
            self.publish_chapters(run, doc)
        with self.engine.begin() as connection:
            for page in pending:
                packet = self.store.human_packet(doc_id, page["page"])
                scope = (
                    "codex-page:"
                    + (str(doc["revision"]) + ":" if doc.get("revision", 1) > 1 else "")
                    + str(page["page"])
                )
                issue_id = str(uuid5(UUID(run["id"]), scope))
                content = {
                    "text": packet["markdown"],
                    "start": boundaries[page["page"] - 1]["start"],
                    "end": boundaries[page["page"] - 1]["end"],
                    "pages": [page["page"]],
                    "images": [page["image"]],
                    "kind": "page",
                    "reasons": page["review"]["issues"],
                    "codex_input_sha256": packet["input_sha256"],
                    "codex_revision": doc.get("revision", 1),
                    "proposed_text": packet.get("suggested_markdown"),
                }
                ref = self.objects.put_bytes(
                    json.dumps(content, ensure_ascii=False).encode(), run_id=run["id"]
                )
                connection.execute(
                    pg_insert(db.review_issues)
                    .values(
                        id=issue_id,
                        run_id=run["id"],
                        scope_key=scope,
                        page=page["page"],
                        state="pending",
                        content=ref,
                        text_sha256=digest(packet["markdown"]),
                    )
                    .on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "content": ref,
                            "text_sha256": digest(packet["markdown"]),
                            "revision": db.review_issues.c.revision + 1,
                        },
                        where=(db.review_issues.c.state == "pending")
                        & db.review_issues.c.draft.is_(None)
                        & (db.review_issues.c.content["sha256"].astext != ref["sha256"]),
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
                "codex_page_progress": page_progress,
                "codex_navigation_pending_count": sum(
                    e["status"] == "conflict" for e in doc.get("navigation", {}).get("entries", [])
                ),
                "codex_scheduling": scheduling,
                "codex_execution": {
                    "state": request.get("state"),
                    "paused": state == "paused",
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
                "content_acceptance": "workflow_test"
                if (self.store.root / ".workflow-test-authorization.json").exists()
                else "machine_or_human",
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
            connection.execute(
                update(db.books)
                .where(db.books.c.id == run["book_id"])
                .values(state="ready" if state == "completed" else state)
            )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run["id"],
                    kind="run.changed",
                    payload={
                        "state": state,
                        "stage": stage,
                        "revision": revision,
                        "pending_count": len(pending),
                        "navigation_pending_count": result["codex_navigation_pending_count"],
                        "revision_pending": result["codex_revision_pending"],
                    },
                )
            )
        coordinator = self.outputs.node(
            run["id"],
            "codex:coordinator",
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
        for task in doc["tasks"]:
            label = {
                "ocr": "识别正文",
                "review": "原页核验",
                "verify": "修正复核",
                "navigation": "目录关联",
                "organization": "文章整理",
            }.get(task["stage"], task["stage"])
            self.outputs.node(
                run["id"],
                "codex:" + task["task_id"],
                kind="agent",
                parent=coordinator,
                label=label,
                objective=f"{label} · 原件第 {task['page']} 页" if task["page"] else f"{label} · 整书任务",
                state=task["state"],
                details={k: task[k] for k in ("stage", "page", "state", "receipt", "result") if k in task},
            )

    def publish_chapters(self, run, doc):
        if doc["state"] != "published":
            return
        with self.engine.connect() as connection:
            if (
                run["result"].get("codex_publication_sha256") == doc["publication"]["markdown_sha256"]
                and run["result"].get("codex_projection_policy") == PROJECTION_POLICY
                and run["result"].get("codex_projected_navigation_sha256")
                == doc.get("navigation", {}).get("sha256")
                and connection.scalar(
                    select(func.count()).select_from(db.chapters).where(db.chapters.c.run_id == run["id"])
                )
            ):
                return
        groups = article_groups(doc, run["id"])
        parts = sorted([p for g in groups for p in g["parts"]], key=lambda p: p["start"])
        text = "".join(p["text"] for p in parts)
        titles = [
            {"page": p["source"]["pages"][0], "title": m.group().strip(), "level": len(m[1])}
            for p in parts
            for m in re.finditer(r"(?m)^(#{1,6})\s+[^\n]+", p["text"])
        ]
        metadata = {**doc["organization"].get("reading_structure", {}), "titles": titles}
        completion = []
        for page in doc["pages"]:
            reviews = [
                t
                for t in doc["tasks"]
                if t["stage"] == "review" and t["page"] == page["page"] and t.get("result")
            ]
            completion.append(
                {
                    "page": page["page"],
                    "structure": reviews[-1]["result"].get("structure", {}) if reviews else {},
                }
            )
        structure = describe_structure(
            {"text": text, "parts": parts}, metadata, completion, {p["page"] for p in doc["pages"]}
        )
        rows = []
        for position, group in enumerate(groups):
            group["navigation"] = doc.get("navigation", {})
            group["structure"] = project_structure(structure, group["parts"])
            ref = self.objects.put_bytes(json.dumps(group, ensure_ascii=False).encode(), run_id=run["id"])
            rows.append(
                {
                    "id": str(uuid5(UUID(run["id"]), f"chapter:{position}")),
                    "book_id": run["book_id"],
                    "run_id": run["id"],
                    "position": position,
                    "title": group["title"],
                    "kind": group["kind"],
                    "content": ref,
                    "pages": sorted({p for part in group["parts"] for p in part["source"]["pages"]}),
                    "codepoints": sum(len(p["text"]) for p in group["parts"]),
                }
            )
        with self.engine.begin() as connection:
            connection.execute(delete(db.chapters).where(db.chapters.c.run_id == run["id"]))
            connection.execute(pg_insert(db.chapters).values(rows).on_conflict_do_nothing())
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run["id"],
                    kind="library.revised"
                    if run["result"].get("codex_publication_sha256")
                    else "book.published",
                    payload={"state": "ready", "stage": "indexing", "revision": run["revision"] + 1},
                )
            )

    @staticmethod
    def due(run):
        result = run.get("result") or {}
        return result.get("bridge_failures", 0) < 3 and result.get("bridge_not_before", 0) <= time.time()
