# models.py

from dataclasses import dataclass
from datetime import datetime

import pytz

# --- Data classes for domain models ---


@dataclass
class User:
    id: int
    username: str
    password_hash: str
    is_admin: bool = False
    is_root_admin: bool = False
    is_temporary: bool = False
    expires_at: str | None = None

    @property
    def is_expired(self) -> bool:
        """
        Return True if this is a temporary user and the
        expiry timestamp is in the past (UTC-aware).
        """
        finland_tz = pytz.timezone("Europe/Helsinki")
        if not self.is_temporary or self.expires_at is None:
            return False
        expires_at_dt = self.expires_at
        if isinstance(expires_at_dt, str):
            try:
                expires_at_dt = datetime.fromisoformat(expires_at_dt)
                if expires_at_dt.tzinfo is None:
                    expires_at_dt = finland_tz.localize(expires_at_dt)
            except Exception:
                return False
        return expires_at_dt <= datetime.now(finland_tz)


@dataclass
class TemperatureHumidity:
    id: int
    timestamp: str
    temperature: float
    humidity: float


@dataclass
class ESP32TemperatureHumidity:
    id: int
    location: str
    timestamp: str
    temperature: float
    humidity: float
    ac_on: bool | None = None


@dataclass
class Status:
    id: int
    timestamp: str
    status: str


@dataclass
class ImageData:
    id: int
    timestamp: str
    image: str  # base64-encoded image data


@dataclass
class TimelapseConf:
    id: int
    image_delay: int
    temphum_delay: int
    status_delay: int


@dataclass
class ThermostatConf:
    # Core persisted configuration with sensible defaults
    id: int = 1
    sleep_active: bool = True
    sleep_start: str | None = None
    sleep_stop: str | None = None
    # Optional weekly schedule as JSON string mapping days → {start, stop}
    sleep_weekly: str | None = None
    # Optional list of sensor locations used to compute control temperature (JSON string)
    control_locations: str | None = None
    target_temp: float = 24.5
    pos_hysteresis: float = 0.5
    neg_hysteresis: float = 0.5
    thermo_active: bool = True
    # Current phase tracking for accurate counters across restarts
    current_phase: str | None = None  # 'on' or 'off'
    phase_started_at: str | None = None  # ISO timestamp when current phase began
    # Local control loop defaults (moved here from ThermostatConfig)
    min_on_s: int = 240
    min_off_s: int = 240
    poll_interval_s: int = 15
    smooth_window: int = 5
    max_stale_s: int | None = 120


@dataclass
class ApiKey:
    id: int
    key_id: str
    name: str
    created_at: str
    created_by: str | None
    revoked: bool
    last_used_at: str | None = None


@dataclass
class BMPData:
    id: int
    timestamp: str
    temperature: float
    pressure: float
    altitude: float


@dataclass
class CarHeaterStatus:
    id: int | None
    timestamp: str
    is_heater_on: bool
    instant_power_w: float
    voltage_v: float | None
    current_a: float | None
    energy_total_wh: float | None
    energy_last_min_wh: float | None
    energy_ts: int | None
    device_temp_c: float | None
    device_temp_f: float | None
    ambient_temp: float | None
    source: str | None


@dataclass
class CarHeaterSensorFrame:
    id: int | None
    received_at: str
    frame_timestamp: str | None = None
    source: str | None = None
    firmware_version: str | None = None
    glass_reference_state: str | None = None
    glass_reference_temp_c: float | None = None
    glass_reference_sampled_at: str | None = None
    glass_comparison_state: str | None = None
    glass_comparison_temp_c: float | None = None
    glass_comparison_sampled_at: str | None = None
    cabin_state: str | None = None
    cabin_temp_c: float | None = None
    cabin_humidity_pct: float | None = None
    cabin_sampled_at: str | None = None


@dataclass
class CarHeaterKFactorSession:
    id: int | None
    start_ts: str
    end_ts: str
    auto_window_date: str | None = None
    auto_window_start: str | None = None
    auto_window_stop: str | None = None
    heater_mode: str | None = None
    outside_t_mean: float | None = None
    outside_t_min: float | None = None
    outside_t_max: float | None = None
    wind_mean: float | None = None
    cabin_t_start: float | None = None
    cabin_t_end: float | None = None
    cabin_t_max: float | None = None
    duration_s: int | None = None
    sample_count: int | None = None
    flags_json: str | None = None
    quality_score: float | None = None
    accepted: bool = False


