"""Inspecting production from the desktop through ``scripts/ops.py``.

Tests drive the command as a subprocess from outside the checkout. A stand-in
``ssh`` first on ``PATH`` records how it was called and runs the remote script
locally with ``sh``, the way the Pi's ``sh`` would after its login shell (fish)
received the command. On that "Pi" side, stand-in ``systemctl``, ``journalctl``
and ``id`` replace the service manager, journal and account, a temporary release
root holds releases whose ``release.py`` is this repository's, and local HTTP
servers play the app and gateway health endpoints. Nothing reaches the real Pi,
service manager, journal or production ports.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops.py"
APP_UNIT, GATEWAY_UNIT = "jannenkoti.service", "esp32_ws.service"
CURRENT, PREVIOUS = "aaaa11112222", "bbbb33334444"
CURRENT_REVISION = CURRENT + "c" * 28
PREVIOUS_REVISION = PREVIOUS + "d" * 28

FAKE_SSH = """\
#!{python}
import json, os, sys
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
host, command = args[separator + 1], args[separator + 2:]
os.execv("/bin/sh", ["sh", "-c", " ".join(command)])
"""

FAKE_SYSTEMCTL = """\
#!{python}
import json, sys
from pathlib import Path

controls = Path({controls!r})
with open(controls / "systemctl.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
states = json.loads((controls / "units.json").read_text())
if sys.argv[1:2] != ["is-active"]:
    sys.exit(1)
sys.exit(0 if all(states.get(unit) == "active" for unit in sys.argv[2:]) else 3)
"""

FAKE_JOURNALCTL = """\
#!{python}
import sys
from pathlib import Path

controls = Path({controls!r})
with open(controls / "journalctl.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
lines = int((controls / "journal-lines").read_text())
hint = controls / "journal-hint"
if hint.exists():
    print(hint.read_text().strip(), file=sys.stderr)
for number in range(lines):
    print(f"Nov 26 21:49:{{number % 60:02d}} raspberrypi gunicorn[1]: journal line {{number}}")
"""

FAKE_ID = """\
#!/bin/sh
case "$1" in
  -u) echo 1000 ;;
  -Gn) cat {groups} ;;
  *) echo "unexpected id $*" >&2; exit 1 ;;
