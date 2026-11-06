"""S3 configuration, immutable read caching and database advisory object locks."""

import time
from collections import OrderedDict
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import text

from hrs.storage.objects import S3Objects


@contextmanager
def object_lock(engine, key):
    """Only operations on the same object wait; no book lock spans S3 I/O."""

    with engine.begin() as connection:
        deadline = time.monotonic() + 300
        while not connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        ):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Object storage operation is still in progress; retry this stage."
                )
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


def object_options(settings):
    return {
        "endpoint": settings.s3_endpoint,
        "bucket": settings.s3_bucket,
        "access_key": settings.s3_access_key.get_secret_value(),
        "secret_key": settings.s3_secret_key.get_secret_value(),
        "region": settings.s3_region,
    }


def objects_for(settings):
    return S3Objects(**object_options(settings))
