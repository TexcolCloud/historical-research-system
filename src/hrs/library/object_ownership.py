"""Library object ownership; S3 transport remains independent of book lifecycle."""

import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs.library.deletion import require_active
from hrs.storage import models as db
from hrs.storage.objects import S3Objects, file_snapshot, object_reference
from hrs.storage.service import object_lock, object_options


class OwnedObjects(S3Objects):
    def __init__(self, *, engine, **options):
        super().__init__(**options)
        self.engine = engine

    def _owned(self, run_id, reference, write):
        if run_id is None:
            # Backups/standalone tooling have no book lifecycle. Production book
            # writers pass run_id; legacy references remain readable.
            return write()
        with object_lock(self.engine, reference["key"]):
            # A durable pin precedes I/O. Failed uploads leave a deletable pin,
            # never an unowned file. Book state locks protect only this short commit.
            with self.engine.begin() as connection:
                book = connection.scalar(
                    select(db.runs.c.book_id).where(db.runs.c.id == str(run_id))
                )
                require_active(connection, book)
                connection.execute(
                    pg_insert(db.object_owners)
                    .values(run_id=str(run_id), key=reference["key"], reference=reference)
                    .on_conflict_do_nothing()
                )
            return write()

    def put_bytes(self, content, media_type="application/json", *, run_id=None):
        content = bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        reference = object_reference(sha, len(content), media_type)
        return self._owned(
            run_id, reference, lambda: super(OwnedObjects, self).put_bytes(content, media_type)
        )

    def put_file(self, path, media_type=None, *, run_id=None):
        with file_snapshot(path, media_type) as (snapshot, reference):
            return self._owned(
                run_id, reference, lambda: self._upload_snapshot(snapshot, reference)
            )


def owned_objects(settings, engine):
    """Pin writes to a producing library run and enforce its deletion lifecycle."""
    return OwnedObjects(engine=engine, **object_options(settings))
