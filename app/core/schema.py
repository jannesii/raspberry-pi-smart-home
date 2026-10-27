"""SQLAlchemy Core schema definitions (phase-in for Alembic)."""

from __future__ import annotations

import logging

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
    Column("current_phase", Text),
    Column("phase_started_at", Text),
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

# Heater evidence has no age-based retention; see migration 20261014_0011.
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

# One row per device status frame that reported glass/cabin sensors. A
# ``*_state`` of NULL means the frame omitted that sensor; values are stored
# only for new samples (state "ok", or "stale" when the frame arrived late).
# Timestamps are UTC ISO text.
car_heater_sensor_frame = Table(
    "car_heater_sensor_frame",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("received_at", Text, nullable=False),
    Column("frame_timestamp", Text),
    Column("source", Text),
    Column("firmware_version", Text),
    Column("glass_reference_state", Text),
    Column("glass_reference_temp_c", Float),
    Column("glass_reference_sampled_at", Text),
    Column("glass_comparison_state", Text),
    Column("glass_comparison_temp_c", Float),
    Column("glass_comparison_sampled_at", Text),
    Column("cabin_state", Text),
    Column("cabin_temp_c", Float),
    Column("cabin_humidity_pct", Float),
    Column("cabin_sampled_at", Text),
    Index("idx_car_heater_sensor_frame_received_at", "received_at"),
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
    Column("session_id", Integer, ForeignKey("car_heater_kfactor_session.id")),
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

ynab_payee_category_stats = Table(
    "ynab_payee_category_stats",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("budget_id", Text, nullable=False),
    Column("payee_normalized", Text, nullable=False),
    Column("category_id", Text, nullable=False),
    Column("count", Integer, nullable=False, server_default=text("0")),
    Column("last_used_at", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    UniqueConstraint(
        "budget_id",
        "payee_normalized",
        "category_id",
        name="uq_ynab_payee_category_stats",
    ),
)

ynab_apply_events = Table(
    "ynab_apply_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("budget_id", Text, nullable=False),
    Column("transaction_id", Text, nullable=False),
    Column("payee_normalized", Text, nullable=False),
    Column("category_id", Text, nullable=False),
    Column("applied_by_username", Text),
    Column("applied_at", Text, nullable=False),
    UniqueConstraint("budget_id", "transaction_id", name="uq_ynab_apply_events"),
)

ynab_bootstrap_state = Table(
    "ynab_bootstrap_state",
    metadata,
    Column("budget_id", Text, primary_key=True),
    Column("bootstrapped_at", Text, nullable=False),
    Column("history_start_date", Text, nullable=False),
    Column("history_end_date", Text, nullable=False),
)

ynab_categorizer_config = Table(
    "ynab_categorizer_config",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("budget_id", Text, nullable=False),
    Column("queue_filter_mode", Text, nullable=False, server_default=text("'strict'")),
    Column("test_mode_enabled", Boolean, nullable=False, server_default=text("false")),
    Column("show_reconciled_transactions", Boolean, nullable=False, server_default=text("false")),
    Column("queue_limit_enabled", Boolean, nullable=False, server_default=text("false")),
    Column("queue_limit_value", Integer, nullable=False, server_default=text("30")),
    Column("queue_limit_unit", Text, nullable=False, server_default=text("'days'")),
    Column("quick_apply_include_medium", Boolean, nullable=False, server_default=text("false")),
    Column("default_category_id", Text),
    Column("custom_rules_json", Text),
    Column("updated_ts", Text, nullable=False),
    UniqueConstraint("budget_id", name="uq_ynab_categorizer_config_budget"),
    CheckConstraint("id = 1", name="ck_ynab_categorizer_config_singleton"),
    CheckConstraint("queue_limit_value >= 1", name="ck_ynab_queue_limit_value"),
    CheckConstraint(
        "queue_limit_unit IN ('days', 'months', 'years')",
        name="ck_ynab_queue_limit_unit",
    ),
)

medicine_purchases = Table(
    "medicine_purchases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("medicine_name", Text, nullable=False),
    Column("medicine_key", Text, nullable=False),
    Column("purchase_date", Text, nullable=False),
    Column("pieces_bought", Integer, nullable=False),
    Column("dose_per_dosing_day", Integer, nullable=False),
    Column("dosing_weekdays_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    CheckConstraint("pieces_bought >= 1", name="ck_medicine_pieces_bought_positive"),
    CheckConstraint("dose_per_dosing_day >= 1", name="ck_medicine_dose_positive"),
)

Index(
    "idx_esp32_temphum_loc_ts_id",
    esp32_temphum.c.location,
    desc(esp32_temphum.c.timestamp),
    desc(esp32_temphum.c.id),
)
Index(
    "idx_esp32_temphum_date_loc",
    esp32_temphum.c.timestamp,
    esp32_temphum.c.location,
)
Index("idx_ac_events_ts", ac_events.c.timestamp)
Index("idx_api_keys_key_id", api_keys.c.key_id, unique=True)
Index(
    "idx_ynab_stats_budget_payee",
    ynab_payee_category_stats.c.budget_id,
    ynab_payee_category_stats.c.payee_normalized,
)
Index(
    "idx_ynab_stats_budget_category",
    ynab_payee_category_stats.c.budget_id,
    ynab_payee_category_stats.c.category_id,
)
Index(
    "idx_medicine_purchases_key_date",
    medicine_purchases.c.medicine_key,
    desc(medicine_purchases.c.purchase_date),
    desc(medicine_purchases.c.id),
)

# Permanent evidence; creation never authorizes a heater command.
car_heater_session = Table(
    "car_heater_session",
    metadata,
    Column("id", Text, primary_key=True),
    Column("trigger", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("observed_at", Text, nullable=False),
    Column("requested_departure_at", Text),
    Column("heating_duration_minutes", Float),
    Column("snapshot", Text, nullable=False),
    Column("outcome", Text),
)
Index("idx_car_heater_session_observed_at", car_heater_session.c.observed_at)

# Owner-recorded installation, append-only: the newest row is current and earlier
# rows are configuration history. Sessions copy the current row into their snapshot.
car_heater_installation = Table(
    "car_heater_installation",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("saved_at", Text, nullable=False),
    Column("saved_by", Text),
    Column("heater", Text),
    Column("power_topology", Text),
    Column("glass_reference_position", Text),
    Column("glass_comparison_position", Text),
    Column("cabin_position", Text),
)

# Execution evidence is separate from driver observations and immutable snapshots.
car_heater_execution = Table(
    "car_heater_execution",
    metadata,
    Column("session_id", Text, ForeignKey("car_heater_session.id"), primary_key=True),
    Column("active", Boolean, nullable=False),
    Column("payload", Text, nullable=False),
)

car_heater_departure_plan = Table(
    "car_heater_departure_plan",
    metadata,
    Column("session_id", Text, ForeignKey("car_heater_session.id"), primary_key=True),
    Column("is_current", Boolean, nullable=False),
    Column("payload", Text, nullable=False),
)
Index(
    "uq_car_heater_departure_current",
    car_heater_departure_plan.c.is_current,
    unique=True,
    sqlite_where=car_heater_departure_plan.c.is_current.is_(True),
    postgresql_where=car_heater_departure_plan.c.is_current.is_(True),
)

# Observation photos are permanent evidence. Content stays in the database so the
# whole-database backup keeps each image with its session association.
car_heater_photo = Table(
    "car_heater_photo",
    metadata,
    Column("id", Text, primary_key=True),
    Column("session_id", Text, ForeignKey("car_heater_session.id"), nullable=False),
    Column("captured_at", Text, nullable=False),
    Column("due_at", Text),
    Column("capture_kind", Text, nullable=False),
    Column("sequence", Integer),
    Column("observation_started_at", Text),
    Column("camera_label", Text),
    Column("received_at", Text, nullable=False),
    Column("content_type", Text, nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("content", LargeBinary, nullable=False),
)
Index(
    "idx_car_heater_photo_session_captured",
    car_heater_photo.c.session_id,
    car_heater_photo.c.captured_at,
)

# One supervised capture run: its window and the scheduled photos it missed.
car_heater_observation = Table(
    "car_heater_observation",
    metadata,
    Column("id", Text, primary_key=True),
    Column("session_id", Text, ForeignKey("car_heater_session.id"), nullable=False),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
    Column("missed", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
Index("idx_car_heater_observation_session", car_heater_observation.c.session_id)

# Permanent weather evidence. Original successful FMI replies are stored once per
# content checksum, compressed, so whole-database backups keep them with the
# retrievals and readings that reference them.
weather_reply = Table(
    "weather_reply",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sha256", Text, nullable=False, unique=True),
    Column("byte_size", Integer, nullable=False),
    Column("content_type", Text, nullable=False),
    Column("compression", Text, nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("first_retrieved_at", Text, nullable=False),
)

# Every collection attempt per source, successful or failed: the retrieval history.
weather_retrieval = Table(
    "weather_retrieval",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("intended_at", Text, nullable=False),
    # Observation windows; forecast checks take the source's default horizon (NULL).
    Column("requested_start", Text),
    Column("requested_end", Text),
    Column("started_at", Text, nullable=False),
    Column("retrieved_at", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("http_status", Integer),
    Column("error", Text),
    Column("reply_id", Integer, ForeignKey("weather_reply.id")),
    Column("location", Text),
    Column("summary", Text, nullable=False),
    # live | initialization | repair: backfilled readings keep their real
    # retrieval time and never look previously available.
    Column("purpose", Text, nullable=False, server_default="live"),
)
Index(
    "idx_weather_retrieval_source_retrieved",
    weather_retrieval.c.kind,
    weather_retrieval.c.source_id,
    weather_retrieval.c.retrieved_at,
)

# Per-source history initialization. Coverage and gaps derive from retrievals;
# this row fixes where collection history starts (new: 24 hours before the
# first collection; upgraded: 24 hours before the first live-only retrieval)
# and when that history was fully attempted, so restarts resume, not restart.
weather_collection_progress = Table(
    "weather_collection_progress",
    metadata,
    Column("kind", Text, primary_key=True),
    Column("source_id", Text, primary_key=True),
    Column("origin", Text, nullable=False),
    Column("history_start", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("initialized_at", Text),
    Column("last_repair_at", Text),
    Column("updated_at", Text, nullable=False),
)

# Station readings at their source time. A changed value for the same station,
# field and time is a new revision; earlier revisions are never overwritten.
weather_observation = Table(
    "weather_observation",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("fmisid", Text, nullable=False),
    Column("parameter", Text, nullable=False),
    Column("observed_at", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("value", Float),
    Column("source_value", Text, nullable=False),
    Column("unit", Text),
    Column("period", Text),
    Column("property_href", Text),
    Column("retrieval_id", Integer, ForeignKey("weather_retrieval.id"), nullable=False),
    UniqueConstraint(
        "fmisid", "parameter", "observed_at", "revision", name="uq_weather_observation_revision"
    ),
)
Index(
    "idx_weather_observation_time",
    weather_observation.c.observed_at,
    weather_observation.c.fmisid,
)

# Point-forecast numerical content, stored once per checksum of its values.
weather_forecast_content = Table(
    "weather_forecast_content",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sha256", Text, nullable=False, unique=True),
    Column("value_count", Integer, nullable=False),
    Column("first_retrieval_id", Integer, ForeignKey("weather_retrieval.id"), nullable=False),
)

# Every returned hourly value of a forecast content, at its own valid time.
weather_forecast_value = Table(
    "weather_forecast_value",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("content_id", Integer, ForeignKey("weather_forecast_content.id"), nullable=False),
    Column("parameter", Text, nullable=False),
    Column("valid_at", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("value", Float),
    Column("source_value", Text, nullable=False),
    Column("unit", Text),
    Column("period", Text),
    Column("statistic", Text),
    Column("property_href", Text),
)
Index("idx_weather_forecast_value_content", weather_forecast_value.c.content_id)

# A distinct forecast version as received: request configuration, model and
# source times, returned location and content. A change in any of them is a new
# version; a repeated origin with different content or result time is a revision.
weather_forecast_version = Table(
    "weather_forecast_version",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Text, nullable=False),
    Column("request", Text, nullable=False),
    Column("procedure", Text),
    Column("analysis_time", Text),
    Column("result_time", Text),
    Column("phenomenon_start", Text),
    Column("phenomenon_end", Text),
    Column("location", Text),
    Column("content_id", Integer, ForeignKey("weather_forecast_content.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("first_retrieval_id", Integer, ForeignKey("weather_retrieval.id"), nullable=False),
    Column("first_retrieved_at", Text, nullable=False),
)
Index(
    "idx_weather_forecast_version_origin",
    weather_forecast_version.c.source_id,
    weather_forecast_version.c.analysis_time,
)

# Every retrieval that received a version, so repeats keep their own reply.
weather_forecast_receipt = Table(
    "weather_forecast_receipt",
    metadata,
    Column("retrieval_id", Integer, ForeignKey("weather_retrieval.id"), primary_key=True),
    Column("version_id", Integer, ForeignKey("weather_forecast_version.id"), primary_key=True),
)
Index("idx_weather_forecast_receipt_version", weather_forecast_receipt.c.version_id)

# A deliberate owner request to play prepared audio on one announcement target.
# Not a reminder occurrence: it is never acknowledged and creates no schedule.
announcement_speaker_test = Table(
    "announcement_speaker_test",
    metadata,
    Column("id", Text, primary_key=True),
    Column("request_id", Text, nullable=False, unique=True),
    Column("requested_at", Text, nullable=False),
    Column("requested_by", Text),
    Column("text", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("target_key", Text, nullable=False),
    Column("volume_percent", Integer, nullable=False),
    Column("chime_percent", Integer, nullable=False),
    CheckConstraint("language IN ('fi', 'en')", name="ck_announcement_speaker_test_language"),
    CheckConstraint("volume_percent BETWEEN 0 AND 100", name="ck_announcement_speaker_test_volume"),
    CheckConstraint("chime_percent BETWEEN 0 AND 100", name="ck_announcement_speaker_test_chime"),
)

# Speaker delivery evidence for one dispatch to one target. Dispatch intent
# (intent_at) is committed before any Cast effect; an attempt left between
# intent and a decisive outcome by a restart becomes 'unknown' and is never
# replayed. Evidence is what the receiver reported, not proof of audibility.
# Holds its own content/volume snapshot so the dispatcher needs only this row.
announcement_attempt = Table(
    "announcement_attempt",
    metadata,
    Column("id", Text, primary_key=True),
    Column("speaker_test_id", Text, ForeignKey("announcement_speaker_test.id")),
    Column(
        "occurrence_id",
        Text,
        ForeignKey("announcement_occurrence.id", name="fk_announcement_attempt_occurrence"),
    ),
    Column("target_key", Text, nullable=False),
    Column("target_name", Text, nullable=False),
    Column("target_host", Text),
    Column("target_device_id", Text),
    Column("speaker_set", Text, nullable=False),
    Column("evidence_scope", Text, nullable=False),
    Column("priority", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("volume_percent", Integer, nullable=False),
    Column("chime_percent", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("reason", Text),
    Column("requested_at", Text, nullable=False),
    Column("dispatch_deadline", Text, nullable=False),
    Column("voice", Text),
    Column("media_key", Text),
    Column("media_duration_s", Float),
    Column("intent_at", Text),
    Column("dispatched_at", Text),
    Column("playback_observed_at", Text),
    Column("finished_at", Text),
    Column("stop_requested_at", Text),
    Column("stop_outcome", Text),
    Column("stop_observed_at", Text),
    Column("volume_before", Float),
    Column("volume_applied", Float),
    Column("volume_restore", Text),
    Column("volume_restored_at", Text),
    Column("updated_at", Text, nullable=False),
    # Media state an urgent announcement replaced ('playing' | 'unknown'), and
    # whether that music could be restored ('unavailable' until #38 adds it).
    Column("interrupted_media", Text),
    Column("media_restore", Text),
    # Occurrence attempts only: 'announcement' (the first), 'repeat' (a further
    # announcement the owner configured; both count in announcements_started)
    # or 'retry' (automatic, after a failure that certainly did not play; not
    # counted as an announcement). NULL for speaker tests.
    Column("purpose", Text),
    # True when the transport established the audio was certainly not played,
    # the only failure an automatic retry may follow.
    Column("certain_not_played", Boolean),
    # Group targets (evidence_scope='group'): JSON list of per-member
    # availability/membership as observed around dispatch and playback. The
    # group's playback report is the only playback evidence; members never
    # report audibility.
    Column("member_evidence", Text),
    CheckConstraint(
        "status IN ('requested', 'dispatching', 'dispatched', 'playing', "
        "'finished', 'failed', 'unknown', 'stopped')",
        name="ck_announcement_attempt_status",
    ),
    CheckConstraint(
        "(speaker_test_id IS NULL) <> (occurrence_id IS NULL)",
        name="ck_announcement_attempt_source",
    ),
)
Index(
    "idx_announcement_attempt_status",
    announcement_attempt.c.status,
    announcement_attempt.c.requested_at,
)
Index("idx_announcement_attempt_speaker_test", announcement_attempt.c.speaker_test_id)
Index("idx_announcement_attempt_occurrence", announcement_attempt.c.occurrence_id)

# An owner-defined rule for when a reminder is due. Never hard-deleted while
# occurrences reference it: deletion sets status='deleted' and keeps history.
# ``kind='one_time'`` uses due_local/due_fold (Europe/Helsinki wall time) and
# due_at (UTC); recurring kinds (#34) store their rule as JSON in
# ``recurrence``. ``next_due_at`` is the next instance to materialize (NULL when
# none, or while paused/deleted); ``revision`` increments on each edit and is
# snapshotted by occurrences. Repeat fields (#35) default to one announcement.
announcement_schedule = Table(
    "announcement_schedule",
    metadata,
    Column("id", Text, primary_key=True),
    Column("request_id", Text, unique=True),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("target_key", Text, nullable=False),
    Column("volume_percent", Integer, nullable=False),
    Column("chime_percent", Integer, nullable=False),
    Column("urgent", Boolean, nullable=False),
    Column("quiet_hours_override", Boolean, nullable=False),
    Column("expiry_minutes", Integer, nullable=False),
    Column("max_announcements", Integer, nullable=False),
    Column("repeat_interval_minutes", Integer),
    Column("due_local", Text),
    Column("due_fold", Integer),
    Column("due_at", Text),
    Column("recurrence", Text),
    Column("next_due_at", Text),
    Column("created_at", Text, nullable=False),
    Column("created_by", Text),
    Column("updated_at", Text, nullable=False),
    Column("paused_at", Text),
    Column("deleted_at", Text),
    CheckConstraint("kind IN ('one_time', 'recurring')", name="ck_announcement_schedule_kind"),
    CheckConstraint(
        "status IN ('active', 'paused', 'deleted')", name="ck_announcement_schedule_status"
    ),
    CheckConstraint("language IN ('fi', 'en')", name="ck_announcement_schedule_language"),
    CheckConstraint("volume_percent BETWEEN 0 AND 100", name="ck_announcement_schedule_volume"),
    CheckConstraint("chime_percent BETWEEN 0 AND 100", name="ck_announcement_schedule_chime"),
    CheckConstraint("expiry_minutes BETWEEN 1 AND 240", name="ck_announcement_schedule_expiry"),
    CheckConstraint("max_announcements >= 1", name="ck_announcement_schedule_announcements"),
)
Index(
    "idx_announcement_schedule_next_due",
    announcement_schedule.c.status,
    announcement_schedule.c.next_due_at,
)

# One instance of a schedule, identified by (schedule_id, instance_due_at) and
# created when that instance becomes due. It snapshots the schedule so later
# edits never rewrite history. Three independent axes:
# - status: scheduling lifecycle (waiting -> announcing -> closed);
# - delivery: summary of speaker evidence from its attempts;
# - disposition: why it closed without delivery or what the owner decided
#   (missed/cancelled here; done/skipped/snoozed/superseded/stopped later).
# fallback_reason marks durable eligibility for one logical fallback (#33).
# Acknowledgement is not stored here; delivery never implies it.
announcement_occurrence = Table(
    "announcement_occurrence",
    metadata,
    Column("id", Text, primary_key=True),
    Column("schedule_id", Text, ForeignKey("announcement_schedule.id"), nullable=False),
    Column("instance_due_at", Text, nullable=False),
    Column("schedule_revision", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("target_key", Text, nullable=False),
    Column("target_name", Text, nullable=False),
    Column("speaker_set", Text, nullable=False),
    Column("volume_percent", Integer, nullable=False),
    Column("chime_percent", Integer, nullable=False),
    Column("urgent", Boolean, nullable=False),
    Column("quiet_hours", Text),
    Column("max_announcements", Integer, nullable=False),
    Column("repeat_interval_minutes", Integer),
    Column("due_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("next_attempt_at", Text),
    Column("status", Text, nullable=False),
    Column("waiting_reason", Text),
    Column("waiting_since", Text),
    Column("waiting_history", Text, nullable=False),
    Column("announcements_started", Integer, nullable=False),
    Column("current_attempt_id", Text),
    Column("delivery", Text, nullable=False),
    Column("delivery_reason", Text),
    Column("disposition", Text),
    Column("disposition_reason", Text),
    Column("closed_at", Text),
    Column("fallback_reason", Text),
    Column("fallback_eligible_at", Text),
    Column("updated_at", Text, nullable=False),
    # Automatic retries after certain non-playback; separate from the owner's
    # configured announcements and bounded within the original expiry.
    Column("retries_started", Integer, nullable=False, server_default=text("0")),
    # What the next dispatch is: 'announcement' (first), 'repeat' or 'retry'.
    Column("next_attempt_purpose", Text),
    UniqueConstraint("schedule_id", "instance_due_at", name="uq_announcement_occurrence_instance"),
    CheckConstraint(
        "status IN ('waiting', 'announcing', 'closed')",
        name="ck_announcement_occurrence_status",
    ),
    CheckConstraint(
        "delivery IN ('none', 'delivered', 'partial', 'failed', 'unknown')",
        name="ck_announcement_occurrence_delivery",
    ),
    CheckConstraint(
        "disposition IS NULL OR disposition IN ('missed', 'cancelled', 'done', 'skipped', "
        "'snoozed', 'superseded', 'stopped')",
        name="ck_announcement_occurrence_disposition",
    ),
    CheckConstraint(
        "fallback_reason IS NULL OR fallback_reason IN ('failed', 'partial', 'unknown', "
        "'expired_undelivered')",
        name="ck_announcement_occurrence_fallback",
    ),
)
Index(
    "idx_announcement_occurrence_status",
    announcement_occurrence.c.status,
    announcement_occurrence.c.due_at,
)
Index(
    "idx_announcement_occurrence_fallback",
    announcement_occurrence.c.fallback_reason,
    announcement_occurrence.c.fallback_eligible_at,
)

# One logical fallback notification per reminder occurrence (unique), created
# once the occurrence records fallback_reason. ``message`` is composed once so
# every retry sends identical content. Attempts are persisted ('sending')
# before each webhook request; a request left 'sending' by a restart becomes
# 'unknown' and is never resent. 'accepted' means Discord took the message,
# not that anyone read it, and is never a reminder acknowledgement. Done/Skip
# before a request starts cancels a pending fallback ('cancelled',
# status_reason 'acknowledged'); a sent or uncertain one stays as recorded.
announcement_fallback = Table(
    "announcement_fallback",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "occurrence_id",
        Text,
        ForeignKey("announcement_occurrence.id"),
        nullable=False,
        unique=True,
    ),
    Column("channel", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("link", Text),
    Column("status", Text, nullable=False),
    Column("status_reason", Text),
    Column("attempts_made", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("next_attempt_at", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("accepted_at", Text),
    Column("completed_at", Text),
    CheckConstraint(
        "status IN ('pending', 'sending', 'accepted', 'unknown', 'failed', 'unavailable', "
        "'cancelled')",
        name="ck_announcement_fallback_status",
    ),
    CheckConstraint(
        "reason IN ('failed', 'partial', 'unknown', 'expired_undelivered')",
        name="ck_announcement_fallback_reason",
    ),
    CheckConstraint(
        "attempts_made BETWEEN 0 AND max_attempts", name="ck_announcement_fallback_attempts"
    ),
)
Index(
    "idx_announcement_fallback_status",
    announcement_fallback.c.status,
    announcement_fallback.c.next_attempt_at,
)

# One webhook request of a fallback notification, numbered 1..max_attempts.
announcement_fallback_attempt = Table(
    "announcement_fallback_attempt",
    metadata,
    Column("id", Text, primary_key=True),
    Column("fallback_id", Text, ForeignKey("announcement_fallback.id"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("http_status", Integer),
    Column("retry_after_s", Float),
    Column("detail", Text),
    Column("intent_at", Text, nullable=False),
    Column("finished_at", Text),
    UniqueConstraint("fallback_id", "number", name="uq_announcement_fallback_attempt_number"),
    CheckConstraint(
        "status IN ('sending', 'accepted', 'rejected', 'rate_limited', 'failed', "
        "'not_configured', 'unknown')",
        name="ck_announcement_fallback_attempt_status",
    ),
)

# The owner's reminder acknowledgement of one occurrence: Done ('done') or
# Skip this occurrence ('skipped'); at most one per occurrence. It records that
# the owner handled the reminder, never a medicine dose, a blood-pressure value
# or delivery evidence. The occurrence state it replaced is kept for history.
announcement_acknowledgement = Table(
    "announcement_acknowledgement",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "occurrence_id",
        Text,
        ForeignKey("announcement_occurrence.id"),
        nullable=False,
        unique=True,
    ),
    Column("action", Text, nullable=False),
    Column("acknowledged_at", Text, nullable=False),
    Column("acknowledged_by", Text),
    Column("prior_status", Text, nullable=False),
    Column("prior_delivery", Text, nullable=False),
    Column("prior_disposition", Text),
    Column("prior_disposition_reason", Text),
    Column("announcements_started", Integer, nullable=False),
    CheckConstraint("action IN ('done', 'skipped')", name="ck_announcement_acknowledgement_action"),
)

# Deliberate postponements retain their original window and request identity.
announcement_snooze = Table(
    "announcement_snooze",
    metadata,
    Column("request_id", Text, primary_key=True),
    Column("occurrence_id", Text, ForeignKey("announcement_occurrence.id"), nullable=False),
    Column("snoozed_at", Text, nullable=False),
    Column("snoozed_by", Text),
    Column("minutes", Integer, nullable=False),
    Column("prior_disposition", Text),
    Column("prior_due_at", Text, nullable=False),
    Column("prior_expires_at", Text, nullable=False),
    Column("due_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    CheckConstraint("minutes IN (5, 10, 15, 30)", name="ck_announcement_snooze_minutes"),
)
