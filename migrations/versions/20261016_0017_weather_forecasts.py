"""Parking-point forecast versions as received, with values and receipts.

Forecast checks have no requested window, so weather_retrieval's requested
start/end become nullable. Original replies reuse weather_reply.

Revision ID: 20261016_0017
Revises: 20261016_0016
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_0017"
down_revision = "20261016_0016"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("weather_retrieval") as batch:
        batch.alter_column("requested_start", existing_type=sa.Text(), nullable=True)
        batch.alter_column("requested_end", existing_type=sa.Text(), nullable=True)

    op.create_table(
        "weather_forecast_content",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("value_count", sa.Integer(), nullable=False),
        sa.Column(
            "first_retrieval_id",
            sa.Integer(),
            sa.ForeignKey("weather_retrieval.id"),
            nullable=False,
        ),
    )

    op.create_table(
        "weather_forecast_value",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "content_id",
            sa.Integer(),
            sa.ForeignKey("weather_forecast_content.id"),
            nullable=False,
        ),
        sa.Column("parameter", sa.Text(), nullable=False),
        sa.Column("valid_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("source_value", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text()),
        sa.Column("period", sa.Text()),
        sa.Column("statistic", sa.Text()),
        sa.Column("property_href", sa.Text()),
    )
    op.create_index("idx_weather_forecast_value_content", "weather_forecast_value", ["content_id"])

    op.create_table(
        "weather_forecast_version",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("procedure", sa.Text()),
        sa.Column("analysis_time", sa.Text()),
        sa.Column("result_time", sa.Text()),
        sa.Column("phenomenon_start", sa.Text()),
        sa.Column("phenomenon_end", sa.Text()),
        sa.Column("location", sa.Text()),
        sa.Column(
            "content_id",
            sa.Integer(),
            sa.ForeignKey("weather_forecast_content.id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "first_retrieval_id",
            sa.Integer(),
            sa.ForeignKey("weather_retrieval.id"),
            nullable=False,
        ),
        sa.Column("first_retrieved_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_weather_forecast_version_origin",
        "weather_forecast_version",
        ["source_id", "analysis_time"],
    )

    op.create_table(
        "weather_forecast_receipt",
        sa.Column(
            "retrieval_id",
            sa.Integer(),
            sa.ForeignKey("weather_retrieval.id"),
            primary_key=True,
        ),
        sa.Column(
            "version_id",
            sa.Integer(),
            sa.ForeignKey("weather_forecast_version.id"),
            primary_key=True,
        ),
    )
    op.create_index(
        "idx_weather_forecast_receipt_version", "weather_forecast_receipt", ["version_id"]
    )


def downgrade():
    # Dropping evidence is irreversible; back up the database before downgrading.
    # Forecast retrieval rows and their replies are kept, so requested_start/end
    # stay nullable; 0016 code always writes both.
    op.drop_index("idx_weather_forecast_receipt_version", table_name="weather_forecast_receipt")
    op.drop_table("weather_forecast_receipt")
    op.drop_index("idx_weather_forecast_version_origin", table_name="weather_forecast_version")
    op.drop_table("weather_forecast_version")
    op.drop_index("idx_weather_forecast_value_content", table_name="weather_forecast_value")
    op.drop_table("weather_forecast_value")
    op.drop_table("weather_forecast_content")
