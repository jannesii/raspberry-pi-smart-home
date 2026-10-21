"""Calibration settings at the authenticated public seams (HTTP and Socket.IO)."""

from datetime import UTC, datetime

import pytest
import test_car_heater_sessions as evidence

from app.services.car_heater import KeepAtTempService, KFactorCalibrator, ReadyByService
from app.services.car_heater.car_heater_service import CarHeaterService

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers


@pytest.fixture
def services(session_app, monkeypatch):
    monkeypatch.setattr(
        "redis.from_url", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
    )
    heater = CarHeaterService(session_app.ctrl, clock=lambda: 1800000000.0)
    session_app.car_heater_service = heater
    install(session_app)

    def suspend_heater_modes():
        # As app.services wires it for bounded starts.
        session_app.kfactor_calibrator.enabled = False

    heater.bind_mode_suspension(suspend_heater_modes)
    yield heater
    heater.stop()


def install(app):
    """(Re)start the app-owned services; a restart reads only what was persisted."""
    heater = app.car_heater_service
    comfort = KeepAtTempService(heater, app.ctrl)
    kfactor = KFactorCalibrator(app.ctrl)
    kfactor.set_car_heater_service(heater)
    kfactor.set_keep_at_temp_service(comfort)
    ready_by = ReadyByService(
        car_heater_service=heater,
        kfactor_calibrator=kfactor,
        keep_at_temp_service=comfort,
        ctrl=app.ctrl,
    )
    kfactor.set_ready_by_service(ready_by)
    app.keep_at_temp_service = comfort
    app.kfactor_calibrator = kfactor
    app.ready_by_service = ready_by


def ready(heater, **extra):
    heater.observe_bounded_status(
        {
            "firmware_version": "3.1.1",
            "bounded": {
                "protocol": 1,
                "ready": True,
                "relay_on": False,
                "boot_id": "relay-one",
                "device_boot_id": "esp-one",
                **extra,
            },
        }
    )


def test_settings_show_saved_values_and_experimental_status(services, client):
    data = client.get("/api/car_heater/calibration").get_json()
    assert data["experimental"] is True
    assert data["state"] == "IDLE"
    assert data["autonomous_enabled"] is False
    assert data["autonomous_start_available"] is True
    assert data["unavailable_reason"] is None
    assert data["model_version"] == "v1"
    # Nothing was ever saved by the owner; defaults are in use.
    assert data["config_saved_at"] is None
    assert data["config"]["grace_samples"] == 3
    assert data["config"]["auto_calib_start_hhmm"] == "00:00"
    assert "test_output_path" not in data["config"]
    assert data["ready_by"] == {
        "enabled": True,
        "reach_tolerance_minutes": 2.0,
        "command_cooldown_s": 30,
    }
    assert data["active_params"]["k_loss_W_per_K"] is not None
    assert data["last_session"] is None


