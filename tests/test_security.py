from __future__ import annotations

from types import SimpleNamespace

from flask import Blueprint, Flask, jsonify
from flask_login import LoginManager

from app import utils
from app.blueprints.auth import auth_bp
from app.blueprints.auth.auth import AuthUser
from app.blueprints.web import web_bp
from app.security import apply_api_cors, require_api_key
from app.utils import require_root_admin_or_redirect, safe_local_redirect_target


class _DummyCtrl:
    def verify_api_key_token(self, token: str):
        if token == "valid-token":
            return {"key_id": "abc123"}
        return None


def _make_app(*, allow_query_param: bool = False, cors_origins: list[str] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test-secret",
        ALLOW_API_KEY_QUERY_PARAM=allow_query_param,
        API_ALLOWED_ORIGINS=cors_origins or [],
    )
    app.ctrl = _DummyCtrl()  # type: ignore[attr-defined]
    apply_api_cors(app)

    @app.route("/api/protected")
    @require_api_key
    def protected():
        return jsonify({"ok": True})

    @app.route("/api/ping")
    def ping():
        return jsonify({"ok": True})

    return app


def test_require_api_key_accepts_header_token():
    app = _make_app()
    client = app.test_client()

    response = client.get("/api/protected", headers={"X-API-Key": "valid-token"})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_require_api_key_rejects_query_param_by_default():
    app = _make_app()
    client = app.test_client()

    response = client.get("/api/protected?api_key=valid-token")

    assert response.status_code == 401
    assert response.get_json()["error"] == "api_key_query_param_disabled"


def test_require_api_key_allows_query_param_when_opted_in():
    app = _make_app(allow_query_param=True)
    client = app.test_client()

    response = client.get("/api/protected?api_key=valid-token")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_apply_api_cors_allows_explicit_origin():
    app = _make_app(cors_origins=["https://example.com"])
    client = app.test_client()

    response = client.get("/api/ping", headers={"Origin": "https://example.com"})

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://example.com"
    assert "Origin" in response.headers.get("Vary", "")


def test_apply_api_cors_skips_unknown_origin():
    app = _make_app(cors_origins=["https://example.com"])
    client = app.test_client()

    response = client.get("/api/ping", headers={"Origin": "https://not-allowed.example"})

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers


def test_root_admin_guard_fails_closed_when_user_lookup_returns_none(monkeypatch):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    monkeypatch.setattr(
        utils,
        "current_user",
        SimpleNamespace(get_id=lambda: "missing-user"),
    )
    monkeypatch.setattr(
        utils,
        "get_ctrl",
        lambda: SimpleNamespace(get_user_by_username=lambda *_args, **_kwargs: None),
    )

    with app.test_request_context("/api/root-only"):
        response, status_code = require_root_admin_or_redirect(
            "Root-Admin required",
            json=True,
        )

    assert status_code == 403
    assert response.get_json()["error"] == "forbidden"


def test_safe_local_redirect_target_rejects_external_urls():
    assert safe_local_redirect_target("https://example.com", "/") == "/"
    assert safe_local_redirect_target("//example.com/path", "/") == "/"
    assert safe_local_redirect_target(r"\example.com", "/") == "/"


def test_safe_local_redirect_target_accepts_local_absolute_path():
    assert safe_local_redirect_target("/settings?tab=users", "/") == "/settings?tab=users"


def test_legacy_delete_user_route_is_not_registered():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(web_bp)

    routes = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/settings/delete_user" not in routes
    assert "/settings/users" in routes


def test_login_rejects_external_next_redirect():
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return AuthUser(user_id)

    web = Blueprint("web", __name__)

    @web.route("/")
    def get_home_page():
        return "home"

    app.ctrl = SimpleNamespace(  # type: ignore[attr-defined]
        get_user_by_username=lambda *_args, **_kwargs: SimpleNamespace(
            is_temporary=False,
            is_admin=False,
        ),
        authenticate_user=lambda *_args, **_kwargs: True,
    )
    app.register_blueprint(auth_bp)
    app.register_blueprint(web)

    response = app.test_client().post(
        "/login?next=https://example.com/phishing",
        data={"username": "viewer", "password": "secret"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
