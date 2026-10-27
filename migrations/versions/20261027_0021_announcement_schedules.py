"""One-time announcement schedules and reminder occurrences.

A schedule says when a reminder is due; an occurrence is one due instance,
unique per (schedule_id, instance_due_at), snapshotting the schedule so later
edits never rewrite history. Occurrence status (waiting/announcing/closed),
delivery evidence and disposition are separate columns; ``fallback_reason``
records durable eligibility for one fallback notification. Attempts gain an
``occurrence_id`` and must belong to exactly one source (test or occurrence),
plus the media state an urgent announcement interrupted.

Schedules are soft-deleted (status='deleted') so occurrence history survives.

Revision ID: 20261027_0021
Revises: 20261027_0020
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0021"
down_revision = "20261027_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement_schedule",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("request_id", sa.Text(), unique=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("volume_percent", sa.Integer(), nullable=False),
        sa.Column("chime_percent", sa.Integer(), nullable=False),
        sa.Column("urgent", sa.Boolean(), nullable=False),
        sa.Column("quiet_hours_override", sa.Boolean(), nullable=False),
        sa.Column("expiry_minutes", sa.Integer(), nullable=False),
        sa.Column("max_announcements", sa.Integer(), nullable=False),
        sa.Column("repeat_interval_minutes", sa.Integer()),
        sa.Column("due_local", sa.Text()),
        sa.Column("due_fold", sa.Integer()),
        sa.Column("due_at", sa.Text()),
        sa.Column("recurrence", sa.Text()),
        sa.Column("next_due_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("paused_at", sa.Text()),
        sa.Column("deleted_at", sa.Text()),
        sa.CheckConstraint(
            "kind IN ('one_time', 'recurring')", name="ck_announcement_schedule_kind"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'deleted')", name="ck_announcement_schedule_status"
        ),
        sa.CheckConstraint("language IN ('fi', 'en')", name="ck_announcement_schedule_language"),
        sa.CheckConstraint(
            "volume_percent BETWEEN 0 AND 100", name="ck_announcement_schedule_volume"
        ),
        sa.CheckConstraint(
            "chime_percent BETWEEN 0 AND 100", name="ck_announcement_schedule_chime"
        ),
        sa.CheckConstraint(
            "expiry_minutes BETWEEN 1 AND 240", name="ck_announcement_schedule_expiry"
        ),
        sa.CheckConstraint("max_announcements >= 1", name="ck_announcement_schedule_announcements"),
    )
    op.create_index(
        "idx_announcement_schedule_next_due",
        "announcement_schedule",
        ["status", "next_due_at"],
    )
    op.create_table(
        "announcement_occurrence",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Text(),
            sa.ForeignKey("announcement_schedule.id"),
            nullable=False,
        ),
        sa.Column("instance_due_at", sa.Text(), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("target_name", sa.Text(), nullable=False),
        sa.Column("speaker_set", sa.Text(), nullable=False),
        sa.Column("volume_percent", sa.Integer(), nullable=False),
        sa.Column("chime_percent", sa.Integer(), nullable=False),
        sa.Column("urgent", sa.Boolean(), nullable=False),
        sa.Column("quiet_hours", sa.Text()),
        sa.Column("max_announcements", sa.Integer(), nullable=False),
        sa.Column("repeat_interval_minutes", sa.Integer()),
        sa.Column("due_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("next_attempt_at", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("waiting_reason", sa.Text()),
        sa.Column("waiting_since", sa.Text()),
        sa.Column("waiting_history", sa.Text(), nullable=False),
        sa.Column("announcements_started", sa.Integer(), nullable=False),
        sa.Column("current_attempt_id", sa.Text()),
        sa.Column("delivery", sa.Text(), nullable=False),
        sa.Column("delivery_reason", sa.Text()),
        sa.Column("disposition", sa.Text()),
        sa.Column("disposition_reason", sa.Text()),
        sa.Column("closed_at", sa.Text()),
        sa.Column("fallback_reason", sa.Text()),
        sa.Column("fallback_eligible_at", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "schedule_id", "instance_due_at", name="uq_announcement_occurrence_instance"
        ),
        sa.CheckConstraint(
            "status IN ('waiting', 'announcing', 'closed')",
            name="ck_announcement_occurrence_status",
        ),
        sa.CheckConstraint(
            "delivery IN ('none', 'delivered', 'partial', 'failed', 'unknown')",
            name="ck_announcement_occurrence_delivery",
        ),
        sa.CheckConstraint(
            "disposition IS NULL OR disposition IN ('missed', 'cancelled', 'done', 'skipped', "
            "'snoozed', 'superseded', 'stopped')",
            name="ck_announcement_occurrence_disposition",
        ),
        sa.CheckConstraint(
            "fallback_reason IS NULL OR fallback_reason IN ('failed', 'partial', 'unknown', "
            "'expired_undelivered')",
            name="ck_announcement_occurrence_fallback",
        ),
    )
    op.create_index(
        "idx_announcement_occurrence_status",
        "announcement_occurrence",
        ["status", "due_at"],
    )
    op.create_index(
        "idx_announcement_occurrence_fallback",
        "announcement_occurrence",
        ["fallback_reason", "fallback_eligible_at"],
    )
    # SQLite needs batch mode (table copy) to add a foreign key and a check.
    with op.batch_alter_table("announcement_attempt") as batch:
        batch.add_column(sa.Column("occurrence_id", sa.Text()))
        batch.add_column(sa.Column("interrupted_media", sa.Text()))
        batch.add_column(sa.Column("media_restore", sa.Text()))
        batch.create_foreign_key(
            "fk_announcement_attempt_occurrence",
            "announcement_occurrence",
            ["occurrence_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_announcement_attempt_source",
            "(speaker_test_id IS NULL) <> (occurrence_id IS NULL)",
        )
    op.create_index(
        "idx_announcement_attempt_occurrence", "announcement_attempt", ["occurrence_id"]
    )


def downgrade():
    op.drop_index("idx_announcement_attempt_occurrence", table_name="announcement_attempt")
    with op.batch_alter_table("announcement_attempt") as batch:
        batch.drop_constraint("ck_announcement_attempt_source", type_="check")
        batch.drop_constraint("fk_announcement_attempt_occurrence", type_="foreignkey")
        batch.drop_column("media_restore")
        batch.drop_column("interrupted_media")
        batch.drop_column("occurrence_id")
    op.drop_index("idx_announcement_occurrence_fallback", table_name="announcement_occurrence")
    op.drop_index("idx_announcement_occurrence_status", table_name="announcement_occurrence")
    op.drop_table("announcement_occurrence")
    op.drop_index("idx_announcement_schedule_next_due", table_name="announcement_schedule")
    op.drop_table("announcement_schedule")
