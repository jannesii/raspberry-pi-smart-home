import os
import threading
import logging
from typing import Any, Dict

from ..extensions import socketio

logger = logging.getLogger(__name__)

"""Service layer for external integrations (AC, Hue, etc.)."""

"""Bootstrap initialization for long-lived services (AC, Hue, Socket events).

Moves heavy wiring out of the app factory to keep it lean and testable.
"""


def _make_notify(handler) -> Any:
    """Create a notifier that emits events to browser views via SocketEventHandler."""

    def _notify(event: str, payload: Dict[str, Any]):
        try:
            handler.emit_to_views(event, payload)
        except Exception:
            try:
                socketio.emit(event, payload)
            except Exception:
                pass

    return _notify


def init_services(app) -> Dict[str, Any]:
    """Initialize AC thermostat, Hue controller, car heater, and Socket events.

    Returns a dictionary of created service instances.
    """
    services: Dict[str, Any] = {}

    # Ensure Socket event handlers are registered (singleton takes care of idempotency)
    from ..sockets import SocketEventHandler

    app.sio_handler = SocketEventHandler(
        socketio, app.ctrl)  # type: ignore[attr-defined]

    # --- AC / Thermostat ---
    AC_DEVICE_ID = os.getenv("AC_DEV_ID")
    AC_IP = os.getenv("AC_IP")
    AC_LOCAL_KEY = os.getenv("AC_LOCAL_KEY")

    try:
        if not all([AC_DEVICE_ID, AC_IP, AC_LOCAL_KEY]):
            raise ValueError(
                "Missing one of AC_DEV_ID, AC_IP, or AC_LOCAL_KEY environment variables"
            )

        from .ac import ACController
        from tinytuya import Device

        winter = True
        tinytuya_device = Device(
            AC_DEVICE_ID, AC_IP, AC_LOCAL_KEY) if not winter else None
        ac_controller = ACController(
            tinytuya_device=tinytuya_device, winter=winter)
        THERMOSTAT_LOCATION = os.getenv(
            "THERMOSTAT_LOCATION", "Tietokonepöytä")
        # Load thermostat configuration from DB (seed default if missing)
        try:
            cfg = app.ctrl.get_thermostat_conf()  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("thermo: failed to load config from DB: %s", e)
            cfg = None
        if cfg is None:
            try:
                logger.warning(
                    "thermo: seeding default thermostat config in DB")
                cfg = app.ctrl.ensure_thermostat_conf_seeded_from(
                    None)  # type: ignore[attr-defined]
            except Exception:
                pass
        from .ac import ACThermostat

        ac_thermostat = ACThermostat(
            ac=ac_controller,
            cfg=cfg,  # type: ignore[arg-type]
            ctrl=app.ctrl,  # type: ignore[attr-defined]
            location=THERMOSTAT_LOCATION,
            notify=_make_notify(app.sio_handler),  # type: ignore[attr-defined]
            winter=winter,
        )
        app.ac_thermostat = ac_thermostat  # type: ignore[attr-defined]
        if not winter:
            t = threading.Thread(target=ac_thermostat.run_forever, daemon=True)
            t.start()
            services["ac_thermostat"] = ac_thermostat
            logger.info(
                "Tuya AC controller started for device %s", AC_DEVICE_ID)
    except Exception as e:
        logger.exception("Failed to initialize AC thermostat: %s", e)

    # --- Hue time-based routine ---
    try:
        from .hue import HueController

        hue_bridge_ip = os.getenv("HUE_BRIDGE_IP")
        hue_username = os.getenv("HUE_USERNAME")
        hue = HueController(hue_bridge_ip, hue_username)
        hue.start_time_based_routine(apply_immediately=False)
        services["hue_controller"] = hue
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
        logger.exception(
            "Failed to initialize keep-at-temperature service: %s", e)

    # --- Sodexo scheduler ---
    try:
        from .sodexo import start_sodexo_webhook_thread

        webhook_url = os.getenv("SODEXO_WEBHOOK_URL")
        hour = int(os.getenv("SODEXO_POST_HOUR"))
        minute = int(os.getenv("SODEXO_POST_MINUTE"))
        if not all([webhook_url, hour, minute]):
            raise ValueError(
                "Set SODEXO_WEBHOOK_URL, SODEXO_POST_HOUR, and SODEXO_POST_MINUTE env vars."
            )
        skip_weekends = True
        stop_event, th = start_sodexo_webhook_thread(
            webhook_url,
            restaurant_name="Sodexo Frami",
            hour=hour,
            minute=minute,
            tz_name="Europe/Helsinki",
            skip_weekends=skip_weekends,
        )
        s = "weekdays" if skip_weekends else "everyday"
        logger.info(
            f"Sodexo-Scheduler started ({s} {hour}:{minute} Europe/Helsinki)."
        )
    except Exception as e:
        logger.exception("Failed to initialize Sodexo scheduler: %s", e)
        
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
            weather_service=weather_service,
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

    return services
