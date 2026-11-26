#!/usr/bin/env python3
"""Inspect Pi production from the desktop through the ``raspi`` SSH alias.

    ops.py status [--json]
    ops.py logs app|gateway|services|release [-n LINES] [--since TIME] [--follow]

``status`` reports the current and previous releases, the last release
operation, both units, and the app's and gateway's health and running revisions,
including the app's database check. It is healthy only when everything was
observed and agrees with the current release; an unreachable Pi or unreadable
state is never healthy. ``logs`` prints recent journal lines of the app, the
gateway or both, or recent release operations, with bounded size.

Inspection only reads: it never restarts services, deploys code or touches the
production database. It needs only the standard library and works from any
directory. Each command runs one fixed POSIX ``sh`` script over SSH, so the
Pi's login shell does not matter.

Exit status: 0 done (status: healthy), 1 not healthy or unknown, 2 refused
arguments, 3 production unreachable or access denied.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass

DEFAULT_HOST = "raspi"
DEFAULT_RELEASE_ROOT = "/home/jannesi/jannenkoti-releases"
# ``is-active`` needs no privileges; never let sudo prompt on the script's stdin.
DEFAULT_SERVICE_MANAGER = "systemctl"
APP_SERVICE, GATEWAY_SERVICE = "jannenkoti.service", "esp32_ws.service"
UNITS = {
    "app": (APP_SERVICE,),
    "gateway": (GATEWAY_SERVICE,),
    "services": (APP_SERVICE, GATEWAY_SERVICE),
}
CONNECT_TIMEOUT = 10
STATUS_TIMEOUT = 120.0
LOGS_TIMEOUT = 60.0

OK, UNHEALTHY, REFUSED, UNREACHABLE = 0, 1, 2, 3
# Exit statuses of the remote scripts below; ssh itself reserves 255.
NO_RELEASE_ROOT, NO_CURRENT_RELEASE, NO_JOURNAL_ACCESS, NO_JOURNAL = 10, 11, 12, 13
RECORD_SEPARATOR = "\x1e"


class Refused(Exception):
    """The arguments are unusable; nothing was contacted."""


class Unreachable(Exception):
    """Production could not be reached, or refused this account access."""


# --- Transport -------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: bytes
    stderr: str
    truncated: bool


def last_line(text: str, fallback: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else fallback


@dataclass(frozen=True)
class Pi:
    """Runs POSIX ``sh`` scripts on the production host over SSH.

    The script travels on stdin to a fixed ``sh -s``, so values are quoted for
    ``sh`` (see :func:`assign`) and never pass through the remote login shell.
    Batch mode means a missing key or unknown host fails instead of prompting.
    """

    host: str

    def command(self) -> list[str]:
        return [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={CONNECT_TIMEOUT}",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "--",
            self.host,
            "sh",
            "-s",
        ]

    def _start(self, script: str, stdout) -> subprocess.Popen:
        try:
            process = subprocess.Popen(
                self.command(), stdin=subprocess.PIPE, stdout=stdout, stderr=subprocess.PIPE
            )
        except OSError:
            raise Unreachable("ssh is unavailable on this desktop") from None
        assert process.stdin is not None
        try:
            process.stdin.write(script.encode())
            process.stdin.close()
        except BrokenPipeError:
            pass
        return process

    def _failure(self, stderr: str) -> Unreachable:
        detail = last_line(stderr, "ssh exited with status 255")
        return Unreachable(f"cannot reach production through {self.host}: {detail}")

    def run(self, script: str, *, timeout: float, max_output: int) -> Result:
        """Run ``script`` and capture at most ``max_output`` bytes of its stdout."""
        process = self._start(script, subprocess.PIPE)
        errors = bytearray()

        def drain():
            assert process.stderr is not None
            for chunk in iter(lambda: process.stderr.read(4096), b""):
                errors.extend(chunk)
                del errors[:-16384]

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        expired = threading.Event()

        def expire():
            expired.set()
            process.kill()

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        output = bytearray()
        truncated = False
        try:
            assert process.stdout is not None
            for chunk in iter(lambda: process.stdout.read(65536), b""):
                output.extend(chunk)
                if len(output) > max_output:
                    truncated = True
                    del output[max_output:]
                    process.kill()
                    break
            process.wait()
        finally:
            timer.cancel()
            reader.join(5)
            process.stdout.close()
        stderr = errors.decode(errors="replace")
        if expired.is_set():
            raise Unreachable(f"production through {self.host} did not answer within {timeout:g}s")
        if process.returncode == 255:
            raise self._failure(stderr)
        return Result(process.returncode, bytes(output), stderr, truncated)

    def stream(self, script: str) -> tuple[int, str]:
        """Run ``script`` with its stdout going straight to ours, until it ends.

        Returns the exit status and stderr; Ctrl-C ends it successfully.
        """
        process = self._start(script, None)
        assert process.stderr is not None
        try:
            stderr = process.stderr.read().decode(errors="replace")
            process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            return OK, ""
        finally:
            process.stderr.close()
        if process.returncode == 255:
            raise self._failure(stderr)
        return process.returncode, stderr


def assign(name: str, value: str) -> str:
    return f"{name}={shlex.quote(value)}"


def remote_script(values: dict[str, str], body: str) -> str:
    """A script whose commands never read the rest of the script from stdin."""
    assignments = "\n".join(assign(name, value) for name, value in values.items())
    return f"main() {{\n{assignments}\n{body.strip()}\n}}\nmain </dev/null\n"


def script_body(template: str, **values: str) -> str:
    """Fill a script template, including the shared exit statuses and checks."""
    return template.format(
        require_root=REQUIRE_ROOT.format(no_root=NO_RELEASE_ROOT).strip(),
        no_current=NO_CURRENT_RELEASE,
        no_access=NO_JOURNAL_ACCESS,
        no_journal=NO_JOURNAL,
        **values,
    )


REQUIRE_ROOT = """
if [ ! -d "$root" ]; then
  echo "no release root at $root" >&2
  exit {no_root}
