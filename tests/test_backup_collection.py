"""Collecting Pi recovery backups onto the desktop, and reporting coverage.

Tests drive ``scripts/ops.py`` as a subprocess from outside the checkout. A
stand-in ``ssh`` first on ``PATH`` runs each remote script locally with ``sh``,
and can refuse connections or cut a transfer off midway. The "Pi" is a
temporary release root whose current release carries this repository's
scripts, and a temporary backup root; the desktop's copies go to another
temporary directory. End-to-end tests create real encrypted backups with
``recovery_backup.py create`` from synthetic PostgreSQL, SQLite and
configuration state, and restore the desktop copy with ``restore`` into an
isolated database. No real Pi, key or desktop state is involved.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import test_recovery_backup as recovery
from test_recovery_backup import create, psql, restore, restore_target, target_database

# Synthetic production state, a disposable cluster and a throwaway key.
postgres, keys, world = recovery.postgres, recovery.keys, recovery.world

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops.py"
ID_FORMAT = "%Y%m%dT%H%M%SZ"

FAKE_SSH = """\
#!{python}
import json, os, subprocess, sys
from pathlib import Path

controls = Path({controls!r})
config = json.loads((controls / "ssh.json").read_text())
args = sys.argv[1:]
with open(controls / "ssh.log", "a") as log:
    log.write(json.dumps(args) + "\\n")
if config.get("unreachable"):
    print("ssh: connect to host 192.0.2.50 port 22: No route to host", file=sys.stderr)
    sys.exit(255)
separator = args.index("--")
command = " ".join(args[separator + 2:])
limit = config.get("cut_after")
if limit is None:
    os.execv("/bin/sh", ["sh", "-c", command])
process = subprocess.Popen(["sh", "-c", command], stdout=subprocess.PIPE)
sent = 0
while sent < limit:
    chunk = process.stdout.read(min(65536, limit - sent))
    if not chunk:
        break
    sys.stdout.buffer.write(chunk)
    sent += len(chunk)
sys.stdout.flush()
if sent >= limit:
    process.kill()
    print("Connection to 192.0.2.50 closed by remote host.", file=sys.stderr)
    sys.exit(255)
