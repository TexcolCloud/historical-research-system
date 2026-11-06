"""Object bytes and lifecycle ownership remain identical if a producer replaces its path."""

from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import insert, select

from hrs.library import object_ownership
from hrs.library.object_ownership import owned_objects
from hrs.storage import models as db
from hrs.storage.service import objects_for


def test_owned_multipart_file_uses_the_bytes_pinned_before_upload(platform, tmp_path, monkeypatch):
    settings, engine = platform
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="Snapshot", state="running"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, state="running", stage="ocr")
        )
    source = tmp_path / "changing.pdf"
    original = b"immutable original\n" * 600000
    source.write_bytes(original)
    real_lock = object_ownership.object_lock

    @contextmanager
    def replace_before_pinning(*args):
        source.write_bytes(b"replacement produced concurrently")
        with real_lock(*args) as connection:
            yield connection

    monkeypatch.setattr(object_ownership, "object_lock", replace_before_pinning)
    objects = owned_objects(settings, engine)
    reference = objects.put_file(source, run_id=run)
    with engine.connect() as connection:
        pin = connection.execute(
            select(db.object_owners.c.reference).where(db.object_owners.c.run_id == run)
        ).scalar_one()
    assert pin == reference
    assert reference["media_type"] == "application/pdf"
    assert objects.read_bytes(reference) == original
    assert source.read_bytes() != original


def test_standalone_upload_uses_the_same_snapshot_for_hash_and_transfer(
    platform, tmp_path, monkeypatch
):
    settings, _ = platform
    source = tmp_path / "changing.txt"
    source.write_bytes(b"retained original")
    objects = objects_for(settings)
    real_transfer = objects.client.upload_file

    def replace_before_transfer(*args, **kwargs):
        source.write_bytes(b"different content")
        return real_transfer(*args, **kwargs)

    monkeypatch.setattr(objects.client, "upload_file", replace_before_transfer)
    real_stream_transfer = objects.client.upload_fileobj

    def replace_before_stream_transfer(*args, **kwargs):
        source.write_bytes(b"different content")
        return real_stream_transfer(*args, **kwargs)

    monkeypatch.setattr(objects.client, "upload_fileobj", replace_before_stream_transfer)
    reference = objects.put_file(source)
    assert objects.read_bytes(reference) == b"retained original"
    assert source.read_bytes() == b"different content"
