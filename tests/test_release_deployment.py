"""Deploying, inspecting and recovering prepared releases.

Tests drive ``scripts/release.py`` as a subprocess against temporary releases
prepared from a temporary Git checkout, a disposable PostgreSQL cluster on a
private socket and a stand-in production environment. The service manager is a
recording fake passed explicitly with ``--service-manager``: it starts the
candidate's stand-in app and gateway as isolated processes from the release
root's ``current`` link, the way the production units do. The real
``systemctl``/``sudo`` never run, and nothing listens on production ports.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import textwrap
import time
import uuid
from pathlib import Path

import pytest
from test_release_preparation import commit_all, git

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "release.py"
PRODUCTION_SECRET = "production-secret-value"
APP_UNIT, GATEWAY_UNIT = "jannenkoti.service", "esp32_ws.service"

STAND_IN_APP = '''\
"""Stand-in production app serving the readiness contract of the real app."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

import candidate
from app.release_identity import release_revision

REVISION = release_revision(Path(__file__).resolve().parent)


def database_state():
    url = make_url(os.environ["DATABASE_URL"])
    if candidate.DATABASE:
        url = url.set(database=candidate.DATABASE)
    try:
        with create_engine(url, poolclass=NullPool).connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return "unavailable"
    return "ok"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        state = database_state()
        body = json.dumps({"revision": REVISION, "database": state}).encode()
        self.send_response(200 if state == "ok" else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
'''

STAND_IN_GATEWAY = '''\
"""Stand-in ESP32 gateway serving the readiness contract of esp32_ws/main.py."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    RECORD = json.loads((Path(__file__).resolve().parents[2] / "release.json").read_text())
    REVISION = RECORD.get("revision")
except (OSError, ValueError):
    REVISION = None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "healthy", "revision": REVISION}).encode()
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", int(os.environ["ESP32_WS_PORT"])), Handler).serve_forever()
'''

FAKE_SYSTEMCTL = '''\
#!{python}
"""Recording stand-in for systemctl that runs the stand-in units from `current`."""
import json, os, signal, subprocess, sys, time
from pathlib import Path

CONFIG = json.loads({config!r})
controls = Path(CONFIG["controls"])
UNITS = {{
    "jannenkoti.service": ("app_standin.py", "", {{"PORT": CONFIG["app_port"]}}),
    "esp32_ws.service": (
        "gateway_standin.py", "esp32_ws", {{"ESP32_WS_PORT": CONFIG["gateway_port"]}}
    ),
}}


def note(line):
    with open(controls / "systemctl.log", "a") as log:
        log.write(line + "\\n")


def pid_of(unit):
    try:
        pid = int((controls / (unit + ".pid")).read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def stop(unit):
    pid = pid_of(unit)
    if pid:
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while pid_of(unit) and time.monotonic() < deadline:
            time.sleep(0.05)
        if pid_of(unit):
            os.killpg(pid, signal.SIGKILL)
    (controls / (unit + ".pid")).unlink(missing_ok=True)


def start(unit):
    if pid_of(unit):
        note("OVERLAP " + unit)
    deadline = time.monotonic() + 60
    while (controls / "hold-start").exists() and time.monotonic() < deadline:
        (controls / "start-waiting").touch()
        time.sleep(0.05)
    pinned = controls / "pin-release"
    release = Path(pinned.read_text().strip()) if pinned.exists() else Path(CONFIG["current"])
    script, subdir, extra = UNITS[unit]
    env = {{"PATH": os.environ.get("PATH", ""), **extra}}
    for line in Path(CONFIG["production_env"]).read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    with open(controls / (unit + ".out"), "a") as output:
        proc = subprocess.Popen(
            [str(release / "venv" / "bin" / "python"), script],
            cwd=release / "code" / subdir,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (controls / (unit + ".pid")).write_text(str(proc.pid))


action, units = sys.argv[1], sys.argv[2:]
note(" ".join(sys.argv[1:]))
if action == "is-active":
    sys.exit(0 if all(pid_of(unit) for unit in units) else 3)
if action in ("stop", "restart"):
    if (controls / "fail-stop").exists():
        sys.exit(1)
    for unit in units:
        stop(unit)
if action in ("start", "restart"):
    for unit in units:
        start(unit)
'''


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def postgres(tmp_path_factory):
    """A disposable cluster listening only on a private Unix socket."""
    initdb = shutil.which("initdb")
    if initdb is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        if not candidates:
            pytest.skip("PostgreSQL server binaries unavailable")
        initdb = str(candidates[-1])
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    work = tmp_path_factory.mktemp("release-postgres")
    data = work / "data"
    subprocess.run(
        [initdb, "-D", str(data), "--auth=trust", "--no-locale", "-E", "UTF8"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-l",
            str(work / "postgres.log"),
            "-o",
            f"-k {work} -c listen_addresses='' -c fsync=off",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield work
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
        )


def sql(world, statement: str) -> str:
    result = subprocess.run(
        ["psql", "-h", str(world["postgres"]), "-d", world["database"], "-Atc", statement],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def world(tmp_path, postgres):
    source = tmp_path / "source"
    (source / "migrations" / "versions").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "app").mkdir()
    (source / "esp32_ws").mkdir()
    (source / ".gitignore").write_text("*.env\n")
    (source / "requirements.txt").write_text("pytest\nruff\n")
    (source / "candidate.py").write_text("DATABASE = None\n")
    (source / "app" / "__init__.py").write_text("")
    shutil.copy(ROOT / "app" / "release_identity.py", source / "app" / "release_identity.py")
    (source / "app_standin.py").write_text(STAND_IN_APP)
    (source / "esp32_ws" / "gateway_standin.py").write_text(STAND_IN_GATEWAY)
    (source / "tests" / "test_candidate.py").write_text(
        "import candidate\n\n\ndef test_value():\n    assert hasattr(candidate, 'DATABASE')\n"
    )
    (source / "migrations" / "versions" / "0001_base.py").write_text(
        'revision = "0001"\ndown_revision = None\n'
    )
    git(source, "init", "-q", "-b", "main")
    commit_all(source, "base")

    database = f"prod_{uuid.uuid4().hex[:12]}"
    subprocess.run(["createdb", "-h", str(postgres), database], check=True, capture_output=True)
    world = {"postgres": postgres, "database": database}
    sql(world, "CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
    sql(world, "INSERT INTO alembic_version VALUES ('0001')")

    # The running production code before the first release (legacy checkout).
    production = tmp_path / "production"
    (production / "migrations" / "versions").mkdir(parents=True)
    (production / "migrations" / "versions" / "0001_base.py").write_text(
        'revision = "0001"\ndown_revision = None\n'
    )
    production_env = tmp_path / "jannenkoti.env"
    production_env.write_text(
        f"DATABASE_URL=postgresql+psycopg:///{database}?host={postgres}\n"
        f"SECRET_KEY={PRODUCTION_SECRET}\n"
        "WEB_USERNAME=owner\n"
        f"WEB_PASSWORD={PRODUCTION_SECRET}\n"
        f"DB_PATH={tmp_path / 'legacy.db'}\n"
        "USE_SQLA_READS=1\n"
    )

    root = tmp_path / "releases"
    controls = tmp_path / "controls"
    controls.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    app_port, gateway_port = free_port(), free_port()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        FAKE_SYSTEMCTL.format(
            python=sys.executable,
            config=json.dumps(
                {
                    "controls": str(controls),
                    "current": str(root / "current"),
                    "production_env": str(production_env),
                    "app_port": str(app_port),
                    "gateway_port": str(gateway_port),
                }
            ),
        )
    )
    systemctl.chmod(0o755)
    # Anything reaching for the host's sudo is recorded and fails.
    sudo = bin_dir / "sudo"
    sudo.write_text(f'#!/bin/sh\necho "sudo $*" >> "{controls / "sudo.log"}"\nexit 1\n')
    sudo.chmod(0o755)

    installer = controls / "install-dependencies"
    installer.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import pathlib, subprocess, sys
            requirements, venv = sys.argv[1], sys.argv[2]
            subprocess.run([{sys.executable!r}, "-m", "venv", "--without-pip", venv], check=True)
            site = next(pathlib.Path(venv).glob("lib/python*/site-packages"))
            (site / "stand-in-packages.pth").write_text({sysconfig.get_paths()["purelib"]!r})
            ruff = {str(Path(sys.executable).with_name("ruff"))!r}
            (pathlib.Path(venv) / "bin" / "ruff").symlink_to(ruff)
            """
        )
    )
    installer.chmod(0o755)

    world.update(
        source=source,
        production=production,
        production_env=production_env,
        root=root,
        controls=controls,
        bin=bin_dir,
        systemctl=systemctl,
        installer=installer,
        app_url=f"http://127.0.0.1:{app_port}",
        gateway_url=f"http://127.0.0.1:{gateway_port}",
    )
    try:
        yield world
    finally:
        for pidfile in controls.glob("*.pid"):
            with contextlib.suppress(OSError, ValueError):
                os.killpg(int(pidfile.read_text()), signal.SIGKILL)


