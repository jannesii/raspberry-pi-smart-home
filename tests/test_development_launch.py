"""The development launch command starts an isolated app with safe defaults.

Tests drive ``scripts/dev.py`` as a subprocess against disposable PostgreSQL,
separate disposable Redis servers and a stand-in production environment whose
configuration, files and Redis instance must remain untouched.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import redis
import requests

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "dev.py"


def write_env(path: Path, values: dict[str, str]) -> Path:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    return path


@pytest.fixture
def stand_in_production(tmp_path):
    """Production-like configuration that development must never reuse."""
    state = tmp_path / "production-state"
    state.mkdir()
    values = {
        "DATABASE_URL": "postgresql+psycopg://jannenkoti:prod-secret@127.0.0.1:5432/jannenkoti",
        "DB_PATH": str(state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(state / "villenkoti.db"),
        "SECRET_KEY": "production-secret-key",
        "WEB_USERNAME": "owner",
        "WEB_PASSWORD": "production-password",
    }
    env_file = write_env(tmp_path / "production.env", values)
    return env_file, values, state


@pytest.fixture
def dev_values(tmp_path):
    state = tmp_path / "dev-state"
    state.mkdir()
    return {
        "DATABASE_URL": "postgresql+psycopg://jannenkoti_dev@127.0.0.1:5432/jannenkoti_dev",
        "REDIS_URL": "redis://127.0.0.1:6380/0",
        "DEV_STATE_DIR": str(state),
        "DB_PATH": str(state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(state / "villenkoti.db"),
        "SECRET_KEY": "development-secret-key",
        "WEB_USERNAME": "dev-tester",
        "WEB_PASSWORD": "development-password",
        "PORT": "5557",
    }


def launch(*args: str, env: dict[str, str] | None = None, timeout: float = 60):
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_development_launch_requires_an_explicit_environment_file(stand_in_production):
    production_env, _values, _state = stand_in_production

    result = launch("run", "--production-env", str(production_env))

    assert result.returncode == 2
    assert "--env-file" in result.stderr


@pytest.mark.parametrize(
    ("setting", "override", "reason"),
    [
        ("DATABASE_URL", None, "DATABASE_URL is required"),
        ("DATABASE_URL", "sqlite:///dev.db", "PostgreSQL"),
        (
            "DATABASE_URL",
            "postgresql+psycopg://jannenkoti:other@127.0.0.1:5432/jannenkoti",
            "production database",
        ),
        (
            "DATABASE_URL",
            "postgresql+psycopg://jannenkoti@127.0.0.1:5432/jannenkoti_dev",
            "production database role",
        ),
        (
            "DATABASE_URL",
            "postgresql+psycopg://jannenkoti_dev@/jannenkoti?host=/var/run/postgresql",
            "production database",
        ),
        ("REDIS_URL", None, "REDIS_URL is required"),
        ("REDIS_URL", f"redis://{socket.gethostname()}:6379/1", "production Redis"),
        ("REDIS_URL", "redis://localhost:6379/5", "production Redis"),
        ("REDIS_URL", "redis://127.0.0.1", "production Redis"),
        ("DEV_STATE_DIR", None, "DEV_STATE_DIR is required"),
        ("DB_PATH", "/tmp/elsewhere/legacy.db", "inside DEV_STATE_DIR"),
        ("VILLENKOTI_DB_PATH", None, "VILLENKOTI_DB_PATH is required"),
        ("SECRET_KEY", "production-secret-key", "SECRET_KEY"),
        ("WEB_PASSWORD", "production-password", "WEB_PASSWORD"),
    ],
)
def test_development_refuses_missing_or_production_targets(
    tmp_path, stand_in_production, dev_values, setting, override, reason
):
    production_env, _production_values, production_state = stand_in_production
    values = dict(dev_values)
    if override is None:
        values.pop(setting)
    else:
        values[setting] = override
    dev_env = write_env(tmp_path / "dev.env", values)

    result = launch(
        "run",
        "--env-file",
        str(dev_env),
        "--production-env",
        str(production_env),
    )

    assert result.returncode == 2, result.stderr
    assert reason in result.stderr
    for secret in ("production-secret-key", "production-password", "prod-secret"):
        assert secret not in result.stderr + result.stdout
    assert list(production_state.iterdir()) == []
    assert not (Path(dev_values["DEV_STATE_DIR"]) / "legacy.db").exists()


def test_development_refuses_an_unreadable_production_reference(tmp_path, dev_values):
    dev_env = write_env(tmp_path / "dev.env", dev_values)

    result = launch(
        "run",
        "--env-file",
        str(dev_env),
        "--production-env",
        str(tmp_path / "missing.env"),
    )

    assert result.returncode == 2
    assert "production environment" in result.stderr


# --- A running development instance -----------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def postgres_socket_dir(tmp_path_factory):
    """Start a disposable cluster with only a private Unix socket, never TCP."""
    initdb = shutil.which("initdb")
    if initdb is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        if not candidates:
            pytest.skip("PostgreSQL server binaries unavailable")
        initdb = str(candidates[-1])
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    work = tmp_path_factory.mktemp("dev-postgres")
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


def start_redis(work: Path) -> tuple[subprocess.Popen, redis.Redis, str]:
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("redis-server unavailable")
    port = free_port()
    work.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [binary, "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--dir", str(work)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
    deadline = time.monotonic() + 10
    while True:
        try:
            client.ping()
            break
        except redis.ConnectionError:
            if time.monotonic() > deadline:
                proc.kill()
                raise
            time.sleep(0.05)
    return proc, client, f"redis://127.0.0.1:{port}/0"


@pytest.fixture(scope="module")
def redis_pair(tmp_path_factory):
    """Separate disposable Redis servers standing in for production and development."""
    work = tmp_path_factory.mktemp("redis")
    production = start_redis(work / "production")
    development = start_redis(work / "development")
    try:
        yield {"production": production, "development": development}
    finally:
        for proc, _client, _url in (production, development):
            proc.terminate()
            proc.wait(timeout=10)


@pytest.fixture(scope="module")
def capture_server():
    """Controlled external transport: records any request development makes."""
    import http.server
    import threading

    received: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _record(self):
            received.append(f"{self.command} {self.path}")
            self.send_response(204)
            self.end_headers()

        do_GET = do_POST = do_PUT = _record

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}", received
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def running_dev(tmp_path_factory, postgres_socket_dir, redis_pair, capture_server):
    """Launch development through the operator command next to a stand-in production."""
    work = tmp_path_factory.mktemp("running-dev")
    production_state = work / "production-state"
    production_state.mkdir()
    production_url = f"postgresql+psycopg://jannenkoti@/jannenkoti?host={postgres_socket_dir}"
    production_values = {
        "DATABASE_URL": production_url,
        "REDIS_URL": redis_pair["production"][2],
        "DB_PATH": str(production_state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(production_state / "villenkoti.db"),
        "SECRET_KEY": "production-secret-key",
        "WEB_USERNAME": "owner",
        "WEB_PASSWORD": "production-password",
    }
    production_env = write_env(work / "production.env", production_values)

    for database in ("jannenkoti", "jannenkoti_dev"):
        subprocess.run(
            ["createdb", "-h", str(postgres_socket_dir), database],
            check=True,
            capture_output=True,
        )
    dev_url = f"postgresql+psycopg:///jannenkoti_dev?host={postgres_socket_dir}"
    migrate_env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL",)}
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini.example", "upgrade", "head"],
        cwd=ROOT,
        env={**migrate_env, "DATABASE_URL": dev_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert migrated.returncode == 0, migrated.stderr[-2000:]

    state = work / "dev-state"
    state.mkdir()
    port = free_port()
    dev_values = {
        "DATABASE_URL": dev_url,
        "USE_SQLA_READS": "1",
        "REDIS_URL": redis_pair["development"][2],
        "DEV_STATE_DIR": str(state),
        "DB_PATH": str(state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(state / "villenkoti.db"),
        "SECRET_KEY": "development-secret-key",
        "WEB_USERNAME": "dev-tester",
        "WEB_PASSWORD": "development-password",
        "PORT": str(port),
        # Credential-like values must not enable household or notification effects.
        "AC_DEV_ID": "synthetic-ac",
        "AC_IP": "127.0.0.1",
        "AC_LOCAL_KEY": "synthetic-local-key",
        "HUE_BRIDGE_IP": capture_server[0],
        "HUE_USERNAME": "synthetic-hue-user",
        "ALERT_WEBHOOK_URL": f"http://{capture_server[0]}/alert",
        "SODEXO_WEBHOOK_URL": f"http://{capture_server[0]}/sodexo",
        "SODEXO_POST_HOUR": "0",
        "SODEXO_POST_MINUTE": "0",
        "YNAB_API_KEY": "synthetic-ynab-key",
        "YNAB_BUDGET_ID": "synthetic-budget",
    }
    dev_env = write_env(work / "dev.env", dev_values)
    log_path = work / "dev.log"
    # Shared host configuration that development must never rewrite.
    home = work / "home"
    novpn_conf = home / ".config" / "novpn" / "devices.conf"
    novpn_conf.parent.mkdir(parents=True)
    novpn_conf.write_text(
        '/usr/local/bin/novpn-device.sh -name "Phone" -mac aa:bb:cc:dd:ee:01 '
        "-novpn false -nodns true\n"
    )
    # Production values in the caller's environment must not leak into development.
    caller_env = {**os.environ, **production_values, "APP_ENV": "production", "HOME": str(home)}
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(LAUNCHER),
                "run",
                "--env-file",
                str(dev_env),
                "--production-env",
                str(production_env),
            ],
            cwd=work,
            env=caller_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 120
    while True:
        if proc.poll() is not None:
            pytest.fail(f"development app exited early:\n{log_path.read_text()[-4000:]}")
        try:
            if requests.get(f"{base_url}/login", timeout=2).status_code == 200:
                break
        except requests.RequestException:
            pass
        if time.monotonic() > deadline:
            os.killpg(proc.pid, signal.SIGKILL)
            pytest.fail(f"development app did not start:\n{log_path.read_text()[-4000:]}")
        time.sleep(0.5)
    try:
        yield {
            "url": base_url,
            "values": dev_values,
            "state": state,
            "production_state": production_state,
            "log": log_path,
            "novpn_conf": novpn_conf,
            "postgres": postgres_socket_dir,
        }
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)


class Browser:
    """A requests session that keeps Secure cookies over the local plain-HTTP bind."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 15)
        response = self.session.request(method, f"{self.base_url}{path}", **kwargs)
        for cookie in self.session.cookies:
            cookie.secure = False
        return response

    def csrf_token(self, path: str = "/login") -> str:
        page = self.request("GET", path)
        match = re.search(
            r'name="csrf_token" value="([^"]+)"|name="csrf-token" content="([^"]+)"', page.text
        )
        assert match, f"no CSRF token on {path}"
        return match.group(1) or match.group(2)

    def login(self, username: str, password: str) -> requests.Response:
        token = self.csrf_token()
        return self.request(
            "POST",
            "/login",
            data={"username": username, "password": password, "csrf_token": token},
            allow_redirects=False,
        )


