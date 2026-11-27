"""Encrypted recovery backups through the operator command seam.

Each test drives ``scripts/recovery_backup.py`` as a subprocess against a
disposable PostgreSQL cluster on a private socket, synthetic SQLite and
configuration files in a temporary directory, and a throwaway GnuPG key. No
production host, database or key is involved.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import test_release_deployment as deployment

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "recovery_backup.py"

# A private socket cluster shared by the module; each test uses new databases.
postgres = deployment.postgres


def psql(socket: Path, database: str, statement: str) -> str:
    result = subprocess.run(
        [
            "psql",
            "-X",
            "-h",
            str(socket),
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            statement,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def create_database(socket: Path, prefix: str) -> str:
    name = f"{prefix}{uuid.uuid4().hex[:10]}"
    psql(socket, "postgres", f"CREATE DATABASE {name}")
    return name


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """A passphrase-less recovery key pair; the public half stands in for the Pi's."""
    if shutil.which("gpg") is None:
        pytest.skip("gpg unavailable")
    # GnuPG sockets live in the home directory, whose path must stay short.
    home = Path(tmp_path_factory.mktemp("gpg"))
    home.chmod(0o700)
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--homedir",
            str(home),
            "--passphrase",
            "",
            "--quick-generate-key",
            "Recovery test <recovery@example.invalid>",
            "default",
            "default",
            "never",
        ],
        check=True,
        capture_output=True,
    )
    public = home.parent / f"{home.name}-public.asc"
    with public.open("wb") as handle:
        subprocess.run(
            ["gpg", "--batch", "--homedir", str(home), "--armor", "--export"],
            check=True,
            stdout=handle,
        )
    yield {"home": home, "public": public}
    subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "all"], capture_output=True)


@pytest.fixture
def world(tmp_path, postgres, keys):
    """Synthetic production state: PostgreSQL, two SQLite files and configuration."""
    production = create_database(postgres, "jannenkoti_")
    psql(
        postgres,
        production,
        "CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);"
        "INSERT INTO alembic_version VALUES ('0042');"
        "CREATE TABLE sensor_locations (id serial PRIMARY KEY, name text NOT NULL);"
        "INSERT INTO sensor_locations (name) VALUES ('Olohuone'), ('Autotalli'), ('Sauna');"
        "CREATE TABLE car_heater_photo (id serial PRIMARY KEY, content bytea);"
        "INSERT INTO car_heater_photo (content) VALUES ('\\xdeadbeef');",
    )
    state = tmp_path / "state"
    state.mkdir()
    legacy = state / "jannenkoti.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE prints (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO prints (name) VALUES ('benchy')")
    villenkoti = state / "villenkoti.db"
    with sqlite3.connect(villenkoti) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO items DEFAULT VALUES")

    config = tmp_path / "config"
    (config / "esp32_ws").mkdir(parents=True)
    production_env = config / "jannenkoti.env"
    production_env.write_text(
        f"DATABASE_URL=postgresql+psycopg:///{production}?host={postgres}\n"
        f"DB_PATH={legacy}\n"
        f"VILLENKOTI_DB_PATH={villenkoti}\n"
        "SECRET_KEY=synthetic-secret\n"
        "HUE_USERNAME=synthetic-hue\n"
    )
    production_env.chmod(0o600)
    gateway_env = config / "esp32_ws" / "esp32_ws.env"
    gateway_env.write_text("ESP32_WS_API_KEY=synthetic-gateway-key\n")
    tunnel = config / "tunnel"
    tunnel.mkdir()
    (tunnel / "credentials.json").write_text('{"TunnelSecret": "synthetic"}\n')
    (tunnel / "config.yml").write_text("tunnel: synthetic\n")

    manifest = tmp_path / "recovery-files.txt"
    manifest.write_text(
        "# Synthetic recovery manifest\n"
        f"required {production_env}\n"
        f"required {gateway_env}\n"
        f"required {tunnel}\n"
        f"optional {config / 'absent.conf'}\n"
    )
    return {
        "tmp": tmp_path,
        "socket": postgres,
        "production": production,
        "production_env": production_env,
        "gateway_env": gateway_env,
        "tunnel": tunnel,
        "legacy": legacy,
        "manifest": manifest,
        "root": tmp_path / "backups",
        "keys": keys,
    }