def release(world, *args: str, timeout: float = 240, popen: bool = False, readiness: float = 20):
    command = [
        sys.executable,
        str(RELEASE),
        *args,
        "--release-root",
        str(world["root"]),
        "--production-env",
        str(world["production_env"]),
    ]
    if not (world["root"] / "current").exists() and args[0] in ("prepare", "status", "deploy"):
        command += ["--production-code", str(world["production"])]
    if args[0] in ("deploy", "recover", "restart") or "--live" in args:
        command += [
            "--service-manager",
            str(world["systemctl"]),
            "--app-url",
            world["app_url"],
            "--gateway-url",
            world["gateway_url"],
            "--readiness-timeout",
            str(readiness),
        ]
    env = {
        **os.environ,
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if popen:
        return subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    return subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)


def prepared(world, change: str | None = None, accept: bool = True) -> str:
    """Commit ``change`` to candidate.py, prepare it and record owner acceptance."""
    if change is not None:
        (world["source"] / "candidate.py").write_text(change)
        commit_all(world["source"], change)
    revision = git(world["source"], "rev-parse", "HEAD")
    result = release(
        world,
        "prepare",
        "main",
        "--source",
        str(world["source"]),
        "--installer",
        str(world["installer"]),
        "--keep",
        "10",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if accept:
        accepted = release(
            world, "accept", revision[:12], "--scope", "feature", "--note", "stand-in check"
        )
        assert accepted.returncode == 0, accepted.stderr
    return revision


def running(world) -> str | None:
    link = world["root"] / "current"
    return link.resolve().name if link.exists() else None


def retained(world) -> str | None:
    link = world["root"] / "previous"
    return link.resolve().name if link.exists() else None


def systemctl_calls(world) -> list[str]:
    log = world["controls"] / "systemctl.log"
    return log.read_text().splitlines() if log.exists() else []


def production_status(world) -> dict:
    result = release(world, "status", "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["production"]


def assert_no_host_effects(world, *outputs: str) -> None:
    assert not (world["controls"] / "sudo.log").exists()
    assert not any(line.startswith("OVERLAP") for line in systemctl_calls(world))
    assert all(PRODUCTION_SECRET not in output for output in outputs)


def test_deploys_a_verified_candidate_and_reports_the_running_revision(world):
    first = prepared(world)

    result = release(world, "deploy", first[:12])

    assert result.returncode == 0, result.stdout + result.stderr
    assert running(world) == first[:12]
    assert retained(world) is None
    calls = systemctl_calls(world)
    stop = calls.index(f"stop {APP_UNIT} {GATEWAY_UNIT}")
    start = calls.index(f"start {GATEWAY_UNIT} {APP_UNIT}")
    assert stop < start
    live = release(world, "status", "--live")
    assert live.returncode == 0, live.stdout + live.stderr
    assert f"app           revision {first}" in live.stdout
    assert f"gateway       revision {first}" in live.stdout

    second = prepared(world, "DATABASE = None\nFEATURE = True\n")
    result = release(world, "deploy", second[:12])

    assert result.returncode == 0, result.stdout + result.stderr
    assert running(world) == second[:12]
    assert retained(world) == first[:12]
    report = production_status(world)
    assert report["current"]["revision"] == second
    assert report["previous"]["revision"] == first
    assert report["last_operation"]["operation"] == "deploy"
    assert report["last_operation"]["state"] == "succeeded"
    # The retained release keeps its own code and dependencies for recovery.
    assert (world["root"] / "previous" / "venv" / "bin" / "python").exists()
    assert_no_host_effects(world, result.stdout, result.stderr, live.stdout)


def test_a_running_process_of_another_revision_is_not_ready_and_compatible_code_returns(world):
    first = prepared(world)
    assert release(world, "deploy", first[:12]).returncode == 0
    second = prepared(world, "DATABASE = None\nFEATURE = True\n")
    # The services come up from the first release, as a stale unit file would make them.
    (world["controls"] / "pin-release").write_text(str(world["root"] / "releases" / first[:12]))

    result = release(world, "deploy", second[:12], readiness=4)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"app runs revision {first}, expected {second}" in result.stderr
    assert f"gateway runs revision {first}, expected {second}" in result.stderr
    # Active services alone did not count as ready.
    assert "is not active" not in result.stderr
    assert running(world) == first[:12]
    assert retained(world) is None
    last = production_status(world)["last_operation"]
    assert last["state"] == "recovered"
    assert last["candidate"] == second[:12]
    assert any("database schema 0001 matches" in step for step in last["steps"])
    assert (world["root"] / "releases" / second[:12] / "code").is_dir()
    assert_no_host_effects(world, result.stdout, result.stderr)


def test_failed_readiness_recovers_the_previous_release_without_touching_the_database(world):
    first = prepared(world)
    assert release(world, "deploy", first[:12]).returncode == 0
    # A release whose app runs but cannot reach its database.
    broken = prepared(world, 'DATABASE = "missing_database"\n')
    schema_before = sql(world, "SELECT version_num FROM alembic_version")

    result = release(world, "deploy", broken[:12], readiness=4)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "app database is unavailable (HTTP 503)" in result.stderr
    assert running(world) == first[:12]
    assert sql(world, "SELECT version_num FROM alembic_version") == schema_before
    live = release(world, "status", "--live")
    assert live.returncode == 0, live.stdout
    assert f"app           revision {first}, database ok" in live.stdout
    assert_no_host_effects(world, result.stdout, result.stderr)


def test_a_failed_first_release_reports_that_no_earlier_release_exists(world):
    broken = prepared(world, 'DATABASE = "missing_database"\n')

    result = release(world, "deploy", broken[:12], readiness=3)

    assert result.returncode == 4, result.stdout + result.stderr
    assert "No earlier release exists to recover to" in result.stderr
    assert "journalctl" in result.stderr
    report = production_status(world)
    assert report["current"]["candidate"] == broken[:12]
    assert report["last_operation"]["state"] == "failed"
    live = release(world, "status", "--live")
    assert live.returncode == 1
    assert "ready         no" in live.stdout


def test_explicit_recovery_returns_to_the_retained_release_when_the_schema_fits(world):
    first = prepared(world)
    assert release(world, "deploy", first[:12]).returncode == 0
    second = prepared(world, "DATABASE = None\nFEATURE = True\n")
    assert release(world, "deploy", second[:12]).returncode == 0

    result = release(world, "recover")

    assert result.returncode == 0, result.stdout + result.stderr
    assert running(world) == first[:12]
    assert retained(world) == second[:12]
    live = release(world, "status", "--live")
    assert f"gateway       revision {first}, status healthy" in live.stdout
    assert production_status(world)["last_operation"]["operation"] == "recover"
    assert_no_host_effects(world, result.stdout, result.stderr)


@pytest.mark.parametrize("database", ["unknown revision", "unreachable"])
def test_recovery_and_deployment_refuse_unknown_database_compatibility(world, database):
    first = prepared(world)
    assert release(world, "deploy", first[:12]).returncode == 0
    second = prepared(world, "DATABASE = None\nFEATURE = True\n")
    assert release(world, "deploy", second[:12]).returncode == 0
    third = prepared(world, "DATABASE = None\nFEATURE = 3\n")
    if database == "unknown revision":
        # Someone changed the schema outside the release workflow.
        sql(world, "UPDATE alembic_version SET version_num = '0002'")
        expected = "database schema is 0002"
    else:
        env = world["production_env"]
        env.write_text(env.read_text().replace(world["database"], "no_such_database"))
        expected = "cannot read the database schema revision"
    calls_before = systemctl_calls(world)

    recover = release(world, "recover")
    deploy = release(world, "deploy", third[:12])

    for result in (recover, deploy):
        assert result.returncode == 2, result.stdout + result.stderr
        assert expected in result.stderr
    assert "separate owner decision" in recover.stderr
    assert systemctl_calls(world) == calls_before
    assert running(world) == second[:12]
    assert retained(world) == first[:12]
    assert_no_host_effects(world, recover.stderr, deploy.stderr)


def test_refuses_candidates_needing_migrations_or_missing_evidence(world):
    unaccepted = prepared(world, accept=False)
    (world["source"] / "migrations" / "versions" / "0002_new_table.py").write_text(
        'revision = "0002"\ndown_revision = "0001"\n'
    )
    commit_all(world["source"], "migration")
    migrating = prepared(world)

    missing_acceptance = release(world, "deploy", unaccepted[:12])
    needs_migration = release(world, "deploy", migrating[:12])

    assert missing_acceptance.returncode == 2
    assert "owner feature acceptance is missing" in missing_acceptance.stderr
    assert needs_migration.returncode == 2
    assert "needs database migrations (0002)" in needs_migration.stderr
    assert systemctl_calls(world) == []
    assert running(world) is None
    assert sql(world, "SELECT version_num FROM alembic_version") == "0001"


def test_release_operations_cannot_interleave_with_a_running_deployment(world):
    first = prepared(world)
    second = prepared(world, "DATABASE = None\nFEATURE = True\n")
    hold = world["controls"] / "hold-start"
    hold.touch()
    deploying = release(world, "deploy", first[:12], popen=True)
    try:
        deadline = time.monotonic() + 60
        while not (world["controls"] / "start-waiting").exists():
            assert deploying.poll() is None, deploying.communicate()
            assert time.monotonic() < deadline, "deployment never reached the service start"
            time.sleep(0.05)

        competing = [
            release(world, "deploy", second[:12]),
            release(world, "recover"),
            release(world, "restart"),
            release(
                world,
                "prepare",
                "main",
                "--source",
                str(world["source"]),
                "--installer",
                str(world["installer"]),
                "--replace",
            ),
        ]
        for result in competing:
            assert result.returncode == 3, result.stdout + result.stderr
            assert "another release operation" in result.stderr
        assert production_status(world)["last_operation"]["state"] == "started"
    finally:
        # Let the deployment finish before teardown stops the services it starts.
        hold.unlink()
        stdout, stderr = deploying.communicate(timeout=120)
    assert deploying.returncode == 0, stdout + stderr
    assert running(world) == first[:12]
    assert_no_host_effects(world, stdout, stderr)


def test_restart_runs_the_selected_release_again_and_verifies_it(world):
    first = prepared(world)
    assert release(world, "deploy", first[:12]).returncode == 0
    calls_before = len(systemctl_calls(world))

    result = release(world, "restart")

    assert result.returncode == 0, result.stdout + result.stderr
    assert running(world) == first[:12]
    calls = systemctl_calls(world)[calls_before:]
    assert f"stop {APP_UNIT} {GATEWAY_UNIT}" in calls
    assert production_status(world)["last_operation"]["operation"] == "restart"
    assert_no_host_effects(world, result.stdout, result.stderr)


def test_operations_refuse_the_host_service_manager_under_tests(world):
    command = [
        sys.executable,
        str(RELEASE),
        "restart",
        "--release-root",
        str(world["root"]),
        "--production-env",
        str(world["production_env"]),
    ]
    env = {**os.environ, "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}"}

    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)

    assert result.returncode == 2
    assert "--service-manager" in result.stderr
    assert systemctl_calls(world) == []
    assert not (world["controls"] / "sudo.log").exists()


@pytest.mark.parametrize("script", ["restart.sh", "restart-no-log.sh"])
@pytest.mark.parametrize("layout", ["checkout", "release"])
def test_legacy_restart_scripts_refuse_outside_the_production_checkout(tmp_path, script, layout):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("sudo", "systemctl", "journalctl", "pre-commit"):
        (bin_dir / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
        (bin_dir / tool).chmod(0o755)
    root = tmp_path / "releases"
    (root / "releases" / "abc").mkdir(parents=True)
    if layout == "release":
        (root / "current").symlink_to(root / "releases" / "abc")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "JANNENKOTI_RELEASE_ROOT": str(root),
    }

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    expected = "release.py restart" if layout == "release" else "not the production checkout"
    assert expected in result.stderr
    assert not log.exists()
