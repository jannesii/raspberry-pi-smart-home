"""Explicit announcement speaker tests and their delivery attempts.

A speaker test is a deliberate owner request, not a reminder occurrence. Each
attempt records dispatch intent before any Cast effect, then only the evidence
the receiver reported (requested, observed playback, failed, unknown,
stopped). Attempts are generic so reminder occurrences can reference them
later; ``speaker_test_id`` is therefore nullable.

Revision ID: 20261027_0020
Revises: 20261021_0019
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0020"
down_revision = "20261021_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement_speaker_test",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("request_id", sa.Text(), nullable=False, unique=True),
        sa.Column("requested_at", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("volume_percent", sa.Integer(), nullable=False),
        sa.Column("chime_percent", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "language IN ('fi', 'en')", name="ck_announcement_speaker_test_language"
        ),
        sa.CheckConstraint(
            "volume_percent BETWEEN 0 AND 100", name="ck_announcement_speaker_test_volume"
        ),
        sa.CheckConstraint(
            "chime_percent BETWEEN 0 AND 100", name="ck_announcement_speaker_test_chime"
        ),
    )
    op.create_table(
        "announcement_attempt",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("speaker_test_id", sa.Text(), sa.ForeignKey("announcement_speaker_test.id")),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("target_name", sa.Text(), nullable=False),
        sa.Column("target_host", sa.Text()),
        sa.Column("target_device_id", sa.Text()),
        sa.Column("speaker_set", sa.Text(), nullable=False),
        sa.Column("evidence_scope", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("volume_percent", sa.Integer(), nullable=False),
        sa.Column("chime_percent", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("requested_at", sa.Text(), nullable=False),
        sa.Column("dispatch_deadline", sa.Text(), nullable=False),
        sa.Column("voice", sa.Text()),
        sa.Column("media_key", sa.Text()),
        sa.Column("media_duration_s", sa.Float()),
        sa.Column("intent_at", sa.Text()),
        sa.Column("dispatched_at", sa.Text()),
        sa.Column("playback_observed_at", sa.Text()),
        sa.Column("finished_at", sa.Text()),
        sa.Column("stop_requested_at", sa.Text()),
        sa.Column("stop_outcome", sa.Text()),
        sa.Column("stop_observed_at", sa.Text()),
        sa.Column("volume_before", sa.Float()),
        sa.Column("volume_applied", sa.Float()),
        sa.Column("volume_restore", sa.Text()),
        sa.Column("volume_restored_at", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('requested', 'dispatching', 'dispatched', 'playing', "
            "'finished', 'failed', 'unknown', 'stopped')",
            name="ck_announcement_attempt_status",
        ),
    )
    op.create_index(
        "idx_announcement_attempt_status", "announcement_attempt", ["status", "requested_at"]
    )
    op.create_index(
        "idx_announcement_attempt_speaker_test", "announcement_attempt", ["speaker_test_id"]
    )


def downgrade():
    op.drop_index("idx_announcement_attempt_speaker_test", table_name="announcement_attempt")
    op.drop_index("idx_announcement_attempt_status", table_name="announcement_attempt")
    op.drop_table("announcement_attempt")
    op.drop_table("announcement_speaker_test")
