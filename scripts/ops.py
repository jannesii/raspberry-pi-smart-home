#!/usr/bin/env python3
"""Operate Pi production from the desktop through the ``raspi`` SSH alias.

    ops.py status [--json]
    ops.py logs app|gateway|services|release [-n LINES] [--since TIME] [--follow]
    ops.py prepare <revision> [--source CHECKOUT] [--device-acceptance] [--replace]
    ops.py candidate [<candidate>] [--json]
    ops.py accept <candidate> --scope feature|device --note TEXT
    ops.py rehearse <candidate> --disposable-env PI_FILE --restore-env PI_FILE
    ops.py deploy <candidate>
    ops.py recover

``status`` reports the current and previous releases, the last release
operation, both units, and the app's and gateway's health and running revisions,
including the app's database check. It is healthy only when everything was
observed and agrees with the current release; an unreachable Pi or unreadable
state is never healthy. ``logs`` prints recent journal lines of the app, the
gateway or both, or recent release operations, with bounded size. Inspection
only reads.

``prepare`` pushes exactly one committed revision of a desktop checkout, and
only the Git objects the Pi lacks, over SSH into the Pi's transfer repository;
GitHub is never involved. The Pi verifies the commit and tree before the running
release's own ``release.py prepare`` exports it, installs its dependencies and
runs the checks. The other release commands run that same tool: ``accept``
records what the owner observed and only after confirmation on the owner's
terminal, and ``deploy``, ``recover`` and ``rehearse`` run as detached jobs on
the Pi, so a lost connection or Ctrl-C only stops watching. ``deploy`` and
``recover`` finish with ``status``. Database restoration is not offered here: it
is a separate owner decision (docs/operations/releases.md).

The command needs only the standard library and works from any directory. Each
command runs one fixed POSIX ``sh`` script over SSH, so the Pi's login shell
does not matter; the one exception is ``git push``, whose single quoted
``git-receive-pack '<path>'`` command the login shell runs.

Exit status: 0 done (status: healthy), 1 failed, not healthy or unknown,
2 refused before any change, 3 production unreachable or access denied,
4 production not verified ready (follow the guidance), 5 another release
operation is running, 130 stopped watching (the operation continues).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

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

OK, UNHEALTHY, REFUSED, UNREACHABLE, ATTENTION, BUSY = 0, 1, 2, 3, 4, 5
INTERRUPTED = 130
# Exit statuses of the remote scripts below; ssh itself reserves 255.
NO_RELEASE_ROOT, NO_CURRENT_RELEASE, NO_JOURNAL_ACCESS, NO_JOURNAL = 10, 11, 12, 13
NO_TRANSFER, IDENTITY_MISMATCH, JOB_LOST, NO_JOBS = 14, 15, 16, 17
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
        return [*self.transport(), self.host, "sh", "-s"]

    @staticmethod
    def transport() -> list[str]:
        """``ssh`` and its options, up to the destination; git appends host and command."""
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
        ]

    def _start(
        self, script: str, stdout, stderr=subprocess.PIPE, *, own_session: bool = False
    ) -> subprocess.Popen:
        try:
            process = subprocess.Popen(
                self.command(),
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                start_new_session=own_session,
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

    def attached(self, script: str) -> int:
        """Run ``script`` with its stdout and stderr going straight to ours.

        For long release operations, whose progress the owner watches. Ctrl-C
        only stops watching: the remote job script keeps the operation running.
        """
        # In its own session ssh never sees the terminal's Ctrl-C, so only this
        # process decides that watching stopped, never a racing ssh exit status.
        process = self._start(script, None, None, own_session=True)
        try:
            process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            return INTERRUPTED
        if process.returncode == 255:
            raise Unreachable(
                f"the connection to production through {self.host} failed or was lost; "
                "a started release operation continues on the Pi (see logs release)"
            )
        return process.returncode


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
        require_tool=REQUIRE_TOOL.format(no_current=NO_CURRENT_RELEASE).strip(),
        no_current=NO_CURRENT_RELEASE,
        no_access=NO_JOURNAL_ACCESS,
        no_journal=NO_JOURNAL,
        no_transfer=NO_TRANSFER,
        mismatch=IDENTITY_MISMATCH,
        job_lost=JOB_LOST,
        no_jobs=NO_JOBS,
        **values,
    )


REQUIRE_ROOT = """
if [ ! -d "$root" ]; then
  echo "no release root at $root" >&2
  exit {no_root}
