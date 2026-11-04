"""The completed car-heater redesign at the authenticated public seams."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import test_car_heater_calibration as calibration
import test_car_heater_sessions as evidence

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers
services = calibration.services
install = calibration.install


def saved_ready_by_schedule(app):
    """A saved Ready-by (cabin target) schedule, reloaded as after a restart."""
    ready_by = datetime.now(ZoneInfo("Europe/Helsinki")) + timedelta(hours=10)
    app.ready_by_service.schedule(ready_by_ts=ready_by, target_temp_c=-5)
    install(app)  # Only the persisted schedule remains.
    return app.ready_by_service.get_schedule()["schedule"]


def test_ready_by_changes_need_csrf_and_cancelling_never_actuates(services, session_app, client):
    heater = services
    broadcasts = []
    session_app.sio_handler = type(
        "Views", (), {"emit_ready_by_status_to_views": lambda self: broadcasts.append(1)}
    )()
    schedule = saved_ready_by_schedule(session_app)
    assert schedule["status"] == "scheduled"

    forged = client.delete("/api/car_heater/ready_by/schedule", json={"turn_off": False})
    assert forged.status_code == 400
    assert client.get("/api/car_heater/ready_by/schedule").get_json()["schedule"]["status"] == (
        "scheduled"
    )
    created = client.post(
        "/api/car_heater/ready_by/schedule",
        json={"ready_by_ts": schedule["ready_by_ts"], "target_temp_c": -5},
    )
    assert created.status_code == 400

    cancelled = client.delete(
        "/api/car_heater/ready_by/schedule",
        json={"reason": "user", "turn_off": False},
        headers=headers(session_app, client),
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    assert cancelled.get_json()["schedule"]["status"] == "canceled"
    assert heater.peek_queued_commands() == []
    # Other open views learn of the HTTP change, as with the Socket.IO event;
    # refused requests changed nothing and announced nothing.
    assert broadcasts == [1]
    install(session_app)
    assert session_app.ready_by_service.get_schedule()["schedule"]["status"] == "canceled"


def test_socket_ready_by_changes_need_the_same_csrf_as_http(services, session_app):
    from app.sockets.ready_by_handlers import handle_ready_by_schedule

    heater = services
    schedule = saved_ready_by_schedule(session_app)
    for forged in (
        {"action": "cancel", "csrf_token": "forged"},
        {
            "action": "schedule",
            "ready_by_ts": schedule["ready_by_ts"],
            "target_temp_c": 0,
            "csrf_token": "forged",
        },
    ):
        refused = calibration.socket_event(session_app, handle_ready_by_schedule, forged)
        assert refused.errors and refused.views == []
    assert session_app.ready_by_service.get_schedule()["schedule"]["target_temp_c"] == -5

    cancelled = calibration.socket_event(
        session_app, handle_ready_by_schedule, {"action": "cancel"}
    )
    assert cancelled.errors == []
    assert cancelled.views[-1][1]["schedule"]["status"] == "canceled"
    assert heater.peek_queued_commands() == []


# Retained functions from milestone 1 and the advanced slices (#14-#17, #27),
# by the view that now owns each one.
RETAINED_FUNCTIONS = {
    "/car_heater": {
        "departureForm": "Departure plan: date/time, Tomorrow, grace, clock changes",
        "departurePreviewButton": "Preview before save",
        "departureSave": "Explicit save",
        "departureCancel": "Cancel upcoming departure",
        "heaterState": "Confirmed heater state",
        "deviceFreshness": "Device report freshness",
        "btnTurnOff": "Immediate Stop / request OFF again",
        "observeLink": "Observe windshield for the active session",
        "manualDuration": "Manual duration",
        "btnTurnOn": "Start timed heating",
        "boundedSessionLink": "Review the heating session",
        "comfortSection": "Comfort heating and target",
        "heroOutsideTemp": "FMI outside observation",
        "sensorGlassReference": "Glass reference",
        "sensorGlassComparison": "Glass comparison",
        "sensorCabinTemp": "Cabin temperature",
        "sensorCabinHumidity": "Cabin humidity",
        "conditionPower": "Heater power",
        "readyBySection": "Ready by (cabin target): status and model estimate",
        "readyByForm": "Ready-by date, time and cabin target",
        "readyBySchedule": "Save or replace the Ready-by schedule",
        "readyByCancel": "Cancel the Ready-by schedule",
    },
    "/car_heater/settings": {
        "preferencesForm": "Comfort hysteresis",
        "installationForm": "Installation record",
        "installationHistory": "Earlier installation records",
        "deviceFacts": "Firmware and relay capability",
        "calibrationForm": "Calibration window and model tuning",
        "calReadyByEnabled": "Allow Ready-by schedules",
        "calReachTolerance": "Ready-by reach tolerance",
        "calCommandCooldown": "Ready-by command cooldown",
        "calAutonomousToggle": "Autonomous calibration",
        "calResetCooldown": "Reset cooldown",
        "calResetDefaults": "Reset model settings",
        "calData": "Recorded calibration data",
        "diagnosticsConnection": "Browser connection",
        "diagnosticsDevice": "Device health",
        "diagnosticsRelay": "Relay connection",
        "deviceTelemetry": "Latest device telemetry",
        "btnOpenCarHeaterCharts": "Detailed charts",
        "commandStages": "Command stages",
        "btnGetLogs": "Logs",
        "btnEspRestart": "Confirmed ESP restart",
        "btnShellyRestart": "Confirmed Shelly restart",
        "heaterRestartDialog": "Restart confirmation",
        "weatherHealthList": "Weather collection health",
        "weatherExport": "Weather exports",
    },
    "/car_heater/sessions": {
        "sessionList": "Newest-first sessions",
        "sessionFilter": "Date filter",
        "sessionCreate": "Record an existing attempt",
    },
}
RETIRED_FROM_OPERATING_PAGES = [
    "car_heater_ui_prototype",  # throwaway prototype host, switcher, synthetic states
    # Unstyled Ready-by scheduler scripts and markup, replaced by the restyled one.
    "car_heater_settings_schedule.js",
    "car_heater_settings_base.js",
    "readyByTimeInput",
    "btnReadyBySchedule",
    "chargeMode",  # battery-charge controls
    "CAR_HEATER_CHARGE_STATE",
    "queueBanner",
    "ios-card",
]


def test_operating_pages_keep_retained_functions_and_drop_transitional_views(
    services, session_app, client
):
    attempt = client.post(
        "/api/car_heater/sessions",
        json={"trigger": "manual", "observed_at": "2026-11-02T05:00:00+02:00"},
        headers=headers(session_app, client),
    ).get_json()
    pages = {
        **RETAINED_FUNCTIONS,
        f"/car_heater/sessions/{attempt['id']}": {
            "sessionDetail": "Session review",
            "sessionWeather": "Session weather coverage and original replies (#27)",
        },
        f"/car_heater/sessions/{attempt['id']}/capture": {"capturePreview": "Capture view"},
    }
    for path, functions in pages.items():
        response = client.get(path)
        assert response.status_code == 200, path
        page = response.get_data(as_text=True)
        for element, function in functions.items():
            assert f'id="{element}"' in page, f"{path}: {function}"
        for retired in RETIRED_FROM_OPERATING_PAGES:
            assert retired not in page, f"{path} still has {retired}"
    for asset in ("js/car_heater_ui_prototype.js", "css/car_heater_ui_prototype.css"):
        assert client.get(f"/static/{asset}").status_code == 404


def test_ready_by_cannot_be_scheduled_while_a_timed_session_owns_the_relay(
    services, session_app, client
):
    heater = services
    calibration.ready(heater)
    heater.start_bounded_session(60)
    ready_by = datetime.now(ZoneInfo("Europe/Helsinki")) + timedelta(hours=10)
    refused = client.post(
        "/api/car_heater/ready_by/schedule",
        json={"ready_by_ts": ready_by.strftime("%Y-%m-%dT%H:%M"), "target_temp_c": -5},
        headers=headers(session_app, client),
    )
    assert refused.status_code == 409
    assert "confirmed stopped" in refused.get_json()["error"]
    assert client.get("/api/car_heater/ready_by/schedule").get_json()["schedule"] is None
