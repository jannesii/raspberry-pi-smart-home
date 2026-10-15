"""Permanent session evidence through authenticated public endpoints."""

import pytest
from flask import Blueprint, Flask
from flask_login import LoginManager, UserMixin
from flask_wtf.csrf import generate_csrf
from sqlalchemy import create_engine
from test_car_heater_history_preservation import isolated_postgres as isolated_postgres

from app.blueprints.api.car_heater import car_bp
from app.blueprints.auth import auth_bp
from app.blueprints.web import web_bp
from app.core import Controller
from app.core.schema import metadata
from app.extensions import csrf


@pytest.fixture
def session_app(tmp_path, monkeypatch):
    from app.core.database import DatabaseManager

    monkeypatch.setattr(DatabaseManager, "_instance", None)
    app = Flask(__name__, template_folder="../app/templates", static_folder="../app/static")
    app.config.update(TESTING=True, SECRET_KEY="isolated-session-test")
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    metadata.create_all(engine)
    ctrl._sa_engine = engine
    app.ctrl = ctrl
    app.extensions["session_engine"] = engine
    app.config["DB_PATH"] = str(tmp_path / "legacy.db")
    login = LoginManager(app)

    class User(UserMixin):
        id = "owner"
        is_admin = True
        is_root_admin = True

    login.user_loader(lambda uid: User() if uid == "owner" else None)
    root = Blueprint("session_api", __name__, url_prefix="/api")
    root.register_blueprint(car_bp)
    app.register_blueprint(root)
    app.register_blueprint(web_bp)
    app.register_blueprint(auth_bp)
    csrf.init_app(app)
    yield app
    engine.dispose()
    ctrl.db.conn.close()


@pytest.fixture
def client(session_app):
    client = session_app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "owner"
        session["_fresh"] = True
    return client


def headers(app, client):
    with app.test_request_context():
        token = generate_csrf()
        from flask import session

        raw = session["csrf_token"]
    with client.session_transaction() as session:
        session["csrf_token"] = raw
    return {"X-CSRFToken": token}


def test_attempts_survive_another_attempt_and_controller_reload(session_app, client):
    auth = headers(session_app, client)
    first = client.post(
        "/api/car_heater/sessions",
        json={
            "trigger": "manual",
            "observed_at": "2026-10-14T06:00:00+03:00",
            "installation": "Reference sensor taped beside mirror",
            "heating_duration_minutes": 60,
        },
        headers=auth,
    )
    assert first.status_code == 201
    attempt = first.get_json()
    assert attempt["heating_duration_minutes"] == 60
    assert attempt["id"]
    assert attempt["requested_departure_at"] is None
    assert attempt["snapshot"]["installation"] == "Reference sensor taped beside mirror"
    assert attempt["snapshot"]["firmware_version"] is None
    assert attempt["outcome"] is None
    second = client.post(
        "/api/car_heater/sessions",
        json={
            "trigger": "observation",
            "observed_at": "2026-10-15T06:00:00+03:00",
        },
        headers=auth,
    )
    assert second.status_code == 201
    session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
    # Reattach the same isolated durable engine after recreating the app-owned Controller.
    session_app.ctrl._sa_engine = session_app.extensions["session_engine"]
    rows = client.get("/api/car_heater/sessions").get_json()
    assert [r["id"] for r in rows] == [second.get_json()["id"], attempt["id"]]
    assert client.get("/api/car_heater/sessions/" + attempt["id"]).get_json() == attempt


@pytest.mark.parametrize("result", ["clear", "wipers_enough", "partial_scrape", "full_scrape"])
def test_outcome_roundtrip_does_not_control_heating(session_app, client, result):
    class NoControls:
        def __getattr__(self, name):
            pytest.fail(f"Evidence must not call heater service: {name}")

    session_app.car_heater_service = NoControls()
    auth = headers(session_app, client)
    attempt = client.post(
        "/api/car_heater/sessions",
        json={
            "trigger": "manual",
            "observed_at": "2026-10-14T06:00:00+03:00",
        },
        headers=auth,
    ).get_json()
    endpoint = "/api/car_heater/sessions/" + attempt["id"]
    response = client.put(
        endpoint + "/outcome",
        json={
            "result": result,
            "already_clear_at_observation": True,
            "observed_at": "2026-10-14T06:10:00+03:00",
            "frost_type": "thin frost",
            "other_windows": "clear",
            "interior_condition": "fog",
        },
        headers=auth,
    )
    assert response.status_code == 200
    saved = client.get(endpoint).get_json()
    assert saved["outcome"]["result"] == result
    assert saved["outcome"]["already_clear_at_observation"] is True
    assert saved["outcome"]["actual_departure_at"] is None
    assert "cleared_at" not in saved["outcome"]
    assert saved["snapshot"] == attempt["snapshot"]
    # Editing an outcome must retain permanent identity and history.
    assert (
        client.put(endpoint + "/outcome", json={"result": "clear"}, headers=auth).status_code == 200
    )
    assert len(client.get("/api/car_heater/sessions").get_json()) == 1