@dataclass
class CarHeaterKFactorResult:
    id: int | None
    session_id: int
    model_version: str
    k_loss_W_per_K: float
    eta: float
    rmse_C: float | None = None
    r2: float | None = None
    confidence: float | None = None
    promoted: bool = False
    created_ts: str | None = None


@dataclass
class CarHeaterKFactorPredictionOutcome:
    id: int | None = None
    predicted_minutes: float | None = None
    actual_minutes: float | None = None
    error_minutes: float | None = None
    cabin_start_c: float | None = None
    cabin_end_c: float | None = None
    target_c: float | None = None
    outside_c: float | None = None
    created_ts: str | None = None


@dataclass
class CarHeaterKFactorActiveParams:
    id: int = 1
    k_loss_W_per_K: float | None = None
    eta: float | None = None
    updated_ts: str | None = None
    source: str | None = None


@dataclass
class CarHeaterKFactorBucketParams:
    id: int | None = None
    t_bucket: int | None = None
    wind_bucket: int | None = None
    k_loss_W_per_K: float | None = None
    eta: float | None = None
    updated_ts: str | None = None
    source: str | None = None


@dataclass
class CarHeaterKFactorConfig:
    id: int = 1
    config_json: str | None = None
    updated_ts: str | None = None


@dataclass
class CarHeaterKFactorCooldown:
    id: int = 1
    cooldown_until: str | None = None
    updated_ts: str | None = None


@dataclass
class CarHeaterReadyByState:
    id: int = 1
    state_json: str | None = None
    updated_ts: str | None = None


@dataclass
class CarHeaterReadyByConfig:
    id: int = 1
    config_json: str | None = None
    updated_ts: str | None = None


@dataclass
class YnabPayeeCategoryStat:
    id: int | None = None
    budget_id: str = ""
    payee_normalized: str = ""
    category_id: str = ""
    count: int = 0
    last_used_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class YnabApplyEvent:
    id: int | None = None
    budget_id: str = ""
    transaction_id: str = ""
    payee_normalized: str = ""
    category_id: str = ""
    applied_by_username: str | None = None
    applied_at: str = ""


@dataclass
class YnabBootstrapState:
    budget_id: str = ""
    bootstrapped_at: str = ""
    history_start_date: str = ""
    history_end_date: str = ""


@dataclass
class YnabCategorizerConfig:
    id: int = 1
    budget_id: str = ""
    queue_filter_mode: str = "strict"
    test_mode_enabled: bool = False
    show_reconciled_transactions: bool = False
    queue_limit_enabled: bool = False
    queue_limit_value: int = 30
    queue_limit_unit: str = "days"
    quick_apply_include_medium: bool = False
    default_category_id: str | None = None
    custom_rules_json: str | None = None
    updated_ts: str = ""


@dataclass
class MedicinePurchase:
    id: int | None = None
    medicine_name: str = ""
    medicine_key: str = ""
    purchase_date: str = ""
    pieces_bought: int = 0
    dose_per_dosing_day: int = 1
    dosing_weekdays_json: str = "[]"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class MedicineRefillCalculation:
    purchase_date: str
    run_out_date: str
    next_purchase_date: str
    flex_days: int
    treatment_days: float
    dosing_days_covered: int
    dosing_weekdays: list[int]


@dataclass
class CarHeaterSession:
    """A durable attempt, distinct from telemetry or an actuator command."""

    id: str
    trigger: str
    created_at: str
    observed_at: str
    requested_departure_at: str | None
    heating_duration_minutes: float | None
    snapshot: dict
    outcome: dict | None


@dataclass
class CarHeaterPhoto:
    """Saved observation photo metadata; content is served separately."""

    id: str
    session_id: str
    captured_at: str
    due_at: str | None
    capture_kind: str
    sequence: int | None
    observation_started_at: str | None
    camera_label: str | None
    received_at: str
    content_type: str
    byte_size: int
    sha256: str