fi
"""

# --- Status ----------------------------------------------------------------------

PROBES = (("app", "database", "ok"), ("gateway", "status", "healthy"))

STATUS_SCRIPT = """
{require_root}
python="$root/current/venv/bin/python"
tool="$root/current/code/scripts/release.py"
if [ ! -x "$python" ] || [ ! -f "$tool" ]; then
  echo "no current release under $root" >&2
  exit {no_current}
fi
exec "$python" "$tool" status --live --json --release-root "$root" {options}
"""


def status_script(args: argparse.Namespace) -> str:
    options = ["--service-manager", args.service_manager]
    if args.app_url:
        options += ["--app-url", args.app_url]
    if args.gateway_url:
        options += ["--gateway-url", args.gateway_url]
    body = script_body(STATUS_SCRIPT, options=shlex.join(options))
    return remote_script({"root": args.release_root}, body)


def assess(payload: dict) -> list[str]:
    """Every reason production is not verified healthy; empty only when it is.

    Starts from the Pi's own readiness problems and adds any it did not state,
    so an older or incomplete report can never pass as healthy.
    """
    production = payload.get("production") or {}
    live = payload.get("live") or {}
    problems = [str(problem) for problem in live.get("problems") or []]

    def add(prefix: str, problem: str) -> None:
        if not any(known.startswith(prefix) for known in problems):
            problems.append(problem)

    expected = (production.get("current") or {}).get("revision")
    if not expected:
        problems.append("no current release is recorded")
    for name, field, healthy in PROBES:
        probe = live.get(name)
        if not probe:
            add(f"{name} ", f"{name} health is unknown")
            continue
        if probe.get("http") != 200:
            add(
                f"{name} health check returned",
                f"{name} health check returned HTTP {probe.get('http')}",
            )
        if probe.get("revision") != expected:
            add(
                f"{name} runs revision",
                f"{name} runs revision {probe.get('revision') or 'unknown'}",
            )
        if probe.get(field) != healthy:
            add(f"{name} {field}", f"{name} {field} is {probe.get(field) or 'unknown'}")
    services = live.get("services") or {}
    for unit in UNITS["services"]:
        if services.get(unit) != "active":
            add(unit, f"{unit} is not active")
    if live.get("ready") is not True and not problems:
        problems.append("release.py did not report production ready")
    return problems


def print_status(host: str, payload: dict, problems: list[str]) -> None:
    production = payload.get("production") or {}
    live = payload.get("live") or {}
    print(f"Production on {host} ({production.get('root') or 'unknown root'})")
    for name in ("current", "previous"):
        entry = production.get(name)
        detail = f"{entry.get('candidate')} revision {entry.get('revision')}" if entry else "none"
        print(f"  {name:<14}{detail}")
    last = production.get("last_operation")
    if last:
        print(
            f"  {'last':<14}{last.get('operation')} {last.get('candidate')} "
            f"{last.get('state')} {last.get('finished_at') or last.get('started_at')}"
        )
    services = live.get("services") or {}
    units = ", ".join(f"{unit} {services.get(unit) or 'unknown'}" for unit in UNITS["services"])
    print(f"  {'services':<14}{units}")
    for name, field, _ in PROBES:
        probe = live.get(name)
        detail = (
            f"revision {probe.get('revision') or 'unknown'}, {field} {probe.get(field) or 'unknown'}"
            if probe
            else "unreachable"
        )
        print(f"  {name:<14}{detail}")
    print(f"  {'healthy':<14}{'no' if problems else 'yes'}")
    for problem in problems:
        print(f"    - {problem}")


def command_status(args: argparse.Namespace) -> int:
    pi = Pi(args.host)
    try:
        result = pi.run(status_script(args), timeout=STATUS_TIMEOUT, max_output=4 * 1024 * 1024)
    except Unreachable as exc:
        return report_unknown(args, str(exc), UNREACHABLE)
    if result.returncode in (NO_RELEASE_ROOT, NO_CURRENT_RELEASE):
        return report_unknown(args, last_line(result.stderr, "no current release"), UNHEALTHY)
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or result.truncated:
            raise ValueError
    except ValueError:
        reason = last_line(result.stderr, f"exit status {result.returncode}")
        return report_unknown(args, f"release status was not readable ({reason})", UNHEALTHY)
    problems = assess(payload)
    if args.json:
        summary = {
            "host": args.host,
            "reachable": True,
            "healthy": not problems,
            "problems": problems,
            "production": payload.get("production"),
            "live": payload.get("live"),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_status(args.host, payload, problems)
    return UNHEALTHY if problems else OK


def report_unknown(args: argparse.Namespace, reason: str, status: int) -> int:
    if args.json:
        summary = {
            "host": args.host,
            "reachable": status != UNREACHABLE,
            "healthy": False,
            "problems": [reason],
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Production on {args.host}: health unknown", file=sys.stderr)
        print(f"  {reason}", file=sys.stderr)
    return status


# --- Logs ------------------------------------------------------------------------

JOURNAL_SCRIPT = """
if [ "$(id -u)" != 0 ] && ! id -Gn | tr ' ' '\\n' | grep -qxE 'adm|systemd-journal|wheel'; then
  echo "the SSH account cannot read the system journal (it needs the adm, systemd-journal or wheel group)" >&2
  exit {no_access}
