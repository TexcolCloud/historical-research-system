"""Drafts are separate S3 references and never open the review gate."""

from alembic import op
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_review_drafts"
down_revision = "0005_recovery"
branch_labels = depends_on = None


def upgrade():
    op.add_column("review_issues", Column("draft", JSONB))


def downgrade():
    raise RuntimeError("Archive review drafts explicitly before removal.")
