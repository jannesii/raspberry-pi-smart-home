from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import Blueprint, Flask, jsonify
from flask_login import LoginManager, UserMixin
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import create_engine, inspect

from app.blueprints.api.medicine_calculator_api import medicine_calculator_bp
from app.blueprints.auth import auth_bp
from app.blueprints.web import web_bp
from app.core import Controller
from app.core.medicine_calculator import calculate_medicine_refill
from app.core.schema import metadata
from app.core.sqlalchemy_engine import get_engine


class _User(UserMixin):
    def __init__(self, username: str):
        self.id = username


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _csrf_token(client) -> str:
    response = client.get("/csrf-token")
    payload = response.get_json() or {}
    return payload.get("csrf_token")


@pytest.fixture
def controller():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctrl = Controller(db_path=f"{tmpdir}/medicine.db")
        ctrl._sa_engine = get_engine(f"{tmpdir}/medicine.db")
        metadata.create_all(ctrl._sa_engine)
        ctrl.register_user("root", password="rootpass", is_admin=True, is_root_admin=True)
        ctrl.register_user("admin", password="adminpass", is_admin=True, is_root_admin=False)
        yield ctrl
        if ctrl._sa_engine:
            ctrl._sa_engine.dispose()


@pytest.fixture
def api_app(controller):
    app = Flask(__name__)
    app.config.update(
        {
            "SECRET_KEY": "test-secret",
            "WTF_CSRF_ENABLED": True,
            "TESTING": True,
        }
    )
    csrf = CSRFProtect()
    csrf.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return _User(user_id)

    @app.route("/csrf-token")
    def csrf_token_route():
        return jsonify({"csrf_token": generate_csrf()})

    app.ctrl = controller  # type: ignore[attr-defined]
    api_root = Blueprint("api_root", __name__, url_prefix="/api")
    api_root.register_blueprint(medicine_calculator_bp)
    app.register_blueprint(api_root)
    return app


@pytest.fixture
def api_client(api_app):
    return api_app.test_client()


def test_refill_uses_purchase_date_plus_one_and_selected_weekdays():
    result = calculate_medicine_refill(
        purchase_date="2026-05-15",
        pieces_bought=1,
        dose_per_dosing_day=1,
        dosing_weekdays=[0],
    )

    assert result.run_out_date == "2026-05-18"
    assert result.next_purchase_date == "2026-05-15"
    assert result.flex_days == 7


def test_refill_thresholds_use_exact_treatment_days():
    sixty = calculate_medicine_refill(
        purchase_date="2026-05-14",
        pieces_bought=60,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4, 5, 6],
    )
    ninety = calculate_medicine_refill(
        purchase_date="2026-05-14",
        pieces_bought=90,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4, 5, 6],
    )
    almost_sixty = calculate_medicine_refill(
        purchase_date="2026-05-14",
        pieces_bought=119,
        dose_per_dosing_day=2,
        dosing_weekdays=[0, 1, 2, 3, 4, 5, 6],
    )

    assert sixty.flex_days == 14
    assert ninety.flex_days == 21
    assert almost_sixty.treatment_days == 59.5
    assert almost_sixty.flex_days == 7


def test_refill_handles_partial_final_dosing_day():
    result = calculate_medicine_refill(
        purchase_date="2026-05-14",
        pieces_bought=45,
        dose_per_dosing_day=2,
        dosing_weekdays=[0, 1, 2, 3, 4],
    )

    assert result.treatment_days == 22.5
    assert result.dosing_days_covered == 23
    assert result.run_out_date == "2026-06-16"
    assert result.next_purchase_date == "2026-06-09"


def test_controller_crud_groups_by_normalized_medicine(controller: Controller):
    first = controller.create_medicine_purchase(
        medicine_name="Test Medicine",
        purchase_date="2026-05-14",
        pieces_bought=30,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4],
    )
    latest = controller.create_medicine_purchase(
        medicine_name=" test   medicine ",
        purchase_date="2026-06-30",
        pieces_bought=60,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4, 5, 6],
    )

    names = controller.get_medicine_names()
    summaries = controller.get_medicine_summaries()

    assert first.medicine_key == latest.medicine_key
    assert names == [{"medicine_key": "test medicine", "medicine_name": "test   medicine"}]
    assert len(summaries) == 1
    assert summaries[0]["latest_purchase"]["id"] == latest.id

    updated = controller.update_medicine_purchase(
        first.id,
        medicine_name="Other Medicine",
        purchase_date="2026-05-20",
        pieces_bought=90,
        dose_per_dosing_day=2,
        dosing_weekdays=[1, 3],
    )
    assert updated.medicine_key == "other medicine"
    assert json.loads(updated.dosing_weekdays_json) == [1, 3]
    assert controller.delete_medicine_purchase(updated.id) is True
    assert controller.get_medicine_purchase(updated.id) is None


