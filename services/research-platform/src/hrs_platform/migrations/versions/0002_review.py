"""Source-bound review issues and append-only human decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002_review"
down_revision = "0001_books"
branch_labels = depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "review_issues",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=False), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("replacement", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "scope_key"),
    )
    op.create_index("review_issues_pending", "review_issues", ["run_id", "state", "page", "id"])
    op.create_table(
        "review_decisions",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("issue_id", UUID(as_uuid=False), sa.ForeignKey("review_issues.id"), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("receipt", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    raise RuntimeError("Review history is not deleted by an automatic downgrade.")
