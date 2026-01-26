from __future__ import annotations

import logging
import time
import os
import re
from dataclasses import dataclass
from typing import Tuple

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, current_app
import socketio
from socketio.exceptions import BadNamespaceError

# ---------------------------
# Configuration & Logging
# ---------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("esp32_server")
logging.getLogger("socketio.client").setLevel(logging.WARNING)

# Throttle login refreshes to avoid hammering backend on outages
_LAST_LOGIN_REFRESH_TS: float = 0.0
_LOGIN_REFRESH_COOLDOWN_S: float = 90.0

# ---------------------------
# Datatypes
# ---------------------------


@dataclass
class BackendConfig:
    server: str
    username: str
    password: str


def on_connect() -> None:
    pass


def on_disconnect() -> None:
    pass


def on_error(data: dict) -> None:
    logger.error("server error: %s", data)


def on_server_shutdown() -> None:
    logger.info("server is shutting down")
    sio.disconnect()


def connect() -> None:
    """Optional: place background loops or one-time connect logic here."""
    pass


sio = socketio.Client(
    logger=True,
    # Avoid infinite background reconnect loops with stale cookies.
    # We'll handle reconnects explicitly to allow session refresh.
    reconnection=False,
)
sio.on("connect", on_connect)
sio.on("disconnect", on_disconnect)
sio.on("error", on_error)
sio.on("server_shutdown", on_server_shutdown)

# ---------------------------
# Backend (CSRF) Session Setup
# ---------------------------

_CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def _require_env() -> BackendConfig:
    load_dotenv("/etc/esp32_server.env", override=True)
    server = os.getenv("SERVER", "").rstrip("/")
    username = os.getenv("USERNAME", "")
    password = os.getenv("PASSWORD", "")
    if not server or not username or not password:
        missing = [
            k
            for k, v in {
                "SERVER": server,
                "USERNAME": username,
                "PASSWORD": password,
            }.items()
            if not v
        ]
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return BackendConfig(server=server, username=username, password=password)


def _extract_csrf_token(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    if not m:
        raise RuntimeError("CSRF token not found on login page")
    return m.group(1)


def _authenticate_session(
    cfg: BackendConfig, timeout: float = 5.0
) -> Tuple[requests.Session, str]:
    """Authenticate using simplified JSON endpoint /login_api.

    Returns a session with cookies set; CSRF token is not needed for code paths,
    so an empty string is returned for legacy compatibility.
    """
    session = requests.Session()
    login_api = f"{cfg.server}/login_api"
    payload = {"username": cfg.username, "password": cfg.password, "remember": True}
    resp = session.post(login_api, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"POST {login_api} failed with status {resp.status_code}")
    try:
        body = resp.json()
    except Exception:
        body = None
    if not body or not body.get("ok"):
        raise RuntimeError("Login failed: invalid credentials or unexpected response")
    logger.info("Authenticated to backend via /login_api")
    return session, ""


def _cookies_header(session: requests.Session) -> dict[str, str]:
    cookie_str = "; ".join(f"{k}={v}" for k, v in session.cookies.get_dict().items())
    return {"Cookie": cookie_str} if cookie_str else {}


def _connect_with_login_refresh(cfg: BackendConfig, session: requests.Session) -> None:
    """
    Try to connect with current cookies; on failure, refresh login and retry once.
    Raises on repeated failure.
    """
    try:
        sio.connect(
            cfg.server,
            headers=_cookies_header(session),
            transports=["websocket", "polling"],
            wait=True,  # block until connected or timeout
            auth={"role": "esp32"},
        )
        return
    except Exception as e:
        logger.warning("Socket.IO connect failed (%s). Considering login refresh...", e)
        # Refresh session (throttled) and retry once
        global _LAST_LOGIN_REFRESH_TS
        now = time.time()
        do_refresh = (now - _LAST_LOGIN_REFRESH_TS) > _LOGIN_REFRESH_COOLDOWN_S
        if not do_refresh:
            raise
        session2, token = _authenticate_session(cfg)
        _LAST_LOGIN_REFRESH_TS = now
        # Update app config if available
        try:
            current_app.config["REST_SESSION"] = session2
            current_app.config["CSRF_TOKEN"] = token
        except Exception:
            pass
        # Hard reset client state
        try:
            sio.disconnect()
        except Exception:
            pass
        # Retry connect with fresh cookies
        sio.connect(
            cfg.server,
            headers=_cookies_header(session2),
            transports=["websocket", "polling"],
            wait=True,
            auth={"role": "esp32"},
        )


def _ensure_sio_connected(cfg: BackendConfig, session: requests.Session) -> None:
    """
    Make sure the global `sio` client is connected to the desired namespace.
    Reconnects with session cookies if needed.
    """
    # Already connected to this namespace?
    try:
        if sio.connected:
            return
    except Exception:
        # Fall through to reconnect
        pass

    # (Re)connect synchronously and only to the desired namespace
    _connect_with_login_refresh(cfg, session)


# ---------------------------
# Flask App Factory
# ---------------------------


def create_app() -> Flask:
    app = Flask(__name__, template_folder="app/templates")

    cfg = _require_env()
    app.config["BACKEND_CONFIG"] = cfg
    app.config["REST_SESSION"] = None  # type: ignore[assignment]
    app.config["CSRF_TOKEN"] = None

    @app.get("/healthz")
    def healthz():
        ready = app.config.get("REST_SESSION") is not None and sio.connected
        status = 200 if ready else 503
        return jsonify({"ok": ready}), status

    @app.post("/temperature")
    def temperature():
        session = current_app.config.get("REST_SESSION")
        if session is None:
            try:
                session, token = _authenticate_session(
                    current_app.config["BACKEND_CONFIG"]
                )
                current_app.config["REST_SESSION"] = session
                current_app.config["CSRF_TOKEN"] = token
            except Exception as e:
                logger.error("Backend not ready: %s", e)
                return jsonify({"ok": False, "error": "backend_unavailable"}), 503

        data = request.get_json(force=True, silent=True) or {}
        logger.info("Got reading: %s", data)

        try:
            _ensure_sio_connected(current_app.config["BACKEND_CONFIG"], session)
            sio.emit("esp32_temphum", data)
        except BadNamespaceError:
            # One forced reconnect attempt
            logger.warning("Namespace not connected; reconnecting once...")
            try:
                sio.disconnect()
            except Exception:
                pass
            try:
                _ensure_sio_connected(current_app.config["BACKEND_CONFIG"], session)
                sio.emit("esp32_temphum", data)
            except Exception as e:
                logger.error("Emit failed after reconnect: %s", e)
                return jsonify({"ok": False, "error": "socket_disconnected"}), 503
        except Exception as e:
            logger.error("Socket.IO emit error: %s", e)
            return jsonify({"ok": False, "error": "socket_error"}), 502

        return jsonify({"ok": True}), 200

    return app


app = create_app()

if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "192.168.10.50")
    port = int(os.getenv("FLASK_RUN_PORT", "8080"))
    app.run(host=host, port=port, debug=True)
