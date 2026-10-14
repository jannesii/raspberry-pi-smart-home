"""Permanent heating attempts and departure outcomes.

Revision ID: 20261014_0012
Revises: 20261014_0011
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_0012"
down_revision = "20261014_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "car_heater_session",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("requested_departure_at", sa.Text()),
        sa.Column("heating_duration_minutes", sa.Float()),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text()),
    )
    op.create_index("idx_car_heater_session_observed_at", "car_heater_session", ["observed_at"])


def downgrade() -> None:
    op.drop_index("idx_car_heater_session_observed_at", table_name="car_heater_session")
    op.drop_table("car_heater_session")
