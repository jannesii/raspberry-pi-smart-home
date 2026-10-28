"""Operators manage one development instance from a selected worktree.

Tests drive ``scripts/dev.py`` lifecycle commands as subprocesses, invoked from
a different checkout than the selected worktree. They use a disposable
PostgreSQL cluster, disposable Redis servers, a stand-in production
environment and a substitute dependency installer; nothing reaches host
services, the network or the production checkout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from test_development_launch import (  # noqa: F401 - pytest fixtures
    Browser,
    free_port,
    postgres_socket_dir,
    redis_pair,
    write_env,
)

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "dev.py"
DEV_MARKER_COMMENT = "jannenkoti development"
PRODUCTION_SECRETS = ("production-secret-key", "production-password", "prod-secret")


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_worktree(path: Path, *, contract: bool = True) -> Path:
    """A feature worktree whose code is this checkout's application."""
    path.mkdir(parents=True)
    if contract:
        (path / "app").symlink_to(ROOT / "app")
    else:
        # Code from before the development contract existed.
        (path / "app").mkdir()
        (path / "app" / "__init__.py").write_text("")
    (path / "migrations").symlink_to(ROOT / "migrations")
    for name in ("run.py", "alembic.ini.example", "requirements.txt"):
        shutil.copy(ROOT / name, path / name)
    git("init", "-q", "-b", "feat/sample", cwd=path)
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "sample feature", cwd=path)
    return path


INSTALLER = """#!/bin/sh
# Substitute dependency installer: records the call and builds a venv whose
# python is the test interpreter, which already has the dependencies.
echo "$1 $2" >> {calls}
while [ -e {hold} ]; do sleep 0.1; done
mkdir -p "$1/bin"
printf '#!/bin/sh\\nexec {python} "$@"\\n' > "$1/bin/python"
chmod +x "$1/bin/python"
"""


@pytest.fixture(scope="module")
def installer(tmp_path_factory):
    work = tmp_path_factory.mktemp("installer")
    calls = work / "calls"
    calls.touch()
    hold = work / "hold"
    script = work / "install"
    script.write_text(INSTALLER.format(calls=calls, hold=hold, python=sys.executable))
    script.chmod(0o755)
    return {"path": script, "calls": calls, "hold": hold}


