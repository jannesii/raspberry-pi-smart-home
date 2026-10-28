#!/usr/bin/env python3
"""Manage the one isolated development instance on this host.

    .venv/bin/python scripts/dev.py setup --worktree ~/Code/server-worktrees/feature
    .venv/bin/python scripts/dev.py start | status | stop
    .venv/bin/python scripts/dev.py reset --yes
    .venv/bin/python scripts/dev.py run --env-file ~/.config/jannenkoti-dev/dev.env

Every command reads only the development environment file (default
``~/.config/jannenkoti-dev/dev.env``) and refuses targets shared with the
production environment reference. The app and its tools receive that file plus
a few process basics (PATH, HOME, locale), never the caller's environment or a
checkout's ``jannenkoti.env``. Nothing here restarts or reconfigures host
services; see docs/operations/development-environment.md.

Exit status: 0 success (status: running), 1 operation failed, 2 refused,
3 not running (status), 4 another development operation is in progress.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

from app.environment import (
    APP_ENV_VAR,
    DEVELOPMENT,
    development_problems,
    production_overlap_problems,
)

logger = logging.getLogger("dev")

DEFAULT_ENV_FILE = Path("~/.config/jannenkoti-dev/dev.env").expanduser()
DEFAULT_PRODUCTION_ENV = Path("/home/jannesi/Code/server/jannenkoti.env")
DEFAULT_PORT = "5557"
# The existing Cloudflare Tunnel route to http://localhost:5557.
PUBLIC_URL = "https://dev.jannenkoti.com"
# Only process basics pass through; everything else must come from the env file.
INHERITED_VARIABLES = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")
# A worktree must contain these to enforce development isolation itself.
CONTRACT_FILES = ("app/environment.py", "app/development_sample.py", "run.py")
STATE_MARKER = ".jannenkoti-development"
# Set on the database by the provisioning SQL; reset never touches another database.
DATABASE_MARKER = "jannenkoti development"
# Reset keeps the installed dependencies and this tool's control files.
KEPT_STATE = frozenset({STATE_MARKER, "venv", "instance"})

OK, FAILED, REFUSED, NOT_RUNNING, BUSY = 0, 1, 2, 3, 4


class Refused(Exception):
    """The command would be unsafe or ambiguous; nothing was changed."""

    def __init__(self, *reasons: str):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class Failed(Exception):
    """The command started but could not complete."""


class Busy(Exception):
    """Another lifecycle operation holds the lock."""


def _read_env_file(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        values = dotenv_values(path)
    except OSError:
        return None
    return {key: value for key, value in values.items() if value is not None}


class Config:
    """A validated development configuration and the production reference."""

    def __init__(self, env_file: Path, production_env: Path, *, strict: bool = True):
        dev_values = _read_env_file(env_file)
        if dev_values is None:
            raise Refused(f"cannot read the development environment file {env_file}")
        self.production_checkout = production_env.resolve().parent
        if not strict:
            # Stopping and inspecting only touch processes this tool recorded.
            self.values = {**dev_values, APP_ENV_VAR: DEVELOPMENT}
            self.values.setdefault("PORT", DEFAULT_PORT)
            state = self.values.get("DEV_STATE_DIR", "")
            if not (state and Path(state).is_absolute() and Path(state).is_dir()):
                raise Refused("DEV_STATE_DIR must be an existing absolute directory")
            self.state_dir = Path(state).resolve()
            return
        production_values = _read_env_file(production_env)
        if production_values is None:
            raise Refused(
                f"cannot read the production environment reference {production_env}; "
                "pass --production-env so shared targets can be detected"
            )
        if dev_values.get(APP_ENV_VAR, DEVELOPMENT).strip().lower() != DEVELOPMENT:
            raise Refused(f"{APP_ENV_VAR} in the development environment file must be development")

        values = {**dev_values, APP_ENV_VAR: DEVELOPMENT}
        values.setdefault("PORT", DEFAULT_PORT)
        problems = development_problems(values)
        problems += [
            p for p in production_overlap_problems(values, production_values) if p not in problems
        ]
        if problems:
            raise Refused(*problems)
        self.values = values
        self.state_dir = Path(values["DEV_STATE_DIR"]).resolve()

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.values['PORT']}"

    def process_env(self, **extra: str) -> dict[str, str]:
        env = {name: os.environ[name] for name in INHERITED_VARIABLES if name in os.environ}
        env.update(self.values)
        env.update(extra)
        return env


def _git(worktree: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _checked_worktree(path: Path, config: Config) -> Path:
    worktree = path.expanduser().resolve()
    if worktree == config.production_checkout:
        raise Refused(f"{worktree} is the production checkout; select a feature worktree")
    toplevel = _git(worktree, "rev-parse", "--show-toplevel") if worktree.is_dir() else None
    if toplevel is None or Path(toplevel).resolve() != worktree:
        raise Refused(f"{worktree} is not a git worktree root")
    missing = [name for name in CONTRACT_FILES if not (worktree / name).is_file()]
    if missing:
        raise Refused(
            f"{worktree} predates the development contract (missing {', '.join(missing)}); "
            "merge the development environment changes into it first"
        )
    return worktree


def _check_state_dir(config: Config, worktree: Path | None = None) -> None:
    """Accept only a state directory that setup created or claimed while empty."""
    state = config.state_dir
    marker = state / STATE_MARKER
    if marker.is_file():
        return
    for protected in filter(None, (worktree, config.production_checkout, Path.home())):
        if protected.resolve().is_relative_to(state):
            raise Refused(f"DEV_STATE_DIR must not contain {protected}")
    if worktree is None or any(state.iterdir()):
        raise Refused(
            f"DEV_STATE_DIR {state} is not marked as development state; "
            "use an empty directory for setup"
        )
    marker.write_text("Development state managed by scripts/dev.py; reset deletes it.\n")
    logger.info("Claimed %s as development state", state)


def _database_engine(config: Config):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    return create_engine(config.values["DATABASE_URL"], poolclass=NullPool)


def _check_database(config: Config) -> None:
    """Require the owner's provisioning marker so no other database is ever reset."""
    from sqlalchemy import text

    try:
        with _database_engine(config).connect() as conn:
            name, comment = conn.execute(
                text(
                    "SELECT current_database(), shobj_description(oid, 'pg_database') "
                    "FROM pg_database WHERE datname = current_database()"
                )
            ).one()
    except Exception as exc:
        raise Failed(f"cannot connect to the development database ({type(exc).__name__})") from exc
    if comment != DATABASE_MARKER:
        raise Refused(
            f"database {name} is not marked as the development database; "
            "provision it with docs/operations/development-host/postgresql.sql"
        )


