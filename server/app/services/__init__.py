import logging
import os
import threading
from typing import Any

from ..extensions import socketio

logger = logging.getLogger(__name__)

"""Service layer for external integrations (AC, Hue, etc.)."""

"""Bootstrap initialization for long-lived services (AC, Hue, Socket events).

Moves heavy wiring out of the app factory to keep it lean and testable.
"""


def _env_float(name: str, default: float) -> float:
    """Parse a float environment variable with a safe default."""
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using default=%s", name, raw, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a bool environment variable with a safe default."""
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _build_tuya_ac_device(device_id: str, ip: str, local_key: str):
    """Construct and configure the TinyTuya AC device from environment settings."""
    from tinytuya import Device

    tuya_version = _env_float("AC_TUYA_VERSION", 3.1)
    timeout_s = _env_float("AC_TUYA_TIMEOUT_S", 5.0)
    persist = _env_bool("AC_TUYA_PERSIST", False)
    logger.debug(
        "AC init TinyTuya config ip=%s version=%s timeout_s=%s persist=%s",
        ip,
        tuya_version,
        timeout_s,
        persist,
    )
    device = Device(
        device_id,
        ip,
        local_key,
        version=tuya_version,
        connection_timeout=timeout_s,
        persist=persist,
    )
    try:
        device.set_version(tuya_version)
        device.set_socketTimeout(timeout_s)
        device.set_socketPersistent(persist)
    except Exception:
        logger.debug("AC init TinyTuya post-init socket tuning skipped", exc_info=True)
    return device


def _make_notify(handler) -> Any:
    """Create a notifier that emits events to browser views via SocketEventHandler."""

    def _notify(event: str, payload: dict[str, Any]):
        try:
            handler.emit_to_views(event, payload)
        except Exception:
            import contextlib

            with contextlib.suppress(Exception):
                socketio.emit(event, payload)

    return _notify