fi
"""

# The running release's own tool performs every release operation, so the
# desktop never needs a newer copy of the release machinery than production has.
REQUIRE_TOOL = """
python="$root/current/venv/bin/python"
tool="$root/current/code/scripts/release.py"
if [ ! -x "$python" ] || [ ! -f "$tool" ]; then
  echo "no current release under $root" >&2
  exit {no_current}
fi
"""

# --- Status ----------------------------------------------------------------------

PROBES = (("app", "database", "ok"), ("gateway", "status", "healthy"))

STATUS_SCRIPT = """
{require_root}
{require_tool}
exec "$python" "$tool" status --live --json --release-root "$root" {options}
"""


def status_script(args: argparse.Namespace) -> str:
    options = ["--service-manager", args.service_manager or DEFAULT_SERVICE_MANAGER]
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


# --- Release operations ----------------------------------------------------------

# Mirrors release.py: a revision tracking one of these never becomes a candidate.
CREDENTIAL_FILE = re.compile(r"(^|/)([^/]*\.env|alembic\.ini)$")
CANDIDATE_NAME = re.compile(r"^[0-9a-f]{12}$")
TRANSFER_SETUP_TIMEOUT = 60.0
TRANSFER_TIMEOUT = 900.0
CANDIDATE_TIMEOUT = 300.0
# release.py's own exit statuses (OK, FAILED, REFUSED, BUSY, ATTENTION) as ours.
RELEASE_OUTCOMES = {0: OK, 1: UNHEALTHY, 2: REFUSED, 3: BUSY, 4: ATTENTION}
# Remote script failures before release.py ran: production was not touched.
BEFORE_RELEASE_TOOL = {
    NO_RELEASE_ROOT: "There is no release root on the Pi",
    NO_CURRENT_RELEASE: "There is no current release on the Pi to run release.py from",
    NO_JOBS: "The Pi's job directory under the release root is not writable",
    IDENTITY_MISMATCH: "The Pi does not hold the exact commit; nothing was prepared",
}

# The Pi keeps transferred commits in a repository without a checkout, so
# release.py's cleanliness check sees no changes and exports exactly the commit.
# receive.fsckObjects verifies every received object before a ref may name it.
TRANSFER_SCRIPT = """
{require_root}
{require_tool}
repo="$root/transfer"
if [ ! -d "$repo/.git" ]; then
  if [ -e "$repo" ]; then
    echo "$repo exists but is not the transfer repository" >&2
    exit {no_transfer}
  fi
  git init -q "$repo" && git -C "$repo" config receive.fsckObjects true || exit {no_transfer}
fi
"""

VERIFY_SCRIPT = """
repo="$root/transfer"
actual=$(git -C "$repo" rev-parse --verify --quiet "refs/candidates/$revision^{{commit}}")
if [ "$actual" != "$revision" ]; then
  echo "identity mismatch: the transferred commit is ${{actual:-missing}}, expected $revision" >&2
  exit {mismatch}
fi
actual=$(git -C "$repo" rev-parse --verify --quiet "$revision^{{tree}}")
if [ "$actual" != "$tree" ]; then
  echo "identity mismatch: the transferred tree is ${{actual:-missing}}, expected $tree" >&2
  exit {mismatch}
fi
echo "Verified $revision (tree $tree) on the Pi"
"""

# The operation runs in its own session with its output in a job log, so a lost
# SSH connection or Ctrl-C on the desktop never interrupts a deployment halfway.
JOB_SCRIPT = """
{require_root}
{require_tool}
{verify}
jobs="$root/jobs"
mkdir -p "$jobs" && chmod 700 "$jobs" || exit {no_jobs}
find "$jobs" -type f -mtime +30 -exec rm -f {{}} + 2>/dev/null
job="$jobs/$(date -u +%Y%m%dT%H%M%SZ)-$operation-$$"
: >"$job.log" || exit {no_jobs}
set -- "$python" "$tool" {arguments} --release-root "$root"
PYTHONUNBUFFERED=1 setsid -w sh -c '
job=$1; shift
"$@" >>"$job.log" 2>&1 </dev/null
echo $? >"$job.tmp" && mv "$job.tmp" "$job.status"
' sh "$job" "$@" </dev/null >/dev/null 2>&1 &
pid=$!
echo "[ops] $operation runs on the Pi as $job.log; it continues if this connection ends"
tail -n +1 -f --pid="$pid" "$job.log"
if [ -f "$job.status" ]; then
  exit "$(cat "$job.status")"