def test_controller_lists_latest_purchase_per_normalized_medicine(controller: Controller):
    controller.create_medicine_purchase(
        medicine_name="Medicine B",
        purchase_date="2026-05-01",
        pieces_bought=30,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4],
    )
    older_a = controller.create_medicine_purchase(
        medicine_name="Medicine A",
        purchase_date="2026-05-15",
        pieces_bought=30,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4],
    )
    same_day_a = controller.create_medicine_purchase(
        medicine_name=" medicine   a ",
        purchase_date="2026-06-01",
        pieces_bought=60,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4],
    )
    latest_a = controller.create_medicine_purchase(
        medicine_name="Medicine A",
        purchase_date="2026-06-01",
        pieces_bought=90,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4],
    )

    latest_purchases = controller.list_latest_medicine_purchases()

    assert [purchase.medicine_key for purchase in latest_purchases] == [
        "medicine a",
        "medicine b",
    ]
    assert latest_purchases[0].id == latest_a.id
    assert latest_purchases[0].id not in {older_a.id, same_day_a.id}
    assert latest_purchases[0].pieces_bought == 90


def test_alembic_upgrade_adds_medicine_purchases_table(tmp_path):
    db_path = tmp_path / "medicine_migration.db"
    engine = create_engine(f"sqlite:///{db_path}")

    try:
        migration = importlib.import_module("migrations.versions.20260514_0009_medicine_calculator")
        with engine.begin() as conn:
            context = MigrationContext.configure(conn)
            operations = Operations(context)
            original_op = migration.op
            migration.op = operations
            try:
                migration.upgrade()
            finally:
                migration.op = original_op

        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("medicine_purchases")}
        assert {
            "medicine_name",
            "medicine_key",
            "purchase_date",
            "pieces_bought",
            "dose_per_dosing_day",
            "dosing_weekdays_json",
        }.issubset(columns)
    finally:
        engine.dispose()


def test_api_requires_root_admin(api_client):
    _login(api_client, "admin")
    response = api_client.get("/api/medicine-calculator/purchases")
    assert response.status_code == 403


def test_api_post_requires_csrf(api_client):
    _login(api_client, "root")
    response = api_client.post(
        "/api/medicine-calculator/purchases",
        json={
            "medicine_name": "Medicine A",
            "purchase_date": "2026-05-14",
            "pieces_bought": 30,
            "dose_per_dosing_day": 1,
            "dosing_weekdays": [0, 1, 2, 3, 4],
        },
    )
    assert response.status_code == 400


def test_api_crud_with_csrf(api_client):
    _login(api_client, "root")
    token = _csrf_token(api_client)

    response = api_client.post(
        "/api/medicine-calculator/purchases",
        headers={"X-CSRFToken": token},
        json={
            "medicine_name": "Medicine A",
            "purchase_date": "2026-05-14",
            "pieces_bought": 30,
            "dose_per_dosing_day": 1,
            "dosing_weekdays": [0, 1, 2, 3, 4],
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    purchase_id = payload["purchase"]["id"]
    assert payload["ok"] is True
    assert payload["summaries"][0]["calculation"]["next_purchase_date"] == "2026-06-18"

    response = api_client.patch(
        f"/api/medicine-calculator/purchases/{purchase_id}",
        headers={"X-CSRFToken": token},
        json={
            "medicine_name": "Medicine A",
            "purchase_date": "2026-05-14",
            "pieces_bought": 45,
            "dose_per_dosing_day": 2,
            "dosing_weekdays": [0, 1, 2, 3, 4],
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["purchase"]["calculation"]["run_out_date"] == "2026-06-16"

    response = api_client.delete(
        f"/api/medicine-calculator/purchases/{purchase_id}",
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200
    assert response.get_json()["purchases"] == []


def test_api_validation_errors(api_client):
    _login(api_client, "root")
    token = _csrf_token(api_client)
    response = api_client.post(
        "/api/medicine-calculator/purchases",
        headers={"X-CSRFToken": token},
        json={
            "medicine_name": "Medicine A",
            "purchase_date": "2026-05-14",
            "pieces_bought": 30,
            "dose_per_dosing_day": 1,
            "dosing_weekdays": [],
        },
    )
    assert response.status_code == 400
    assert "dosing weekday" in response.get_json()["message"].lower()


class _SettingsCtrlStub:
    def get_user_by_username(self, username: str, include_pw: bool = False):
        if username == "root":
            return SimpleNamespace(is_root_admin=True, is_expired=False)
        if username == "admin":
            return SimpleNamespace(is_root_admin=False, is_expired=False)
        return None


@pytest.fixture
def web_app():
    root = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(root / "app" / "templates"),
        static_folder=str(root / "app" / "static"),
    )
    app.config.update({"SECRET_KEY": "test-secret", "TESTING": True})
    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return _User(user_id)

    app.ctrl = _SettingsCtrlStub()  # type: ignore[attr-defined]
    app.register_blueprint(auth_bp)
    app.register_blueprint(web_bp)
    return app


def test_settings_link_only_renders_for_root_admin(web_app):
    client = web_app.test_client()

    _login(client, "admin")
    response = client.get("/settings")
    assert response.status_code == 200
    assert b"Medicine Calculator" not in response.data

    _login(client, "root")
    response = client.get("/settings")
    assert response.status_code == 200
    assert b"Medicine Calculator" in response.data
