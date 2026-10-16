"""Add FK for kfactor result session.

Revision ID: 20260126_0002
Revises: 20260126_0001
Create Date: 2026-01-26 00:00:00.000000
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "20260126_0002"
down_revision = "20260126_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    logger.debug("kfactor result fk upgrade start")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "car_heater_kfactor_result" not in inspector.get_table_names():
        logger.warning("car_heater_kfactor_result missing; skipping FK migration")
        return
    logger.debug("kfactor result fk upgrade adding FK constraint")
    op.create_foreign_key(
        "fk_kfactor_result_session",
        "car_heater_kfactor_result",
        "car_heater_kfactor_session",
        ["session_id"],
        ["id"],
    )
    logger.debug("kfactor result fk upgrade complete")


def downgrade() -> None:
    logger.debug("kfactor result fk downgrade start")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "car_heater_kfactor_result" not in inspector.get_table_names():
        logger.warning("car_heater_kfactor_result missing; skipping FK downgrade")
        return
    logger.debug("kfactor result fk downgrade dropping FK constraint")
    op.drop_constraint("fk_kfactor_result_session", "car_heater_kfactor_result", type_="foreignkey")
    logger.debug("kfactor result fk downgrade complete")