fi
if ! command -v journalctl >/dev/null 2>&1; then
  echo "journalctl is not available on the production host" >&2
  exit {no_journal}
fi
exec journalctl {options}
"""

RELEASE_LOG_SCRIPT = """
{require_root}
if [ -f "$root/operations.log" ]; then
  tail -n "$lines" "$root/operations.log"
fi
printf '\\036\\n'
if [ -f "$root/operation.json" ]; then
  cat "$root/operation.json"
fi
"""


def journal_script(args: argparse.Namespace) -> str:
    # Not --quiet: journalctl's own hints, such as missing permissions, stay visible.
    options = ["--no-pager", "-o", "short-iso"]
    for unit in UNITS[args.which]:
        options += ["-u", unit]
    options += ["-n", str(args.lines)]
    if args.follow:
        options.append("-f")
    if args.since:
        options += ["--since", args.since]
    return remote_script({}, script_body(JOURNAL_SCRIPT, options=shlex.join(options)))


def command_logs(args: argparse.Namespace) -> int:
    if args.which == "release":
        if args.follow or args.since:
            raise Refused("release logs take only --lines")
        return release_logs(args)
    pi = Pi(args.host)
    if args.follow:
        return journal_outcome(*pi.stream(journal_script(args)))
    result = pi.run(journal_script(args), timeout=LOGS_TIMEOUT, max_output=args.max_bytes)
    sys.stdout.write(result.stdout.decode(errors="replace"))
    sys.stdout.flush()
    if result.truncated:
        note(f"truncated at {args.max_bytes} bytes; use fewer --lines or --since")
        return OK
    if result.returncode == OK:
        for line in result.stderr.splitlines()[-5:]:
            note(line)
    return journal_outcome(result.returncode, result.stderr)


def journal_outcome(status: int, stderr: str) -> int:
    if status == OK:
        return OK
    if status in (NO_JOURNAL_ACCESS, NO_JOURNAL):
        raise Unreachable(f"access denied: {last_line(stderr, 'journal unavailable')}")
    if stderr:
        note(last_line(stderr, ""))
    note(f"journalctl exited with status {status}")
    return UNHEALTHY


def release_logs(args: argparse.Namespace) -> int:
    pi = Pi(args.host)
    script = remote_script(
        {"root": args.release_root, "lines": str(args.lines)}, script_body(RELEASE_LOG_SCRIPT)
    )
    result = pi.run(script, timeout=LOGS_TIMEOUT, max_output=args.max_bytes)
    if result.returncode == NO_RELEASE_ROOT:
        note(last_line(result.stderr, "no release root"))
        return UNHEALTHY
    if result.returncode != OK:
        note(f"reading release operations failed: {last_line(result.stderr, 'no details')}")
        return UNHEALTHY
    text = result.stdout.decode(errors="replace")
    finished, _, latest = text.partition(RECORD_SEPARATOR + "\n")
    records = []
    for line in finished.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            note("skipped an unreadable operations.log line")
    if not records:
        print("No finished release operations")
    for record in records:
        print_operation(record)
    try:
        current = json.loads(latest) if latest.strip() else None
    except ValueError:
        current = None
        if not result.truncated:
            note("operation.json is not readable")
    if isinstance(current, dict) and current.get("state") == "started":
        print_operation(current, unfinished=True)
    if result.truncated:
        note(f"truncated at {args.max_bytes} bytes; use fewer --lines")
    return OK


def print_operation(record: dict, *, unfinished: bool = False) -> None:
    state = f"{record.get('state')}, not finished" if unfinished else record.get("state")
    when = record.get("finished_at") or record.get("started_at")
    replaced = f" (replacing {record['replaced']})" if record.get("replaced") else ""
    print(f"{when} {record.get('operation')} {record.get('candidate')} {state}{replaced}")
    for step in record.get("steps") or []:
        print(f"    {step}")
    for problem in record.get("problems") or []:
        print(f"    problem: {problem}")
    for line in record.get("guidance") or []:
        print(f"    {line}")


def note(text: str) -> None:
    print(text, file=sys.stderr)


# --- Command line ----------------------------------------------------------------


def ssh_destination(value: str) -> str:
    if not value.strip() or value.startswith("-") or any(c.isspace() for c in value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an SSH destination")
    return value


def absolute_root(value: str) -> str:
    if not value.startswith("/"):
        raise argparse.ArgumentTypeError("the Pi's release root must be an absolute path")
    return value


def journal_time(value: str) -> str:
    if not value.strip() or value.startswith("-") or not value.isprintable():
        raise argparse.ArgumentTypeError(f"{value!r} is not a journal time")
    return value


def bounded(low: int, high: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return number

    return parse


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--host",
        type=ssh_destination,
        default=DEFAULT_HOST,
        help=f"SSH destination (default {DEFAULT_HOST})",
    )
    common.add_argument(
        "--release-root",
        type=absolute_root,
        default=DEFAULT_RELEASE_ROOT,
        help=f"release root on the Pi (default {DEFAULT_RELEASE_ROOT})",
    )
    # Stand-ins for tests; production uses the release tool's own defaults.
    common.add_argument(
        "--service-manager", default=DEFAULT_SERVICE_MANAGER, help=argparse.SUPPRESS
    )
    common.add_argument("--app-url", help=argparse.SUPPRESS)
    common.add_argument("--gateway-url", help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    status_parser = commands.add_parser(
        "status", parents=[common], help="current release, service health and revisions"
    )
    status_parser.add_argument("--json", action="store_true", help="print a JSON summary")
    status_parser.set_defaults(handler=command_status)

    logs_parser = commands.add_parser(
        "logs", parents=[common], help="recent app, gateway or release-operation logs"
    )
    logs_parser.add_argument("which", choices=(*UNITS, "release"))
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=bounded(1, 5000),
        default=100,
        help="journal lines, or release operations (default 100)",
    )
    logs_parser.add_argument(
        "--since",
        type=journal_time,
        help="journal entries since, e.g. '1 hour ago' or '2026-11-26 08:00'",
    )
    logs_parser.add_argument(
        "-f", "--follow", action="store_true", help="keep printing new journal lines (Ctrl-C ends)"
    )
    logs_parser.add_argument(
        "--max-bytes",
        type=bounded(1024, 16 * 1024 * 1024),
        default=1024 * 1024,
        help="output bytes to print (default 1 MiB; not applied with --follow)",
    )
    logs_parser.set_defaults(handler=command_logs)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        return args.handler(args)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return REFUSED
    except Unreachable as exc:
        print(str(exc), file=sys.stderr)
        return UNREACHABLE


if __name__ == "__main__":
    sys.exit(main())
