"""Runtime environment selection and the development isolation contract.

``APP_ENV`` selects the environment. An unset value keeps the existing
production behavior; ``development`` turns on the safe development policy and
requires explicit, isolated targets (see ADR 0004). Development refuses to start
rather than fall back to production connection defaults.

This module only reads configuration; importing it has no side effects.
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

APP_ENV_VAR = "APP_ENV"
PRODUCTION = "production"
DEVELOPMENT = "development"

# Production has always used this instance implicitly; development must not.
PRODUCTION_REDIS_URL = "redis://localhost:6379"
PRODUCTION_PORTS = frozenset({5555, 5556})
# The existing Cloudflare Tunnel routes this origin to http://localhost:5557.
DEVELOPMENT_PUBLIC_ORIGIN = "https://dev.jannenkoti.com"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


class EnvironmentConfigError(RuntimeError):
    """Raised when the selected environment's configuration is unsafe."""


def selected_environment(environ: Mapping[str, str] | None = None) -> str:
    raw = (environ if environ is not None else os.environ).get(APP_ENV_VAR, "")
    value = raw.strip().lower()
    if value in ("", PRODUCTION):
        return PRODUCTION
    if value == DEVELOPMENT:
        return DEVELOPMENT
    raise EnvironmentConfigError(f"{APP_ENV_VAR} must be '{PRODUCTION}' or '{DEVELOPMENT}'")


def is_development() -> bool:
    return selected_environment() == DEVELOPMENT


def _canonical_host(host: str) -> str:
    """Name every way of reaching this machine "localhost", so aliases still match.

    Unix-socket directories, the hostname and LAN addresses all reach the same
    local servers as ``localhost``; an address this host can bind is local.
    """
    host = host.lower()
    if host in _LOCAL_HOSTS or host.startswith("/"):
        return "localhost"
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return host
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.bind((address, 0))
        except OSError:
            continue
        return "localhost"
    return host


def _redis_endpoint(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    if parts.scheme == "unix":
        return ("unix", parts.path, None)
    return ("tcp", _canonical_host(parts.hostname or ""), parts.port or 6379)


def _database_endpoint(url: str):
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    host = parsed.host or parsed.query.get("host") or ""
    if isinstance(host, tuple):
        host = host[0]
    return parsed, (_canonical_host(host), parsed.port or 5432)


def _check_redis(name: str, url: str, production_url: str) -> str | None:
    try:
        endpoint = _redis_endpoint(url)
    except ValueError:
        return f"{name} is not a valid Redis URL"
    if endpoint in (_redis_endpoint(PRODUCTION_REDIS_URL), _redis_endpoint(production_url)):
        # Pub/Sub ignores logical database numbers, so a different /N is not isolation.
        return f"{name} targets the production Redis instance; use a separate Redis server"
    return None


def development_problems(values: Mapping[str, str | None]) -> list[str]:
    """Return why ``values`` are not a self-contained development configuration."""
    problems: list[str] = []

    def required(name: str) -> str | None:
        value = (values.get(name) or "").strip()
        if not value:
            problems.append(f"{name} is required in development")
            return None
        return value

    database_url = required("DATABASE_URL")
    if database_url and not database_url.startswith("postgresql"):
        problems.append("DATABASE_URL must point at a dedicated PostgreSQL development database")

    redis_url = required("REDIS_URL")
    if redis_url:
        problem = _check_redis("REDIS_URL", redis_url, PRODUCTION_REDIS_URL)
        if problem:
            problems.append(problem)
    rate_limit_url = (values.get("RATE_LIMIT_STORAGE_URI") or "").strip()
    if rate_limit_url and not rate_limit_url.startswith("memory://"):
        problem = _check_redis("RATE_LIMIT_STORAGE_URI", rate_limit_url, PRODUCTION_REDIS_URL)
        if problem:
            problems.append(problem)

    for name in ("SECRET_KEY", "WEB_USERNAME", "WEB_PASSWORD"):
        required(name)

    port = (values.get("PORT") or "").strip()
    if port:
        if not port.isdigit():
            problems.append("PORT must be an integer")
        elif int(port) in PRODUCTION_PORTS:
            problems.append("PORT must not be a production service port")

    state_dir = required("DEV_STATE_DIR")
    state_path = Path(state_dir).resolve() if state_dir else None
    if state_dir and not (Path(state_dir).is_absolute() and Path(state_dir).is_dir()):
        problems.append("DEV_STATE_DIR must be an existing absolute directory")
        state_path = None
    for name in ("DB_PATH", "VILLENKOTI_DB_PATH"):
        path = required(name)
        if path and state_path is not None and not Path(path).resolve().is_relative_to(state_path):
            problems.append(f"{name} must be inside DEV_STATE_DIR")
    return problems


def production_overlap_problems(
    development: Mapping[str, str | None], production: Mapping[str, str | None]
) -> list[str]:
    """Return development settings that would reuse production targets or secrets."""
    problems: list[str] = []

    dev_db = development.get("DATABASE_URL") or ""
    prod_db = production.get("DATABASE_URL") or ""
    if dev_db.startswith("postgresql") and prod_db.startswith("postgresql"):
        try:
            dev_url, dev_server = _database_endpoint(dev_db)
            prod_url, prod_server = _database_endpoint(prod_db)
        except Exception:
            problems.append("DATABASE_URL could not be compared with production")
        else:
            if dev_server == prod_server and dev_url.database == prod_url.database:
                problems.append("DATABASE_URL targets the production database")
            elif dev_url.username and dev_url.username == prod_url.username:
                problems.append("DATABASE_URL uses the production database role")

    prod_redis = production.get("REDIS_URL") or PRODUCTION_REDIS_URL
    for name in ("REDIS_URL", "RATE_LIMIT_STORAGE_URI"):
        url = (development.get(name) or "").strip()
        if url and not url.startswith("memory://"):
            problem = _check_redis(name, url, prod_redis)
            if problem and problem not in problems:
                problems.append(problem)

    for name in ("SECRET_KEY", "WEB_PASSWORD"):
        value = development.get(name)
        if value and value == production.get(name):
            problems.append(f"{name} must differ from production")

    for name in ("DB_PATH", "VILLENKOTI_DB_PATH"):
        dev_path = development.get(name)
        prod_path = production.get(name)
        if dev_path and prod_path and Path(dev_path).resolve() == Path(prod_path).resolve():
            problems.append(f"{name} must differ from production")
    return problems
