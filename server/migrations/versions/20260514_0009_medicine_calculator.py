"""add medicine calculator purchases

Revision ID: 20260514_0009
Revises: 20260330_0008
Create Date: 2026-05-14 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260514_0009"
down_revision = "20260330_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "medicine_purchases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("medicine_name", sa.Text(), nullable=False),
        sa.Column("medicine_key", sa.Text(), nullable=False),
        sa.Column("purchase_date", sa.Text(), nullable=False),
        sa.Column("pieces_bought", sa.Integer(), nullable=False),
        sa.Column("dose_per_dosing_day", sa.Integer(), nullable=False),
        sa.Column("dosing_weekdays_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("pieces_bought >= 1", name="ck_medicine_pieces_bought_positive"),
        sa.CheckConstraint("dose_per_dosing_day >= 1", name="ck_medicine_dose_positive"),
    )
    op.create_index(
        "idx_medicine_purchases_key_date",
        "medicine_purchases",
        ["medicine_key", "purchase_date", "id"],
    )


def downgrade() -> None:
    op.drop_index("idx_medicine_purchases_key_date", table_name="medicine_purchases")
    op.drop_table("medicine_purchases")
