"""Permanent session-linked observation photos and capture windows.

Photo content is held in the database so whole-database backups include it.

Revision ID: 20261015_0015
Revises: 20261015_0014
"""

import sqlalchemy as sa
from alembic import op

revision = "20261015_0015"
down_revision = "20261015_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "car_heater_photo",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("car_heater_session.id"), nullable=False),
        sa.Column("captured_at", sa.Text(), nullable=False),
        sa.Column("due_at", sa.Text()),
        sa.Column("capture_kind", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer()),
        sa.Column("observation_started_at", sa.Text()),
        sa.Column("camera_label", sa.Text()),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
    )
    op.create_index(
        "idx_car_heater_photo_session_captured",
        "car_heater_photo",
        ["session_id", "captured_at"],
    )

    op.create_table(
        "car_heater_observation",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("car_heater_session.id"), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text()),
        sa.Column("missed", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_car_heater_observation_session", "car_heater_observation", ["session_id"])


def downgrade():
    # Dropping evidence is irreversible; export photos before downgrading.
    op.drop_index("idx_car_heater_observation_session", table_name="car_heater_observation")
    op.drop_table("car_heater_observation")
    op.drop_index("idx_car_heater_photo_session_captured", table_name="car_heater_photo")
    op.drop_table("car_heater_photo")
