"""Pin immutable objects to their producing run before publishing references."""

import hashlib
import mimetypes
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

from hrs_runtime.object_storage import S3Objects, file_digest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import schema as db

external_heartbeat = ContextVar("external_heartbeat", default=False)


@contextmanager
def object_lock(engine, key):
    """Only operations on the same object wait; no book lock spans S3 I/O."""
    from temporalio import activity

    with engine.begin() as connection:
        deadline = time.monotonic() + 300
        while not connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        ):
            if activity.in_activity() and not external_heartbeat.get():
                activity.heartbeat()
            if time.monotonic() >= deadline:
                raise TimeoutError("Object storage operation is still in progress; retry this stage.")
            time.sleep(0.1)
        yield connection


# Bounded immutable reader cache; no book-sized copies accumulate on disk.

_reader_cache = OrderedDict()
_reader_lock = Lock()
READER_BYTES = 32 * 1024 * 1024


def cached_bytes(objects, reference):
    key = (reference["key"], reference["sha256"], reference["byte_length"])
    with _reader_lock:
        if key in _reader_cache:
            _reader_cache.move_to_end(key)
            return _reader_cache[key]
    value = objects.read_bytes(reference)
    if len(value) <= READER_BYTES:
        with _reader_lock:
            _reader_cache[key] = value
            while sum(map(len, _reader_cache.values())) > READER_BYTES:
                _reader_cache.popitem(last=False)
    return value


def object_response(objects, reference, range_header=None, *, headers=None):
    """Proxy a verified immutable S3 object, retaining PDF single-range support."""
    import re

    from fastapi import HTTPException
    from fastapi.responses import Response, StreamingResponse
    from starlette.background import BackgroundTask

    size = reference["byte_length"]
    response_headers = {
        "ETag": '"' + reference["sha256"] + '"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        **(headers or {}),
    }
    options = {}
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if not match or not any(match.groups()) or size == 0:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        a, b = match.groups()
        start = int(a) if a else max(0, size - int(b))
        end = min(int(b), size - 1) if a and b else size - 1
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        options["Range"] = f"bytes={start}-{end}"
        response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    response = objects.client.get_object(Bucket=objects.bucket, Key=reference["key"], **options)
    body = response["Body"]
    expected_length = end - start + 1 if range_header else size
    if (
        response["ContentLength"] != expected_length
        or response.get("Metadata", {}).get("sha256") != reference["sha256"]
    ):
        body.close()
        raise HTTPException(502, "原件存储校验失败。")
    response_headers["Content-Length"] = str(expected_length)

    def chunks():
        try:
            yield from body.iter_chunks(1024 * 1024)
        finally:
            body.close()

    return StreamingResponse(
        chunks(),
        status_code=206 if range_header else 200,
        media_type=reference["media_type"],
        headers=response_headers,
        background=BackgroundTask(body.close),
    )


class OwnedObjects(S3Objects):
    def __init__(self, *, engine, **options):
        super().__init__(**options)
        self.engine = engine

    def _owned(self, run_id, reference, write):
        if run_id is None:
            # Backups/standalone tooling have no book lifecycle. Production book
            # writers pass run_id; legacy references remain readable.
            return write()
        from .deletion import require_active

        with object_lock(self.engine, reference["key"]):
            # A durable pin precedes I/O. Failed uploads leave a deletable pin,
            # never an unowned file. Book state locks protect only this short commit.
            with self.engine.begin() as connection:
                book = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == str(run_id)))
                require_active(connection, book)
                connection.execute(
                    pg_insert(db.object_owners)
                    .values(run_id=str(run_id), key=reference["key"], reference=reference)
                    .on_conflict_do_nothing()
                )
            return write()

    def put_bytes(self, content, media_type="application/json", *, run_id=None):
        sha = hashlib.sha256(content).hexdigest()
        reference = {
            "key": f"hrs/v2/objects/{sha[:2]}/{sha}",
            "sha256": sha,
            "byte_length": len(content),
            "media_type": media_type,
        }
        return self._owned(
            run_id, reference, lambda: super(OwnedObjects, self).put_bytes(content, media_type)
        )

    def put_file(self, path, media_type=None, *, run_id=None):
        sha, size = file_digest(path)
        reference = {
            "key": f"hrs/v2/objects/{sha[:2]}/{sha}",
            "sha256": sha,
            "byte_length": size,
            "media_type": media_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream",
        }
        return self._owned(run_id, reference, lambda: super(OwnedObjects, self).put_file(path, media_type))
