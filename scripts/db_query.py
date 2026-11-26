"""Bounded database queries against an explicitly selected target.

    db_query.py production "SELECT count(*) FROM sensor_locations"
    db_query.py development --max-rows 20 < query.sql

``production`` connects through an SSH tunnel (default host ``raspi``) as the
dedicated read-only role, and refuses credentials that can write or administer
the database. ``development`` connects directly to a separately configured local
database and commits writes. Credentials come only from a private file outside
the repository (default ``~/.config/jannenkoti-ops/database.env``); application
environment files, ``DATABASE_URL`` and libpq defaults are never consulted.

Rows print as tab-separated text with a header. Row counts, truncation and
failures go to stderr. Exit status: 0 done, 1 query or connection failure,
2 refused arguments, configuration or credentials.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CREDENTIALS = Path("~/.config/jannenkoti-ops/database.env")
TARGETS = ("development", "production")
TUNNEL_WAIT = 15.0


class Refused(Exception):
    """The command will not run with these arguments or this configuration."""


class Failed(Exception):
    """The connection or query failed."""


def bounded(low: float, high: float, kind=int):
    def parse(value: str):
        try:
            number = kind(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be between {low:g} and {high:g}")
        return number

    return parse


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bounded query against an explicitly selected database."
    )
    parser.add_argument("target", choices=TARGETS, help="database to query")
    parser.add_argument("sql", nargs="?", help="SQL text; read from stdin when omitted or '-'")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS,
        help=f"private credential file (default {DEFAULT_CREDENTIALS})",
    )
    parser.add_argument(
        "--timeout",
        type=bounded(0.1, 300, float),
        default=15.0,
        help="statement timeout in seconds (default 15)",
    )
    parser.add_argument(
        "--max-rows", type=bounded(1, 10000), default=100, help="rows to print (default 100)"
    )
    parser.add_argument(
        "--max-bytes",
        type=bounded(256, 1_048_576),
        default=65536,
        help="output bytes to print (default 65536)",
    )
    parser.add_argument(
        "--max-cell",
        type=bounded(8, 100_000),
        default=500,
        help="characters per cell (default 500)",
    )
    return parser.parse_args(argv)


# --- Configuration -------------------------------------------------------------


def load_credentials(path: Path) -> dict[str, str]:
    path = path.expanduser()
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise Refused(f"credential file {path} does not exist") from None
    if not stat.S_ISREG(info.st_mode):
        raise Refused(f"credential file {path} must be a regular file, not a link")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Refused(f"credential file {path} must be owned by you and private (chmod 600)")
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise Refused("credential file must be outside the repository")
    return {key: value for key, value in dotenv_values(resolved).items() if value}


def loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_url(value: str | None, key: str) -> URL:
    if not value:
        raise Refused(f"{key} is not configured in the credential file")
    try:
        url = make_url(value)
    except ArgumentError:
        raise Refused(f"{key} is not a database URL") from None
    if url.get_backend_name() != "postgresql" or not url.database:
        raise Refused(f"{key} must be a PostgreSQL URL naming a database")
    return url


@dataclass(frozen=True)
class Target:
    """A validated database to query and how to reach it."""

    name: str
    url: URL
    read_only: bool
    ssh_host: str | None = None

    @property
    def secrets(self) -> tuple[str | None, ...]:
        return (self.url.password, self.url.render_as_string(hide_password=False))


def production_target(config: dict[str, str]) -> Target:
    url = parse_url(config.get("PRODUCTION_DATABASE_URL"), "PRODUCTION_DATABASE_URL")
    if not url.username or not url.password:
        raise Refused("PRODUCTION_DATABASE_URL must name the read-only role and its password")
    if url.username == "postgres":
        raise Refused("PRODUCTION_DATABASE_URL must use the read-only role, not an administrator")
    # The address is resolved on the Pi through the tunnel: PostgreSQL stays local there.
    if "host" in url.query or not loopback(url.host or ""):
        raise Refused("PRODUCTION_DATABASE_URL must use the Pi's loopback TCP address")
    ssh_host = config.get("PRODUCTION_SSH_HOST", "raspi")
    if ssh_host.startswith("-") or not ssh_host.strip():
        raise Refused("PRODUCTION_SSH_HOST is not a valid SSH destination")
    return Target("production", url, read_only=True, ssh_host=ssh_host)


def development_target(config: dict[str, str]) -> Target:
    url = parse_url(config.get("DEVELOPMENT_DATABASE_URL"), "DEVELOPMENT_DATABASE_URL")
    socket_dir = url.query.get("host")
    local = (
        isinstance(socket_dir, str) and socket_dir.startswith("/")
        if socket_dir is not None
        else loopback(url.host or "")
    )
    if not local:
        raise Refused(
            "DEVELOPMENT_DATABASE_URL must use a loopback address or local socket directory"
        )
    port = url.query.get("port")
    if port is not None and not str(port).isdigit():
        raise Refused("DEVELOPMENT_DATABASE_URL has an invalid port")
    if config.get("PRODUCTION_DATABASE_URL"):
        # A loopback port may itself be a forward to the Pi, so the address alone
        # cannot prove isolation: also keep the database and role names apart.
        production = parse_url(config["PRODUCTION_DATABASE_URL"], "PRODUCTION_DATABASE_URL")
        if production.username == url.username:
            raise Refused("development and production must use different database roles")
        if production.database == url.database:
            raise Refused("development must use a different database name than production")
    return Target("development", url, read_only=False)


# --- Connection ------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def tunnel(ssh_host: str, remote_host: str, remote_port: int) -> Iterator[int]:
    port = free_port()
    command = [
        "ssh",
        "-N",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-L",
        f"127.0.0.1:{port}:{remote_host}:{remote_port}",
        "--",
        ssh_host,
    ]
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
    except OSError:
        raise Failed("ssh is unavailable; cannot reach production") from None
    try:
        deadline = time.monotonic() + TUNNEL_WAIT
        while True:
            if process.poll() is not None:
                detail = (
                    (process.stderr.read() if process.stderr else b"")
                    .decode(errors="replace")
                    .strip()
                )
                last = detail.splitlines()[-1] if detail else f"exit status {process.returncode}"
                raise Failed(f"SSH tunnel through {ssh_host} failed: {last}")
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.5):
                break
            if time.monotonic() > deadline:
                raise Failed(f"SSH tunnel through {ssh_host} did not open within {TUNNEL_WAIT:g}s")
            time.sleep(0.1)
        yield port
    finally:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stderr:
            process.stderr.close()


@contextlib.contextmanager
def endpoint(target: Target) -> Iterator[tuple[str, int | None]]:
    """Yield the local host and port that reach the target's database."""
    url = target.url
    if target.ssh_host is not None:
        with tunnel(target.ssh_host, url.host or "127.0.0.1", url.port or 5432) as port:
            yield "127.0.0.1", port
        return
    socket_port = url.query.get("port")
    yield (
        str(url.query.get("host") or url.host),
        url.port or (int(str(socket_port)) if socket_port else None),
    )


