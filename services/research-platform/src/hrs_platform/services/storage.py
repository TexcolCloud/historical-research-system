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

from hrs_platform import models as db

storage_heartbeat = ContextVar("storage_heartbeat", default=None)


@contextmanager
def object_lock(engine, key):
    """Only operations on the same object wait; no book lock spans S3 I/O."""

    with engine.begin() as connection:
        deadline = time.monotonic() + 300
        while not connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        ):
            if heartbeat := storage_heartbeat.get():
                heartbeat()
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



class OwnedObjects(S3Objects):
    def __init__(self, *, engine, **options):
        super().__init__(**options)
        self.engine = engine

    def _owned(self, run_id, reference, write):
        if run_id is None:
            # Backups/standalone tooling have no book lifecycle. Production book
            # writers pass run_id; legacy references remain readable.
            return write()
        from hrs_platform.services.deletion import require_active

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


def objects_for(settings, engine=None):
    return OwnedObjects(
        engine=engine,
        endpoint=settings.s3_endpoint,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        region=settings.s3_region,
    )
