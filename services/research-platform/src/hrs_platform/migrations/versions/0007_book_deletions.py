"""Recoverable book deletion receipts."""

from alembic import op

from hrs_platform.schema import book_deletions

revision = "0007_book_deletions"
down_revision = "0006_review_drafts"
branch_labels = depends_on = None


def upgrade():
    book_deletions.create(op.get_bind())


def downgrade():
    raise RuntimeError("Finish outstanding deletions before removing their receipts.")