def connect(target: Target, *, host: str, port: int | None, timeout: float):
    import psycopg

    url, read_only = target.url, target.read_only

    # Only the explicit URL may supply connection settings or credentials.
    for key in [key for key in os.environ if key.startswith("PG")]:
        del os.environ[key]
    options = (
        f"-c statement_timeout={int(timeout * 1000)} -c idle_in_transaction_session_timeout=60000"
    )
    if read_only:
        options += " -c default_transaction_read_only=on"
    params: dict[str, str | int | None] = {
        "host": host,
        "dbname": url.database or "",
        "user": url.username or "",
        "password": url.password or "",
        "connect_timeout": 10,
        "options": options,
        "passfile": os.devnull,
        "application_name": "jannenkoti-db-query",
    }
    if port:
        params["port"] = port
    conn = psycopg.connect(psycopg.conninfo.make_conninfo("", **params))
    conn.read_only = read_only
    return conn


def require_read_only_role(conn) -> None:
    """Refuse production credentials that could write or administer the database."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
                     OR r.rolbypassrls,
                   EXISTS (SELECT FROM pg_auth_members m WHERE m.member = r.oid),
                   has_database_privilege(current_database(), 'CREATE')
                     OR EXISTS (
                       SELECT FROM pg_namespace s
                       WHERE s.nspname NOT LIKE 'pg\\_%' AND s.nspname <> 'information_schema'
                         AND has_schema_privilege(s.oid, 'CREATE')),
                   EXISTS (
                     SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                       AND n.nspname NOT LIKE 'pg_toast%'
                       AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
                       AND (c.relowner = r.oid
                         OR (c.relkind <> 'S' AND has_table_privilege(
                               c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
                         OR (c.relkind = 'S' AND has_sequence_privilege(c.oid, 'USAGE,UPDATE'))))
            FROM pg_roles r WHERE r.rolname = current_user
            """
        )
        row = cur.fetchone()
    conn.rollback()
    if row is None or any(row):
        raise Refused(
            "production credentials are not the read-only role (they can write, create, "
            "administer or assume other roles); refusing"
        )


