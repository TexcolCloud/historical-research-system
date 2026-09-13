"""Book uploads, durable runs and transactional notifications."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_books"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "books",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "uploads",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True),
        sa.Column("book_id", pg.UUID(as_uuid=False), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True),
        sa.Column("book_id", pg.UUID(as_uuid=False), sa.ForeignKey("books.id"), nullable=False),
        sa.Column(
            "upload_id", pg.UUID(as_uuid=False), sa.ForeignKey("uploads.id"), nullable=False, unique=True
        ),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", pg.JSONB()),
        sa.Column("conversion", pg.JSONB()),
        sa.Column("result", pg.JSONB()),
        sa.Column("error", pg.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("sequence", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("book_id", pg.UUID(as_uuid=False), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=False)),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dedup_key", sa.Text(), nullable=False, unique=True),
        sa.Column("run_id", pg.UUID(as_uuid=False), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("events_book_sequence", "events", ["book_id", "sequence"])
    op.create_index("outbox_pending", "outbox", ["id"], postgresql_where=sa.text("delivered = false"))


def downgrade():
    raise RuntimeError("Destructive downgrade is not an application operation.")
