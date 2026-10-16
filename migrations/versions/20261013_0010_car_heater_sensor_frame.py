"""add car heater glass and cabin sensor frames

Revision ID: 20261013_0010
Revises: 20260514_0009
Create Date: 2026-10-13 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261013_0010"
down_revision = "20260514_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "car_heater_sensor_frame",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("frame_timestamp", sa.Text()),
        sa.Column("source", sa.Text()),
        sa.Column("firmware_version", sa.Text()),
        sa.Column("glass_reference_state", sa.Text()),
        sa.Column("glass_reference_temp_c", sa.Float()),
        sa.Column("glass_reference_sampled_at", sa.Text()),
        sa.Column("glass_comparison_state", sa.Text()),
        sa.Column("glass_comparison_temp_c", sa.Float()),
        sa.Column("glass_comparison_sampled_at", sa.Text()),
        sa.Column("cabin_state", sa.Text()),
        sa.Column("cabin_temp_c", sa.Float()),
        sa.Column("cabin_humidity_pct", sa.Float()),
        sa.Column("cabin_sampled_at", sa.Text()),
    )
    op.create_index(
        "idx_car_heater_sensor_frame_received_at",
        "car_heater_sensor_frame",
        ["received_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_car_heater_sensor_frame_received_at", table_name="car_heater_sensor_frame")
    op.drop_table("car_heater_sensor_frame")
