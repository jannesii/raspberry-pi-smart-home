"""Owner-recorded car-heater installation history.

Append-only: each save adds a row and the newest is current, so moving a
sensor or heater stays visible as configuration history. Existing sessions are
untouched; only sessions created afterwards copy the current record.

Revision ID: 20261021_0019
Revises: 20261017_0018
"""

import sqlalchemy as sa
from alembic import op

revision = "20261021_0019"
down_revision = "20261017_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "car_heater_installation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("saved_at", sa.Text(), nullable=False),
        sa.Column("saved_by", sa.Text()),
        sa.Column("heater", sa.Text()),
        sa.Column("power_topology", sa.Text()),
        sa.Column("glass_reference_position", sa.Text()),
        sa.Column("glass_comparison_position", sa.Text()),
        sa.Column("cabin_position", sa.Text()),
    )


def downgrade():
    op.drop_table("car_heater_installation")
