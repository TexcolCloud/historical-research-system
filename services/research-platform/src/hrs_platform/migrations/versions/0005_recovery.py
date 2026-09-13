"""Explicit recovery attempts retain all prior committed outputs."""

import sqlalchemy as sa
from alembic import op

revision = "0005_recovery"
down_revision = "0004_cards"
branch_labels = depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("recovery_attempt", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    raise RuntimeError("Recovery history must be archived explicitly.")
