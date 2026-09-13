"""Book intake transactions. tusd owns bytes; Temporal owns execution."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import schema as db
from .contracts import UploadSession


def create_upload(engine, settings, request, request_id=None, upload_token=None):
    if not request.filename.lower().endswith(".pdf") or request.byte_length > settings.upload_max_bytes:
        raise HTTPException(422, "请选择大小在上传限制内的 PDF 文件。")
    if "/" in request.filename or "\\" in request.filename or "\x00" in request.filename:
        raise HTTPException(422, "文件名不能包含路径。")
    if request_id and (not upload_token or not 32 <= len(upload_token) <= 128):
        raise HTTPException(422, "幂等上传请求需要有效的上传令牌。")
    session_id = str(request_id or uuid4())
    book_id = str(uuid5(NAMESPACE_URL, f"hrs/upload/{session_id}"))
    token = upload_token if request_id else secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=1)
    with engine.begin() as connection:
        connection.execute(
            pg_insert(db.books)
            .values(id=book_id, title=request.filename[:-4], state="uploading")
            .on_conflict_do_nothing(index_elements=["id"])
        )
        from .deletion import require_active
        require_active(connection, book_id)
        existing = (
            connection.execute(select(db.uploads).where(db.uploads.c.id == session_id))
            .mappings()
            .one_or_none()
        )
        if existing:
            if (
                existing["filename"] != request.filename
                or existing["byte_length"] != request.byte_length
                or not secrets.compare_digest(
                    existing["token_hash"], hashlib.sha256(token.encode()).hexdigest()
                )
            ):
                raise HTTPException(409, "同一上传请求不能改用另一个文件或令牌。")
            return UploadSession(
                session_id=session_id,
                book_id=book_id,
                token=token,
                endpoint=settings.upload_endpoint,
                expires_at=existing["expires_at"],
            )
        connection.execute(
            insert(db.uploads).values(
                id=session_id,
                book_id=book_id,
                filename=request.filename,
                byte_length=request.byte_length,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                state="created",
                expires_at=expires,
            )
        )
    return UploadSession(
        session_id=session_id,
        book_id=book_id,
        token=token,
        endpoint=settings.upload_endpoint,
        expires_at=expires,
    )


def handle_tus(engine, settings, hook, authorization):
    upload = hook.Event.Upload
    try:
        session_id = str(UUID(upload.MetaData.get("session_id", "")))
    except ValueError as error:
        raise HTTPException(403, "上传许可缺失。") from error
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if hook.Type in {"post-create", "post-receive"}:
        from .deletion import DELETING
        from .upload_cleanup import terminate_upload

        with engine.connect() as connection:
            state = connection.scalar(
                select(db.books.c.state).join(db.uploads).where(db.uploads.c.id == session_id)
            )
        if state is None or state in DELETING:
            if hook.Type == "post-create":
                # Handles a POST admitted immediately before the deletion request.
                terminate_upload(settings, upload.ID, session_id)
            return {
                "StopUpload": True,
                "HTTPResponse": {"StatusCode": 410, "Body": "本书已删除，上传已终止。"},
            }
        return {}
    with engine.begin() as connection:
        session = (
            connection.execute(select(db.uploads).where(db.uploads.c.id == session_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        if session is None or not secrets.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(), session["token_hash"]
        ):
            raise HTTPException(403, "上传许可无效。")
        from .deletion import require_active

        require_active(connection, session["book_id"])
        if (
            upload.Size != session["byte_length"]
            or upload.SizeIsDeferred
            or upload.IsFinal
            or upload.IsPartial
        ):
            raise HTTPException(422, "上传大小或模式与许可不符。")
        if hook.Type == "pre-create":
            if session["expires_at"] < datetime.now(UTC) or session["state"] == "received":
                raise HTTPException(409, "上传许可已过期或已完成。")
            # A retried POST gets its own object, never overwriting an earlier multipart upload.
            return {
                "ChangeFileInfo": {
                    "ID": f"{session_id}/{uuid4()}",
                    "MetaData": {"session_id": session_id, "filename": session["filename"]},
                }
            }
        storage = upload.Storage or {}
        key = storage.get("Key", "")
        prefix = f"{settings.upload_prefix}{session_id}/"
        try:
            if not key.startswith(prefix):
                raise ValueError("prefix")
            UUID(key.removeprefix(prefix))
        except ValueError as error:
            raise HTTPException(422, "上传对象与许可不符。") from error
        if storage.get("Type") != "s3store" or storage.get("Bucket") != settings.s3_bucket:
            raise HTTPException(422, "上传对象必须位于配置的 S3 桶。")
        if upload.Offset != upload.Size:
            raise HTTPException(422, "上传尚未完成。")
        if session["state"] == "received":
            return {}
        run_id = str(uuid4())
        connection.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=session["book_id"],
                upload_id=session_id,
                state="queued",
                stage="verify_upload",
                source={"upload_key": key, "byte_length": upload.Size},
            )
        )
        connection.execute(update(db.uploads).where(db.uploads.c.id == session_id).values(state="received"))
        connection.execute(update(db.books).where(db.books.c.id == session["book_id"]).values(state="queued"))
        connection.execute(
            insert(db.events).values(
                book_id=session["book_id"],
                run_id=run_id,
                kind="upload.received",
                payload={"stage": "verify_upload"},
            )
        )
        connection.execute(
            insert(db.outbox).values(
                dedup_key=f"start:{run_id}", run_id=run_id, kind="start_book", payload={"run_id": run_id}
            )
        )
    return {}


def book_query():
    return (
        select(
            db.books,
            db.runs.c.id.label("run_id"),
            db.runs.c.stage,
            func.coalesce(db.runs.c.revision, 0).label("revision"),
        )
        .outerjoin(db.runs, (db.books.c.id == db.runs.c.book_id) & (db.runs.c.kind == "book"))
        .where(db.books.c.state != "deleted")
    )


def get_book(engine, book_id):
    with engine.connect() as connection:
        row = connection.execute(book_query().where(db.books.c.id == str(book_id))).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "书籍不存在。")
    return dict(row)


def list_books(engine, offset=0, limit=100):
    with engine.connect() as connection:
        return list(
            connection.execute(
                book_query().order_by(db.books.c.created_at.desc(), db.books.c.id).offset(offset).limit(limit)
            ).mappings()
        )


def get_run(engine, run_id):
    with engine.connect() as connection:
        result = (
            connection.execute(select(db.runs).where(db.runs.c.id == str(run_id))).mappings().one_or_none()
        )
    if result is None:
        raise HTTPException(404, "运行不存在。")
    return dict(result)