def test_partial_saves_keep_absent_values_and_survive_restart(services, session_app, client):
    auth = headers(session_app, client)
    saved = client.patch(
        "/api/car_heater/calibration",
        json={"config": {"grace_samples": 5, "auto_calib_start_hhmm": "22:30"}},
        headers=auth,
    )
    assert saved.status_code == 200
    body = saved.get_json()
    assert body["config"]["grace_samples"] == 5
    assert body["config"]["auto_calib_start_hhmm"] == "22:30"
    assert body["config_saved_at"] is not None

    saved = client.patch(
        "/api/car_heater/calibration",
        json={"config": {"quality_threshold": 0.75}, "ready_by": {"command_cooldown_s": 60}},
        headers=auth,
    )
    assert saved.status_code == 200
    # Absent fields keep their saved values on both settings records.
    assert saved.get_json()["config"]["grace_samples"] == 5
    assert saved.get_json()["ready_by"] == {
        "enabled": True,
        "reach_tolerance_minutes": 2.0,
        "command_cooldown_s": 60,
    }

    install(session_app)
    reloaded = client.get("/api/car_heater/calibration").get_json()
    assert reloaded["config"]["grace_samples"] == 5
    assert reloaded["config"]["auto_calib_start_hhmm"] == "22:30"
    assert reloaded["config"]["quality_threshold"] == 0.75
    assert reloaded["config"]["min_session_minutes"] == 15
    assert reloaded["ready_by"]["command_cooldown_s"] == 60
    assert reloaded["ready_by"]["reach_tolerance_minutes"] == 2.0
    assert reloaded["autonomous_enabled"] is False


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"config": {"grace_samples": 11}}, "between 1 and 10"),
        ({"config": {"grace_samples": 2.5}}, "whole number"),
        ({"config": {"quality_threshold": True}}, "must be a number"),
        ({"config": {"auto_calib_stop_hhmm": "24:00"}}, "HH:MM"),
        ({"config": {"test_output_path": "/tmp/x"}}, "cannot be changed"),
        ({"config": {"autonomous_target_temp_c": 20}}, "cannot be changed"),
        ({"ready_by": {"command_cooldown_s": 1}}, "between 5 and 300"),
        ({"autonomous_enabled": "yes"}, "on or off"),
        ({"model_version": "v2"}, "Unknown"),
        ({}, "Expected"),
        # Limits are also checked against the saved counterpart.
        ({"config": {"alpha_min": 0.45, "alpha_max": 0.2}}, "must not be greater"),
        ({"config": {"max_session_minutes": 30, "min_session_minutes": 45}}, "must not be"),
    ],
)
def test_invalid_changes_save_nothing(services, session_app, client, body, message):
    auth = headers(session_app, client)
    # A valid field next to the invalid one is not saved either.
    body = (
        {**body, "config": {"min_session_minutes": 20, **body.get("config", {})}} if body else body
    )
    refused = client.patch("/api/car_heater/calibration", json=body, headers=auth)
    assert refused.status_code == 400
    assert message in refused.get_json()["error"]
    install(session_app)
    data = client.get("/api/car_heater/calibration").get_json()
    assert data["config"]["min_session_minutes"] == 15
    assert data["config_saved_at"] is None


def test_browser_mutations_need_csrf(services, client):
    refused = client.patch("/api/car_heater/calibration", json={"config": {"grace_samples": 4}})
    assert refused.status_code == 400
    assert client.get("/api/car_heater/calibration").get_json()["config"]["grace_samples"] == 3


def test_failed_save_reports_failure_and_keeps_previous_values(
    services, session_app, client, monkeypatch
):
    auth = headers(session_app, client)
    client.patch("/api/car_heater/calibration", json={"config": {"grace_samples": 4}}, headers=auth)

    def unavailable(_config):
        raise OSError("database unavailable")

    monkeypatch.setattr(session_app.ctrl, "save_kfactor_config", unavailable)
    monkeypatch.setattr(session_app.ctrl, "save_ready_by_config", unavailable)
    failed = client.patch(
        "/api/car_heater/calibration", json={"config": {"grace_samples": 6}}, headers=auth
    )
    assert failed.status_code == 500
    assert "Not all calibration settings were saved" in failed.get_json()["error"]
    # The reply carries what the server actually runs with.
    assert failed.get_json()["config"]["grace_samples"] == 4
    failed = client.patch(
        "/api/car_heater/calibration", json={"ready_by": {"enabled": False}}, headers=auth
    )
    assert failed.status_code == 500
    assert failed.get_json()["ready_by"]["enabled"] is True
    assert client.get("/api/car_heater/calibration").get_json()["config"]["grace_samples"] == 4