fi
echo "the $operation job ended without an exit status; see $job.log" >&2
exit {job_lost}
"""


def local_git(source: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        raise Refused("git is unavailable on this desktop") from None


def resolve_candidate(source: Path, revision: str) -> tuple[Path, str, str]:
    """The checkout, commit and tree to release, applying release.py's source rules."""
    if revision.startswith("-"):
        raise Refused(f"revision {revision!r} is not a revision name")
    toplevel = local_git(source, "rev-parse", "--show-toplevel")
    if toplevel.returncode != 0:
        raise Refused(f"{source} is not a Git checkout")
    checkout = Path(toplevel.stdout.strip())
    dirty = local_git(checkout, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise Refused(
            f"{checkout} has uncommitted changes; commit or stash them so the candidate "
            "is exactly a committed revision"
        )
    resolved = local_git(checkout, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if resolved.returncode != 0:
        raise Refused(f"revision {revision!r} does not name a commit in {checkout}")
    if "ambiguous" in resolved.stderr:
        raise Refused(f"revision {revision!r} is ambiguous; name the branch, tag or commit")
    commit = resolved.stdout.strip()
    tree = local_git(checkout, "rev-parse", f"{commit}^{{tree}}").stdout.strip()
    names = local_git(checkout, "ls-tree", "-r", "-z", "--name-only", commit).stdout
    credentials = [name for name in names.split("\0") if name and CREDENTIAL_FILE.search(name)]
    if credentials:
        raise Refused(f"revision {commit} tracks credential files: {', '.join(credentials)}")
    return checkout, commit, tree


def transfer(args: argparse.Namespace, checkout: Path, commit: str) -> None:
    """Push exactly ``commit`` and the objects it needs to the Pi's transfer repository.

    Only objects the Pi lacks travel; refs of earlier candidates let Git skip the
    rest. Nothing is fetched from or published to any other remote.
    """
    pi = Pi(args.host)
    setup = pi.run(
        remote_script({"root": args.release_root}, script_body(TRANSFER_SCRIPT)),
        timeout=TRANSFER_SETUP_TIMEOUT,
        max_output=65536,
    )
    if setup.returncode != OK:
        raise TransferFailed(last_line(setup.stderr, f"exit status {setup.returncode}"))
    ssh = shlex.join(pi.transport())
    env = {
        **os.environ,
        "GIT_SSH_COMMAND": ssh,
        # Only "host command" follows GIT_SSH_COMMAND, after its closing "--".
        "GIT_SSH_VARIANT": "simple",
        "GIT_TERMINAL_PROMPT": "0",
    }
    destination = f"ssh://{args.host}{args.release_root.rstrip('/')}/transfer"
    try:
        pushed = subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "push",
                "--quiet",
                "--no-verify",
                destination,
                f"{commit}:refs/candidates/{commit}",
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TRANSFER_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise TransferFailed(f"no answer within {TRANSFER_TIMEOUT:g}s") from None
    if pushed.returncode != 0:
        raise TransferFailed(push_failure(pushed.stderr, pushed.returncode))


GIT_BOILERPLATE = (
    "fatal: Could not read from remote repository",
    "Please make sure you have the correct access rights",
    "and the repository exists",
)


def push_failure(stderr: str, status: int) -> str:
    """The cause git push reported, without its generic access-rights hint."""
    lines = [
        line.strip()
        for line in stderr.splitlines()
        if line.strip() and not line.strip().startswith(GIT_BOILERPLATE)
    ]
    return "; ".join(lines[-3:]) or f"git push exited with status {status}"


class TransferFailed(Exception):
    """The commit did not reach the Pi intact; nothing was prepared."""


def release_options(args: argparse.Namespace, *names: str) -> list[str]:
    """Options for the Pi's release.py that were given, by their release.py names."""
    options = []
    for name in names:
        value = getattr(args, name.replace("-", "_"), None)
        if value is True:
            options.append(f"--{name}")
        elif value not in (None, False):
            options += [f"--{name}", str(value)]
    return options


def run_job(
    args: argparse.Namespace,
    operation: str,
    arguments: list[str],
    *,
    values: dict[str, str] | None = None,
    verify: bool = False,
    source: bool = False,
) -> int:
    """Run one release.py operation on the Pi as a detached job and follow it."""
    fragment = shlex.join(arguments) + (' --source "$root/transfer"' if source else "")
    body = script_body(
        JOB_SCRIPT,
        verify=script_body(VERIFY_SCRIPT).strip() if verify else "",
        arguments=fragment,
    )
    script = remote_script(
        {"root": args.release_root, "operation": operation, **(values or {})}, body
    )
    return Pi(args.host).attached(script)


def candidate_report(args: argparse.Namespace, candidate: str) -> dict | None:
    result = run_tool(
        args, ["status", candidate, "--json", *release_options(args, "production-env")]
    )
    try:
        report = json.loads(result.stdout)
    except ValueError:
        return None
    return report if isinstance(report, dict) and not result.truncated else None


IDENTITY_SCRIPT = """
{require_root}
cat "$root/releases/$candidate/release.json"
"""


def candidate_identity(args: argparse.Namespace, candidate: str) -> tuple[str | None, str | None]:
    """The revision and tree the candidate's own record states."""
    script = remote_script(
        {"root": args.release_root, "candidate": candidate}, script_body(IDENTITY_SCRIPT)
    )
    result = Pi(args.host).run(script, timeout=CANDIDATE_TIMEOUT, max_output=1024 * 1024)
    try:
        record = json.loads(result.stdout)
    except ValueError:
        return None, None
    if not isinstance(record, dict) or result.truncated:
        return None, None
    return record.get("revision"), record.get("tree")


def release_outcome(status: int, operation: str) -> int:
    """Our exit status for a remote release.py run, explaining anything unexpected."""
    if status == INTERRUPTED:
        note(
            f"Stopped watching; the {operation} continues on the Pi. "
            "Follow it with: ops.py logs release, ops.py candidate"
        )
        return INTERRUPTED
    if status in BEFORE_RELEASE_TOOL:
        note(BEFORE_RELEASE_TOOL[status] + "; see docs/operations/releases.md")
        return UNHEALTHY
    if status in RELEASE_OUTCOMES:
        return RELEASE_OUTCOMES[status]
    note(f"The {operation} did not finish (exit status {status}); inspect: ops.py logs release")
    return UNHEALTHY


def command_prepare(args: argparse.Namespace) -> int:
    checkout, commit, tree = resolve_candidate(args.source.absolute(), args.revision)
    candidate = commit[:12]
    print(f"{args.revision} is {commit} in {checkout}")
    print(f"Transferring it to {args.host} ...")
    try:
        transfer(args, checkout, commit)
    except TransferFailed as exc:
        note(f"Transfer failed: {exc}")
        note("Nothing was prepared; the running release is unchanged.")
        return UNHEALTHY
    arguments = [
        "prepare",
        commit,
        *release_options(
            args, "production-env", "installer", "device-acceptance", "replace", "keep"
        ),
    ]
    status = run_job(
        args,
        "prepare",
        arguments,
        values={"revision": commit, "tree": tree},
        verify=True,
        source=True,
    )
    outcome = release_outcome(status, "preparation")
    if outcome == INTERRUPTED:
        return outcome
    report = candidate_report(args, candidate)
    if report is None:
        if outcome == OK:
            note(f"Candidate {candidate} could not be inspected after preparation")
            return UNHEALTHY
        note("The running release is unchanged.")
        return outcome
    revision, recorded_tree = candidate_identity(args, candidate)
    # A failed preparation may stop before recording the tree, never the revision.
    if (
        revision != commit
        or recorded_tree not in (tree, None)
        or (outcome == OK and recorded_tree is None)
    ):
        note(
            f"identity mismatch: candidate {candidate} records revision {revision} "
            f"and tree {recorded_tree}, expected {commit} and {tree}"
        )
        return UNHEALTHY
    if outcome != OK:
        note(f"Candidate {candidate} is {report.get('state')}; the running release is unchanged.")
        return outcome
    remaining = report.get("blockers") or []
    if report.get("deployable"):
        print(f"Candidate {candidate} is prepared and deployable: ops.py deploy {candidate}")
    else:
        print(f"Candidate {candidate} is prepared; before deployment:")
        for blocker in remaining:
            print(f"  - {blocker}")
    return OK


TOOL_SCRIPT = """
{require_root}
{require_tool}
exec "$python" "$tool" {arguments} --release-root "$root"
"""


def run_tool(args: argparse.Namespace, arguments: list[str]) -> Result:
    script = remote_script(
        {"root": args.release_root},
        script_body(TOOL_SCRIPT, arguments=shlex.join(arguments)),
    )
    return Pi(args.host).run(script, timeout=CANDIDATE_TIMEOUT, max_output=4 * 1024 * 1024)


def tool_outcome(result: Result, operation: str) -> int:
    for line in result.stderr.splitlines()[-20:]:
        note(line)
    return release_outcome(result.returncode, operation)


def command_candidate(args: argparse.Namespace) -> int:
    arguments = ["status", *([args.candidate] if args.candidate else [])]
    arguments += release_options(args, "production-env")
    if args.json:
        arguments.append("--json")
    result = run_tool(args, arguments)
    sys.stdout.write(result.stdout.decode(errors="replace"))
    return tool_outcome(result, args.command)


def command_accept(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise Refused(
            "acceptance records what the owner observed; run it in the owner's own "
            "terminal, where it asks for confirmation"
        )
    report = candidate_report(args, args.candidate)
    if report is None:
        note(f"Candidate {args.candidate} could not be inspected on {args.host}")
        return UNHEALTHY
    print(f"Candidate {args.candidate} revision {report.get('revision')} ({report.get('state')})")
    for name, check in (report.get("checks") or {}).items():
        print(f"  check {name:<8}{check.get('status')}")
    print(f"  {args.scope} acceptance note: {args.note}")
    try:
        answer = input(
            f"Record that you observed this candidate ({args.scope})? "
            "Type the candidate name to confirm: "
        )
    except EOFError:
        answer = ""
    if answer.strip() != args.candidate:
        raise Refused("acceptance was not confirmed; nothing was recorded")
    arguments = ["accept", args.candidate, "--scope", args.scope, "--note", args.note]
    result = run_tool(args, arguments + release_options(args, "production-env"))
    sys.stdout.write(result.stdout.decode(errors="replace"))
    return tool_outcome(result, args.command)


OPERATION_OPTIONS = (
    "production-env",
    "service-manager",
    "app-url",
    "gateway-url",
    "readiness-timeout",
)


def failure_guidance(operation: str) -> None:
    for line in (
        f"The {operation} did not leave production verified ready on the requested release.",
        "Inspect: ops.py status, ops.py logs release -n 1, "
        "ops.py logs services --since '30 minutes ago'",
        "Return to the previous release when its database schema still fits: ops.py recover",
        "Restoring a database backup discards later writes and is a separate owner decision, "
        "never code recovery: see 'Explicit database restoration' in docs/operations/releases.md.",
    ):
        note(line)


def operation_with_status(args: argparse.Namespace, operation: str, arguments: list[str]) -> int:
    status = run_job(args, operation, arguments)
    outcome = release_outcome(status, operation)
    if outcome in (INTERRUPTED, REFUSED, BUSY) or status in BEFORE_RELEASE_TOOL:
        if outcome == REFUSED:
            note("Refused before any change; production is untouched.")
        return outcome
    print()
    healthy = command_status(argparse.Namespace(**{**vars(args), "json": False})) == OK
    if outcome == OK and not healthy:
        outcome = UNHEALTHY
    if outcome != OK:
        failure_guidance(operation)
    return outcome


def command_deploy(args: argparse.Namespace) -> int:
    arguments = ["deploy", args.candidate, *release_options(args, *OPERATION_OPTIONS)]
    return operation_with_status(args, "deployment", arguments)


def command_recover(args: argparse.Namespace) -> int:
    arguments = ["recover", *release_options(args, *OPERATION_OPTIONS)]
    return operation_with_status(args, "recovery", arguments)


def command_rehearse(args: argparse.Namespace) -> int:
    arguments = [
        "rehearse",
        args.candidate,
        "--disposable-env",
        args.disposable_env,
        "--restore-env",
        args.restore_env,
        *release_options(args, "production-env"),
    ]
    return release_outcome(run_job(args, "rehearsal", arguments), "rehearsal")


# --- Command line ----------------------------------------------------------------


def ssh_destination(value: str) -> str:
    if not value.strip() or value.startswith("-") or any(c.isspace() for c in value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an SSH destination")
    return value


def absolute_root(value: str) -> str:
    if not value.startswith("/"):
        raise argparse.ArgumentTypeError("the Pi's release root must be an absolute path")
    return value


def absolute_path(value: str) -> str:
    if not value.startswith("/"):
        raise argparse.ArgumentTypeError(f"{value!r} must be an absolute path on the Pi")
    return value


def journal_time(value: str) -> str:
    if not value.strip() or value.startswith("-") or not value.isprintable():
        raise argparse.ArgumentTypeError(f"{value!r} is not a journal time")
    return value


def candidate_name(value: str) -> str:
    if not CANDIDATE_NAME.match(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a candidate (12 hex characters)")
    return value


def printable(value: str) -> str:
    if not value.strip() or not value.isprintable():
        raise argparse.ArgumentTypeError("needs printable text on one line")
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
    common.add_argument("--service-manager", help=argparse.SUPPRESS)
    common.add_argument("--app-url", help=argparse.SUPPRESS)
    common.add_argument("--gateway-url", help=argparse.SUPPRESS)
    common.add_argument("--production-env", type=absolute_path, help=argparse.SUPPRESS)
    common.add_argument("--readiness-timeout", type=bounded(1, 600), help=argparse.SUPPRESS)

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

    prepare_parser = commands.add_parser(
        "prepare",
        parents=[common],
        help="transfer an exact committed revision to the Pi and prepare it there",
    )
    prepare_parser.add_argument("revision", help="branch, tag or commit of the desktop checkout")
    prepare_parser.add_argument(
        "--source",
        type=Path,
        default=Path.cwd(),
        help="desktop checkout or worktree (default: the current directory)",
    )
    prepare_parser.add_argument(
        "--device-acceptance", action="store_true", help="require real-device acceptance too"
    )
    prepare_parser.add_argument(
        "--replace", action="store_true", help="rebuild a prepared candidate, discarding evidence"
    )
    prepare_parser.add_argument("--keep", type=bounded(1, 50), help="candidates to retain")
    prepare_parser.add_argument("--installer", type=absolute_path, help=argparse.SUPPRESS)
    prepare_parser.set_defaults(handler=command_prepare)

    candidate_parser = commands.add_parser(
        "candidate", parents=[common], help="prepared candidates, their evidence and blockers"
    )
    candidate_parser.add_argument("candidate", nargs="?", type=candidate_name)
    candidate_parser.add_argument("--json", action="store_true", help="print release.py's JSON")
    candidate_parser.set_defaults(handler=command_candidate)

    accept_parser = commands.add_parser(
        "accept", parents=[common], help="record acceptance you observed (asks to confirm)"
    )
    accept_parser.add_argument("candidate", type=candidate_name)
    accept_parser.add_argument("--scope", choices=("feature", "device"), required=True)
    accept_parser.add_argument("--note", type=printable, required=True, help="what you checked")
    accept_parser.set_defaults(handler=command_accept)

    rehearse_parser = commands.add_parser(
        "rehearse",
        parents=[common],
        help="rehearse a candidate's migrations on disposable PostgreSQL",
    )
    rehearse_parser.add_argument("candidate", type=candidate_name)
    rehearse_parser.add_argument("--disposable-env", type=absolute_path, required=True)
    rehearse_parser.add_argument("--restore-env", type=absolute_path, required=True)
    rehearse_parser.set_defaults(handler=command_rehearse)

    deploy_parser = commands.add_parser(
        "deploy", parents=[common], help="switch production to a deployable candidate"
    )
    deploy_parser.add_argument("candidate", type=candidate_name)
    deploy_parser.set_defaults(handler=command_deploy)

    recover_parser = commands.add_parser(
        "recover", parents=[common], help="return production to the previous release"
    )
    recover_parser.set_defaults(handler=command_recover)
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
