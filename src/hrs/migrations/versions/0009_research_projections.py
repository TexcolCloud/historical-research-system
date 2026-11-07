"""Read-only SQL projections of the native research workflow."""

from alembic import op

from hrs.storage.models import research_cards, research_entities, research_runs

revision = "0009_research_projections"
down_revision = "0008_pipeline_reliability"
branch_labels = depends_on = None


def upgrade():
    for table in (research_runs, research_cards, research_entities):
        table.create(op.get_bind())


def downgrade():
    raise RuntimeError("Research provenance and human decisions must be retained.")