def psql(socket_dir: Path, database: str, sql: str) -> str:
    return subprocess.run(
        ["psql", "-h", str(socket_dir), "-d", database, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def provision_database(socket_dir: Path, name: str, *, mark: bool = True) -> None:
    """What the owner's provisioning SQL does for the development database."""
    subprocess.run(["createdb", "-h", str(socket_dir), name], check=True, capture_output=True)
    if mark:
        psql(socket_dir, name, f"COMMENT ON DATABASE {name} IS '{DEV_MARKER_COMMENT}'")


@pytest.fixture(scope="module")
def environment(tmp_path_factory, postgres_socket_dir, redis_pair, installer):  # noqa: F811
    """A provisioned development configuration beside a stand-in production."""
    work = tmp_path_factory.mktemp("lifecycle")
    production_checkout = work / "production"
    production_checkout.mkdir()
    production_state = work / "production-state"
    production_state.mkdir()
    provision_database(postgres_socket_dir, "jannenkoti", mark=False)
    production_values = {
        "DATABASE_URL": f"postgresql+psycopg://jannenkoti@/jannenkoti?host={postgres_socket_dir}",
        "REDIS_URL": redis_pair["production"][2],
        "DB_PATH": str(production_state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(production_state / "villenkoti.db"),
        "SECRET_KEY": "production-secret-key",
        "WEB_USERNAME": "owner",
        "WEB_PASSWORD": "production-password",
    }
    production_env = write_env(production_checkout / "jannenkoti.env", production_values)

    provision_database(postgres_socket_dir, "jannenkoti_dev")
    state = work / "dev-state"
    state.mkdir()
    values = {
        "DATABASE_URL": f"postgresql+psycopg:///jannenkoti_dev?host={postgres_socket_dir}",
        "REDIS_URL": f"redis://127.0.0.1:{free_port()}/0",
        "DEV_STATE_DIR": str(state),
        "DB_PATH": str(state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(state / "villenkoti.db"),
        "SECRET_KEY": "development-secret-key",
        "WEB_USERNAME": "dev-tester",
        "WEB_PASSWORD": "development-password",
        "PORT": str(free_port()),
    }
    env_file = write_env(work / "dev.env", values)
    elsewhere = work / "elsewhere"
    elsewhere.mkdir()
    return {
        "work": work,
        "env_file": env_file,
        "values": values,
        "state": state,
        "production_env": production_env,
        "production_values": production_values,
        "production_checkout": production_checkout,
        "production_state": production_state,
        "postgres": postgres_socket_dir,
        "installer": installer,
        "elsewhere": elsewhere,
        "url": f"http://127.0.0.1:{values['PORT']}",
    }


def dev(environment, command: str, *args: str, timeout: float = 240, env_file=None):
    """Run an operator command from a directory outside every checkout."""
    argv = [
        sys.executable,
        str(LAUNCHER),
        command,
        "--env-file",
        str(env_file or environment["env_file"]),
        "--production-env",
        str(environment["production_env"]),
    ]
    if command == "setup":
        argv += ["--installer", str(environment["installer"]["path"])]
    # Production values in the caller's shell must not leak into development.
    caller = {**os.environ, **environment["production_values"], "APP_ENV": "production"}
    result = subprocess.run(
        [*argv, *args],
        cwd=environment["elsewhere"],
        env=caller,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    for secret in (*PRODUCTION_SECRETS, "development-password"):
        assert secret not in result.stdout + result.stderr, command
    return result


def installer_calls(environment) -> list[str]:
    return environment["installer"]["calls"].read_text().splitlines()


def assert_production_untouched(environment) -> None:
    assert list(environment["production_state"].iterdir()) == []
    assert sorted(p.name for p in environment["production_checkout"].iterdir()) == [
        "jannenkoti.env"
    ]
    tables = psql(
        environment["postgres"],
        "jannenkoti",
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'",
    )
    assert tables == "0"


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda env: env["work"] / "missing", "not a git worktree"),
        (lambda env: make_worktree(env["work"] / "old", contract=False), "development contract"),
        (lambda env: env["production_checkout"], "production checkout"),
    ],
    ids=["missing", "before-contract", "production"],
)
def test_setup_refuses_worktrees_that_cannot_be_isolated(environment, make, reason):
    target = make(environment)
    before = installer_calls(environment)

    result = dev(environment, "setup", "--worktree", str(target))

    assert result.returncode == 2, result.stdout + result.stderr
    assert reason in result.stderr
    assert installer_calls(environment) == before
    assert list(environment["state"].iterdir()) == []
    assert_production_untouched(environment)


@pytest.fixture(scope="module")
def worktree(environment):
    return make_worktree(environment["work"] / "feature")


def sample_state(environment) -> tuple[str, str]:
    users = psql(
        environment["postgres"],
        "jannenkoti_dev",
        "SELECT username || ':' || is_root_admin FROM users ORDER BY username",
    )
    readings = psql(
        environment["postgres"],
        "jannenkoti_dev",
        "SELECT location || ':' || temperature || ':' || humidity FROM esp32_temphum "
        "ORDER BY location",
    )
    return users, readings


SAMPLE = (
    "dev-tester:true",
    "Kehitys makuuhuone:19:45\nKehitys olohuone:21.5:40\nKehitys ulkona:-4.5:85",
)


def test_setup_prepares_dependencies_schema_and_sample_data(environment, worktree):
    result = dev(environment, "setup", "--worktree", str(worktree))

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(worktree) in result.stdout
    assert "feat/sample" in result.stdout
    venv = environment["state"] / "venv"
    assert installer_calls(environment)[-1] == f"{venv} {worktree / 'requirements.txt'}"
    assert sample_state(environment) == SAMPLE
    assert_production_untouched(environment)

    # Setting up again keeps development data and the installed dependencies.
    calls = installer_calls(environment)
    again = dev(environment, "setup", "--worktree", str(worktree))
    assert again.returncode == 0, again.stdout + again.stderr
    assert installer_calls(environment) == calls
    assert sample_state(environment) == SAMPLE


def test_setup_refuses_ambiguous_development_targets(environment, worktree):
    provision_database(environment["postgres"], "jannenkoti_unmarked", mark=False)
    for name, change in (
        (
            "database",
            {"DATABASE_URL": environment["values"]["DATABASE_URL"].replace("_dev", "_unmarked")},
        ),
        ("state", {}),
    ):
        state = environment["work"] / f"ambiguous-{name}"
        state.mkdir()
        if name == "state":
            (state / "notes.txt").write_text("not development state")
        values = {
            **environment["values"],
            "DEV_STATE_DIR": str(state),
            "DB_PATH": str(state / "legacy.db"),
            "VILLENKOTI_DB_PATH": str(state / "villenkoti.db"),
            **change,
        }
        env_file = write_env(environment["work"] / f"ambiguous-{name}.env", values)
        calls = installer_calls(environment)

        result = dev(environment, "setup", "--worktree", str(worktree), env_file=env_file)

        assert result.returncode == 2, name
        assert "not marked" in result.stderr, name
        assert installer_calls(environment) == calls
        assert sorted(p.name for p in state.iterdir()) == (["notes.txt"] if name == "state" else [])
    assert_production_untouched(environment)


@pytest.fixture(scope="module")
def selected(environment, worktree):
    """The worktree selected and prepared; any instance is stopped afterwards."""
    result = dev(environment, "setup", "--worktree", str(worktree))
    assert result.returncode == 0, result.stdout + result.stderr
    yield worktree
    dev(environment, "stop")


def log_in(environment) -> Browser:
    browser = Browser(environment["url"])
    values = environment["values"]
    response = browser.login(values["WEB_USERNAME"], values["WEB_PASSWORD"])
    assert response.status_code == 302, response.text[:500]
    return browser


def add_user(browser: Browser, username: str) -> None:
    token = browser.csrf_token("/settings/add_user")
    response = browser.request(
        "POST",
        "/settings/add_user",
        data={
            "csrf_token": token,
            "username": username,
            "password": "synthetic-pass",
            "password_confirm": "synthetic-pass",
        },
        allow_redirects=False,
    )
    assert response.status_code == 302, response.text[:500]


def usernames(environment) -> list[str]:
    return psql(
        environment["postgres"], "jannenkoti_dev", "SELECT username FROM users ORDER BY username"
    ).splitlines()


def test_start_status_stop_manage_one_persistent_instance(environment, selected, redis_pair):  # noqa: F811
    stopped = dev(environment, "status")
    assert stopped.returncode == 3, stopped.stdout + stopped.stderr
    for fact in ("development", "stopped", str(selected), "feat/sample"):
        assert fact in stopped.stdout, fact

    started = dev(environment, "start")
    assert started.returncode == 0, started.stdout + started.stderr
    assert str(selected) in started.stdout
    status = dev(environment, "status")
    assert status.returncode == 0, status.stdout + status.stderr
    for fact in ("running", "feat/sample", environment["url"], "https://dev.jannenkoti.com"):
        assert fact in status.stdout, fact

    add_user(log_in(environment), "dev-persisted")
    second = dev(environment, "start")
    assert second.returncode == 2
    assert "already running" in second.stderr

    assert dev(environment, "stop").returncode == 0
    with pytest.raises(requests.ConnectionError):
        requests.get(f"{environment['url']}/login", timeout=2)
    assert dev(environment, "status").returncode == 3
    assert dev(environment, "stop").returncode == 0  # stopping again is harmless

    # Data survives a restart of the instance.
    assert dev(environment, "start").returncode == 0
    log_in(environment)
    assert "dev-persisted" in usernames(environment)
    assert dev(environment, "stop").returncode == 0

    assert redis_pair["production"][1].keys("*") == []
    assert_production_untouched(environment)


def test_lifecycle_operations_do_not_interleave(environment, worktree):
    state = environment["work"] / "busy-state"
    state.mkdir()
    values = {
        **environment["values"],
        "DEV_STATE_DIR": str(state),
        "DB_PATH": str(state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(state / "villenkoti.db"),
    }
    env_file = write_env(environment["work"] / "busy.env", values)
    hold = environment["installer"]["hold"]
    hold.touch()
    argv = [
        sys.executable,
        str(LAUNCHER),
        "setup",
        "--worktree",
        str(worktree),
        "--env-file",
        str(env_file),
        "--production-env",
        str(environment["production_env"]),
        "--installer",
        str(environment["installer"]["path"]),
    ]
    calls = len(installer_calls(environment))
    setup = subprocess.Popen(argv, cwd=environment["elsewhere"], stdout=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while len(installer_calls(environment)) == calls:
            assert setup.poll() is None and time.monotonic() < deadline, "setup did not install"
            time.sleep(0.1)

        for command, expected in (("start", 4), ("stop", 4), ("reset", 4), ("status", 4)):
            args = ("--yes",) if command == "reset" else ()
            result = dev(environment, command, *args, env_file=env_file)
            assert result.returncode == expected, (command, result.stdout + result.stderr)
            assert "setup" in result.stdout + result.stderr, command
    finally:
        hold.unlink()
        setup.wait(timeout=120)
    assert setup.returncode == 0
    assert dev(environment, "status", env_file=env_file).returncode == 3


def test_reset_returns_development_to_the_sample_state(environment, selected, redis_pair):  # noqa: F811
    state = environment["state"]
    assert dev(environment, "start").returncode == 0
    add_user(log_in(environment), "dev-scratch")
    import redis as redis_client

    # Something pending in development's Redis must not survive a reset.
    dev_redis = redis_client.Redis.from_url(environment["values"]["REDIS_URL"])
    dev_redis.rpush("pending-effects", "synthetic")
    (state / "scratch.txt").write_text("development file")

    running = dev(environment, "reset", "--yes")
    assert running.returncode == 2
    assert "stop" in running.stderr
    assert dev(environment, "stop").returncode == 0
    unconfirmed = dev(environment, "reset")
    assert unconfirmed.returncode == 2
    assert "--yes" in unconfirmed.stderr
    assert "dev-scratch" in usernames(environment)

    calls = installer_calls(environment)
    result = dev(environment, "reset", "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert sample_state(environment) == SAMPLE
    assert not (state / "scratch.txt").exists()
    assert installer_calls(environment) == calls
    assert dev(environment, "start").returncode == 0
    try:
        log_in(environment)
        assert dev_redis.exists("pending-effects") == 0
    finally:
        assert dev(environment, "stop").returncode == 0
    assert redis_pair["production"][1].keys("*") == []
    assert_production_untouched(environment)


def test_reset_refuses_production_and_ambiguous_targets(environment, selected):
    before = usernames(environment)
    production_target = write_env(
        environment["work"] / "reset-production.env",
        {**environment["values"], "DATABASE_URL": environment["production_values"]["DATABASE_URL"]},
    )
    result = dev(environment, "reset", "--yes", env_file=production_target)
    assert result.returncode == 2
    assert "production database" in result.stderr

    psql(environment["postgres"], "jannenkoti_dev", "COMMENT ON DATABASE jannenkoti_dev IS NULL")
    try:
        result = dev(environment, "reset", "--yes")
    finally:
        psql(
            environment["postgres"],
            "jannenkoti_dev",
            f"COMMENT ON DATABASE jannenkoti_dev IS '{DEV_MARKER_COMMENT}'",
        )
    assert result.returncode == 2
    assert "not marked" in result.stderr

    assert usernames(environment) == before
    assert_production_untouched(environment)


@pytest.fixture(scope="module")
def started(environment, selected):
    result = dev(environment, "start")
    assert result.returncode == 0, result.stdout + result.stderr
    yield environment["url"]
    dev(environment, "stop")


def set_cookies(response: requests.Response) -> dict[str, str]:
    headers = response.raw.headers.getlist("Set-Cookie")
    return {header.split("=", 1)[0]: header for header in headers}


def test_development_is_marked_and_uses_its_own_browser_session(environment, started):
    anonymous = Browser(started)
    login_page = anonymous.request("GET", "/login")
    assert "Kehitysympäristö" in login_page.text
    assert "feat/sample" in login_page.text
    assert re.search(r"css/base\.css\?v=\d{8}-\d+", login_page.text)

    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    values = environment["values"]
    response = anonymous.request(
        "POST",
        "/login",
        data={
            "username": values["WEB_USERNAME"],
            "password": values["WEB_PASSWORD"],
            "remember": "on",
            "csrf_token": token,
        },
        allow_redirects=False,
    )
    assert response.status_code == 302
    cookies = set_cookies(response)
    # Distinct names: browsers share cookies across ports on 127.0.0.1/localhost.
    assert "session" not in cookies
    assert "remember_token" not in cookies
    for name in ("jannenkoti_dev_session", "jannenkoti_dev_remember"):
        attributes = cookies[name].lower()
        assert "secure" in attributes and "httponly" in attributes, name
        assert "domain=" not in attributes, name

    settings = anonymous.request("GET", "/settings")
    assert settings.status_code == 200
    assert "Kehitysympäristö" in settings.text


def test_development_keeps_authentication_csrf_and_device_api_rules(environment, started):
    anonymous = Browser(started)
    assert "/login" in anonymous.request(
        "GET", "/settings/users", allow_redirects=False
    ).headers.get("Location", "")
    # The device endpoint stays explicitly CSRF-exempt but still requires its API key.
    device = anonymous.request("POST", "/api/esp32_temphum", json={"temperature": 1})
    assert device.status_code == 401

    owner = log_in(environment)
    missing_csrf = owner.request(
        "POST", "/settings/add_user", data={"username": "x"}, allow_redirects=False
    )
    assert missing_csrf.status_code == 400
    add_user(owner, "dev-viewer")

    viewer = Browser(started)
    # The JSON login has its own rate-limit budget; the form login's is spent.
    signed_in = viewer.request(
        "POST", "/login_api", json={"username": "dev-viewer", "password": "synthetic-pass"}
    )
    assert signed_in.status_code == 200, signed_in.text[:300]
    root_only = viewer.request("GET", "/settings/network_bypass", allow_redirects=False)
    assert root_only.status_code == 302

    # Socket.IO accepts the development origins, not production's.
    def handshake(origin: str) -> int:
        return anonymous.request(
            "GET", "/socket.io/?EIO=4&transport=polling", headers={"Origin": origin}
        ).status_code

    assert handshake("https://dev.jannenkoti.com") == 200
    assert handshake(started) == 200
    assert handshake("https://jannenkoti.com") == 400


@pytest.mark.parametrize("helper", ["restart.sh", "restart-no-log.sh"])
def test_production_restart_helpers_refuse_other_checkouts(tmp_path, helper):
    """From a worktree the helpers must not fall through to restarting production."""
    fakes = tmp_path / "bin"
    fakes.mkdir()
    calls = tmp_path / "calls"
    for name in ("sudo", "systemctl", "journalctl", "pre-commit"):
        fake = fakes / name
        fake.write_text(f'#!/bin/sh\necho "{name} $*" >> {calls}\n')
        fake.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / helper)],
        cwd=work,
        env={**os.environ, "PATH": f"{fakes}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "scripts/dev.py" in result.stderr
    assert not calls.exists()


def test_development_indicator_and_navigation_in_a_browser(environment, started):
    pytest.importorskip("playwright")
    # Restart for a fresh login rate-limit budget.
    assert dev(environment, "stop").returncode == 0
    assert dev(environment, "start").returncode == 0
    values = environment["values"]
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests" / "browser" / "development_indicator_flow.py"),
            started,
            values["WEB_USERNAME"],
            values["WEB_PASSWORD"],
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if "Executable doesn't exist" in result.stderr:
        pytest.skip("Run python -m playwright install chromium")
    assert result.returncode == 0, result.stdout + result.stderr
