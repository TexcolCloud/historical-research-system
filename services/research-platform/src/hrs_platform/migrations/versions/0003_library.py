"""Immutable chapter objects, stage outputs and actual execution nodes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003_library"
down_revision = "0002_review"
branch_labels = depends_on = None


def upgrade():
    op.alter_column("runs", "upload_id", nullable=True)
    op.add_column("runs", sa.Column("kind", sa.String(30), nullable=False, server_default="book"))
    op.add_column("runs", sa.Column("parent_run_id", UUID(as_uuid=False), sa.ForeignKey("runs.id")))
    op.create_table(
        "chapters",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=False), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=False), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("pages", JSONB(), nullable=False),
        sa.Column("codepoints", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "position"),
    )
    op.create_table(
        "stage_outputs",
        sa.Column("run_id", UUID(as_uuid=False), sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("step", sa.Text(), primary_key=True),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("reference", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "execution_nodes",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=False), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("parent_id", UUID(as_uuid=False)),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("details", JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("execution_nodes_run", "execution_nodes", ["run_id", "started_at"])


def downgrade():
    raise RuntimeError("Published sources and model evidence require an explicit archival operation.")