def test_authentication_csrf_validation_and_missing_sessions(session_app, client):
    anonymous = session_app.test_client()
    assert anonymous.get("/api/car_heater/sessions").status_code == 401
    assert anonymous.get("/api/car_heater/sessions/unknown").status_code == 401
    assert client.post("/api/car_heater/sessions", json={}).status_code == 400
    assert (
        client.put("/api/car_heater/sessions/unknown/outcome", json={"result": "clear"}).status_code
        == 400
    )
    auth = headers(session_app, client)
    for body in (
        [],
        {"trigger": "invalid"},
        {"observed_at": "2026-10-14T06:00"},
        {"observed_at": "2026-10-14T06:00+03:00", "installation": 5},
    ):
        assert client.post("/api/car_heater/sessions", json=body, headers=auth).status_code == 400
    assert client.get("/api/car_heater/sessions/unknown").status_code == 404
    assert (
        client.put(
            "/api/car_heater/sessions/unknown/outcome", json={"result": "clear"}, headers=auth
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/car_heater/sessions/unknown/outcome", json={"result": "success"}, headers=auth
        ).status_code
        == 400
    )
    assert client.get("/api/car_heater/sessions?date=invalid").status_code == 400


def test_date_filter_uses_helsinki_departure_or_observation_date(session_app, client):
    auth = headers(session_app, client)
    for observed, departure in [
        ("2026-10-13T23:30:00+00:00", None),
        ("2026-10-14T06:00:00+03:00", "2026-10-15T07:00:00+03:00"),
    ]:
        assert (
            client.post(
                "/api/car_heater/sessions",
                json={
                    "observed_at": observed,
                    "requested_departure_at": departure,
                },
                headers=auth,
            ).status_code
            == 201
        )
    assert len(client.get("/api/car_heater/sessions?date=2026-10-14").get_json()) == 1
    assert len(client.get("/api/car_heater/sessions?date=2026-10-15").get_json()) == 1
    assert client.get("/api/car_heater/sessions?date=2026-10-13").get_json() == []


def test_firmware_snapshot_keeps_report_provenance_without_sensor_values(session_app, client):
    from app.core.models import CarHeaterSensorFrame

    session_app.ctrl.record_car_heater_sensor_frame(
        CarHeaterSensorFrame(
            id=None,
            received_at="2026-10-14T05:00:00+00:00",
            firmware_version="3.0.0",
            glass_reference_state="missing",
            cabin_state="missing",
        )
    )
    auth = headers(session_app, client)
    first = client.post(
        "/api/car_heater/sessions",
        json={
            "observed_at": "2026-10-14T08:10:00+03:00",
        },
        headers=auth,
    ).get_json()
    assert first["snapshot"]["firmware_version"] == "3.0.0"
    assert first["snapshot"]["firmware_source"] == "device_report"
    assert first["snapshot"]["firmware_reported_at"] == "2026-10-14T05:00:00+00:00"
    second = client.post(
        "/api/car_heater/sessions",
        json={
            "observed_at": "2026-10-14T08:10:00+03:00",
            "firmware_version": "3.1.0",
        },
        headers=auth,
    ).get_json()
    assert second["snapshot"]["firmware_source"] == "driver_recorded"
    assert second["snapshot"]["firmware_reported_at"] is None
    assert client.get("/api/car_heater/sessions/" + first["id"]).get_json() == first


