"""Application configuration loader.

Centralizes environment parsing and default values to keep `create_app` tidy
and prevent scattered env parsing across the codebase.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _json_list(env_var: str, default: list[str]) -> list[str]:
    raw = os.getenv(env_var)
    logger.debug("_json_list parsing env_var=%s raw_set=%s", env_var, raw is not None)
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
    logger.debug("_env_int parsing env_var=%s raw=%s", env_var, raw)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{env_var} must be an integer") from exc


def _env_float(env_var: str, default: float) -> float:
    raw = os.getenv(env_var)
    logger.debug("_env_float parsing env_var=%s raw=%s", env_var, raw)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{env_var} must be a number") from exc


def _env_bool(env_var: str, default: bool) -> bool:
    raw = os.getenv(env_var)
    logger.debug("_env_bool parsing env_var=%s raw=%s", env_var, raw)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{env_var} must be a boolean")


def load_settings() -> dict[str, Any]:
    logger.debug("load_settings called")
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY is missing - add to environment.")

    whitelist = _json_list("RATE_LIMIT_WHITELIST", [])
    allowed_ws = _json_list("ALLOWED_WS_ORIGINS", ["http://127.0.0.1:5555"])
    allowed_api_cors = _json_list("API_ALLOWED_ORIGINS", [])

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
        # Database
        "DB_PATH": os.getenv("DB_PATH"),
        "DATABASE_URL": os.getenv("DATABASE_URL"),
        # Rate limit whitelist for request_filter
        "whitelist": whitelist,
        # Explicit API CORS allowlist
        "API_ALLOWED_ORIGINS": allowed_api_cors,
        # Backwards-compatibility escape hatch for legacy integrations
        "ALLOW_API_KEY_QUERY_PARAM": _env_bool("ALLOW_API_KEY_QUERY_PARAM", False),
        # Sockets
        "ALLOWED_WS_ORIGINS": allowed_ws,
        # HVAC / Thermostat shared settings
        "THERMOSTAT_LOCATION": os.getenv("THERMOSTAT_LOCATION", "Tietokonepöytä"),
        "ROOM_THERMAL_CAPACITY_J_PER_K": os.getenv("ROOM_THERMAL_CAPACITY_J_PER_K"),
        "HUE_HTTP_TIMEOUT_S": _env_float("HUE_HTTP_TIMEOUT_S", 5.0),
        # YNAB categorizer
        "YNAB_API_KEY": os.getenv("YNAB_API_KEY"),
        "YNAB_BUDGET_ID": os.getenv("YNAB_BUDGET_ID"),
        "YNAB_HTTP_TIMEOUT_S": _env_int("YNAB_HTTP_TIMEOUT_S", 15),
        "YNAB_HTTP_RETRIES": _env_int("YNAB_HTTP_RETRIES", 2),
    }

    logger.debug(
        "load_settings completed db_path_set=%s database_url_set=%s api_cors_origins=%d",
        bool(settings["DB_PATH"]),
        bool(settings["DATABASE_URL"]),
        len(settings["API_ALLOWED_ORIGINS"]),
    )
    return settings