sys.exit(process.wait())
"""


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


class Desktop:
    """The desktop's copy root and the stand-in path to a temporary Pi."""

    def __init__(self, tmp: Path, pi_backups: Path):
        self.tmp = tmp
        self.controls = tmp / "controls"
        self.bin = tmp / "bin"
        self.release_root = tmp / "pi" / "jannenkoti-releases"
        self.pi_backups = pi_backups
        self.copies = tmp / "desktop" / "jannenkoti-backups"
        self.elsewhere = tmp / "elsewhere"
        for directory in (self.controls, self.bin, self.elsewhere):
            directory.mkdir(parents=True, exist_ok=True)
        write_executable(
            self.bin / "ssh",
            FAKE_SSH.format(python=sys.executable, controls=str(self.controls)),
        )
        # Production itself is not under test: its units are stopped stand-ins.
        write_executable(self.bin / "systemctl", "#!/bin/sh\nexit 3\n")
        release = self.release_root / "releases" / "aaaa11112222"
        (release / "code").mkdir(parents=True)
        (release / "code" / "scripts").symlink_to(ROOT / "scripts")
        (release / "venv" / "bin").mkdir(parents=True)
        write_executable(
            release / "venv" / "bin" / "python", f'#!/bin/sh\nexec {sys.executable} "$@"\n'
        )
        (self.release_root / "current").symlink_to("releases/aaaa11112222")
        self.ssh()

    def ssh(self, *, unreachable: bool = False, cut_after: int | None = None) -> None:
        config = {"unreachable": unreachable, "cut_after": cut_after}
        (self.controls / "ssh.json").write_text(json.dumps(config))

    def ops(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("JANNENKOTI_", "PG", "DATABASE_URL"))
        }
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["TZ"] = "UTC"
        command, *rest = args
        if command == "status":
            rest += [
                "--service-manager",
                str(self.bin / "systemctl"),
                "--app-url",
                "http://127.0.0.1:9",
                "--gateway-url",
                "http://127.0.0.1:9",
            ]
        roots = [
            "--release-root",
            str(self.release_root),
            "--backup-root",
            str(self.pi_backups),
            "--copy-root",
            str(self.copies),
        ]
        return subprocess.run(
            [sys.executable, str(OPS), command, *roots, *rest],
            cwd=self.elsewhere,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def copied(self) -> list[str]:
        daily = self.copies / "daily"
        return sorted(path.name for path in daily.iterdir()) if daily.is_dir() else []

    def report(self) -> tuple[int, dict]:
        result = self.ops("backups", "--json")
        return result.returncode, json.loads(result.stdout)


def snapshot(root: Path) -> dict[str, tuple[int, str]]:
    """Every file under ``root`` with its size and checksum."""
    return {
        str(path.relative_to(root)): (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_collected_copy_restores_on_the_desktop_and_counts_as_fresh_coverage(world):
    assert create(world).returncode == 0
    desktop = Desktop(world["tmp"], world["root"])
    before = snapshot(world["root"])

    result = desktop.ops("collect")

    assert result.returncode == 0, result.stderr + result.stdout
    [pi_backup] = sorted(path.name for path in (world["root"] / "daily").iterdir())
    assert desktop.copied() == [pi_backup]
    copy = desktop.copies / "daily" / pi_backup
    assert (copy / "backup.tar.gpg").read_bytes() == (
        world["root"] / "daily" / pi_backup / "backup.tar.gpg"
    ).read_bytes()
    assert (copy / "backup.tar.gpg").stat().st_mode & 0o077 == 0
    # Collection only reads the Pi: nothing there changes, not even status.
    assert snapshot(world["root"]) == before

    target = restore_target(world)
    restored = restore(world, copy, target, world["tmp"] / "restored")
    assert restored.returncode == 0, restored.stderr
    assert "verified" in restored.stdout
    assert psql(
        world["socket"],
        target_database(target),
        "SELECT string_agg(name, ',' ORDER BY id) FROM sensor_locations",
    ) == ("Olohuone,Autotalli,Sauna")

    code, report = desktop.report()
    assert code == 0, report
    assert report["healthy"] is True and report["problems"] == []
    assert report["pi"]["last_success"]["id"] == pi_backup
    assert report["desktop"]["latest_copy"]["id"] == pi_backup
    assert report["desktop"]["latest_copy"]["age_hours"] < 1


def synthetic_backup(root: Path, moment: datetime, content: bytes | None = None) -> str:
    """A completed backup as ``create`` publishes it, with opaque ciphertext."""
    name = moment.strftime(ID_FORMAT)
    directory = root / "daily" / name
    directory.mkdir(parents=True)
    content = content if content is not None else os.urandom(4096) + name.encode()
    (directory / "backup.tar.gpg").write_bytes(content)
    record = {
        "format": 1,
        "id": name,
        "status": "complete",
        "created_at": moment.isoformat(timespec="seconds"),
        "finished_at": (moment + timedelta(seconds=40)).isoformat(timespec="seconds"),
        "artifact": "backup.tar.gpg",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    (directory / "backup.json").write_text(json.dumps(record))
    return name


def pi_succeeded(root: Path, name: str) -> None:
    """Record ``name`` as the Pi's latest successful backup, as ``create`` does."""
    record = json.loads((root / "daily" / name / "backup.json").read_text())
    attempt = {"id": name, "started_at": record["created_at"]}
    status = {
        "last_attempt": attempt | {"finished_at": record["finished_at"], "result": "success"},
        "last_success": {"id": name, "finished_at": record["finished_at"]},
    }
    (root / "status.json").write_text(json.dumps(status))


@pytest.fixture
def desktop(tmp_path):
    pi_backups = tmp_path / "pi" / "jannenkoti-backups"
    pi_backups.mkdir(parents=True)
    return Desktop(tmp_path, pi_backups)


def days_ago(days: float) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(days=days)


def test_an_offline_desktop_catches_up_on_every_retained_backup(desktop):
    older = [synthetic_backup(desktop.pi_backups, days_ago(d)) for d in (3, 2, 1)]
    desktop.ssh(unreachable=True)

    offline = desktop.ops("collect")

    assert offline.returncode == 3
    assert "No route to host" in offline.stderr
    assert desktop.copied() == []
    code, report = desktop.report()
    assert code == 3
    assert report["pi"]["known"] is False
    assert any("Pi backup status unknown" in problem for problem in report["problems"])
    assert any("latest collection" in p and "failed" in p for p in report["problems"])

    newest = synthetic_backup(desktop.pi_backups, days_ago(0.1))
    pi_succeeded(desktop.pi_backups, newest)
    desktop.ssh()

    result = desktop.ops("collect")

    assert result.returncode == 0, result.stderr
    assert desktop.copied() == sorted([*older, newest])
    code, report = desktop.report()
    assert code == 0, report["problems"]
    assert report["desktop"]["latest_copy"]["id"] == newest

    again = desktop.ops("collect")
    assert again.returncode == 0
    assert "up to date" in again.stdout
    assert desktop.copied() == sorted([*older, newest])


def test_an_interrupted_copy_is_never_published_and_the_next_run_completes_it(desktop):
    name = synthetic_backup(desktop.pi_backups, days_ago(0.1), os.urandom(200_000))
    pi_succeeded(desktop.pi_backups, name)
    desktop.ssh(cut_after=50_000)

    cut = desktop.ops("collect")

    assert cut.returncode == 3, cut.stderr
    assert "closed by remote host" in cut.stderr
    assert desktop.copied() == []
    assert list((desktop.copies / "incomplete").iterdir()) == []
    code, report = desktop.report()
    assert code == 1
    assert report["desktop"]["latest_copy"] is None
    assert "no verified desktop copy of a Pi backup" in report["problems"]

    # A copy left behind by a hard kill is removed, never counted.
    leftover = desktop.copies / "incomplete" / name
    leftover.mkdir()
    (leftover / "backup.tar.gpg").write_bytes(b"partial")
    desktop.ssh()

    result = desktop.ops("collect")

    assert result.returncode == 0, result.stderr
    assert f"Removed interrupted copy {name}" in result.stderr
    assert desktop.copied() == [name]
    assert list((desktop.copies / "incomplete").iterdir()) == []
    assert desktop.report()[0] == 0


def test_only_completed_backups_that_match_their_records_are_copied(desktop):
    good = synthetic_backup(desktop.pi_backups, days_ago(2))
    unrecorded = synthetic_backup(desktop.pi_backups, days_ago(1.5))
    (desktop.pi_backups / "daily" / unrecorded / "backup.json").unlink()
    unfinished = synthetic_backup(desktop.pi_backups, days_ago(1))
    record_path = desktop.pi_backups / "daily" / unfinished / "backup.json"
    record = json.loads(record_path.read_text())
    record_path.write_text(json.dumps(record | {"status": "running"}))
    corrupted = synthetic_backup(desktop.pi_backups, days_ago(0.1))
    with (desktop.pi_backups / "daily" / corrupted / "backup.tar.gpg").open("r+b") as handle:
        handle.write(b"flipped")
    attempt = desktop.pi_backups / "incomplete" / days_ago(0).strftime(ID_FORMAT)
    attempt.mkdir(parents=True)
    (attempt / "backup.tar.gpg").write_bytes(b"in progress")

    result = desktop.ops("collect")

    assert result.returncode == 1
    assert f"the copy of {corrupted} does not match its record" in result.stderr
    assert f"Skipped Pi backup {unfinished}: no complete record" in result.stderr
    assert desktop.copied() == [good]
    _, report = desktop.report()
    last = report["desktop"]["last_collection"]
    assert last["result"] == "failed" and last["copied"] == [good]


def test_desktop_copies_keep_the_newest_backup_of_each_of_14_days(desktop):
    # The desktop already holds 16 days of copies; the Pi keeps its own 14.
    held = [synthetic_backup(desktop.copies, days_ago(d)) for d in range(3, 19)]
    early_today = days_ago(0).replace(hour=0, minute=30)  # 02:30 Helsinki
    superseded = synthetic_backup(desktop.copies, early_today)
    newest = synthetic_backup(desktop.pi_backups, early_today + timedelta(hours=1))
    older_on_pi = synthetic_backup(desktop.pi_backups, days_ago(30))  # outside retention
    pi_succeeded(desktop.pi_backups, newest)
    notes = desktop.copies / "daily" / "notes"
    notes.mkdir()

    result = desktop.ops("collect")

    assert result.returncode == 0, result.stderr
    copies = set(desktop.copied()) - {"notes"}
    assert len(copies) == 14
    assert newest in copies and superseded not in copies
    assert older_on_pi not in copies  # never copied just to be pruned
    assert set(held[:13]) <= copies and not set(held[13:]) & copies
    assert notes.is_dir()
    _, report = desktop.report()
    assert sorted(report["desktop"]["last_collection"]["pruned"]) == sorted(
        [superseded, *held[13:]]
    )
    assert report["desktop"]["last_collection"]["copied"] == [newest]


def test_a_recent_copy_of_an_old_backup_is_still_stale_coverage(desktop):
    stale = synthetic_backup(desktop.pi_backups, days_ago(2))  # 48 h old
    pi_succeeded(desktop.pi_backups, stale)

    assert desktop.ops("collect").returncode == 0  # copied just now
    code, report = desktop.report()

    assert code == 1
    latest = report["desktop"]["latest_copy"]
    assert latest["id"] == stale and latest["age_hours"] > 36
    assert latest["copied_at"] >= days_ago(0.01).isoformat()
    problems = "\n".join(report["problems"])
    assert "Pi: latest successful backup is older than 36 hours" in problems
    assert f"newest desktop copy holds backup {stale}" in problems
    assert "older than 36 hours" in problems
    text = desktop.ops("backups")
    assert text.returncode == 1
    assert "coverage      attention" in text.stdout


def test_a_damaged_desktop_copy_is_not_counted_as_coverage(desktop):
    older = synthetic_backup(desktop.pi_backups, days_ago(1))
    newest = synthetic_backup(desktop.pi_backups, days_ago(0.1))
    pi_succeeded(desktop.pi_backups, newest)
    assert desktop.ops("collect").returncode == 0
    (desktop.copies / "daily" / newest / "backup.tar.gpg").write_bytes(b"truncated")

    code, report = desktop.report()

    assert code == 1
    assert report["desktop"]["latest_copy"]["id"] == older
    assert f"desktop copy {newest} is damaged or incomplete; not counted" in report["problems"]


def test_a_failed_pi_backup_shows_even_when_older_copies_are_fresh(desktop):
    name = synthetic_backup(desktop.pi_backups, days_ago(0.5))
    pi_succeeded(desktop.pi_backups, name)
    status = json.loads((desktop.pi_backups / "status.json").read_text())
    status["last_attempt"] = {
        "id": days_ago(0.01).strftime(ID_FORMAT),
        "result": "failed",
        "error": "database dump failed",
    }
    (desktop.pi_backups / "status.json").write_text(json.dumps(status))
    assert desktop.ops("collect").returncode == 0

    code, report = desktop.report()

    assert code == 1
    assert report["desktop"]["latest_copy"]["id"] == name
    assert any("failed: database dump failed" in p for p in report["problems"])


def test_operations_status_reports_coverage_beside_production_health(desktop):
    """Production health still decides ``status``; coverage is shown with it."""
    name = synthetic_backup(desktop.pi_backups, days_ago(0.2))
    pi_succeeded(desktop.pi_backups, name)
    # The stand-in release has no running services to report: production is
    # unhealthy, and the recovery section is still reported in full.
    result = desktop.ops("status", "--json")

    summary = json.loads(result.stdout)
    assert result.returncode == 1
    recovery = summary["recovery"]
    assert recovery["pi"]["last_success"]["id"] == name
    assert recovery["desktop"]["latest_copy"] is None
    assert "no verified desktop copy of a Pi backup" in recovery["problems"]

    text = desktop.ops("status")
    assert "Recovery backups" in text.stdout
    assert f"pi backup     {name}" in text.stdout
    assert "desktop copy  none" in text.stdout


def test_an_unreachable_pi_leaves_its_backups_explicitly_unknown_in_status(desktop):
    synthetic_backup(desktop.pi_backups, days_ago(0.2))
    assert desktop.ops("collect").returncode == 0
    desktop.ssh(unreachable=True)

    result = desktop.ops("status", "--json")

    assert result.returncode == 3
    recovery = json.loads(result.stdout)["recovery"]
    assert recovery["healthy"] is False
    assert recovery["pi"]["known"] is False
    assert "cannot reach production" in recovery["pi"]["error"]
    assert recovery["desktop"]["latest_copy"] is not None  # still the desktop's own state
    text = desktop.ops("backups")
    assert text.returncode == 3
    assert "pi backup     unknown (cannot reach production" in text.stdout


def test_a_second_collection_while_one_runs_changes_nothing(desktop):
    import fcntl

    desktop.copies.mkdir(parents=True)
    with (desktop.copies / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = desktop.ops("collect")
    assert result.returncode == 5
    assert "another collection is running" in result.stderr
    assert not (desktop.copies / "status.json").exists()


SYSTEMD = ROOT / "docs" / "operations" / "systemd"


def test_desktop_timer_collects_hourly_and_soon_after_startup():
    timer = recovery.unit(SYSTEMD / "jannenkoti-backup-collect.timer.example")
    assert timer["Timer"]["OnCalendar"] == ["hourly"]
    assert timer["Timer"]["OnStartupSec"] == ["5min"]
    assert timer["Timer"]["Unit"] == ["jannenkoti-backup-collect.service"]
    assert timer["Install"]["WantedBy"] == ["timers.target"]
    service = recovery.unit(SYSTEMD / "jannenkoti-backup-collect.service.example")["Service"]
    assert service["Type"] == ["oneshot"]
    assert service["UMask"] == ["0077"]
    [start] = service["ExecStart"]
    command = start.split()
    assert command[0] == "/usr/bin/python3"
    assert command[1].endswith("/scripts/ops.py") and command[2:] == ["collect"]

    analyze = shutil.which("systemd-analyze")
    if analyze:
        result = subprocess.run(
            [analyze, "calendar", "--iterations=2", "hourly"],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )
        assert result.returncode == 0, result.stderr
        assert "*-*-* *:00:00" in result.stdout


def test_the_pi_backup_schedule_does_not_depend_on_the_desktop():
    pi_timer = recovery.unit(SYSTEMD / "jannenkoti-backup.timer.example")["Timer"]
    pi_service = recovery.unit(SYSTEMD / "jannenkoti-backup.service.example")
    assert pi_timer["OnCalendar"] == ["*-*-* 03:15:00 Europe/Helsinki"]
    dependencies = " ".join(
        " ".join(values) for section in pi_service.values() for values in section.values()
    )
    assert "collect" not in dependencies and "desktop" not in dependencies.lower()