def test_bounded_session_owns_the_relay_until_confirmed_off(services, session_app, client):
    heater = services
    auth = headers(session_app, client)
    enabled = client.patch(
        "/api/car_heater/calibration", json={"autonomous_enabled": True}, headers=auth
    )
    assert enabled.status_code == 200
    assert enabled.get_json()["autonomous_enabled"] is True

    ready(heater)
    run = heater.start_bounded_session(60)
    # The bounded start suspends autonomous calibration.
    state = client.get("/api/car_heater/calibration").get_json()
    assert state["autonomous_enabled"] is False
    assert state["autonomous_start_available"] is False
    assert "confirmed OFF" in state["unavailable_reason"]

    for body in (
        {"autonomous_enabled": True},
        {"autonomous_enabled": True, "config": {"grace_samples": 7}},
    ):
        refused = client.patch("/api/car_heater/calibration", json=body, headers=auth)
        assert refused.status_code == 409
        assert "confirmed OFF" in refused.get_json()["error"]
        assert refused.get_json()["autonomous_enabled"] is False
    # Resetting to defaults cannot re-enable autonomous mode either.
    reset = client.post("/api/car_heater/calibration/actions/reset_defaults", headers=auth)
    assert reset.status_code == 200
    assert reset.get_json()["autonomous_enabled"] is False
    install(session_app)
    assert client.get("/api/car_heater/calibration").get_json()["config"]["grace_samples"] == 3
    assert heater.get_bounded_session()["phase"] == "starting"
    assert not any(c.get("source") == "kfactor_calibration" for c in heater.peek_queued_commands())

    # Model settings stay editable; only autonomous heating is owned by the session.
    edited = client.patch(
        "/api/car_heater/calibration", json={"config": {"grace_samples": 2}}, headers=auth
    )
    assert edited.status_code == 200

    heater.stop_bounded_session()
    refused = client.patch(
        "/api/car_heater/calibration", json={"autonomous_enabled": True}, headers=auth
    )
    assert refused.status_code == 409
    ready(heater, session_id=run["session_id"], phase="stopped")
    started = client.patch(
        "/api/car_heater/calibration", json={"autonomous_enabled": True}, headers=auth
    )
    assert started.status_code == 200
    assert started.get_json()["autonomous_enabled"] is True
    assert started.get_json()["unavailable_reason"] is None


def test_reset_cooldown_and_defaults_actions(services, session_app, client):
    auth = headers(session_app, client)
    client.patch(
        "/api/car_heater/calibration",
        json={"config": {"grace_samples": 9, "auto_calib_stop_hhmm": "06:00"}},
        headers=auth,
    )
    # Arrange a finished autonomous session's cooldown.
    session_app.kfactor_calibrator._set_cooldown(datetime.now(UTC), True, "session_finished")
    assert client.get("/api/car_heater/calibration").get_json()["autonomous_cooldown_until"]

    cleared = client.post("/api/car_heater/calibration/actions/reset_cooldown", headers=auth)
    assert cleared.status_code == 200
    assert cleared.get_json()["autonomous_cooldown_until"] is None
    assert cleared.get_json()["passive_cooldown_until"] is None

    reset = client.post("/api/car_heater/calibration/actions/reset_defaults", headers=auth)
    assert reset.status_code == 200
    assert reset.get_json()["config"]["grace_samples"] == 3
    assert reset.get_json()["config"]["auto_calib_stop_hhmm"] == "23:59"
    install(session_app)
    assert client.get("/api/car_heater/calibration").get_json()["config"]["grace_samples"] == 3

    unknown = client.post("/api/car_heater/calibration/actions/recalibrate", headers=auth)
    assert unknown.status_code == 404
    forged = client.post("/api/car_heater/calibration/actions/reset_defaults")
    assert forged.status_code == 400


class RecordingHandler:
    def __init__(self):
        self.views = []
        self.errors = []
        self.socketio = self

    def emit(self, event, payload=None, **_kw):
        if event == "error":
            self.errors.append(payload)
        else:
            self.views.append((event, payload))

    def emit_to_views(self, event, payload=None):
        self.views.append((event, payload))

    def flash(self, message, category="info"):
        self.errors.append({"message": message})


def socket_event(app, handle, data):
    """A calibration Socket.IO event as the logged-in owner, with a valid CSRF token."""
    from flask_login import login_user
    from flask_wtf.csrf import generate_csrf

    handler = RecordingHandler()
    with app.test_request_context():
        login_user(app.login_manager._user_callback("owner"))
        token = generate_csrf()
        handle(handler, data if "csrf_token" in data else {**data, "csrf_token": token})
    return handler


