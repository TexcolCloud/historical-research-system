"""Committed event delivery order shared by all stream readers."""
from sqlalchemy import func, select, text, update

from hrs_platform import models as schema


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
