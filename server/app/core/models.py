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
        expiry timestamp is in the past (UTC‑aware).
        """
        finland_tz = pytz.timezone('Europe/Helsinki')
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
class CarHeaterKFactorActiveParams:
    id: int = 1
    k_loss_W_per_K: float | None = None
    eta: float | None = None
    updated_ts: str | None = None
    source: str | None = None
