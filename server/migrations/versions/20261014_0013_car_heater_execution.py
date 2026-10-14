"""Durable bounded execution and milestones.

Revision ID: 20261014_0013
Revises: 20261014_0012
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_0013"
down_revision = "20261014_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "car_heater_execution",
        sa.Column(
            "session_id", sa.Text(), sa.ForeignKey("car_heater_session.id"), primary_key=True
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("car_heater_execution")
