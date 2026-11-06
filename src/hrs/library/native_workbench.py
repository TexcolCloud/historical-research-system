"""Attach an existing native source to the SQL reading projection without importing again."""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import insert, or_, select, text, update

from hrs.library.deletion import DELETING, require_active
from hrs.library.errors import ServiceError
from hrs.library.native_workspace import WrongWorkspace, require_workspace
from hrs.processing.store import Store
from hrs.processing.workflow_rules import require_active as require_native_active
from hrs.processing.workspace import identity, resolve_workspace
from hrs.storage import models as db


def attach_document(settings, engine, document_id, workspace_id):
    store = Store(resolve_workspace(settings.codex_root))
    workspace = identity(store)
    if workspace_id != workspace["workspace_id"]:
        raise ServiceError(409, "原生任务与当前网页工作区不一致，请重新打开正确的工作台。")
    try:
        document = store.status(document_id)
        require_native_active(document, allow_paused=True)
        store.verify_evidence(document)
    except (ValueError, OSError, KeyError) as error:
        raise ServiceError(409, "当前原生任务不能连接阅读工作台：" + str(error)) from error
    key = f"native-workbench:{workspace_id}:{document_id}"
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )
        rows = (
            connection.execute(
                select(db.runs, db.books.c.state.label("book_state"))
                .join(db.books)
                .where(
                    db.runs.c.kind == "book",
                    or_(
                        db.runs.c.result["codex_document_id"].astext == document_id,
                        db.runs.c.result["native_attach_document_id"].astext == document_id,
                    ),
                )
                .order_by(db.runs.c.created_at)
            )
            .mappings()
            .all()
        )
        current, unknown_legacy = None, False
        for row in rows:
            try:
                current = require_workspace(engine, row, store, connection=connection)
                current = {**current, "book_state": row["book_state"]}
                break
            except WrongWorkspace:
                unknown_legacy = unknown_legacy or not (row["result"] or {}).get(
                    "codex_workspace_id"
                )
        if current is None and unknown_legacy:
            raise WrongWorkspace()
        if current:
            if current["book_state"] in DELETING:
                raise ServiceError(409, "此原生材料对应的书籍已删除或正在删除，不能重新创建。")
            book_id, run_id = current["book_id"], current["id"]
        else:
            book_id = str(uuid5(NAMESPACE_URL, key))
            run_id = str(uuid5(UUID(book_id), "native-projection"))
            connection.execute(
                insert(db.books).values(id=book_id, title=document["title"], state="queued")
            )
            connection.execute(
                insert(db.runs).values(
                    id=run_id,
                    book_id=book_id,
                    kind="book",
                    state="queued",
                    stage="verify_upload",
                    result={
                        "processing_backend": "codex",
                        "native_attach_document_id": document_id,
                        "codex_workspace_id": workspace_id,
                    },
                )
            )
            connection.execute(
                insert(db.outbox).values(
                    dedup_key=f"native-attach:{run_id}",
                    run_id=run_id,
                    kind="start_book",
                    payload={},
                )
            )
            connection.execute(
                insert(db.events).values(
                    book_id=book_id,
                    run_id=run_id,
                    kind="run.changed",
                    payload={"state": "queued", "stage": "verify_upload"},
                )
            )
    return {
        "workspace_id": workspace_id,
        "document_id": document_id,
        "book_id": book_id,
        "run_id": run_id,
        "url": f"/#/books/{book_id}?tab=progress",
    }


def complete_attachment(settings, engine, run, store, objects):
    """Use the ordinary start/retry outbox path; retain failures for that same run."""
    document = store.status(run["result"]["native_attach_document_id"])
    require_native_active(document, allow_paused=True)
    if run["result"]["codex_workspace_id"] != identity(store)["workspace_id"]:
        raise ValueError("Native attachment workspace changed")
    store.verify_evidence(document)
    source = store.artifact_path(document, document["source"]["path"])
    retained = objects.put_file(source, document["source"].get("media_type"), run_id=run["id"])
    if retained["sha256"] != document["source"]["sha256"]:
        raise ValueError("Native attachment source hash differs from retained evidence")
    with engine.begin() as connection:
        require_active(connection, run["book_id"])
        result = (
            connection.scalar(
                select(db.runs.c.result).where(db.runs.c.id == run["id"]).with_for_update()
            )
            or {}
        )
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run["id"])
            .values(
                source=retained,
                state="running",
                stage="ocr",
                error=None,
                result={**result, "codex_document_id": document["id"]},
            )
        )