def command(world, *args: str, env: dict[str, str] | None = None):
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(world["tmp"]),
        "TZ": "UTC",
        **(env or {}),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=world["tmp"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )


def create(world, *extra: str, env: dict[str, str] | None = None):
    return command(
        world,
        "create",
        "--backup-root",
        str(world["root"]),
        "--production-env",
        str(world["production_env"]),
        "--manifest",
        str(world["manifest"]),
        "--recipient",
        str(world["keys"]["public"]),
        *extra,
        env=env,
    )


def completed(world) -> list[Path]:
    daily = world["root"] / "daily"
    return sorted(daily.iterdir()) if daily.is_dir() else []


def restore_target(world) -> Path:
    database = create_database(world["socket"], "recovery_test_")
    target = world["tmp"] / f"restore-{database}.env"
    target.write_text(
        f"DATABASE_URL=postgresql+psycopg:///{database}?host={world['socket']}\n"
        "RECOVERY_RESTORE_TARGET=isolated\n"
    )
    return target


def restore(world, backup: Path, target: Path, restore_dir: Path, *extra: str):
    return command(
        world,
        "restore",
        str(backup),
        "--target-env",
        str(target),
        "--restore-dir",
        str(restore_dir),
        "--gnupg-home",
        str(world["keys"]["home"]),
        *extra,
    )


def target_database(target: Path) -> str:
    line = next(line for line in target.read_text().splitlines() if line.startswith("DATABASE_URL"))
    return line.split("///", 1)[1].split("?", 1)[0]


def test_backup_is_encrypted_and_restores_rows_and_required_files(world):
    result = create(world)
    assert result.returncode == 0, result.stderr

    [backup] = completed(world)
    artifact = backup / "backup.tar.gpg"
    assert artifact.is_file()
    assert artifact.stat().st_mode & 0o077 == 0
    ciphertext = artifact.read_bytes()
    for secret in (b"synthetic-secret", b"synthetic-gateway-key", b"Olohuone", b"benchy"):
        assert secret not in ciphertext
    record = json.loads((backup / "backup.json").read_text())
    assert record["status"] == "complete"
    assert "synthetic-secret" not in (backup / "backup.json").read_text()

    target = restore_target(world)
    restore_dir = world["tmp"] / "restored"
    result = restore(world, backup, target, restore_dir)
    assert result.returncode == 0, result.stderr + result.stdout

    database = target_database(target)
    assert psql(
        world["socket"], database, "SELECT string_agg(name, ',' ORDER BY id) FROM sensor_locations"
    ) == ("Olohuone,Autotalli,Sauna")
    assert psql(world["socket"], database, "SELECT version_num FROM alembic_version") == "0042"
    assert (
        psql(world["socket"], database, "SELECT encode(content, 'hex') FROM car_heater_photo")
        == "deadbeef"
    )

    files = restore_dir / "files"
    restored_env = files / str(world["production_env"]).lstrip("/")
    assert "SECRET_KEY=synthetic-secret" in restored_env.read_text()
    assert (files / str(world["gateway_env"]).lstrip("/")).read_text() == (
        "ESP32_WS_API_KEY=synthetic-gateway-key\n"
    )
    assert (files / str(world["tunnel"]).lstrip("/") / "credentials.json").is_file()
    with sqlite3.connect(restore_dir / "sqlite" / "DB_PATH.sqlite3") as conn:
        assert conn.execute("SELECT name FROM prints").fetchall() == [("benchy",)]
    assert "verified" in result.stdout


