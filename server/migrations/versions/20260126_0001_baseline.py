"""Baseline schema (pre-Alembic).

Revision ID: 20260126_0001
Revises:
Create Date: 2026-01-26 00:00:00.000000
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "20260126_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    logger.debug("baseline upgrade start")
    bool_false = sa.text("false")
    bool_true = sa.text("true")
    logger.debug("baseline upgrade bool defaults true=%s false=%s", bool_true.text, bool_false.text)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=bool_false),
        sa.Column("is_root_admin", sa.Boolean(), nullable=False, server_default=bool_false),
        sa.Column("is_temporary", sa.Boolean(), server_default=bool_false),
        sa.Column("expires_at", sa.Text()),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text()),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=bool_false),
        sa.Column("last_used_at", sa.Text()),
        sa.UniqueConstraint("key_id"),
    )
    op.create_table(
        "esp32_temphum",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("humidity", sa.Float(), nullable=False),
        sa.Column("ac_on", sa.Boolean()),
    )
    op.create_table(
        "ac_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("is_on", sa.Boolean(), nullable=False),
        sa.Column("source", sa.Text()),
        sa.Column("note", sa.Text()),
    )
    op.create_table(
        "status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_status_singleton"),
    )
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("image", sa.Text(), nullable=False),
    )
    op.create_table(
        "timelapse_conf",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("image_delay", sa.Integer(), nullable=False),
        sa.Column("temphum_delay", sa.Integer(), nullable=False),
        sa.Column("status_delay", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_timelapse_conf_singleton"),
    )
    op.create_table(
        "thermostat_conf",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sleep_active", sa.Boolean(), nullable=False),
        sa.Column("sleep_start", sa.Text()),
        sa.Column("sleep_stop", sa.Text()),
        sa.Column("target_temp", sa.Float(), nullable=False),
        sa.Column("pos_hysteresis", sa.Float(), nullable=False),
        sa.Column("neg_hysteresis", sa.Float(), nullable=False),
        sa.Column("thermo_active", sa.Boolean(), nullable=False, server_default=bool_true),
        sa.Column("total_on_s", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_off_s", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("min_on_s", sa.Integer(), nullable=False, server_default=sa.text("240")),
        sa.Column("min_off_s", sa.Integer(), nullable=False, server_default=sa.text("240")),
        sa.Column("poll_interval_s", sa.Integer(), nullable=False, server_default=sa.text("15")),
        sa.Column("smooth_window", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("max_stale_s", sa.Integer()),
        sa.Column("sleep_weekly", sa.Text()),
        sa.Column("control_locations", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_thermostat_conf_singleton"),
    )
    op.create_table(
        "gcode_commands",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("gcode", sa.Text(), nullable=False),
    )
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_table(
        "bmp_sensor_data",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("pressure", sa.Float(), nullable=False),
        sa.Column("altitude", sa.Float(), nullable=False),
    )
    op.create_table(
        "car_heater_status",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("is_heater_on", sa.Boolean(), nullable=False),
        sa.Column("instant_power_w", sa.Float(), nullable=False),
        sa.Column("voltage_v", sa.Float()),
        sa.Column("current_a", sa.Float()),
        sa.Column("energy_total_wh", sa.Float()),
        sa.Column("energy_last_min_wh", sa.Float()),
        sa.Column("energy_ts", sa.Integer()),
        sa.Column("device_temp_c", sa.Float()),
        sa.Column("device_temp_f", sa.Float()),
        sa.Column("ambient_temp", sa.Float()),
        sa.Column("source", sa.Text()),
    )
    op.create_table(
        "car_heater_charge_mode",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=bool_false),
        sa.Column("threshold_w", sa.Float(), nullable=False, server_default=sa.text("20.0")),
        sa.Column("power_cut", sa.Boolean(), nullable=False, server_default=bool_false),
        sa.Column("power_cut_at", sa.Text()),
        sa.Column("last_instant_power_w", sa.Float()),
        sa.Column("seen_above_threshold", sa.Boolean(), nullable=False, server_default=bool_false),
        sa.CheckConstraint("id = 1", name="ck_car_heater_charge_mode_singleton"),
    )
    op.create_table(
        "car_heater_keep_at_temp",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_temperature_c", sa.Float()),
        sa.Column("hysteresis_c", sa.Float()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=bool_false),
        sa.CheckConstraint("id = 1", name="ck_car_heater_keep_at_temp_singleton"),
    )
    op.create_table(
        "car_heater_kfactor_session",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("start_ts", sa.Text(), nullable=False),
        sa.Column("end_ts", sa.Text(), nullable=False),
        sa.Column("auto_window_date", sa.Text()),
        sa.Column("auto_window_start", sa.Text()),
        sa.Column("auto_window_stop", sa.Text()),
        sa.Column("heater_mode", sa.Text()),
        sa.Column("outside_t_mean", sa.Float()),
        sa.Column("outside_t_min", sa.Float()),
        sa.Column("outside_t_max", sa.Float()),
        sa.Column("wind_mean", sa.Float()),
        sa.Column("cabin_t_start", sa.Float()),
        sa.Column("cabin_t_end", sa.Float()),
        sa.Column("cabin_t_max", sa.Float()),
        sa.Column("duration_s", sa.Integer()),
        sa.Column("sample_count", sa.Integer()),
        sa.Column("flags_json", sa.Text()),
        sa.Column("quality_score", sa.Float()),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=bool_false),
    )
    op.create_table(
        "car_heater_kfactor_result",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer()),
        sa.Column("model_version", sa.Text()),
        sa.Column("k_loss_W_per_K", sa.Float()),
        sa.Column("eta", sa.Float()),
        sa.Column("rmse_C", sa.Float()),
        sa.Column("r2", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("promoted", sa.Boolean(), nullable=False, server_default=bool_false),
        sa.Column("created_ts", sa.Text()),
    )
    op.create_table(
        "car_heater_kfactor_prediction_outcome",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("predicted_minutes", sa.Float()),
        sa.Column("actual_minutes", sa.Float()),
        sa.Column("error_minutes", sa.Float()),
        sa.Column("cabin_start_c", sa.Float()),
        sa.Column("cabin_end_c", sa.Float()),
        sa.Column("target_c", sa.Float()),
        sa.Column("outside_c", sa.Float()),
        sa.Column("created_ts", sa.Text()),
    )
    op.create_table(
        "car_heater_kfactor_active_params",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("k_loss_W_per_K", sa.Float()),
        sa.Column("eta", sa.Float()),
        sa.Column("updated_ts", sa.Text()),
        sa.Column("source", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_kfactor_active_params_singleton"),
    )
    op.create_table(
        "car_heater_kfactor_bucket_params",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("t_bucket", sa.Integer()),
        sa.Column("wind_bucket", sa.Integer(), nullable=False),
        sa.Column("k_loss_W_per_K", sa.Float()),
        sa.Column("eta", sa.Float()),
        sa.Column("updated_ts", sa.Text()),
        sa.Column("source", sa.Text()),
        sa.UniqueConstraint("t_bucket", "wind_bucket", name="uq_kfactor_bucket_params"),
    )
    op.create_table(
        "car_heater_kfactor_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_json", sa.Text()),
        sa.Column("updated_ts", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_kfactor_config_singleton"),
    )
    op.create_table(
        "car_heater_kfactor_cooldown",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cooldown_until", sa.Text()),
        sa.Column("updated_ts", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_kfactor_cooldown_singleton"),
    )
    op.create_table(
        "car_heater_ready_by_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("state_json", sa.Text()),
        sa.Column("updated_ts", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_ready_by_state_singleton"),
    )
    op.create_table(
        "car_heater_ready_by_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_json", sa.Text()),
        sa.Column("updated_ts", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_ready_by_config_singleton"),
    )
    op.create_table(
        "logging_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_json", sa.Text()),
        sa.Column("updated_ts", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_logging_control_singleton"),
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_esp32_temphum_loc_ts_id
        ON esp32_temphum (location, timestamp DESC, id DESC)
        """
    )
    logger.debug("baseline upgrade creating esp32_temphum index on (timestamp, location)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_esp32_temphum_date_loc
        ON esp32_temphum (timestamp, location)
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_ac_events_ts ON ac_events (timestamp)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys (key_id)")

    logger.debug("baseline upgrade inserting defaults")
    op.execute(
        """
        INSERT INTO status (id, timestamp, status)
        VALUES (1, NOW(), 'IDLE')
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO timelapse_conf (id, image_delay, temphum_delay, status_delay)
        VALUES (1, 5, 10, 15)
        ON CONFLICT (id) DO NOTHING
        """
    )
    logger.debug("baseline upgrade complete")


def downgrade() -> None:
    logger.debug("baseline downgrade start")
    op.execute("DROP INDEX IF EXISTS idx_esp32_temphum_loc_ts_id")
    op.execute("DROP INDEX IF EXISTS idx_esp32_temphum_date_loc")
    op.execute("DROP INDEX IF EXISTS idx_ac_events_ts")
    op.execute("DROP INDEX IF EXISTS idx_api_keys_key_id")

    op.drop_table("logging_control")
    op.drop_table("car_heater_ready_by_config")
    op.drop_table("car_heater_ready_by_state")
    op.drop_table("car_heater_kfactor_cooldown")
    op.drop_table("car_heater_kfactor_config")
    op.drop_table("car_heater_kfactor_bucket_params")
    op.drop_table("car_heater_kfactor_active_params")
    op.drop_table("car_heater_kfactor_prediction_outcome")
    op.drop_table("car_heater_kfactor_result")
    op.drop_table("car_heater_kfactor_session")
    op.drop_table("car_heater_keep_at_temp")
    op.drop_table("car_heater_charge_mode")
    op.drop_table("car_heater_status")
    op.drop_table("bmp_sensor_data")
    op.drop_table("logs")
    op.drop_table("gcode_commands")
    op.drop_table("thermostat_conf")
    op.drop_table("timelapse_conf")
    op.drop_table("images")
    op.drop_table("status")
    op.drop_table("ac_events")
    op.drop_table("esp32_temphum")
    op.drop_table("api_keys")
    op.drop_table("users")
    logger.debug("baseline downgrade complete")