def test_socket_events_match_http_validation_ownership_and_results(services, session_app, client):
    from app.sockets.kfactor_handlers import handle_kfactor_control
    from app.sockets.ready_by_handlers import handle_ready_by_control

    heater = services
    saved = socket_event(
        session_app,
        handle_kfactor_control,
        {"action": "update_config", "config": {"grace_samples": 6}},
    )
    assert saved.errors == []
    assert saved.views[-1] == (
        "calibration_settings",
        client.get("/api/car_heater/calibration").get_json(),
    )
    assert saved.views[-1][1]["config"]["grace_samples"] == 6

    # Ready-by tuning merges partially, like PATCH.
    saved = socket_event(
        session_app,
        handle_ready_by_control,
        {"action": "update_config", "config": {"command_cooldown_s": 45}},
    )
    assert saved.errors == []
    assert saved.views[-1][1]["ready_by"] == {
        "enabled": True,
        "reach_tolerance_minutes": 2.0,
        "command_cooldown_s": 45,
    }

    invalid = socket_event(
        session_app,
        handle_kfactor_control,
        {"action": "update_config", "config": {"grace_samples": 0}},
    )
    assert "between 1 and 10" in invalid.errors[-1]["message"]
    forged = socket_event(
        session_app,
        handle_kfactor_control,
        {"action": "reset_defaults", "csrf_token": "forged"},
    )
    assert forged.errors and forged.views == []
    forged = socket_event(
        session_app,
        handle_ready_by_control,
        {"action": "update_config", "config": {"enabled": False}, "csrf_token": "forged"},
    )
    assert forged.errors and forged.views == []

    ready(heater)
    heater.start_bounded_session(60)
    refused = socket_event(
        session_app, handle_kfactor_control, {"action": "set_enabled", "enabled": True}
    )
    assert "confirmed OFF" in refused.errors[-1]["message"]
    # Refusals re-send the saved state so the page drops its unsaved toggle.
    assert refused.views[-1][1]["autonomous_enabled"] is False
    assert refused.views[-1][1]["autonomous_start_available"] is False

    install(session_app)
    data = client.get("/api/car_heater/calibration").get_json()
    assert data["config"]["grace_samples"] == 6
    assert data["ready_by"]["command_cooldown_s"] == 45
    assert data["autonomous_enabled"] is False


# Every control of the retired calibration dialog and where it lives now.
RETAINED_SETTINGS = [
    # Ready-by configuration panel
    "ready_by.enabled",
    "ready_by.reach_tolerance_minutes",
    "ready_by.command_cooldown_s",
    # Calibration window
    "config.auto_calib_start_hhmm",
    "config.auto_calib_stop_hhmm",
    # Session rules, quality, model defaults
    "config.grace_samples",
    "config.min_session_minutes",
    "config.max_session_minutes",
    "config.min_temp_rise_C",
    "config.cooldown_minutes",
    "config.bucket_lookback_days",
    "config.quality_threshold",
    "config.early_window_minutes",
    "config.drop_threshold_C",
    "config.spike_threshold_C",
    "config.default_eta",
    "config.default_k_loss_W_per_K",
    "config.mass_factor",
    # Expert tuning
    "config.eta_min",
    "config.eta_max",
    "config.k_loss_min",
    "config.k_loss_max",
    "config.mass_factor_min",
    "config.mass_factor_max",
    "config.tout_smooth_window",
    "config.informative_min_minutes",
    "config.curvature_ratio_max",
    "config.curvature_max_sample_distance_s",
    "config.prior_lambda_k",
    "config.prior_lambda_eta",
    "config.base_alpha",
    "config.alpha_min",
    "config.alpha_max",
]
RETAINED_ACTIONS_AND_READOUTS = [
    "calAutonomousToggle",  # Auto-calibration toggle
    "calResetCooldown",  # Reset cooldown
    "calResetDefaults",  # Reset to defaults
    "calState",  # Status
    "calParams",  # Active parameters k_loss and η
    "calLastSession",  # Last session
    "calWindow",  # Calibration window readout
    "calCooldown",  # Cooldown until
    "calData",  # Live session, recent sessions, bucket coverage, statistics
    "calDataRefresh",  # Refresh data
]


def test_settings_page_keeps_every_retained_calibration_control(services, client):
    page = client.get("/car_heater/settings").get_data(as_text=True)
    for name in RETAINED_SETTINGS:
        assert f'name="{name}"' in page, name
    for element in RETAINED_ACTIONS_AND_READOUTS:
        assert f'id="{element}"' in page, element
    # The old dialog and its unauthenticated auto-save script are gone.
    assert "carHeaterSettingsModal" not in page
    assert "car_heater_settings_kfactor.js" not in page


def test_data_lists_recorded_sessions_without_controlling_heating(services, client):
    heater = services
    data = client.get("/api/car_heater/calibration/data").get_json()
    assert data["recent_sessions"] == []
    assert data["bucket_coverage"] == []
    assert data["statistics"]["total_sessions"] == 0
    assert heater.peek_queued_commands() == []
