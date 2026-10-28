"""Bounded PostgreSQL migration/backup operations for the release command.

No application startup is imported. Connection credentials are passed through
child environments, and subprocess output is never echoed to operator logs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


class DatabaseFailure(Exception):
    pass


def database_url(environment: Path) -> str:
    values = dotenv_values(environment)
    value = values.get("DATABASE_URL")
    if not value or not value.startswith("postgresql"):
        raise DatabaseFailure("an explicit PostgreSQL DATABASE_URL is required")
    return value


def disposable_url(environment: Path, production: str) -> str:
    values = dotenv_values(environment)
    url = database_url(environment)
    parsed = make_url(url)
    if values.get("RELEASE_DISPOSABLE") != "yes":
        raise DatabaseFailure("disposable environment requires RELEASE_DISPOSABLE=yes")
    # Resolve connection aliases conservatively; disposable targets must use a
    # named private socket, never a TCP connection or the system socket.
    host = str(parsed.query.get("host", ""))
    if (
        not host.startswith("/")
        or host.startswith(("/run/", "/var/run/"))
        or not parsed.database
        or not parsed.database.startswith("release_test_")
        or make_url(production).database == parsed.database
    ):
        raise DatabaseFailure(
            "disposable target must use a private socket and release_test_ database"
        )
    return url


def run(command: list[str], *, url: str, cwd: Path | None = None) -> None:
    parsed = make_url(url)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DATABASE_URL": url,
        "PGHOST": str(parsed.query.get("host") or parsed.host or "localhost"),
        "PGPORT": str(parsed.port or parsed.query.get("port") or 5432),
        "PGDATABASE": parsed.database or "",
        "PGUSER": parsed.username or os.environ.get("USER", ""),
        "PGPASSWORD": parsed.password or "",
        "PGCONNECT_TIMEOUT": "10",
        "PGOPTIONS": "-c statement_timeout=120000 -c lock_timeout=10000",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        raise DatabaseFailure(f"{Path(command[0]).name} unavailable or timed out") from None
    if result.returncode:
        raise DatabaseFailure(
            f"{Path(command[0]).name} failed; inspect database state before recovery"
        )


def connection(url: str):
    return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})


def revisions(url: str) -> list[str]:
    try:
        with connection(url).connect() as conn:
            return sorted(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
    except Exception:
        raise DatabaseFailure("cannot establish database baseline") from None


def require_empty(url: str) -> None:
    try:
        with connection(url).connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
                    "AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','v','m','S','f')"
                )
            ).scalar()
    except Exception:
        raise DatabaseFailure("cannot establish disposable database is empty") from None
    if tables:
        raise DatabaseFailure("disposable database must be empty; use a fresh target")


def migrate(directory: Path, url: str, revision: str = "head") -> None:
    code = directory / "code"
    if not (code / "migrations" / "env.py").is_file():
        raise DatabaseFailure("candidate lacks executable Alembic migration infrastructure")
    # Config is constructed in the child, avoiding ignored credential-bearing ini files.
    runner = (
        "from alembic.config import Config; from alembic import command; "
        "c=Config(); c.set_main_option('script_location','migrations'); "
        f"command.upgrade(c, {revision!r})"
    )
    run([str(directory / "venv/bin/python"), "-c", runner], url=url, cwd=code)


def dump(url: str, target: Path) -> None:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        run(["pg_dump", "--format=custom", "--no-owner", "--file", str(target)], url=url)
    except DatabaseFailure:
        target.unlink(missing_ok=True)
        raise


def restore(url: str, backup: Path) -> None:
    run(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--dbname",
            make_url(url).database,
            str(backup),
        ],
        url=url,
    )


def signature(url: str) -> str:
    """Logical dump comparison includes synthetic rows as well as schema."""
    import hashlib
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "state.sql"
        run(["pg_dump", "--no-owner", "--no-privileges", "--file", str(path)], url=url)
        lines = [
            line
            for line in path.read_text().splitlines()
            if not line.startswith(("--", "\\restrict", "\\unrestrict"))
        ]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def identity(url: str) -> str:
    import hashlib

    parsed = make_url(url).set(password=None)
    return hashlib.sha256(parsed.render_as_string(hide_password=False).encode()).hexdigest()


def replace(url: str, backup: Path) -> None:
    # pg_restore --clean removes only objects present in the old backup, leaving
    # newly migrated tables behind. Recreate public explicitly for an exact reset.
    try:
        with connection(url).begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            conn.execute(text("SET LOCAL statement_timeout = '120s'"))
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        restore(url, backup)
    except DatabaseFailure:
        raise
    except Exception:
        raise DatabaseFailure("database replacement failed or partially completed") from None
