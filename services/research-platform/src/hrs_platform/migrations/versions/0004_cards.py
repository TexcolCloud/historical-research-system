"""Machine-checked card revisions belong to an explicit book run."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004_cards"
down_revision = "0003_library"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "cards",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=False), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=False), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("checks", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("cards_book", "cards", ["book_id", "created_at"])


def downgrade():
    raise RuntimeError("Card evidence requires an explicit archival operation.")
