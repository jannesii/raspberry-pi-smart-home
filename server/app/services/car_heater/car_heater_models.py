from dataclasses import dataclass

@dataclass
class CommandStatus:
    turn_on: str | None = None
    turn_off: str | None = None
    get_logs: str | None = None
    esp_restart: str | None = None
    shelly_restart: str | None = None


@dataclass
class ChargeModeState:
    enabled: bool = False
    threshold_w: float = 20.0
    power_cut: bool = False
    power_cut_at: str | None = None
    last_instant_power_w: float | None = None
    seen_above_threshold: bool = False
    
@dataclass
class KeepAtTempSettings:
    target_temperature_c: float | None = None   
    hysteresis_c: float | None = None   
    enabled: bool | None = None 