def init_services(app) -> dict[str, Any]:
    """Initialize AC thermostat, Hue controller, car heater, and Socket events.

    Returns a dictionary of created service instances.
    """
    services: dict[str, Any] = {}
    app.ynab_categorizer_service = None  # type: ignore[attr-defined]
    app.hue_ctrl = None  # type: ignore[attr-defined]
    keep_at_temp_service = None
    ready_by_service = None

    # Ensure Socket event handlers are registered (singleton takes care of idempotency)
    from ..sockets import SocketEventHandler

    logger.debug("Initializing socket event handler")
    app.sio_handler = SocketEventHandler(socketio, app.ctrl)  # type: ignore[attr-defined]

    # --- AC / Thermostat ---
    AC_DEVICE_ID = os.getenv("AC_DEV_ID")
    AC_IP = os.getenv("AC_IP")
    AC_LOCAL_KEY = os.getenv("AC_LOCAL_KEY")
    WINTER_MODE = os.getenv("WINTER_MODE", "False").lower() in ("true", "1", "yes")
    logger.debug(
        "AC init env device_id_set=%s ip=%s local_key_set=%s winter_mode=%s",
        bool(AC_DEVICE_ID),
        AC_IP,
        bool(AC_LOCAL_KEY),
        WINTER_MODE,
    )

    try:
        if not all([AC_DEVICE_ID, AC_IP, AC_LOCAL_KEY]):
            raise ValueError(
                "Missing one of AC_DEV_ID, AC_IP, or AC_LOCAL_KEY environment variables"
            )

        from .ac import ACController

        tinytuya_device = (
            _build_tuya_ac_device(AC_DEVICE_ID, AC_IP, AC_LOCAL_KEY) if not WINTER_MODE else None
        )
        ac_controller = ACController(tinytuya_device=tinytuya_device, winter=WINTER_MODE)
        thermostat_location = app.config.get("THERMOSTAT_LOCATION", "Tietokonepöytä")
        # Load thermostat configuration from DB (seed default if missing)
        try:
            cfg = app.ctrl.get_thermostat_conf()  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("thermo: failed to load config from DB: %s", e)
            cfg = None
        if cfg is None:
            try:
                logger.warning("thermo: seeding default thermostat config in DB")
                cfg = app.ctrl.ensure_thermostat_conf_seeded_from(None)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("thermo: failed to seed default thermostat config")
                raise
        from .ac import ACThermostat

        ac_thermostat = ACThermostat(
            ac=ac_controller,
            cfg=cfg,  # type: ignore[arg-type]
            ctrl=app.ctrl,  # type: ignore[attr-defined]
            location=thermostat_location,
            notify=_make_notify(app.sio_handler),  # type: ignore[attr-defined]
            winter=WINTER_MODE,
        )
        app.ac_thermostat = ac_thermostat  # type: ignore[attr-defined]
        if not WINTER_MODE:
            t = threading.Thread(target=ac_thermostat.run_forever, daemon=True)
            t.start()
            services["ac_thermostat"] = ac_thermostat
            logger.info("Tuya AC controller started for device %s", AC_DEVICE_ID)
    except Exception as e:
        logger.exception("Failed to initialize AC thermostat: %s", e)

    # --- Hue time-based routine ---
    try:
        from .hue import HueController

        hue_bridge_ip = os.getenv("HUE_BRIDGE_IP")
        hue_username = os.getenv("HUE_USERNAME")
        logger.debug(
            "Hue init config bridge_set=%s username_set=%s",
            bool(hue_bridge_ip),
            bool(hue_username),
        )
        if not hue_bridge_ip or not hue_username:
            logger.info("Hue routine disabled: missing HUE_BRIDGE_IP or HUE_USERNAME")
        else:
            hue_timeout_s = float(app.config.get("HUE_HTTP_TIMEOUT_S", 5.0))
            logger.debug("Starting Hue controller timeout=%s", hue_timeout_s)
            hue_ctrl = HueController(
                hue_bridge_ip,
                hue_username,
                request_timeout_s=hue_timeout_s,
            )
            app.hue_ctrl = hue_ctrl  # type: ignore[attr-defined]
            hue_ctrl.start_time_based_routine(apply_immediately=False)
            services["hue_controller"] = hue_ctrl
            logger.info("Hue time-based routine started.")
    except Exception as e:
        logger.exception("Failed to initialize Hue routine: %s", e)

    # --- Car heater service (command queue for ESP) ---
    car_heater_service = None
    try:
        from .car_heater import CarHeaterService

        car_heater_service = CarHeaterService(app.ctrl)
        # Expose via both app attributes and config for compatibility
        app.car_heater_service = car_heater_service
        services["car_heater_service"] = car_heater_service
        logger.info("Car heater service initialized and stored in app.config")
    except Exception as e:
        logger.exception("Failed to initialize car heater service: %s", e)

    try:
        from .car_heater import KeepAtTempService

        keep_at_temp_service = KeepAtTempService(
            car_heater_service=car_heater_service,
            ctrl=app.ctrl,  # type: ignore[attr-defined]
        )
        app.keep_at_temp_service = keep_at_temp_service
        services["keep_at_temp_service"] = keep_at_temp_service
        logger.info("Keep-at-temperature service initialized")
    except Exception as e:
        logger.exception("Failed to initialize keep-at-temperature service: %s", e)

    # --- Sodexo scheduler ---
    try:
        from .sodexo import start_sodexo_webhook_thread

        webhook_url = os.getenv("SODEXO_WEBHOOK_URL")
        hour_raw = os.getenv("SODEXO_POST_HOUR")
        minute_raw = os.getenv("SODEXO_POST_MINUTE")
        if not all([webhook_url, hour_raw, minute_raw]):
            raise ValueError(
                "Set SODEXO_WEBHOOK_URL, SODEXO_POST_HOUR, and SODEXO_POST_MINUTE env vars."
            )
        hour = int(hour_raw)
        minute = int(minute_raw)
        skip_weekends = True
        _ = start_sodexo_webhook_thread(
            webhook_url,
            restaurant_name="Sodexo Frami",
            hour=hour,
            minute=minute,
            tz_name="Europe/Helsinki",
            skip_weekends=skip_weekends,
        )
        s = "weekdays" if skip_weekends else "everyday"
        logger.info(f"Sodexo-Scheduler started ({s} {hour}:{minute} Europe/Helsinki).")
    except Exception as e:
        logger.exception("Failed to initialize Sodexo scheduler: %s", e)

    # --- Alert webhook batcher ---
    try:
        from .alert_webhook import start_alert_batcher

        start_alert_batcher()
        logger.info("Alert webhook batcher started.")
    except Exception as e:
        logger.exception("Failed to start alert webhook batcher: %s", e)

    # --- Weather service ---
    weather_service = None
    try:
        from .weather import WeatherService

        weather_service = WeatherService()
        app.weather_service = weather_service
        services["weather_service"] = weather_service
        logger.info("Weather service initialized")
    except Exception as e:
        logger.exception("Failed to initialize weather service: %s", e)

    # --- KFactor calibration (Ready-by model) ---
    kfactor_calibrator = None
    try:
        from .car_heater import KFactorCalibrator

        kfactor_calibrator = KFactorCalibrator(
            ctrl=getattr(app, "ctrl", None),
        )
        app.kfactor_calibrator = kfactor_calibrator
        services["kfactor_calibrator"] = kfactor_calibrator
        logger.info("KFactor calibrator initialized")
    except Exception as e:
        logger.exception("Failed to initialize KFactor calibrator: %s", e)

    # --- Ready-by scheduling service ---
    try:
        from .car_heater import ReadyByService

        if car_heater_service is None or kfactor_calibrator is None:
            raise RuntimeError("Missing car_heater_service or kfactor_calibrator")
        ready_by_service = ReadyByService(
            car_heater_service=car_heater_service,
            kfactor_calibrator=kfactor_calibrator,
            keep_at_temp_service=keep_at_temp_service,
            ctrl=app.ctrl,  # type: ignore[attr-defined]
            weather_service=weather_service,
        )
        app.ready_by_service = ready_by_service
        services["ready_by_service"] = ready_by_service
        logger.info("ReadyBy service initialized")
    except Exception as e:
        logger.exception("Failed to initialize ReadyBy service: %s", e)

    # --- KFactor calibrator dependencies wiring ---
    if kfactor_calibrator is not None:
        kfactor_calibrator.set_car_heater_service(car_heater_service)
        kfactor_calibrator.set_ready_by_service(ready_by_service)
        kfactor_calibrator.set_keep_at_temp_service(keep_at_temp_service)
        kfactor_calibrator.set_weather_service(weather_service)

    # --- ESP32 Redis bridge (for WebSocket service communication) ---
    try:
        from .esp32_redis_bridge import init_esp32_redis_bridge

        esp32_bridge = init_esp32_redis_bridge(app)
        if esp32_bridge is not None:
            app.esp32_redis_bridge = esp32_bridge
            services["esp32_redis_bridge"] = esp32_bridge
            logger.info("ESP32 Redis bridge initialized")
    except Exception as e:
        logger.exception("Failed to initialize ESP32 Redis bridge: %s", e)

    # --- YNAB categorizer ---
    try:
        ynab_api_key = app.config.get("YNAB_API_KEY")
        ynab_budget_id = app.config.get("YNAB_BUDGET_ID")
        ynab_timeout_s = int(app.config.get("YNAB_HTTP_TIMEOUT_S", 15))
        ynab_retries = int(app.config.get("YNAB_HTTP_RETRIES", 2))
        logger.debug(
            "YNAB init config budget_set=%s timeout=%s retries=%s",
            bool(ynab_budget_id),
            ynab_timeout_s,
            ynab_retries,
        )

        if not ynab_api_key or not ynab_budget_id:
            logger.info(
                "YNAB categorizer disabled: missing YNAB_API_KEY or YNAB_BUDGET_ID environment variables"
            )
        else:
            from .ynab import YnabCategorizerService, YNABClient

            ynab_client = YNABClient(
                api_key=ynab_api_key,
                budget_id=ynab_budget_id,
                timeout_s=ynab_timeout_s,
                retries=ynab_retries,
            )
            ynab_service = YnabCategorizerService(app.ctrl, ynab_client, ynab_budget_id)  # type: ignore[attr-defined]
            app.ynab_categorizer_service = ynab_service  # type: ignore[attr-defined]
            services["ynab_categorizer_service"] = ynab_service
            logger.info("YNAB categorizer service initialized for budget=%s", ynab_budget_id)
    except Exception as e:
        logger.exception("Failed to initialize YNAB categorizer service: %s", e)

    return services
