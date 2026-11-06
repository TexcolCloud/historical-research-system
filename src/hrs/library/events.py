"""Committed event delivery order shared by all stream readers."""

from sqlalchemy import func, select, text, update

from hrs.storage import models as schema


def read_events(engine, position):
    """Assign delivery order only to committed events, serialized across readers."""
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(1388427267)"))
        pending = connection.scalars(
            select(schema.events.c.sequence)
            .where(schema.events.c.delivery_sequence.is_(None))
            .order_by(schema.events.c.sequence)
            .limit(500)
        ).all()
        for identity in pending:
            connection.execute(
                update(schema.events)
                .where(schema.events.c.sequence == identity)
                .values(delivery_sequence=func.nextval("event_delivery_sequence"))
            )
        rows = (
            connection.execute(
                select(schema.events)
                .where(schema.events.c.delivery_sequence > position)
                .order_by(schema.events.c.delivery_sequence)
                .limit(100)
            )
            .mappings()
            .all()
        )
        return [{**row, "sequence": row["delivery_sequence"]} for row in rows]


def workspace_snapshot(engine):
    """One MVCC snapshot and delivery cursor; uncommitted events remain future deltas."""
    from sqlalchemy.exc import OperationalError

    from hrs.library.books import book_query

    for attempt in range(3):
        try:
            with engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                with connection.begin():
                    connection.execute(text("SELECT pg_advisory_xact_lock(1388427267)"))
                    connection.execute(
                        update(schema.events)
                        .where(schema.events.c.delivery_sequence.is_(None))
                        .values(delivery_sequence=func.nextval("event_delivery_sequence"))
                    )
                    cursor = (
                        connection.scalar(select(func.max(schema.events.c.delivery_sequence))) or 0
                    )
                    books = list(
                        connection.execute(
                            book_query().order_by(
                                schema.books.c.created_at.desc(), schema.books.c.id
                            )
                        ).mappings()
                    )
                    return {"cursor": cursor, "books": books}
        except OperationalError as error:
            if getattr(error.orig, "sqlstate", None) != "40001" or attempt == 2:
                raise
