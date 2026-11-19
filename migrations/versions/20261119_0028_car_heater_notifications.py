"""Durable delivery and deduplication for car-heater problem notifications."""

import sqlalchemy as sa
from alembic import op

revision = "20261119_0028"
down_revision = "20261027_0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "car_heater_notification",
        sa.Column("event_key", sa.Text(), primary_key=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("next_attempt_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_car_heater_notification_pending",
        "car_heater_notification",
        ["status", "next_attempt_at"],
    )


def downgrade():
    op.drop_index("ix_car_heater_notification_pending", table_name="car_heater_notification")
    op.drop_table("car_heater_notification")
