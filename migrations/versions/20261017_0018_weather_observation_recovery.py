"""Observation history initialization progress and retrieval purpose.

Existing retrievals become ``purpose='live'``: #24 collected live windows only.
No progress row is created here, so an upgraded archive initializes its
24-hour history at the next collector start instead of assuming it exists.

Revision ID: 20261017_0018
Revises: 20261016_0017
"""

import sqlalchemy as sa
from alembic import op

revision = "20261017_0018"
down_revision = "20261016_0017"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("weather_retrieval") as batch:
        batch.add_column(sa.Column("purpose", sa.Text(), nullable=False, server_default="live"))

    op.create_table(
        "weather_collection_progress",
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.Text(), primary_key=True),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("history_start", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("initialized_at", sa.Text()),
        sa.Column("last_repair_at", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )


def downgrade():
    # Backfilled readings stay; only their purpose label and progress go.
    op.drop_table("weather_collection_progress")
    with op.batch_alter_table("weather_retrieval") as batch:
        batch.drop_column("purpose")