def status(world) -> tuple[int, dict]:
    result = command(world, "status", "--backup-root", str(world["root"]), "--json")
    return result.returncode, json.loads(result.stdout)


def plaintext_left(world) -> list[Path]:
    """Every file under the backup root other than completed ciphertext and records."""
    allowed = {"backup.tar.gpg", "backup.json", "status.json", "runs.log", ".lock"}
    return [p for p in world["root"].rglob("*") if p.is_file() and p.name not in allowed]


def break_database(world) -> None:
    text = world["production_env"].read_text()
    world["production_env"].write_text(text.replace(world["production"], "jannenkoti_missing"))


@pytest.mark.parametrize("fault", ["dump", "encryption", "required file"])
def test_failed_attempt_never_replaces_the_last_success(world, fault):
    assert create(world).returncode == 0
    [first] = completed(world)
    code, before = status(world)
    assert code == 0 and before["healthy"]

    if fault == "dump":
        break_database(world)
    elif fault == "encryption":
        world["keys"]["public"].write_text("not a key\n")
    else:
        world["gateway_env"].unlink()
    try:
        result = create(world)
    finally:
        if fault == "encryption":
            subprocess.run(
                ["gpg", "--batch", "--homedir", str(world["keys"]["home"]), "--armor", "--export"],
                check=True,
                stdout=world["keys"]["public"].open("wb"),
            )
    assert result.returncode == 1
    assert "failed" in result.stderr
    assert "synthetic-secret" not in result.stderr + result.stdout

    assert completed(world) == [first]
    assert plaintext_left(world) == []
    code, after = status(world)
    assert code == 1
    assert after["last_success"] == before["last_success"]
    assert after["last_attempt"]["result"] == "failed"
    assert after["last_attempt"]["id"] != first.name
    assert not after["healthy"]


def test_killed_attempt_is_cleaned_up_and_never_restorable(world):
    leftover = world["root"] / "incomplete" / "20261101T031500Z"
    (leftover / "staging").mkdir(parents=True)
    (leftover / "staging" / "postgresql.dump").write_bytes(b"plaintext household data")
    (leftover / "backup.tar.gpg").write_bytes(b"partial ciphertext")

    restore_dir = world["tmp"] / "never"
    refused = restore(world, leftover, restore_target(world), restore_dir)
    assert refused.returncode == 2
    assert "not a completed backup" in refused.stderr
    assert not restore_dir.exists()

    result = create(world)
    assert result.returncode == 0, result.stderr
    assert not leftover.exists()
    assert "Removed incomplete attempt 20261101T031500Z" in result.stderr
    assert plaintext_left(world) == []
    assert [path.name for path in completed(world)] != ["20261101T031500Z"]


def test_truncated_copy_of_a_completed_backup_is_refused(world):
    assert create(world).returncode == 0
    [backup] = completed(world)
    copy = world["tmp"] / "copy" / backup.name
    shutil.copytree(backup, copy)
    artifact = copy / "backup.tar.gpg"
    artifact.write_bytes(artifact.read_bytes()[:-100])

    restore_dir = world["tmp"] / "never"
    result = restore(world, copy, restore_target(world), restore_dir)
    assert result.returncode == 2
    assert "incomplete or corrupted" in result.stderr
    assert not restore_dir.exists()


def fake_completed(world, moment) -> str:
    name = moment.strftime("%Y%m%dT%H%M%SZ")
    path = world["root"] / "daily" / name
    path.mkdir(parents=True)
    (path / "backup.tar.gpg").write_bytes(b"older ciphertext")
    (path / "backup.json").write_text('{"status": "complete"}\n')
    return name