def test_migration_preserves_older_telemetry_and_exposes_permanent_sessions(
    session_app, client, monkeypatch
):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.core.models import CarHeaterStatus
    from app.core.schema import (
        car_heater_departure_plan,
        car_heater_execution,
        car_heater_session,
    )

    ctrl = session_app.ctrl
    ctrl.record_car_heater_status(
        CarHeaterStatus(
            id=None,
            timestamp="2020-01-15 07:00:00+02:00",
            is_heater_on=False,
            instant_power_w=0,
            ambient_temp=-10,
            voltage_v=None,
            current_a=None,
            energy_total_wh=None,
            energy_last_min_wh=None,
            energy_ts=None,
            device_temp_c=None,
            device_temp_f=None,
            source="fixture",
        )
    )
    engine = session_app.extensions["session_engine"]
    car_heater_departure_plan.drop(engine, checkfirst=True)
    car_heater_execution.drop(engine, checkfirst=True)
    car_heater_session.drop(engine)
    migration = importlib.import_module("migrations.versions.20261014_0012_car_heater_sessions")
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        execution_migration = importlib.import_module(
            "migrations.versions.20261014_0013_car_heater_execution"
        )
        monkeypatch.setattr(execution_migration, "op", Operations(MigrationContext.configure(conn)))
        execution_migration.upgrade()
        departure_migration = importlib.import_module(
            "migrations.versions.20261015_0014_car_heater_departure"
        )
        monkeypatch.setattr(departure_migration, "op", Operations(MigrationContext.configure(conn)))
        departure_migration.upgrade()
    assert (
        client.get("/api/car_heater/history?date=2020-01-15").get_json()["rows"][0]["ambient_temp"]
        == -10
    )
    auth = headers(session_app, client)
    attempt = client.post(
        "/api/car_heater/sessions",
        json={
            "observed_at": "2026-10-14T08:00:00+03:00",
        },
        headers=auth,
    )
    assert attempt.status_code == 201
    assert client.get("/api/car_heater/sessions/" + attempt.get_json()["id"]).status_code == 200
    assert len(client.get("/api/car_heater/history?date=2020-01-15").get_json()["rows"]) == 1


def test_postgresql_sessions_migration_and_outcome_roundtrip(
    session_app, client, monkeypatch, request
):
    from sqlalchemy.engine import URL

    # Reuse the repository's private Unix-socket PostgreSQL fixture.
    _, socket = request.getfixturevalue("isolated_postgres")
    url = URL.create(
        "postgresql+psycopg", database="postgres", query={"host": str(socket), "port": "55439"}
    )
    engine = create_engine(url)
    from app.core.schema import car_heater_sensor_frame, car_heater_session, car_heater_status

    metadata.create_all(
        engine, tables=[car_heater_status, car_heater_sensor_frame, car_heater_session]
    )
    original = session_app.ctrl._sa_engine
    session_app.ctrl._sa_engine = engine
    session_app.extensions["session_engine"] = engine
    try:
        test_migration_preserves_older_telemetry_and_exposes_permanent_sessions(
            session_app, client, monkeypatch
        )
        auth = headers(session_app, client)
        attempt = client.get("/api/car_heater/sessions").get_json()[0]
        endpoint = "/api/car_heater/sessions/" + attempt["id"]
        assert (
            client.put(
                endpoint + "/outcome", json={"result": "partial_scrape"}, headers=auth
            ).status_code
            == 200
        )
        # A new Controller/engine must read the same permanent evidence.
        engine.dispose()
        session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
        session_app.ctrl._sa_engine = create_engine(url)
        assert client.get(endpoint).get_json()["outcome"]["result"] == "partial_scrape"
        # Exercise the public bounded service against the migrated private PG DB.
        from app.services.car_heater.car_heater_service import CarHeaterService

        # Only this service's settings are needed; do not bootstrap unrelated
        # legacy schema defaults as part of a bounded-execution check.
        with session_app.ctrl._sa_engine.begin() as conn:
            conn.exec_driver_sql("""CREATE TABLE car_heater_charge_mode (
                id INTEGER PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT false,
                threshold_w DOUBLE PRECISION NOT NULL DEFAULT 20,
                power_cut BOOLEAN NOT NULL DEFAULT false, power_cut_at TEXT,
                last_instant_power_w DOUBLE PRECISION,
                seen_above_threshold BOOLEAN NOT NULL DEFAULT false)""")
        monkeypatch.setattr(
            "redis.from_url", lambda *a, **kw: (_ for _ in ()).throw(OSError("isolated"))
        )
        heater = CarHeaterService(session_app.ctrl, clock=lambda: 1800000000)
        recovered = None
        try:
            heater.observe_bounded_status(
                {
                    "bounded": {
                        "protocol": 1,
                        "ready": True,
                        "relay_on": False,
                        "boot_id": "shelly-pg",
                        "device_boot_id": "esp-pg",
                    }
                }
            )
            run = heater.start_bounded_session(60)
            assert (
                client.get("/api/car_heater/sessions/" + run["session_id"]).get_json()["execution"][
                    "finish_at"
                ]
                == 1800003600
            )
            heater.stop()
            recovered = CarHeaterService(session_app.ctrl, clock=lambda: 1800000010)
            assert recovered.get_bounded_session()["session_id"] == run["session_id"]
            assert recovered.get_bounded_session()["finish_at"] == 1800003600
            assert [c["action"] for c in recovered.get_queued_commands()] == ["turn_off"]
        finally:
            heater.stop()
            if recovered:
                recovered.stop()

    finally:
        session_app.ctrl._sa_engine.dispose()
        session_app.ctrl._sa_engine = original
        session_app.extensions["session_engine"] = original
