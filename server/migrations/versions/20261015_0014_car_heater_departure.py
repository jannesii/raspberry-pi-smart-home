"""Permanent departure plan revisions and a single current plan.

Revision ID: 20261015_0014
Revises: 20261014_0013
"""

import sqlalchemy as sa
from alembic import op

revision = "20261015_0014"
down_revision = "20261014_0013"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "car_heater_departure_plan",
        sa.Column(
            "session_id", sa.Text(), sa.ForeignKey("car_heater_session.id"), primary_key=True
        ),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_index(
        "uq_car_heater_departure_current",
        table.name,
        ["is_current"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
        postgresql_where=sa.text("is_current = true"),
    )


def downgrade():
    op.drop_index("uq_car_heater_departure_current", table_name="car_heater_departure_plan")
    op.drop_table("car_heater_departure_plan")