@dataclass
class AnnouncementSpeakerTest:
    """An explicit speaker test request; never a reminder occurrence."""

    id: str
    request_id: str
    requested_at: str
    requested_by: str | None
    text: str
    language: str
    target_key: str
    volume_percent: int
    chime_percent: int


@dataclass
class AnnouncementAttempt:
    """Speaker delivery evidence for one dispatch; not proof of audibility."""

    id: str
    speaker_test_id: str | None
    occurrence_id: str | None
    target_key: str
    target_name: str
    target_host: str | None
    target_device_id: str | None
    speaker_set: list[str]
    evidence_scope: str
    priority: int
    text: str
    language: str
    volume_percent: int
    chime_percent: int
    status: str
    reason: str | None
    requested_at: str
    dispatch_deadline: str
    voice: str | None
    media_key: str | None
    media_duration_s: float | None
    intent_at: str | None
    dispatched_at: str | None
    playback_observed_at: str | None
    finished_at: str | None
    stop_requested_at: str | None
    stop_outcome: str | None
    stop_observed_at: str | None
    volume_before: float | None
    volume_applied: float | None
    volume_restore: str | None
    volume_restored_at: str | None
    updated_at: str
    interrupted_media: str | None = None
    media_restore: str | None = None
    purpose: str | None = None
    certain_not_played: bool | None = None
    # Group targets: [{key, name, availability, in_group,
    # left_group_during_playback}]; None for single speakers.
    member_evidence: list[dict] | None = None


@dataclass
class AnnouncementSchedule:
    """When a reminder is due; Helsinki wall time is authoritative for one-time."""

    id: str
    request_id: str | None
    kind: str
    status: str
    revision: int
    text: str
    language: str
    target_key: str
    volume_percent: int
    chime_percent: int
    urgent: bool
    quiet_hours_override: bool
    expiry_minutes: int
    max_announcements: int
    repeat_interval_minutes: int | None
    due_local: str | None
    due_fold: int | None
    due_at: str | None
    recurrence: dict | None
    next_due_at: str | None
    created_at: str
    created_by: str | None
    updated_at: str
    paused_at: str | None
    deleted_at: str | None


@dataclass
class AnnouncementOccurrence:
    """One schedule instance; disposition and delivery evidence stay separate."""

    id: str
    schedule_id: str
    instance_due_at: str
    schedule_revision: int
    created_at: str
    text: str
    language: str
    target_key: str
    target_name: str
    speaker_set: list[str]
    volume_percent: int
    chime_percent: int
    urgent: bool
    quiet_hours: str | None
    max_announcements: int
    repeat_interval_minutes: int | None
    due_at: str
    expires_at: str
    next_attempt_at: str | None
    status: str
    waiting_reason: str | None
    waiting_since: str | None
    waiting_history: list[dict]
    announcements_started: int
    current_attempt_id: str | None
    delivery: str
    delivery_reason: str | None
    disposition: str | None
    disposition_reason: str | None
    closed_at: str | None
    fallback_reason: str | None
    fallback_eligible_at: str | None
    updated_at: str
    retries_started: int = 0
    next_attempt_purpose: str | None = None


@dataclass
class AnnouncementFallbackAttempt:
    """One webhook request; 'accepted' is Discord's answer, not readership."""

    id: str
    fallback_id: str
    number: int
    status: str
    http_status: int | None
    retry_after_s: float | None
    detail: str | None
    intent_at: str
    finished_at: str | None


@dataclass
class AnnouncementFallback:
    """The one logical fallback notification of a reminder occurrence."""

    id: str
    occurrence_id: str
    channel: str
    reason: str
    message: str
    link: str | None
    status: str
    status_reason: str | None
    attempts_made: int
    max_attempts: int
    next_attempt_at: str | None
    created_at: str
    updated_at: str
    accepted_at: str | None
    completed_at: str | None


@dataclass
class AnnouncementAcknowledgement:
    """The owner's Done or Skip for one occurrence; never a health record."""

    id: str
    occurrence_id: str
    action: str
    acknowledged_at: str
    acknowledged_by: str | None
    prior_status: str
    prior_delivery: str
    prior_disposition: str | None
    prior_disposition_reason: str | None
    announcements_started: int
