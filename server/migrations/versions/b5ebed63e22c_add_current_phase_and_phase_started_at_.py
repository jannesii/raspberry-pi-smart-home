"""add_current_phase_and_phase_started_at_to_thermostat_conf

Revision ID: b5ebed63e22c
Revises: 20260126_0002
Create Date: 2026-02-01 10:48:20.219954

"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "b5ebed63e22c"
down_revision = "20260126_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    logger.debug("migration upgrade %s", revision)
    # Add phase tracking columns to thermostat_conf
    with op.batch_alter_table("thermostat_conf", schema=None) as batch_op:
        batch_op.add_column(sa.Column("current_phase", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("phase_started_at", sa.Text(), nullable=True))


def downgrade() -> None:
    logger.debug("migration downgrade %s", revision)
    # Remove phase tracking columns from thermostat_conf
    with op.batch_alter_table("thermostat_conf", schema=None) as batch_op:
        batch_op.drop_column("phase_started_at")
        batch_op.drop_column("current_phase")