def test_retention_keeps_the_newest_backup_of_each_of_14_days_only(world):
    from datetime import UTC, datetime, timedelta

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    older = [
        fake_completed(world, today - timedelta(days=d, hours=-1, minutes=-15))
        for d in range(1, 17)
    ]
    yesterday_noon = fake_completed(world, today - timedelta(days=1) + timedelta(hours=12))
    unrelated = world["root"] / "daily" / "notes"
    unrelated.mkdir()
    release_backup = world["tmp"] / "releases" / "backups" / "80fcf23461c5-20261119T105708.dump"
    release_backup.parent.mkdir(parents=True)
    release_backup.write_bytes(b"pre-migration dump")

    result = create(world)
    assert result.returncode == 0, result.stderr

    names = {path.name for path in completed(world)} - {"notes"}
    assert len(names) == 14
    assert yesterday_noon in names
    assert older[0] not in names  # yesterday 01:15 superseded by yesterday's noon backup
    assert set(older[1:13]) <= names  # 2..13 days ago
    assert not set(older[13:]) & names  # 14..16 days ago
    assert unrelated.is_dir()
    assert release_backup.read_bytes() == b"pre-migration dump"
    _, summary = status(world)
    assert len(summary["completed"]) == 14
    assert sorted(summary["last_attempt"]["pruned"]) == sorted([older[0], *older[13:]])


def test_status_reports_a_success_older_than_36_hours_as_stale(world):
    assert status(world)[0] == 1  # nothing recorded yet
    assert create(world).returncode == 0
    assert status(world)[0] == 0

    record = json.loads((world["root"] / "status.json").read_text())
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(hours=37)).isoformat(timespec="seconds")
    record["last_success"]["finished_at"] = old
    (world["root"] / "status.json").write_text(json.dumps(record))
    code, summary = status(world)
    assert code == 1
    assert any("older than 36 hours" in problem for problem in summary["problems"])


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("DATABASE_URL={production_url}\n", "RECOVERY_RESTORE_TARGET=isolated"),
        ("DATABASE_URL={production_url}\nRECOVERY_RESTORE_TARGET=isolated\n", "recovery_test_"),
        (
            "DATABASE_URL=postgresql+psycopg://u:p@192.168.1.50/recovery_test_x\n"
            "RECOVERY_RESTORE_TARGET=isolated\n",
            "local socket or loopback",
        ),
        ("RECOVERY_RESTORE_TARGET=isolated\n", "PostgreSQL DATABASE_URL"),
    ],
)
def test_restore_refuses_production_and_ambiguous_targets(world, target, reason):
    assert create(world).returncode == 0
    [backup] = completed(world)
    production_url = f"postgresql+psycopg:///{world['production']}?host={world['socket']}"
    target_env = world["tmp"] / "target.env"
    target_env.write_text(target.format(production_url=production_url))

    restore_dir = world["tmp"] / "never"
    result = restore(world, backup, target_env, restore_dir)
    assert result.returncode == 2
    assert reason in result.stderr
    assert not restore_dir.exists()
    assert (
        psql(world["socket"], world["production"], "SELECT count(*) FROM sensor_locations") == "3"
    )


def test_restore_refuses_a_nonempty_database_or_directory(world):
    assert create(world).returncode == 0
    [backup] = completed(world)

    occupied = world["tmp"] / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("owner file\n")
    result = restore(world, backup, restore_target(world), occupied)
    assert result.returncode == 2
    assert (occupied / "keep.txt").read_text() == "owner file\n"

    target = restore_target(world)
    psql(world["socket"], target_database(target), "CREATE TABLE unrelated (id int)")
    restore_dir = world["tmp"] / "restored"
    result = restore(world, backup, target, restore_dir)
    assert result.returncode == 1
    assert "must be empty" in result.stderr
    assert not restore_dir.exists()


def test_restore_without_the_private_key_fails_and_leaves_no_plaintext(world, tmp_path_factory):
    assert create(world).returncode == 0
    [backup] = completed(world)
    empty_home = Path(tmp_path_factory.mktemp("nokey"))
    empty_home.chmod(0o700)

    restore_dir = world["tmp"] / "restored"
    result = command(
        world,
        "restore",
        str(backup),
        "--target-env",
        str(restore_target(world)),
        "--restore-dir",
        str(restore_dir),
        "--gnupg-home",
        str(empty_home),
    )
    assert result.returncode == 1
    assert "private key" in result.stderr
    assert not restore_dir.exists()


