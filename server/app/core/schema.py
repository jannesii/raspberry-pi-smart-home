"""SQLAlchemy Core schema definitions (phase-in for Alembic)."""

from __future__ import annotations

import logging

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    desc,
    text,
)

logger = logging.getLogger(__name__)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", Text, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("is_admin", Boolean, nullable=False, server_default=text("0")),
    Column("is_root_admin", Boolean, nullable=False, server_default=text("0")),
    Column("is_temporary", Boolean, server_default=text("0")),
    Column("expires_at", Text),
)

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key_id", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("secret_hash", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("created_by", Text),
    Column("revoked", Boolean, nullable=False, server_default=text("0")),
    Column("last_used_at", Text),
)

esp32_temphum = Table(
    "esp32_temphum",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("location", Text, nullable=False),
    Column("timestamp", Text, nullable=False),
    Column("temperature", Float, nullable=False),
    Column("humidity", Float, nullable=False),
    Column("ac_on", Boolean),
)

ac_events = Table(
    "ac_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", Text, nullable=False),
    Column("is_on", Boolean, nullable=False),
    Column("source", Text),
    Column("note", Text),
)

status = Table(
    "status",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("timestamp", Text, nullable=False),
    Column("status", Text, nullable=False),
    CheckConstraint("id = 1", name="ck_status_singleton"),
)

images = Table(
    "images",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", Text, nullable=False),
    Column("image", Text, nullable=False),
)

timelapse_conf = Table(
    "timelapse_conf",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("image_delay", Integer, nullable=False),
    Column("temphum_delay", Integer, nullable=False),
    Column("status_delay", Integer, nullable=False),
    CheckConstraint("id = 1", name="ck_timelapse_conf_singleton"),
)

thermostat_conf = Table(
    "thermostat_conf",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sleep_active", Boolean, nullable=False),
    Column("sleep_start", Text),
    Column("sleep_stop", Text),
    Column("target_temp", Float, nullable=False),
    Column("pos_hysteresis", Float, nullable=False),
    Column("neg_hysteresis", Float, nullable=False),
    Column("thermo_active", Boolean, nullable=False, server_default=text("1")),
    Column("total_on_s", Integer, nullable=False, server_default=text("0")),
    Column("total_off_s", Integer, nullable=False, server_default=text("0")),
    Column("min_on_s", Integer, nullable=False, server_default=text("240")),
    Column("min_off_s", Integer, nullable=False, server_default=text("240")),
    Column("poll_interval_s", Integer, nullable=False, server_default=text("15")),
    Column("smooth_window", Integer, nullable=False, server_default=text("5")),
    Column("max_stale_s", Integer),
    Column("sleep_weekly", Text),
    Column("control_locations", Text),
    CheckConstraint("id = 1", name="ck_thermostat_conf_singleton"),
)

gcode_commands = Table(
    "gcode_commands",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", Text, nullable=False),
    Column("gcode", Text, nullable=False),
)

logs = Table(
    "logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("message", Text, nullable=False),
)

bmp_sensor_data = Table(
    "bmp_sensor_data",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", Text, nullable=False),
    Column("temperature", Float, nullable=False),
    Column("pressure", Float, nullable=False),
    Column("altitude", Float, nullable=False),
)

car_heater_status = Table(
    "car_heater_status",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", Text, nullable=False),
    Column("is_heater_on", Boolean, nullable=False),
    Column("instant_power_w", Float, nullable=False),
    Column("voltage_v", Float),
    Column("current_a", Float),
    Column("energy_total_wh", Float),
    Column("energy_last_min_wh", Float),
    Column("energy_ts", Integer),
    Column("device_temp_c", Float),
    Column("device_temp_f", Float),
    Column("ambient_temp", Float),
    Column("source", Text),
)

car_heater_charge_mode = Table(
    "car_heater_charge_mode",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("enabled", Boolean, nullable=False, server_default=text("0")),
    Column("threshold_w", Float, nullable=False, server_default=text("20.0")),
    Column("power_cut", Boolean, nullable=False, server_default=text("0")),
    Column("power_cut_at", Text),
    Column("last_instant_power_w", Float),
    Column("seen_above_threshold", Boolean, nullable=False, server_default=text("0")),
    CheckConstraint("id = 1", name="ck_car_heater_charge_mode_singleton"),
)

