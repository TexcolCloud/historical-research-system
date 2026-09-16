"""Durable object ownership and commit-safe event delivery cursors."""

import sqlalchemy as sa
from alembic import op

from hrs_platform.models import object_owners

revision = "0008_pipeline_reliability"
down_revision = "0007_book_deletions"
branch_labels = depends_on = None


def upgrade():
    object_owners.create(op.get_bind())
    op.execute("CREATE SEQUENCE event_delivery_sequence")
    op.add_column("events", sa.Column("delivery_sequence", sa.BigInteger(), nullable=True))
    # Preserve already-issued SSE cursors during rollout.
    op.execute("UPDATE events SET delivery_sequence = sequence")
    op.execute("SELECT setval('event_delivery_sequence', COALESCE(MAX(sequence), 0) + 1, false) FROM events")
    op.create_index("ix_events_delivery_sequence", "events", ["delivery_sequence"], unique=True)


def downgrade():
    raise RuntimeError("Object ownership and issued event cursors must be retained.")