def test_restored_app_settings_select_development_mode_and_restored_copies(world):
    assert create(world).returncode == 0
    [backup] = completed(world)
    target = restore_target(world)
    restore_dir = world["tmp"] / "restored"
    assert restore(world, backup, target, restore_dir).returncode == 0

    overrides = dict(
        line.split("=", 1)
        for line in (restore_dir / "isolated-overrides.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert overrides["APP_ENV"] == "development"
    assert target_database(target) in overrides["DATABASE_URL"]
    assert Path(overrides["DB_PATH"]).is_relative_to(restore_dir)
    assert Path(overrides["VILLENKOTI_DB_PATH"]).is_relative_to(restore_dir)
    assert (restore_dir / "isolated-overrides.env").stat().st_mode & 0o077 == 0
    assert restore_dir.stat().st_mode & 0o077 == 0


SYSTEMD = ROOT / "docs" / "operations" / "systemd"


def unit(path: Path) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current = None
    lines = path.read_text().replace("\\\n", " ").splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
        else:
            key, _, value = line.partition("=")
            current.setdefault(key, []).append(" ".join(value.split()))
    return sections


def test_timer_runs_the_backup_daily_at_0315_helsinki_and_catches_up():
    timer = unit(SYSTEMD / "jannenkoti-backup.timer.example")
    assert timer["Timer"]["OnCalendar"] == ["*-*-* 03:15:00 Europe/Helsinki"]
    assert timer["Timer"]["Persistent"] == ["true"]
    assert timer["Timer"]["Unit"] == ["jannenkoti-backup.service"]
    assert timer["Install"]["WantedBy"] == ["timers.target"]

    analyze = shutil.which("systemd-analyze")
    if analyze:
        result = subprocess.run(
            [analyze, "calendar", "--iterations=2", timer["Timer"]["OnCalendar"][0]],
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "Europe/Helsinki", "LC_ALL": "C"},
        )
        assert result.returncode == 0, result.stderr
        elapses = [
            line
            for line in result.stdout.splitlines()
            if "Next elapse" in line or "Iteration #2" in line
        ]
        assert len(elapses) == 2
        assert all("03:15:00 EET" in line or "03:15:00 EEST" in line for line in elapses)


def test_backup_service_runs_the_current_release_command_once_privately():
    service = unit(SYSTEMD / "jannenkoti-backup.service.example")["Service"]
    assert service["Type"] == ["oneshot"]
    assert service["User"] == ["jannesi"]
    assert service["UMask"] == ["0077"]
    [start] = service["ExecStart"]
    assert start.split()[:3] == [
        "/home/jannesi/jannenkoti-releases/current/venv/bin/python",
        "/home/jannesi/jannenkoti-releases/current/code/scripts/recovery_backup.py",
        "create",
    ]
    # The defaults the unit relies on are the documented production locations.
    assert (ROOT / "docs/operations/production-host/recovery-files.txt").is_file()


def stand_in(world, name: str, script: str) -> dict[str, str]:
    """Put an executable ``name`` first on PATH; returns the env for ``create``."""
    directory = world["tmp"] / "stand-ins"
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return {"PATH": f"{directory}:{os.environ['PATH']}"}


def test_failed_pg_dump_and_mid_stream_encryption_leave_no_backup_or_plaintext(world):
    assert create(world).returncode == 0
    [first] = completed(world)

    failing_dump = stand_in(
        world,
        "pg_dump",
        'for a; do case "$prev" in --file) printf partial > "$a";; esac; prev="$a"; done\nexit 1\n',
    )
    result = create(world, env=failing_dump)
    assert result.returncode == 1
    assert "database dump failed" in result.stderr
    assert completed(world) == [first]
    assert plaintext_left(world) == []

    real_gpg = shutil.which("gpg")
    shutil.rmtree(world["tmp"] / "stand-ins")
    failing_gpg = stand_in(
        world,
        "gpg",
        'case " $* " in *" --encrypt "*)\n'
        '  for a; do case "$prev" in --output) head -c 100 > "$a";; esac; prev="$a"; done\n'
        "  exit 2;;\nesac\n"
        f'exec {real_gpg} "$@"\n',
    )
    result = create(world, env=failing_gpg)
    assert result.returncode == 1
    assert "encryption failed" in result.stderr
    assert completed(world) == [first]
    assert plaintext_left(world) == []
    code, summary = status(world)
    assert code == 1
    assert summary["last_success"]["id"] == first.name


def start_slow_backup(world):
    started = world["tmp"] / "dump-started"
    env = stand_in(world, "pg_dump", f'touch "{started}"\nexec sleep 60\n')
    process = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "create",
            "--backup-root",
            str(world["root"]),
            "--production-env",
            str(world["production_env"]),
            "--manifest",
            str(world["manifest"]),
            "--recipient",
            str(world["keys"]["public"]),
        ],
        env={"PATH": env["PATH"], "HOME": str(world["tmp"]), "TZ": "UTC"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    import time

    deadline = time.monotonic() + 30
    while not started.exists():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return process


def test_sigterm_removes_staging_and_records_an_interrupted_attempt(world):
    import signal

    process = start_slow_backup(world)
    assert status(world)[1]["last_attempt"]["result"] == "running"
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)
    assert process.returncode == 1
    assert completed(world) == []
    assert plaintext_left(world) == []
    assert not any((world["root"] / "incomplete").iterdir())
    code, summary = status(world)
    assert code == 1
    assert summary["last_attempt"]["result"] == "failed"
    assert summary["last_attempt"]["error"] == "interrupted"