car_heater_keep_at_temp = Table(
    "car_heater_keep_at_temp",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("target_temperature_c", Float),
    Column("hysteresis_c", Float),
    Column("enabled", Boolean, nullable=False, server_default=text("0")),
    CheckConstraint("id = 1", name="ck_car_heater_keep_at_temp_singleton"),
)

car_heater_kfactor_session = Table(
    "car_heater_kfactor_session",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("start_ts", Text, nullable=False),
    Column("end_ts", Text, nullable=False),
    Column("auto_window_date", Text),
    Column("auto_window_start", Text),
    Column("auto_window_stop", Text),
    Column("heater_mode", Text),
    Column("outside_t_mean", Float),
    Column("outside_t_min", Float),
    Column("outside_t_max", Float),
    Column("wind_mean", Float),
    Column("cabin_t_start", Float),
    Column("cabin_t_end", Float),
    Column("cabin_t_max", Float),
    Column("duration_s", Integer),
    Column("sample_count", Integer),
    Column("flags_json", Text),
    Column("quality_score", Float),
    Column("accepted", Boolean, nullable=False, server_default=text("0")),
)

car_heater_kfactor_result = Table(
    "car_heater_kfactor_result",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", Integer),
    Column("model_version", Text),
    Column("k_loss_W_per_K", Float),
    Column("eta", Float),
    Column("rmse_C", Float),
    Column("r2", Float),
    Column("confidence", Float),
    Column("promoted", Boolean, nullable=False, server_default=text("0")),
    Column("created_ts", Text),
)

car_heater_kfactor_prediction_outcome = Table(
    "car_heater_kfactor_prediction_outcome",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("predicted_minutes", Float),
    Column("actual_minutes", Float),
    Column("error_minutes", Float),
    Column("cabin_start_c", Float),
    Column("cabin_end_c", Float),
    Column("target_c", Float),
    Column("outside_c", Float),
    Column("created_ts", Text),
)

car_heater_kfactor_active_params = Table(
    "car_heater_kfactor_active_params",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("k_loss_W_per_K", Float),
    Column("eta", Float),
    Column("updated_ts", Text),
    Column("source", Text),
    CheckConstraint("id = 1", name="ck_kfactor_active_params_singleton"),
)

car_heater_kfactor_bucket_params = Table(
    "car_heater_kfactor_bucket_params",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("t_bucket", Integer),
    Column("wind_bucket", Integer, nullable=False),
    Column("k_loss_W_per_K", Float),
    Column("eta", Float),
    Column("updated_ts", Text),
    Column("source", Text),
    UniqueConstraint("t_bucket", "wind_bucket", name="uq_kfactor_bucket_params"),
)

car_heater_kfactor_config = Table(
    "car_heater_kfactor_config",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("config_json", Text),
    Column("updated_ts", Text),
    CheckConstraint("id = 1", name="ck_kfactor_config_singleton"),
)

car_heater_kfactor_cooldown = Table(
    "car_heater_kfactor_cooldown",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("cooldown_until", Text),
    Column("updated_ts", Text),
    CheckConstraint("id = 1", name="ck_kfactor_cooldown_singleton"),
)

car_heater_ready_by_state = Table(
    "car_heater_ready_by_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("state_json", Text),
    Column("updated_ts", Text),
    CheckConstraint("id = 1", name="ck_ready_by_state_singleton"),
)

car_heater_ready_by_config = Table(
    "car_heater_ready_by_config",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("config_json", Text),
    Column("updated_ts", Text),
    CheckConstraint("id = 1", name="ck_ready_by_config_singleton"),
)

logging_control = Table(
    "logging_control",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("config_json", Text),
    Column("updated_ts", Text),
    CheckConstraint("id = 1", name="ck_logging_control_singleton"),
)

Index(
    "idx_esp32_temphum_loc_ts_id",
    esp32_temphum.c.location,
    desc(esp32_temphum.c.timestamp),
    desc(esp32_temphum.c.id),
)
Index(
    "idx_esp32_temphum_date_loc",
    text("date(timestamp)"),
    esp32_temphum.c.location,
)
Index("idx_ac_events_ts", ac_events.c.timestamp)
Index("idx_api_keys_key_id", api_keys.c.key_id, unique=True)