@pytest.fixture(scope="module")
def dev_browser(running_dev):
    """The dedicated test identity's session; login is rate limited, so log in once."""
    browser = Browser(running_dev["url"])
    response = browser.login(
        running_dev["values"]["WEB_USERNAME"], running_dev["values"]["WEB_PASSWORD"]
    )
    assert response.status_code == 302, response.text[:500]
    return browser


def test_development_serves_authenticated_pages_to_the_dedicated_test_identity(
    running_dev, dev_browser
):
    anonymous = Browser(running_dev["url"])
    protected = anonymous.request("GET", "/car_heater", allow_redirects=False)
    assert protected.status_code == 302
    assert "/login" in protected.headers["Location"]

    # The production login is not seeded into development.
    assert anonymous.login("owner", "production-password").status_code == 200

    assert dev_browser.request("GET", "/car_heater", allow_redirects=False).status_code == 200

    # Writable legacy state lives in the development state directory only.
    assert (running_dev["state"] / "legacy.db").exists()
    assert list(running_dev["production_state"].iterdir()) == []
    # The stand-in production database was never migrated or written.
    tables = subprocess.run(
        [
            "psql",
            "-h",
            str(running_dev["postgres"]),
            "-d",
            "jannenkoti",
            "-tAc",
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert tables.stdout.strip() == "0"


def test_the_app_itself_refuses_development_without_isolated_targets(tmp_path):
    """Started outside the launcher, development still neither reads jannenkoti.env
    nor falls back to the production Redis default."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "app").symlink_to(ROOT / "app")
    shutil.copy(ROOT / "run.py", checkout / "run.py")
    (checkout / "jannenkoti.env").write_text("REDIS_URL=redis://127.0.0.1:6390/0\n")
    state = tmp_path / "state"
    state.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "APP_ENV": "development",
        "DATABASE_URL": f"postgresql+psycopg:///none?host={tmp_path}",
        "DEV_STATE_DIR": str(state),
        "DB_PATH": str(state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(state / "villenkoti.db"),
        "SECRET_KEY": "development-secret-key",
        "WEB_USERNAME": "dev-tester",
        "WEB_PASSWORD": "development-password",
    }

    result = subprocess.run(
        [sys.executable, "-c", "import run"],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode != 0
    assert "REDIS_URL is required in development" in result.stderr
    assert not (state / "legacy.db").exists()


def drain(pubsub, seconds: float) -> list[dict]:
    messages = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        message = pubsub.get_message(timeout=0.1)
        if message and message["type"] == "pmessage":
            messages.append(message)
    return messages


def test_development_redis_traffic_stays_on_its_own_redis_server(redis_pair, dev_browser):
    production = redis_pair["production"][1]
    development = redis_pair["development"][1]

    # Browser Socket.IO queue and telemetry bridge subscribe only to development.
    for channel in ("esp32:status", "esp32:temperature:telemetry"):
        assert development.pubsub_numsub(channel)[0][1] >= 1, channel
        assert production.pubsub_numsub(channel)[0][1] == 0, channel

    watchers = {}
    for name in ("production", "development"):
        # Socket.IO publishes pickled payloads, so watch without decoding.
        watchers[name] = redis.Redis.from_url(redis_pair[name][2]).pubsub()
        watchers[name].psubscribe("*")
        drain(watchers[name], 0.2)

    token = dev_browser.csrf_token("/car_heater")
    queued = dev_browser.request(
        "POST",
        "/api/car_heater/queue",
        json={"action": "turn_off"},
        headers={"X-CSRFToken": token},
    )
    assert queued.status_code == 200, queued.text

    published = drain(watchers["development"], 2)
    channels = {message["channel"] for message in published}
    # Heater command publisher and the browser Socket.IO queue.
    assert {b"esp32:commands", b"flask-socketio"} <= channels
    assert drain(watchers["production"], 0.5) == []
    # Rate-limit counters from the login also live on the development server.
    assert development.keys("LIMITS*")
    assert production.keys("*") == []


def test_development_keeps_integrations_off_and_reports_them_unavailable(
    running_dev, dev_browser, capture_server
):
    _address, received = capture_server

    # Household control and finance integrations report unavailability honestly.
    assert dev_browser.request("GET", "/api/ac/status").status_code == 503
    ynab = dev_browser.request("GET", "/api/ynab-categorizer/queue")
    assert ynab.status_code == 503

    log = running_dev["log"].read_text()
    for integration in ("AC thermostat", "Hue", "Sodexo", "Alert webhook", "Weather archive"):
        assert f"{integration} disabled in development" in log, integration
    for secret in ("synthetic-local-key", "synthetic-ynab-key", "development-password"):
        assert secret not in log
    time.sleep(1)
    assert received == []


def test_development_refuses_to_change_host_configuration(running_dev, dev_browser):
    conf = running_dev["novpn_conf"]
    before = (conf.read_bytes(), conf.stat().st_mtime_ns)

    # Reading the shared file is harmless and stays available.
    devices = dev_browser.request("GET", "/api/novpn/devices")
    assert devices.status_code == 200
    assert devices.json()["devices"][0]["mac"] == "aa:bb:cc:dd:ee:01"

    for method, path, body in (
        ("POST", "/api/novpn/update", {"mac": "aa:bb:cc:dd:ee:01", "novpn": True}),
        ("POST", "/api/novpn/temp_bypass", {"mac": "aa:bb:cc:dd:ee:01", "minutes": 1}),
        ("GET", "/api/novpn/quick_bypass", None),
    ):
        refused = dev_browser.request(method, path, json=body)
        assert refused.status_code == 409, path
        assert refused.json()["error"] == "unavailable_in_development"

    token = dev_browser.csrf_token("/settings/network_bypass")
    page = dev_browser.request(
        "POST",
        "/settings/network_bypass",
        data={
            "csrf_token": token,
            "add_device": "1",
            "new_name": "Laptop",
            "new_mac": "aa:bb:cc:dd:ee:02",
        },
    )
    assert page.status_code == 200
    assert "disabled in development" in page.text

    # Mutations still require authentication rather than revealing the refusal.
    anonymous = Browser(running_dev["url"])
    blocked = anonymous.request("POST", "/api/novpn/update", json={}, allow_redirects=False)
    assert blocked.status_code in (302, 401)

    assert (conf.read_bytes(), conf.stat().st_mtime_ns) == before
    assert not conf.with_name("devices.conf.tmp").exists()


PRODUCTION_PROBE = """
import sys, time
import redis
import run

time.sleep(1.5)
watcher = redis.Redis.from_url(sys.argv[1])
print("status-subscribers", watcher.pubsub_numsub("esp32:status")[0][1])
"""


def test_production_startup_keeps_reading_its_env_file_and_starting_integrations(
    tmp_path, redis_pair, capture_server
):
    """Without APP_ENV the app behaves as production always has."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "app").symlink_to(ROOT / "app")
    shutil.copy(ROOT / "run.py", checkout / "run.py")
    state = tmp_path / "state"
    state.mkdir()
    redis_url = redis_pair["production"][2]
    write_env(
        checkout / "jannenkoti.env",
        {
            "REDIS_URL": redis_url,
            "DB_PATH": str(state / "legacy.db"),
            "VILLENKOTI_DB_PATH": str(state / "villenkoti.db"),
            "SECRET_KEY": "production-secret-key",
            "WEB_USERNAME": "owner",
            "WEB_PASSWORD": "production-password",
            "ALERT_WEBHOOK_URL": f"http://{capture_server[0]}/alert",
            "SODEXO_WEBHOOK_URL": f"http://{capture_server[0]}/sodexo",
            "SODEXO_POST_HOUR": "0",
            "SODEXO_POST_MINUTE": "0",
            "WEATHER_ARCHIVE_ENABLED": "false",
        },
    )

    result = subprocess.run(
        [sys.executable, "-c", PRODUCTION_PROBE, redis_url],
        cwd=checkout,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-3000:]
    assert "status-subscribers 1" in result.stdout
    assert "Alert webhook batcher started." in result.stderr
    assert "Sodexo-Scheduler started" in result.stderr
    assert "disabled in development" not in result.stderr
    assert (state / "legacy.db").exists()