def _run_logged(argv: list[str], *, cwd: Path, env: dict[str, str], log: Path, what: str) -> None:
    with log.open("a") as out:
        out.write(f"--- {what}\n")
        out.flush()
        result = subprocess.run(argv, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        raise Failed(f"{what} failed (exit {result.returncode}); see {log}")


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_dependencies(config: Config, worktree: Path, installer: Path | None) -> None:
    requirements = worktree / "requirements.txt"
    venv = config.state_dir / "venv"
    digest = _file_digest(requirements)
    selection = _read_selection(config)
    if (
        selection
        and selection.get("requirements_sha256") == digest
        and (venv / "bin" / "python").exists()
    ):
        logger.info("Dependencies unchanged; reusing %s", venv)
        return
    env = {name: os.environ[name] for name in INHERITED_VARIABLES if name in os.environ}
    log = _control_dir(config) / "setup.log"
    logger.info("Installing %s into %s", requirements.relative_to(worktree), venv)
    if installer is not None:
        _run_logged(
            [str(installer), str(venv), str(requirements)],
            cwd=worktree,
            env=env,
            log=log,
            what="dependency installation",
        )
    else:
        if not (venv / "bin" / "python").exists():
            _run_logged(
                [sys.executable, "-m", "venv", str(venv)],
                cwd=worktree,
                env=env,
                log=log,
                what="virtual environment creation",
            )
        _run_logged(
            [str(venv / "bin" / "python"), "-m", "pip", "install", "-r", str(requirements)],
            cwd=worktree,
            env=env,
            log=log,
            what="dependency installation",
        )


def _prepare_database(config: Config, worktree: Path) -> None:
    """Migrate the development database and add the idempotent sample data."""
    python = str(config.state_dir / "venv" / "bin" / "python")
    env = config.process_env(USE_SQLA_READS="1")
    log = _control_dir(config) / "setup.log"
    logger.info("Applying migrations from %s", worktree)
    _run_logged(
        [python, "-m", "alembic", "-c", "alembic.ini.example", "upgrade", "head"],
        cwd=worktree,
        env=env,
        log=log,
        what="migration",
    )
    _run_logged(
        [python, "-m", "app.development_sample"],
        cwd=worktree,
        env=env,
        log=log,
        what="sample data",
    )


def _control_dir(config: Config) -> Path:
    path = config.state_dir / "instance"
    path.mkdir(exist_ok=True)
    return path


def _read_selection(config: Config) -> dict | None:
    try:
        return json.loads((config.state_dir / "instance" / "selection.json").read_text())
    except (OSError, ValueError):
        return None


def _revision(worktree: Path) -> str:
    branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") or "unknown branch"
    commit = _git(worktree, "rev-parse", "--short", "HEAD") or "unknown commit"
    dirty = " + uncommitted changes" if _git(worktree, "status", "--porcelain") else ""
    return f"{branch} @ {commit}{dirty}"


def _describe_worktree(worktree: Path) -> str:
    return f"{worktree} ({_revision(worktree)})"


@contextmanager
def _exclusive(config: Config, operation: str):
    """Hold the lifecycle lock so setup/start/stop/reset never interleave."""
    with (_control_dir(config) / "lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            raise Busy(handle.read().strip() or "another operation") from None
        handle.seek(0)
        handle.truncate()
        handle.write(operation)
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle, fcntl.LOCK_UN)


def _busy_with(config: Config) -> str | None:
    path = config.state_dir / "instance" / "lock"
    if not path.exists():
        return None
    with path.open("r") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return handle.read().strip() or "another operation"
        fcntl.flock(handle, fcntl.LOCK_UN)
    return None


# --- Processes this tool started ------------------------------------------------


def _start_time(pid: int) -> str | None:
    """The kernel start time identifies a process even if its PID is reused."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[1].split()
    if fields[0] in ("Z", "X"):
        return None
    return fields[19]


def _record_process(config: Config, name: str, pid: int) -> None:
    record = {"pid": pid, "start_time": _start_time(pid)}
    (_control_dir(config) / f"{name}.json").write_text(json.dumps(record))


def _live_process(config: Config, name: str) -> int | None:
    try:
        record = json.loads((config.state_dir / "instance" / f"{name}.json").read_text())
    except (OSError, ValueError):
        return None
    pid = record.get("pid")
    if isinstance(pid, int) and record.get("start_time") == _start_time(pid):
        return pid
    return None


def _stop_process(config: Config, name: str) -> bool:
    pid = _live_process(config, name)
    if pid is not None:
        logger.info("Stopping development %s (pid %d)", name, pid)
        for sig, wait in ((signal.SIGTERM, 30.0), (signal.SIGKILL, 10.0)):
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + wait
            while _live_process(config, name) == pid and time.monotonic() < deadline:
                time.sleep(0.2)
            if _live_process(config, name) is None:
                break
        if _live_process(config, name) is not None:
            raise Failed(f"development {name} (pid {pid}) did not stop")
    (config.state_dir / "instance" / f"{name}.json").unlink(missing_ok=True)
    return pid is not None


def _port_in_use(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _managed_redis_port(config: Config) -> int:
    """start runs development's own Redis; it never attaches to an existing server."""
    parts = urlsplit(config.values["REDIS_URL"])
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme != "redis" or parts.hostname not in ("127.0.0.1", "localhost") or not port:
        raise Refused(
            "REDIS_URL must be redis://127.0.0.1:<port> so start can run development's own Redis"
        )
    if parts.password:
        raise Refused("REDIS_URL must not contain a password; development Redis is local-only")
    return port


def _http_ready(url: str, timeout: float = 3) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/login", timeout=timeout) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def _redis_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
            sock.sendall(b"PING\r\n")
            return sock.recv(16).startswith(b"+PONG")
    except OSError:
        return False


def _selected_worktree(config: Config) -> Path | None:
    selection = _read_selection(config)
    return Path(selection["worktree"]) if selection and selection.get("worktree") else None


def _launch(argv: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> int:
    with log.open("a") as out:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid


# --- Commands -------------------------------------------------------------------


def setup(args: argparse.Namespace) -> int:
    config = Config(args.env_file, args.production_env)
    worktree = _checked_worktree(args.worktree, config)
    _check_database(config)
    _check_state_dir(config, worktree)
    with _exclusive(config, "setup"):
        if _live_process(config, "app"):
            raise Refused("development is running; stop it before selecting a worktree")
        _install_dependencies(config, worktree, args.installer)
        _prepare_database(config, worktree)
        selection = {
            "worktree": str(worktree),
            "requirements_sha256": _file_digest(worktree / "requirements.txt"),
        }
        (_control_dir(config) / "selection.json").write_text(json.dumps(selection))
    print(f"Development worktree selected: {_describe_worktree(worktree)}")
    print(f"Sample data ready; log in as {config.values['WEB_USERNAME']}.")
    return OK


def start(args: argparse.Namespace) -> int:
    config = Config(args.env_file, args.production_env)
    _check_state_dir(config)
    with _exclusive(config, "start"):
        selected = _selected_worktree(config)
        if selected is None:
            raise Refused("no worktree is selected; run setup --worktree PATH first")
        worktree = _checked_worktree(selected, config)
        if _live_process(config, "app"):
            raise Refused(f"development is already running from {_describe_worktree(worktree)}")
        selection = _read_selection(config) or {}
        python = config.state_dir / "venv" / "bin" / "python"
        digest = _file_digest(worktree / "requirements.txt")
        if not python.exists() or selection.get("requirements_sha256") != digest:
            raise Refused("dependencies changed since setup; run setup again for this worktree")
        redis_port = _managed_redis_port(config)
        port = int(config.values["PORT"])
        for busy_port in (port, redis_port):
            if _port_in_use(busy_port):
                raise Refused(f"port {busy_port} is already in use on this host")
        redis_server = shutil.which("redis-server")
        if redis_server is None:
            raise Failed("redis-server is not installed")
        control = _control_dir(config)

        basics = {name: os.environ[name] for name in INHERITED_VARIABLES if name in os.environ}
        _stop_process(config, "redis")  # a leftover from an interrupted start
        redis_pid = _launch(
            [
                redis_server,
                "--port",
                str(redis_port),
                "--bind",
                "127.0.0.1",
                "--protected-mode",
                "yes",
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                str(control),
            ],
            cwd=control,
            env=basics,
            log=control / "redis.log",
        )
        _record_process(config, "redis", redis_pid)
        deadline = time.monotonic() + 10
        while not _redis_ready(redis_port):
            if time.monotonic() > deadline or not _live_process(config, "redis"):
                _stop_process(config, "redis")
                raise Failed(f"development Redis did not start; see {control / 'redis.log'}")
            time.sleep(0.1)

        label = _describe_worktree(worktree)
        logger.info("Starting development app from %s", label)
        # Only the branch and commit reach the page; the path stays local.
        app_pid = _launch(
            [str(python), *_gunicorn_args(f"127.0.0.1:{port}")],
            cwd=worktree,
            env=config.process_env(USE_SQLA_READS="1", DEV_WORKTREE_LABEL=_revision(worktree)),
            log=control / "app.log",
        )
        _record_process(config, "app", app_pid)
        deadline = time.monotonic() + args.timeout
        while not _http_ready(config.local_url):
            if time.monotonic() > deadline or not _live_process(config, "app"):
                _stop_process(config, "app")
                _stop_process(config, "redis")
                raise Failed(f"development app did not become ready; see {control / 'app.log'}")
            time.sleep(0.5)
    print(f"Development running: {label}")
    print(f"Local: {config.local_url}  Public: {PUBLIC_URL}")
    return OK


def _wipe_database(config: Config) -> None:
    from sqlalchemy import text

    with _database_engine(config).begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def _wipe_state(config: Config) -> None:
    for entry in config.state_dir.iterdir():
        if entry.name in KEPT_STATE:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def reset(args: argparse.Namespace) -> int:
    if not args.yes:
        raise Refused("reset deletes all development data and files; repeat with --yes")
    config = Config(args.env_file, args.production_env)
    _check_state_dir(config)
    with _exclusive(config, "reset"):
        if _live_process(config, "app") or _live_process(config, "redis"):
            raise Refused("development is running; stop it before reset")
        selected = _selected_worktree(config)
        if selected is None:
            raise Refused("no worktree is selected; run setup --worktree PATH first")
        worktree = _checked_worktree(selected, config)
        if not (config.state_dir / "venv" / "bin" / "python").exists():
            raise Refused("development dependencies are missing; run setup again")
        _check_database(config)
        logger.info("Deleting development database objects and state files")
        _wipe_database(config)
        _wipe_state(config)
        # Redis is never persisted, so nothing queued before the reset can be replayed.
        _prepare_database(config, worktree)
    print(f"Development reset to the sample state using {_describe_worktree(worktree)}")
    return OK


def stop(args: argparse.Namespace) -> int:
    config = Config(args.env_file, args.production_env, strict=False)
    _check_state_dir(config)
    with _exclusive(config, "stop"):
        stopped_app = _stop_process(config, "app")
        stopped_redis = _stop_process(config, "redis")
    print(
        "Development stopped." if stopped_app or stopped_redis else "Development was not running."
    )
    return OK


def status(args: argparse.Namespace) -> int:
    config = Config(args.env_file, args.production_env, strict=False)
    _check_state_dir(config)
    worktree = _selected_worktree(config)
    print(f"Environment: {DEVELOPMENT}")
    print(f"Worktree: {_describe_worktree(worktree) if worktree else 'none selected (run setup)'}")
    busy = _busy_with(config)
    if busy:
        print(f"State: busy ({busy} in progress)")
        return BUSY
    app_pid = _live_process(config, "app")
    redis_pid = _live_process(config, "redis")
    if app_pid is None and redis_pid is None:
        print("State: stopped")
        return NOT_RUNNING
    healthy = app_pid is not None and redis_pid is not None and _http_ready(config.local_url)
    print(f"State: {'running' if healthy else 'unhealthy'}")
    print(f"App pid: {app_pid or 'not running'}; Redis pid: {redis_pid or 'not running'}")
    print(f"Local: {config.local_url}  Public: {PUBLIC_URL}")
    return OK if healthy else FAILED


def run(args: argparse.Namespace) -> int:
    """Run the development app of this checkout in the foreground (no lifecycle)."""
    config = Config(args.env_file, args.production_env)
    env = config.process_env()
    bind = f"127.0.0.1:{env['PORT']}"
    logger.info("Starting development app from %s on http://%s", ROOT, bind)
    os.chdir(ROOT)
    argv = [sys.executable, *_gunicorn_args(bind)]
    os.execve(sys.executable, argv, env)
    return OK  # pragma: no cover - execve does not return


def _gunicorn_args(bind: str) -> list[str]:
    return [
        "-m",
        "gunicorn",
        "--workers",
        "1",
        "--worker-class",
        "eventlet",
        "--bind",
        bind,
        "--timeout",
        "120",
        "run:app",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str, *, env_required: bool = False):
        sub = commands.add_parser(name, help=help_text)
        if env_required:
            sub.add_argument("--env-file", type=Path, required=True)
        else:
            sub.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
        sub.add_argument("--production-env", type=Path, default=DEFAULT_PRODUCTION_ENV)
        return sub

    setup_parser = command("setup", "select a worktree and prepare its dependencies and data")
    setup_parser.add_argument("--worktree", type=Path, required=True)
    setup_parser.add_argument(
        "--installer",
        type=Path,
        help="executable called as INSTALLER VENV_DIR REQUIREMENTS (default: venv + pip)",
    )
    start_parser = command("start", "start the selected worktree in the background")
    start_parser.add_argument("--timeout", type=float, default=120, help="seconds to wait")
    command("stop", "stop the development app and its Redis")
    reset_parser = command("reset", "return the stopped instance to the sample state")
    reset_parser.add_argument("--yes", action="store_true", help="confirm deleting dev data")
    command("status", "show the selected worktree and whether development is running")
    command("run", "run this checkout's development app in the foreground", env_required=True)
    return parser


HANDLERS = {
    "setup": setup,
    "start": start,
    "stop": stop,
    "status": status,
    "reset": reset,
    "run": run,
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[dev] %(message)s")
    try:
        return HANDLERS[args.command](args)
    except Refused as refused:
        print(f"Development {args.command} refused:", file=sys.stderr)
        for reason in refused.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return REFUSED
    except Failed as failed:
        print(f"Development {args.command} failed: {failed}", file=sys.stderr)
        return FAILED
    except Busy as busy:
        print(f"Development {args.command} refused: {busy} is in progress", file=sys.stderr)
        return BUSY


if __name__ == "__main__":
    sys.exit(main())