# --- Output ----------------------------------------------------------------------


def format_cell(value, limit: int) -> str:
    if value is None:
        return "NULL"
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Output:
    def __init__(self, max_bytes: int):
        self.remaining = max_bytes
        self.truncated = False

    def line(self, text: str) -> bool:
        size = len(text.encode()) + 1
        if size > self.remaining:
            self.truncated = True
            return False
        self.remaining -= size
        sys.stdout.write(text + "\n")
        return True


def print_results(cur, args, out: Output) -> None:
    """Print each result set of the executed SQL within the shared limits.

    Printing stops at the first byte-limit cut, so output never resumes with a
    partial later result set.
    """
    while True:
        if cur.description is None:
            note(cur.statusmessage or "OK")
        else:
            header = "\t".join(format_cell(c.name, args.max_cell) for c in cur.description)
            printed, more = 0, False
            if out.line(header):
                rows = cur.fetchmany(args.max_rows + 1)
                more = len(rows) > args.max_rows
                for row in rows[: args.max_rows]:
                    if not out.line("\t".join(format_cell(v, args.max_cell) for v in row)):
                        break
                    printed += 1
            total = cur.rowcount
            if more or out.truncated:
                of = f"{total}" if total is not None and total >= 0 else "more"
                note(f"truncated: printed {printed} of {of} rows (limit --max-rows/--max-bytes)")
            else:
                note(f"({printed} row{'s' if printed != 1 else ''})")
        if out.truncated:
            if cur.nextset():
                note("truncated: later result sets not printed (limit --max-bytes)")
            return
        if not cur.nextset():
            return


def note(text: str) -> None:
    print(text, file=sys.stderr)


# --- Command ---------------------------------------------------------------------


def scrub(message: str, *secrets: str | None) -> str:
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return message


def failure(exc, target: str, secrets: tuple[str | None, ...]) -> Failed:
    import psycopg

    if isinstance(exc, psycopg.errors.QueryCanceled):
        return Failed(f"{target}: query cancelled by the --timeout limit")
    diag = getattr(exc, "diag", None)
    primary = getattr(diag, "message_primary", None) if diag else None
    if isinstance(exc, psycopg.OperationalError) and not primary:
        lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
        primary = lines[-1] if lines else "connection failed"
        return Failed(scrub(f"{target}: cannot connect: {primary}", *secrets))
    state = f" ({exc.sqlstate})" if getattr(exc, "sqlstate", None) else ""
    return Failed(
        scrub(f"{target}: query failed{state}: {primary or type(exc).__name__}", *secrets)
    )


def run(args: argparse.Namespace) -> None:
    import psycopg

    sql = args.sql if args.sql not in (None, "-") else sys.stdin.read()
    if not sql.strip():
        raise Refused("no SQL given")
    config = load_credentials(args.credentials)
    target = (production_target if args.target == "production" else development_target)(config)
    with endpoint(target) as (host, port):
        try:
            conn = connect(target, host=host, port=port, timeout=args.timeout)
        except psycopg.Error as exc:
            raise failure(exc, target.name, target.secrets) from None
        with conn:
            try:
                if target.read_only:
                    require_read_only_role(conn)
                # statement_timeout is a session setting the SQL itself could lift;
                # the client-side cancel bounds the whole run regardless.
                watchdog = threading.Timer(args.timeout, conn.cancel_safe)
                watchdog.daemon = True
                watchdog.start()
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql.encode())
                        print_results(cur, args, Output(args.max_bytes))
                finally:
                    watchdog.cancel()
                if target.read_only:
                    conn.rollback()
                else:
                    conn.commit()
            except psycopg.Error as exc:
                raise failure(exc, target.name, target.secrets) from None


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        run(args)
    except Refused as exc:
        note(f"refused: {exc}")
        return 2
    except Failed as exc:
        note(str(exc))
        return 1
    except Exception as exc:  # Never print a traceback that could carry connection details.
        note(f"{args.target}: unexpected {type(exc).__name__}; no details shown")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
