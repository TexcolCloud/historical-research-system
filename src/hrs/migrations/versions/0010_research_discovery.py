"""Rebuildable research discovery index generations."""

from alembic import op

from hrs.storage.models import research_discovery_indexes, research_entities

revision = "0010_research_discovery"
down_revision = "0009_research_projections"
branch_labels = depends_on = None


def upgrade():
    research_discovery_indexes.create(op.get_bind())
    for index in research_entities.indexes:
        if index.name == "ix_research_entities_canonical":
            index.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("Research source and decision projections require an explicit migration.")