def test_hard_killed_attempt_is_reported_as_not_finished(world):
    assert create(world).returncode == 0
    process = start_slow_backup(world)
    process.kill()
    process.communicate(timeout=30)

    code, summary = status(world)
    assert code == 1
    assert any("did not finish" in problem for problem in summary["problems"])
    assert summary["last_success"]["id"] == completed(world)[0].name


def test_status_with_unreadable_metadata_reports_a_problem_not_a_traceback(world):
    world["root"].mkdir()
    (world["root"] / "status.json").write_text('{"last_success": {"finished_at": "soon"}}')
    result = command(world, "status", "--backup-root", str(world["root"]))
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "unreadable" in result.stdout


def test_retention_days_are_helsinki_days_whatever_the_process_time_zone(world):
    from datetime import UTC, datetime, timedelta

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # 22:30 UTC is already the next day in Helsinki (UTC+2/+3); noon UTC is not.
    late = fake_completed(world, today - timedelta(days=3) + timedelta(hours=22, minutes=30))
    noon = fake_completed(world, today - timedelta(days=2) + timedelta(hours=12))

    assert create(world).returncode == 0  # the command runs with TZ=UTC
    names = {path.name for path in completed(world)}
    assert noon in names
    assert late not in names


def test_failed_restore_keeps_a_restore_directory_the_owner_created(world, tmp_path_factory):
    assert create(world).returncode == 0
    [backup] = completed(world)
    empty_home = Path(tmp_path_factory.mktemp("nokey2"))
    empty_home.chmod(0o700)
    restore_dir = world["tmp"] / "prepared"
    restore_dir.mkdir()

    result = command(
        world,
        "restore",
        str(backup),
        "--target-env",
        str(restore_target(world)),
        "--restore-dir",
        str(restore_dir),
        "--gnupg-home",
        str(empty_home),
    )
    assert result.returncode == 1
    assert restore_dir.is_dir()
    assert list(restore_dir.iterdir()) == []
