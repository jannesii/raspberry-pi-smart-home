"""Application configuration loader.

Centralizes environment parsing and default values to keep `create_app` tidy
and prevent scattered env parsing across the codebase.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import timedelta
from typing import Any


def _json_list(env_var: str, default: list[str]) -> list[str]:
    raw = os.getenv(env_var)
    if not raw:
        return list(default)
    try:
        vals = json.loads(raw)
        if isinstance(vals, list):
            return vals
        # allow single string to be promoted to list
        if isinstance(vals, str):
            return [vals]
    except json.JSONDecodeError:
        pass
    raise RuntimeError(f"{env_var} isn't valid JSON list")


def _env_int(env_var: str, default: int) -> int:
    raw = os.getenv(env_var)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{env_var} must be an integer") from exc


def load_settings() -> dict[str, Any]:
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY is missing - add to environment.")

    whitelist = _json_list("RATE_LIMIT_WHITELIST", [])
    allowed_ws = _json_list("ALLOWED_WS_ORIGINS", ["http://127.0.0.1:5555"])

    settings: dict[str, Any] = {
        # Flask
        "SECRET_KEY": secret,
        "PERMANENT_SESSION_LIFETIME": timedelta(days=7),
        "SESSION_COOKIE_SECURE": True,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        # Forms / CSRF
        "WTF_CSRF_TIME_LIMIT": None,
        # Auth seeding
        "WEB_USERNAME": os.getenv("WEB_USERNAME"),
        "WEB_PASSWORD": os.getenv("WEB_PASSWORD"),
        # DB path
        "DB_PATH": os.getenv("DB_PATH", os.path.join(tempfile.gettempdir())),
        # Rate limit whitelist for request_filter
        "whitelist": whitelist,
        # Sockets
        "ALLOWED_WS_ORIGINS": allowed_ws,
        # HVAC / Thermostat shared settings
        "THERMOSTAT_LOCATION": os.getenv("THERMOSTAT_LOCATION", "Tietokonepöytä"),
        "ROOM_THERMAL_CAPACITY_J_PER_K": os.getenv("ROOM_THERMAL_CAPACITY_J_PER_K"),
        # YNAB categorizer
        "YNAB_API_KEY": os.getenv("YNAB_API_KEY"),
        "YNAB_BUDGET_ID": os.getenv("YNAB_BUDGET_ID"),
        "YNAB_HTTP_TIMEOUT_S": _env_int("YNAB_HTTP_TIMEOUT_S", 15),
        "YNAB_HTTP_RETRIES": _env_int("YNAB_HTTP_RETRIES", 2),
    }

    return settings
