from __future__ import annotations

from flask import Flask, jsonify

from app.security import apply_api_cors, require_api_key


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
