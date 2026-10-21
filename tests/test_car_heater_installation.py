"""Installation & devices settings at the authenticated public seams."""

import test_car_heater_comfort as comfort
import test_departure_heating as departures

from app.core import Controller

session_app = comfort.session_app
client = comfort.client
headers = comfort.headers
heater = comfort.heater
ready = comfort.ready
departure = departures.departure


def test_record_saves_partially_keeps_history_and_survives_reload(heater, session_app, client):
    auth = headers(session_app, client)
    initial = client.get("/api/car_heater/installation").get_json()
    assert initial["record"] is None
    assert initial["history"] == []

    first = client.patch(
        "/api/car_heater/installation",
        json={"glass_reference_position": "Lower middle of the driver's view"},
        headers=auth,
    )
    assert first.status_code == 200
    record = first.get_json()["record"]
    assert record["glass_reference_position"] == "Lower middle of the driver's view"
    assert record["heater"] is None
    assert record["saved_by"] == "owner"
    assert record["saved_at"]

    second = client.patch(
        "/api/car_heater/installation",
        json={"heater": "  DEFA cabin heater, 1350 W  ", "cabin_position": ""},
        headers=auth,
    )
    record = second.get_json()["record"]
    # Absent fields keep their values; text is trimmed and blank clears.
    assert record["glass_reference_position"] == "Lower middle of the driver's view"
    assert record["heater"] == "DEFA cabin heater, 1350 W"
    assert record["cabin_position"] is None

    session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
    session_app.ctrl._sa_engine = session_app.extensions["session_engine"]
    reloaded = client.get("/api/car_heater/installation").get_json()
    assert reloaded["record"] == record
    # Position changes stay as configuration history, newest first.
    assert [h["heater"] for h in reloaded["history"]] == ["DEFA cabin heater, 1350 W", None]


def test_invalid_or_unauthorized_changes_save_nothing(heater, session_app, client):
    auth = headers(session_app, client)
    for body in ({"heater": "x" * 501}, {"heater": 5}, {"relay_ip": "10.0.0.2"}, {}):
        response = client.patch("/api/car_heater/installation", json=body, headers=auth)
        assert response.status_code == 400, body
        assert response.get_json()["error"]
    assert client.patch("/api/car_heater/installation", json={"heater": "A"}).status_code == 400
    assert client.get("/api/car_heater/installation").get_json()["history"] == []


def test_device_facts_report_only_what_devices_reported(heater, session_app, client):
    svc, _clock = heater
    devices = client.get("/api/car_heater/installation").get_json()["devices"]
    assert devices["firmware"] == {"reported": None, "tested": "3.1.1", "matches": None}
    assert devices["relay"]["reported"] is False
    assert devices["relay"]["ready"] is None
    # No hardware has reported: every sensor is honestly unavailable.
    assert [(s["key"], s["state"], s["temperature_c"]) for s in devices["sensors"]] == [
        ("glass_reference", "missing", None),
        ("glass_comparison", "missing", None),
        ("cabin", "missing", None),
    ]
    assert devices["sensors"][0]["model"] == "TMP117"

    ready(svc)
    devices = client.get("/api/car_heater/installation").get_json()["devices"]
    assert devices["firmware"] == {"reported": "3.1.1", "tested": "3.1.1", "matches": True}
    assert devices["relay"]["reported"] is True
    assert devices["relay"]["ready"] is True
    assert devices["relay"]["protocol"] == 1
    assert devices["relay"]["age_s"] == 0

    svc.observe_bounded_status({"firmware_version": "3.0.0", "bounded": {"protocol": 1}})
    devices = client.get("/api/car_heater/installation").get_json()["devices"]
    assert devices["firmware"]["matches"] is False
    assert devices["relay"]["ready"] is False


def test_new_heating_sessions_record_the_saved_installation(heater, session_app, client):
    svc, _clock = heater
    auth = headers(session_app, client)
    client.patch(
        "/api/car_heater/installation",
        json={"glass_reference_position": "Lower middle", "power_topology": "Shelly relay"},
        headers=auth,
    )
    ready(svc)
    run = svc.start_bounded_session(30)
    snapshot = client.get(f"/api/car_heater/sessions/{run['session_id']}").get_json()["snapshot"]
    assert snapshot["installation"]["glass_reference_position"] == "Lower middle"
    assert snapshot["installation"]["power_topology"] == "Shelly relay"
    assert snapshot["firmware_version"] == "3.1.1"

    # Later edits never rewrite an existing session's snapshot.
    client.patch(
        "/api/car_heater/installation", json={"glass_reference_position": "Moved"}, headers=auth
    )
    again = client.get(f"/api/car_heater/sessions/{run['session_id']}").get_json()["snapshot"]
    assert again["installation"]["glass_reference_position"] == "Lower middle"


def test_departure_plans_record_the_installation_saved_at_planning(departure, session_app, client):
    auth = headers(session_app, client)
    client.patch(
        "/api/car_heater/installation", json={"cabin_position": "Shaded console"}, headers=auth
    )
    plan = client.post(
        "/api/car_heater/departure", json={"departure_at": "2027-01-16T08:00"}, headers=auth
    ).get_json()
    snapshot = client.get(f"/api/car_heater/sessions/{plan['session_id']}").get_json()["snapshot"]
    assert snapshot["installation"]["cabin_position"] == "Shaded console"
    assert snapshot["software_version"] == "departure-v1"


def test_migration_adds_history_table_without_touching_sessions(tmp_path, monkeypatch):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from app.core.schema import car_heater_session, metadata

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    metadata.create_all(engine, tables=[car_heater_session])
    with engine.begin() as conn:
        conn.execute(
            car_heater_session.insert().values(
                id="kept",
                trigger="manual",
                created_at="2026-10-21T05:00:00+00:00",
                observed_at="2026-10-21T05:00:00+00:00",
                snapshot="{}",
            )
        )
    migration = importlib.import_module("migrations.versions.20261021_0019_car_heater_installation")
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
    columns = {c["name"] for c in inspect(engine).get_columns("car_heater_installation")}
    assert columns == {
        "id",
        "saved_at",
        "saved_by",
        "heater",
        "power_topology",
        "glass_reference_position",
        "glass_comparison_position",
        "cabin_position",
    }
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.downgrade()
        assert len(conn.execute(car_heater_session.select()).all()) == 1
    assert "car_heater_installation" not in inspect(engine).get_table_names()
    engine.dispose()


def test_heating_still_starts_when_the_installation_migration_is_missing(
    heater, session_app, client
):
    from app.core.schema import car_heater_installation

    car_heater_installation.drop(session_app.extensions["session_engine"])
    svc, _clock = heater
    ready(svc)
    run = svc.start_bounded_session(30)
    snapshot = client.get(f"/api/car_heater/sessions/{run['session_id']}").get_json()["snapshot"]
    assert snapshot["installation"] is None
    view = client.get("/api/car_heater/installation")
    assert view.status_code == 200
    assert view.get_json()["record_available"] is False
    assert view.get_json()["devices"]["relay"]["ready"] is True
