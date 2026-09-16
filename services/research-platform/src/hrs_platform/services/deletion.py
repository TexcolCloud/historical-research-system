"""One book deletion use case: stop producers, collect ownership, then erase outputs.

Only the empty book tombstone and a small receipt survive, so SSE and repeated
DELETE requests remain meaningful. Content-addressed objects referenced by other
books are retained. Cleanup is retryable and never reports success before I/O.
"""


from fastapi import HTTPException
from sqlalchemy import insert, select, update

from hrs_platform import models as db

DELETING = {"deleting", "delete_failed", "deleted"}


def require_active(connection, book_id):
    if book_id is None:
        raise HTTPException(409, "书籍不存在或已删除。")
    state = connection.scalar(select(db.books.c.state).where(db.books.c.id == str(book_id)).with_for_update())
    if state is None or state in DELETING:
        raise HTTPException(409, "本书正在删除或已删除，不能继续处理。")


def require_run_active(connection, run_id):
    """Acquire lifecycle locks in book -> run -> issue order everywhere."""
    book = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == str(run_id)))
    require_active(connection, book)


def receipt(engine, book_id):
    with engine.connect() as connection:
        row = (
            connection.execute(select(db.book_deletions).where(db.book_deletions.c.book_id == str(book_id)))
            .mappings()
            .one()
        )
    return {"book_id": row["book_id"], "state": row["state"], "error": row["error"]}


def request_deletion(engine, book_id):
    book_id = str(book_id)
    with engine.begin() as connection:
        book = (
            connection.execute(select(db.books).where(db.books.c.id == book_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        if book is None:
            raise HTTPException(404, "书籍不存在。")
        prior = (
            connection.execute(select(db.book_deletions).where(db.book_deletions.c.book_id == book_id))
            .mappings()
            .one_or_none()
        )
        if prior and prior["state"] != "failed":
            return {"book_id": book_id, "state": prior["state"], "error": prior["error"]}
        if prior:
            connection.execute(
                update(db.book_deletions)
                .where(db.book_deletions.c.book_id == book_id)
                .values(state="pending", error=None, attempt=prior["attempt"] + 1)
            )
        else:
            connection.execute(insert(db.book_deletions).values(book_id=book_id, state="pending"))
        connection.execute(update(db.books).where(db.books.c.id == book_id).values(state="deleting"))
        connection.execute(
            update(db.outbox)
            .where(db.outbox.c.run_id.in_(select(db.runs.c.id).where(db.runs.c.book_id == book_id)))
            .values(delivered=True)
        )
        connection.execute(
            insert(db.events).values(book_id=book_id, kind="book.deleting", payload={"state": "deleting"})
        )
    return {"book_id": book_id, "state": "pending", "error": None}