esac
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HealthServer:
    """A local stand-in for one health endpoint answering a mutable response."""

    def __init__(self, path: str, body: dict):
        self.path, self.body, self.http = path, body, 200
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                found = self.path == server.path
                payload = json.dumps(server.body if found else {}).encode()
                self.send_response(server.http if found else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def make_release(root: Path, name: str, revision: str) -> Path:
    directory = root / "releases" / name
    (directory / "code").mkdir(parents=True)
    (directory / "code" / "scripts").symlink_to(ROOT / "scripts")
    (directory / "venv" / "bin").mkdir(parents=True)
    write_executable(
        directory / "venv" / "bin" / "python", f'#!/bin/sh\nexec {sys.executable} "$@"\n'
    )
    # Not "prepared": status then reports the release without re-evaluating checks.
    record = {"candidate": name, "revision": revision, "state": "deployed-stand-in"}
    (directory / "release.json").write_text(json.dumps(record))
    return directory


class Pi:
    def __init__(self, tmp_path: Path):
        self.controls = tmp_path / "controls"
        self.bin = tmp_path / "bin"
        self.root = tmp_path / "pi" / "jannenkoti-releases"
        self.elsewhere = tmp_path / "elsewhere"
        for directory in (self.controls, self.bin, self.root, self.elsewhere):
            directory.mkdir(parents=True)
        python = sys.executable
        controls = str(self.controls)
        write_executable(self.bin / "ssh", FAKE_SSH.format(python=python, controls=controls))
        write_executable(
            self.bin / "systemctl", FAKE_SYSTEMCTL.format(python=python, controls=controls)
        )
        write_executable(
            self.bin / "journalctl", FAKE_JOURNALCTL.format(python=python, controls=controls)
        )
        write_executable(self.bin / "id", FAKE_ID.format(groups=self.controls / "groups"))
        self.ssh(unreachable=False)
        self.units(active=True)
        self.groups("jannesi adm sudo")
        self.journal(5)
        make_release(self.root, CURRENT, CURRENT_REVISION)
        make_release(self.root, PREVIOUS, PREVIOUS_REVISION)
        (self.root / "current").symlink_to(f"releases/{CURRENT}")
        (self.root / "previous").symlink_to(f"releases/{PREVIOUS}")
        self.app = HealthServer("/healthz", {"revision": CURRENT_REVISION, "database": "ok"})
        self.gateway = HealthServer("/health", {"revision": CURRENT_REVISION, "status": "healthy"})

    def ssh(self, *, unreachable: bool) -> None:
        (self.controls / "ssh.json").write_text(json.dumps({"unreachable": unreachable}))

    def units(self, *, active: bool) -> None:
        state = "active" if active else "inactive"
        (self.controls / "units.json").write_text(
            json.dumps({APP_UNIT: state, GATEWAY_UNIT: state})
        )

    def groups(self, groups: str) -> None:
        (self.controls / "groups").write_text(groups + "\n")

    def journal(self, lines: int) -> None:
        (self.controls / "journal-lines").write_text(str(lines))

    def calls(self, name: str) -> list[str]:
        log = self.controls / f"{name}.log"
        return log.read_text().splitlines() if log.exists() else []

    def ops(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("JANNENKOTI_", "PG", "DATABASE_URL"))
        }
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        stand_ins = [
            "--release-root",
            str(self.root),
            # Never the real backup roots: status also reports recovery coverage.
            "--backup-root",
            str(self.controls / "pi-backups"),
            "--copy-root",
            str(self.controls / "backup-copies"),
            "--service-manager",
            str(self.bin / "systemctl"),
            "--app-url",
            self.app.url,
            "--gateway-url",
            self.gateway.url,
        ]
        command, *rest = args
        return subprocess.run(
            [sys.executable, str(OPS), command, *stand_ins, *rest],
            cwd=self.elsewhere,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def close(self):
        self.app.stop()
        self.gateway.stop()


@pytest.fixture
def pi(tmp_path):
    world = Pi(tmp_path)
    yield world
    world.close()


def assert_inspection_only(pi: Pi) -> None:
    """Inspection may ask whether units are active but never changes them."""
    for call in pi.calls("systemctl"):
        assert call.split()[0] == "is-active", call


def test_status_reports_the_running_releases_and_their_health_through_raspi(pi):
    result = pi.ops("status")

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"current       {CURRENT} revision {CURRENT_REVISION}" in out
    assert f"previous      {PREVIOUS} revision {PREVIOUS_REVISION}" in out
    assert f"app           revision {CURRENT_REVISION}, database ok" in out
    assert f"gateway       revision {CURRENT_REVISION}, status healthy" in out
    assert f"{APP_UNIT} active" in out and f"{GATEWAY_UNIT} active" in out
    assert "healthy       yes" in out
    # No backups exist here: coverage needs attention, production stays healthy.
    assert "Recovery backups" in out and "coverage      attention" in out
    (call,) = [json.loads(line) for line in pi.calls("ssh")]
    assert "BatchMode=yes" in call
    # The Pi's login shell is fish: the remote command is a fixed POSIX entry point.
    assert call[call.index("--") + 1 :] == ["raspi", "sh", "-s"]
    assert_inspection_only(pi)


def test_a_process_of_another_revision_or_a_failed_database_check_is_not_healthy(pi):
    pi.gateway.body = {"revision": PREVIOUS_REVISION, "status": "healthy"}
    pi.app.body, pi.app.http = {"revision": CURRENT_REVISION, "database": "unavailable"}, 503

    result = pi.ops("status")

    assert result.returncode == 1
    assert "healthy       no" in result.stdout
    assert f"gateway runs revision {PREVIOUS_REVISION}" in result.stdout
    assert "app database is unavailable" in result.stdout
    assert "healthy       yes" not in result.stdout


def test_stopped_services_and_unanswered_health_checks_are_not_healthy(pi):
    pi.units(active=False)
    pi.app.stop()
    pi.gateway.stop()

    result = pi.ops("status", "--json")

    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["healthy"] is False and summary["reachable"] is True
    problems = "\n".join(summary["problems"])
    assert f"{APP_UNIT} is not active" in problems
    assert f"{GATEWAY_UNIT} is not active" in problems
    assert "app health check" in problems and "unreachable" in problems
    assert summary["production"]["current"]["revision"] == CURRENT_REVISION
    assert_inspection_only(pi)


def test_an_unreachable_pi_is_reported_as_a_transport_failure_not_health(pi):
    pi.ssh(unreachable=True)

    result = pi.ops("status")

    assert result.returncode == 3
    assert "cannot reach production through raspi" in result.stderr
    assert "No route to host" in result.stderr
    assert "health unknown" in result.stderr
    assert "healthy       yes" not in result.stdout


def test_a_missing_current_release_is_unknown_rather_than_healthy(pi):
    (pi.root / "current").unlink()

    result = pi.ops("status", "--json")

    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["healthy"] is False
    assert summary["problems"] == [f"no current release under {pi.root}"]


def test_unreadable_release_status_is_unknown_rather_than_healthy(pi):
    tool = pi.root / "releases" / CURRENT / "code" / "scripts"
    tool.unlink()
    tool.mkdir()
    write_executable(tool / "release.py", "print('not json')\n")

    result = pi.ops("status")

    assert result.returncode == 1
    assert "release status was not readable" in result.stderr
    assert "healthy       yes" not in result.stdout


def test_arguments_that_could_become_ssh_options_are_refused(pi):
    result = pi.ops("status", "--host", "-oProxyCommand=touch pwned")

    assert result.returncode == 2
    assert pi.calls("ssh") == []


@pytest.mark.parametrize(
    ("which", "units"),
    [("app", [APP_UNIT]), ("gateway", [GATEWAY_UNIT]), ("services", [APP_UNIT, GATEWAY_UNIT])],
)
def test_recent_journal_lines_of_the_app_and_gateway(pi, which, units):
    result = pi.ops("logs", which, "-n", "3", "--since", "1 hour ago")

    assert result.returncode == 0, result.stderr
    assert "journal line 4" in result.stdout
    (call,) = pi.calls("journalctl")
    arguments = call.split()
    assert [arguments[i + 1] for i, a in enumerate(arguments) if a == "-u"] == units
    assert arguments[arguments.index("-n") + 1] == "3"
    assert "--no-pager" in arguments and "-f" not in arguments
    assert call.endswith("--since 1 hour ago")
    assert_inspection_only(pi)


def test_journal_output_is_bounded_and_the_cut_is_reported(pi):
    pi.journal(5000)

    result = pi.ops("logs", "app", "-n", "5000", "--max-bytes", "4096")

    assert result.returncode == 0, result.stderr
    assert len(result.stdout.encode()) <= 4096
    assert "truncated at 4096 bytes" in result.stderr


def test_following_the_journal_streams_until_it_ends(pi):
    result = pi.ops("logs", "gateway", "--follow")

    assert result.returncode == 0, result.stderr
    assert "journal line 4" in result.stdout
    (call,) = pi.calls("journalctl")
    assert "-f" in call.split()


def test_an_account_without_journal_access_is_a_clear_permission_failure(pi):
    pi.groups("jannesi sudo")

    result = pi.ops("logs", "app")

    assert result.returncode == 3
    assert "cannot read the system journal" in result.stderr
    assert "adm, systemd-journal or wheel" in result.stderr
    assert pi.calls("journalctl") == []


@pytest.mark.parametrize("lines", ["0", "5001", "many"])
def test_unbounded_or_invalid_line_counts_are_refused(pi, lines):
    result = pi.ops("logs", "app", "-n", lines)

    assert result.returncode == 2
    assert pi.calls("ssh") == []


def test_journal_options_cannot_be_smuggled_through_since(pi):
    result = pi.ops("logs", "app", "--since", "--file=/etc/shadow")

    assert result.returncode == 2
    assert pi.calls("ssh") == []


def operation(name: str, candidate: str, state: str, steps: list[str], **extra) -> dict:
    return {
        "operation": name,
        "candidate": candidate,
        "revision": CURRENT_REVISION,
        "replaced": PREVIOUS,
        "state": state,
        "started_at": "2026-11-19T10:57:30.000+00:00",
        "finished_at": "2026-11-19T10:57:37.000+00:00" if state != "started" else None,
        "steps": steps,
        "problems": extra.get("problems", []),
        "guidance": extra.get("guidance", []),
    }


def test_recent_release_operations_with_their_steps_and_problems(pi):
    older = operation("restart", PREVIOUS, "succeeded", ["2026-11-01T08:00:00 older step"])
    failed = operation(
        "deploy",
        CURRENT,
        "recovered",
        ["2026-11-19T10:57:32 Maintenance window starts"],
        problems=["app health check returned HTTP 503"],
        guidance=["Inspect the candidate logs."],
    )
    (pi.root / "operations.log").write_text(
        "".join(json.dumps(entry) + "\n" for entry in (older, failed))
    )
    (pi.root / "operation.json").write_text(json.dumps(failed, indent=2))

    result = pi.ops("logs", "release", "-n", "1")

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"deploy {CURRENT} recovered" in out
    assert "Maintenance window starts" in out
    assert "problem: app health check returned HTTP 503" in out
    assert "Inspect the candidate logs." in out
    assert "older step" not in out
    assert out.count(f"deploy {CURRENT}") == 1


def test_an_unfinished_release_operation_is_shown_after_the_finished_ones(pi):
    running = operation("deploy", CURRENT, "started", ["2026-11-26T09:00:00 Applying migrations"])
    (pi.root / "operation.json").write_text(json.dumps(running, indent=2))

    result = pi.ops("logs", "release")

    assert result.returncode == 0, result.stderr
    assert "No finished release operations" in result.stdout
    assert f"deploy {CURRENT} started, not finished" in result.stdout
    assert "Applying migrations" in result.stdout


def test_release_logs_without_a_release_root_are_reported(pi):
    result = pi.ops("logs", "release", "--release-root", str(pi.root.parent / "missing"))

    assert result.returncode == 1
    assert "no release root" in result.stderr


def test_release_logs_refuse_journal_only_options(pi):
    result = pi.ops("logs", "release", "--follow")

    assert result.returncode == 2
    assert pi.calls("ssh") == []


def test_journal_hints_about_missing_access_reach_the_desktop(pi):
    pi.journal(0)
    hint = "No journal files were opened due to insufficient permissions."
    (pi.controls / "journal-hint").write_text(hint)

    result = pi.ops("logs", "app")

    assert hint in result.stderr
