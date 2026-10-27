"""Reminder acknowledgements, bounded repeats and automatic retries.

``announcement_acknowledgement`` holds the owner's Done or Skip for one
occurrence (unique per occurrence), separate from delivery evidence and from
any health record; it keeps the occurrence state it replaced.

Occurrences gain ``retries_started`` (automatic retries after a failure that
certainly did not play, counted apart from the configured announcements) and
``next_attempt_purpose`` (announcement | repeat | retry). Attempts gain
``purpose`` and ``certain_not_played``. Existing rows keep NULL purposes and
zero retries, which the service reads as "first announcement".

Revision ID: 20261027_0023
Revises: 20261027_0021
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0023"
down_revision = "20261027_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement_acknowledgement",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "occurrence_id",
            sa.Text(),
            sa.ForeignKey("announcement_occurrence.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("acknowledged_at", sa.Text(), nullable=False),
        sa.Column("acknowledged_by", sa.Text()),
        sa.Column("prior_status", sa.Text(), nullable=False),
        sa.Column("prior_delivery", sa.Text(), nullable=False),
        sa.Column("prior_disposition", sa.Text()),
        sa.Column("prior_disposition_reason", sa.Text()),
        sa.Column("announcements_started", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "action IN ('done', 'skipped')", name="ck_announcement_acknowledgement_action"
        ),
    )
    with op.batch_alter_table("announcement_occurrence") as batch:
        batch.add_column(
            sa.Column("retries_started", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch.add_column(sa.Column("next_attempt_purpose", sa.Text()))
    with op.batch_alter_table("announcement_attempt") as batch:
        batch.add_column(sa.Column("purpose", sa.Text()))
        batch.add_column(sa.Column("certain_not_played", sa.Boolean()))


def downgrade():
    with op.batch_alter_table("announcement_attempt") as batch:
        batch.drop_column("certain_not_played")
        batch.drop_column("purpose")
    with op.batch_alter_table("announcement_occurrence") as batch:
        batch.drop_column("next_attempt_purpose")
        batch.drop_column("retries_started")
    op.drop_table("announcement_acknowledgement")
