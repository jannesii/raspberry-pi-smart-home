"""Permanent station-observation archive with original FMI replies.

Reply content is stored compressed in the database so whole-database backups
keep it with the retrievals and readings that reference it.

Revision ID: 20261016_0016
Revises: 20261015_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_0016"
down_revision = "20261015_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weather_reply",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("compression", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("first_retrieved_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "weather_retrieval",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("intended_at", sa.Text(), nullable=False),
        sa.Column("requested_start", sa.Text(), nullable=False),
        sa.Column("requested_end", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("reply_id", sa.Integer(), sa.ForeignKey("weather_reply.id")),
        sa.Column("location", sa.Text()),
        sa.Column("summary", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_weather_retrieval_source_retrieved",
        "weather_retrieval",
        ["kind", "source_id", "retrieved_at"],
    )

    op.create_table(
        "weather_observation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fmisid", sa.Text(), nullable=False),
        sa.Column("parameter", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("source_value", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text()),
        sa.Column("period", sa.Text()),
        sa.Column("property_href", sa.Text()),
        sa.Column(
            "retrieval_id", sa.Integer(), sa.ForeignKey("weather_retrieval.id"), nullable=False
        ),
        sa.UniqueConstraint(
            "fmisid",
            "parameter",
            "observed_at",
            "revision",
            name="uq_weather_observation_revision",
        ),
    )
    op.create_index(
        "idx_weather_observation_time",
        "weather_observation",
        ["observed_at", "fmisid"],
    )


def downgrade():
    # Dropping evidence is irreversible; back up the database before downgrading.
    op.drop_index("idx_weather_observation_time", table_name="weather_observation")
    op.drop_table("weather_observation")
    op.drop_index("idx_weather_retrieval_source_retrieved", table_name="weather_retrieval")
    op.drop_table("weather_retrieval")
    op.drop_table("weather_reply")
