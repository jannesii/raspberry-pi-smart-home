"""Deploying exact desktop commits through ``scripts/ops.py``.

Tests drive the desktop command as a subprocess from outside the checkout. A
stand-in ``ssh`` first on ``PATH`` records each call and runs the remote side
locally with ``sh``: the scripts ``ops.py`` sends to ``sh -s`` and the
``git-receive-pack`` that ``git push`` asks for. That "Pi" is the deployment
tests' world (``test_release_deployment``): a temporary release root, a
disposable PostgreSQL cluster on a private socket, a recording service manager
running stand-in units from ``current`` and a stand-in dependency installer.

The world's first release is deployed directly with this repository's
``release.py``, the way production already runs, and carries that tool so the
desktop command drives the running release's own machinery. The desktop checkout
has no reachable remote: transfers never involve GitHub.
"""

from __future__ import annotations

import json
import os
import pty
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_release_deployment import (  # noqa: F401  (fixtures)
    PRODUCTION_SECRET,
    postgres,
    release,
    running,
    sql,
    systemctl_calls,
    world,
)
from test_release_migrations import disposable
from test_release_preparation import commit_all, git

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops.py"

FAKE_SSH = """\
#!{python}
import json, os, subprocess, sys, time
from pathlib import Path

controls = Path({controls!r})
args = sys.argv[1:]
with open(controls / "ssh.log", "a") as log:
    log.write(json.dumps(args) + "\\n")
separator = args.index("--")
host, command = args[separator + 1], " ".join(args[separator + 2:])
if (controls / "unreachable").exists():
    print("ssh: connect to host 192.0.2.50 port 22: No route to host", file=sys.stderr)
    sys.exit(255)
if command.startswith("git-receive-pack") and (controls / "drop-transfer").exists():
    # The connection dies midway: after the Pi advertised its refs, part of the
    # push request arrives and then nothing more.
    receiver = subprocess.Popen(["sh", "-c", command], stdin=subprocess.PIPE)
    request = os.read(0, 65536)
    receiver.stdin.write(request[: max(1, len(request) // 2)])
    receiver.stdin.flush()
    time.sleep(0.2)
    receiver.kill()
    receiver.wait()
    print("client_loop: send disconnect: Connection reset by peer", file=sys.stderr)
    sys.exit(255)
os.execv("/bin/sh", ["sh", "-c", command])
"""


MIGRATION_ENV = (
    "import os\nfrom alembic import context\nfrom sqlalchemy import create_engine\n"
    'with create_engine(os.environ["DATABASE_URL"]).connect() as conn:\n'
    "    context.configure(connection=conn)\n"
    "    with context.begin_transaction():\n"
    "        context.run_migrations()\n"
)
BASELINE = (
    'from alembic import op\nrevision = "0001"\ndown_revision = None\n'
    'def upgrade():\n    op.execute("CREATE TABLE samples (value text)")\n'
)


@pytest.fixture
def desk(world):  # noqa: F811
    """The deployment world plus a desktop checkout, a stand-in ssh and a first release."""
    source = world["source"]
    (source / "scripts").mkdir()
    for name in ("release.py", "release_database.py", "test.py"):
        shutil.copy(ROOT / "scripts" / name, source / "scripts" / name)
    # The checkout's own virtualenv runs the desktop suite: this interpreter's.
    (source / ".venv").symlink_to(Path(sys.prefix))
    (source / ".gitignore").write_text("*.env\n.venv\n")
    # An executable migration history, as in the release migration tests.
    (source / "migrations" / "env.py").write_text(MIGRATION_ENV)
    for code in (source, world["production"]):
        (code / "migrations" / "versions" / "0001_base.py").write_text(BASELINE)
    first = commit_all(source, "release tooling")
    (source / "jannenkoti.env").write_text("SECRET_KEY=desktop-only\n")
    # A remote that cannot be reached: nothing may depend on publishing first.
    git(source, "remote", "add", "origin", str(world["controls"] / "no-such-github"))
    ssh = world["bin"] / "ssh"
    ssh.write_text(FAKE_SSH.format(python=sys.executable, controls=str(world["controls"])))
    ssh.chmod(0o755)
    for step in (
        ["prepare", "main", "--source", str(source), "--installer", str(world["installer"])],
        ["accept", first[:12], "--scope", "feature", "--note", "bootstrap"],
        ["deploy", first[:12]],
    ):
        result = release(world, *step)
        assert result.returncode == 0, result.stdout + result.stderr
    world.update(first=first, elsewhere=world["controls"].parent / "elsewhere")
    world["elsewhere"].mkdir()
    return world


def ops_env(desk) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("JANNENKOTI_", "PG", "DATABASE_URL", "GIT_"))
    }
    env.update(
        PATH=f"{desk['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        PYTHONDONTWRITEBYTECODE="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        XDG_STATE_HOME=str(desk["controls"] / "state"),
    )
    return env


def ops_command(desk, *args: str) -> list[str]:
    """The desktop command with the world's stand-ins for Pi paths and services."""
    command, *rest = args
    stand_ins = [
        "--release-root",
        str(desk["root"]),
        "--production-env",
        str(desk["production_env"]),
    ]
    if command == "check":  # desktop only: no Pi stand-ins
        stand_ins = []
    if command == "prepare":
        stand_ins += ["--installer", str(desk["installer"])]
    if command in ("deploy", "recover"):
        stand_ins += [
            "--service-manager",
            str(desk["systemctl"]),
            "--app-url",
            desk["app_url"],
            "--gateway-url",
            desk["gateway_url"],
            "--readiness-timeout",
            "20",
        ]
    return [sys.executable, str(OPS), command, *stand_ins, *rest]


def ops(desk, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ops_command(desk, *args),
        cwd=desk["elsewhere"],
        env=ops_env(desk),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def start_ops(desk, *args: str, **popen) -> subprocess.Popen:
    return subprocess.Popen(
        ops_command(desk, *args), cwd=desk["elsewhere"], env=ops_env(desk), **popen
    )


def wait_for(path: Path, process: subprocess.Popen, seconds: float = 120) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline and process.poll() is None
        time.sleep(0.05)


def children(pid: int) -> list[int]:
    found = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    return [int(child) for child in found.stdout.split()]


def wait_until_finished(desk, seconds: float = 120) -> dict:
    """The release operation record once the operation on the Pi has ended."""
    operation = desk["root"] / "operation.json"
    deadline = time.monotonic() + seconds
    while (record := json.loads(operation.read_text())).get("state") == "started":
        assert time.monotonic() < deadline
        time.sleep(0.1)
    return record


def commit_change(desk, value: str) -> str:
    (desk["source"] / "candidate.py").write_text(f"DATABASE = None\nVALUE = {value!r}\n")
    return commit_all(desk["source"], f"change {value}")


def candidate_record(desk, revision: str) -> dict:
    return json.loads((desk["root"] / "releases" / revision[:12] / "release.json").read_text())


def ssh_calls(desk) -> list[list[str]]:
    log = desk["controls"] / "ssh.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def assert_running_release_unchanged(desk, services_before: list[str]) -> None:
    assert running(desk) == desk["first"][:12]
    assert systemctl_calls(desk) == services_before


def saved_pass(desk, revision: str) -> Path:
    return desk["controls"] / "state" / "jannenkoti-ops" / "checks" / f"{revision}.json"


def test_check_records_a_full_desktop_pass_for_the_exact_commit(desk):
    (desk["source"] / "tests" / "test_grouped.py").write_text(
        "import pytest\n\n\nclass TestGrouped:\n"
        "    def test_runs(self):\n        assert True\n\n"
        "    @pytest.mark.skip(reason='needs a browser')\n"
        "    def test_needs_browser(self):\n        pass\n\n"
        "    @pytest.mark.xfail(reason='known gap')\n"
        "    def test_known_gap(self):\n        assert False\n"
    )
    revision = commit_change(desk, "checked")
    # Uncommitted edits in the checkout cannot reach the run: it tests an export.
    (desk["source"] / "tests" / "test_local_only.py").write_text(
        "def test_x():\n    assert False\n"
    )

    result = ops(desk, "check", "main", "--source", str(desk["source"]))

    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(saved_pass(desk, revision).read_text())
    assert record["kind"] == "jannenkoti-desktop-tests"
    assert record["commit"] == revision
    assert record["tree"] == git(desk["source"], "rev-parse", "HEAD^{tree}")
    assert record["complete"] is True
    assert (record["collected"], record["failed"], record["errors"]) == (4, 0, 0)
    assert record["skipped_tests"] == ["tests/test_grouped.py::TestGrouped::test_needs_browser"]
    assert record["python"] == ".".join(map(str, sys.version_info[:3]))
    # One worker per CPU by default; never more workers than tests.
    assert record["workers"] == min(os.cpu_count(), 4)
    assert record["pins"] == []
    assert saved_pass(desk, revision).stat().st_mode & 0o777 == 0o600
    assert f"Desktop pass for {revision}" in result.stdout
    assert ssh_calls(desk) == []


def test_a_failing_desktop_suite_records_no_pass(desk):
    (desk["source"] / "tests" / "test_candidate.py").write_text(
        "def test_regression():\n    assert False\n"
    )
    revision = commit_all(desk["source"], "broken")

    result = ops(desk, "check", "main", "--source", str(desk["source"]))

    assert result.returncode == 1
    assert "1 failed" in result.stderr
    assert not saved_pass(desk, revision).exists()


def test_a_virtualenv_missing_a_pin_is_refused_before_running_the_suite(desk):
    (desk["source"] / "requirements.txt").write_text("pytest\nruff\nnot-installed-anywhere==1.0\n")
    revision = commit_all(desk["source"], "new pin")

    result = ops(desk, "check", "main", "--source", str(desk["source"]))

    assert result.returncode == 2
    assert "not-installed-anywhere==1.0" in result.stderr
    assert "pip install -r requirements.txt" in result.stderr
    assert "Collected" not in result.stdout
    assert not saved_pass(desk, revision).exists()


def test_prepare_sends_the_desktop_pass_and_reuses_a_saved_one(desk):
    revision = commit_change(desk, "reused")
    checked = ops(desk, "check", "main", "--source", str(desk["source"]), "--workers", "1")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(saved_pass(desk, revision).read_text())["workers"] == 1

    result = ops(desk, "prepare", "main", "--source", str(desk["source"]))

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Using the saved desktop pass for {revision}" in result.stdout
    assert "Collected" not in result.stdout
    tests = candidate_record(desk, revision)["checks"]["tests"]
    assert tests["status"] == "passed" and tests["source"] == "desktop"


def test_an_older_pi_release_tool_prepares_with_its_own_suite_run(desk):
    revision = commit_change(desk, "bootstrap")
    tool = desk["root"] / "current" / "code" / "scripts" / "release.py"
    real = tool.with_name("release-real.py")
    tool.rename(real)
    # The release before ADR 0006: no --desktop-tests option.
    tool.write_text(
        "import runpy, sys\n"
        f"open({str(desk['controls'] / 'old-tool.log')!r}, 'a').write(' '.join(sys.argv) + '\\n')\n"
        "if '--desktop-tests' in sys.argv:\n"
        "    sys.exit('release.py: error: unrecognized arguments: --desktop-tests')\n"
        "if sys.argv[1:3] == ['prepare', '--help']:\n"
        "    print('usage: release.py prepare [-h] [--source SOURCE] revision')\n"
        "    sys.exit(0)\n"
        f"sys.argv[0] = {str(real)!r}\n"
        f"runpy.run_path({str(real)!r}, run_name='__main__')\n"
    )

    result = ops(desk, "prepare", "main", "--source", str(desk["source"]))

    assert "predates desktop test passes" in result.stdout
    assert "Collected" not in result.stdout
    assert not saved_pass(desk, revision).exists()
    calls = (desk["controls"] / "old-tool.log").read_text()
    assert f"prepare {revision}" in calls and "--desktop-tests" not in calls
    assert candidate_record(desk, revision)["revision"] == revision


def test_an_unanswered_capability_question_stops_before_any_transfer(desk):
    commit_change(desk, "unanswered")
    tool = desk["root"] / "current" / "code" / "scripts" / "release.py"
    tool.rename(tool.with_name("release-real.py"))
    tool.write_text("import sys\nsys.exit('release.py: cannot start')\n")

    result = ops(desk, "prepare", "main", "--source", str(desk["source"]))

    assert result.returncode == 3, result.stdout + result.stderr
    assert "could not ask the Pi's release.py what it accepts" in result.stderr
    assert "runs the full suite itself" not in result.stdout
    assert not (desk["root"] / "transfer").exists()


def test_prepares_the_exact_desktop_commit_on_the_pi_without_github(desk):
    revision = commit_change(desk, "transferred")
    services_before = systemctl_calls(desk)

    result = ops(desk, "prepare", "main", "--source", str(desk["source"]))

    assert result.returncode == 0, result.stdout + result.stderr
    record = candidate_record(desk, revision)
    assert record["state"] == "prepared"
    assert record["revision"] == revision
    assert record["checks"]["tests"]["status"] == "passed"
    assert record["checks"]["tests"]["source"] == "desktop"
    assert "Collected 1 tests" in result.stdout
    assert record["tree"] == git(desk["source"], "rev-parse", "HEAD^{tree}")
    # Preparation leaves the running release and services alone.
    assert_running_release_unchanged(desk, services_before)
    # Only committed content travels: local untracked state stays on the desktop.
    code = desk["root"] / "releases" / revision[:12] / "code"
    assert (code / "candidate.py").read_text().endswith("VALUE = 'transferred'\n")
    assert not (code / "jannenkoti.env").exists()
    assert f"main is {revision}" in result.stdout
    assert "owner feature acceptance is missing" in result.stdout
    calls = ssh_calls(desk)
    assert all(call[call.index("--") + 1] == "raspi" for call in calls)
    assert all("BatchMode=yes" in call for call in calls)


def test_operations_on_the_pi_handle_interrupts_like_a_terminal_run(desk):
    """A detached job must not leave Ctrl-C ignored for what it runs, such as the checks."""
    commit_change(desk, "signals")
    controls = desk["controls"]
    installer = controls / "recording-installer"
    installer.write_text(
        f'#!/bin/sh\ngrep "^SigIgn:" /proc/self/status > "{controls}/installer-signals"\n'
        f'exec "{desk["installer"]}" "$@"\n'
    )
    installer.chmod(0o755)

    result = ops(
        desk, "prepare", "main", "--source", str(desk["source"]), "--installer", str(installer)
    )

    assert result.returncode == 0, result.stdout + result.stderr
    ignored = int((controls / "installer-signals").read_text().split()[1], 16)
    assert not ignored & (1 << (signal.SIGINT - 1)), "SIGINT is ignored"
    assert not ignored & (1 << (signal.SIGQUIT - 1)), "SIGQUIT is ignored"


def test_a_dropped_transfer_prepares_nothing_and_keeps_the_running_release(desk):
    revision = commit_change(desk, "dropped")
    services_before = systemctl_calls(desk)
    (desk["controls"] / "drop-transfer").touch()

    result = ops(desk, "prepare", "main", "--source", str(desk["source"]))

    assert result.returncode == 1
    assert "Transfer failed" in result.stderr
    assert "Connection reset by peer" in result.stderr
    assert "Nothing was prepared; the running release is unchanged." in result.stderr
    assert not (desk["root"] / "releases" / revision[:12]).exists()
    refs = git(desk["root"] / "transfer", "for-each-ref", "--format=%(refname)")
    assert revision not in refs
    assert_running_release_unchanged(desk, services_before)


def test_a_commit_the_pi_does_not_hold_exactly_is_not_prepared(desk):
    revision = commit_change(desk, "tampered")
    services_before = systemctl_calls(desk)
    # A Pi-side hook that moves the received ref elsewhere stands in for any
    # corruption between the desktop and preparation.
    transfer = desk["root"] / "transfer"
    git(transfer.parent, "init", "-q", str(transfer))
    hook = transfer / ".git" / "hooks" / "post-receive"
    hook.write_text(
        f'#!/bin/sh\nwhile read old new ref; do git update-ref "$ref" {desk["first"]}; done\n'
    )
    hook.chmod(0o755)

    result = ops(desk, "prepare", "main", "--source", str(desk["source"]))

    assert result.returncode == 1
    assert f"identity mismatch: the transferred commit is {desk['first']}" in result.stderr
    assert not (desk["root"] / "releases" / revision[:12]).exists()
    assert_running_release_unchanged(desk, services_before)


def test_an_interrupted_preparation_is_never_deployable_and_keeps_the_running_release(desk):
    revision = commit_change(desk, "interrupted")
    services_before = systemctl_calls(desk)
    controls = desk["controls"]
    installer = controls / "slow-installer"
    installer.write_text(
        f'#!/bin/sh\ntouch "{controls}/install-waiting"\n'
        f'while [ ! -f "{controls}/install-release" ]; do sleep 0.05; done\nexit 1\n'
    )
    installer.chmod(0o755)
    process = start_ops(
        desk,
        "prepare",
        "main",
        "--source",
        str(desk["source"]),
        "--installer",
        str(installer),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for(controls / "install-waiting", process)
        # The preparing release.py process on the Pi ends abruptly.
        os.kill(candidate_record(desk, revision)["pid"], signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=60)
    finally:
        (controls / "install-release").touch()
        if process.poll() is None:
            process.kill()

    assert process.returncode == 1, stdout + stderr
    assert "preparation did not finish" in stderr
    assert f"Candidate {revision[:12]} is incomplete" in stderr
    assert_running_release_unchanged(desk, services_before)
    status = json.loads(
        release(desk, "status", revision[:12], "--json").stdout,
    )
    assert status["deployable"] is False


def test_missing_production_settings_fail_preparation_before_any_downtime(desk):
    commit_change(desk, "unready")
    services_before = systemctl_calls(desk)
    unready = desk["controls"] / "unready.env"
    unready.write_text(
        "\n".join(
            line
            for line in desk["production_env"].read_text().splitlines()
            if not line.startswith("SECRET_KEY=")
        )
    )

    result = ops(
        desk, "prepare", "main", "--source", str(desk["source"]), "--production-env", str(unready)
    )

    assert result.returncode == 1
    assert "production setting SECRET_KEY is missing" in result.stdout
    assert PRODUCTION_SECRET not in result.stdout + result.stderr
    assert_running_release_unchanged(desk, services_before)


@pytest.mark.parametrize("problem", ["uncommitted", "credential", "unknown", "option"])
def test_unusable_sources_are_refused_before_contacting_the_pi(desk, problem):
    revision = "main"
    if problem == "uncommitted":
        (desk["source"] / "candidate.py").write_text("DATABASE = 'edited'\n")
    elif problem == "credential":
        (desk["source"] / "config").mkdir()
        (desk["source"] / "config" / "alembic.ini").write_text("[alembic]\n")
        git(desk["source"], "add", "-f", "config/alembic.ini")
        commit_all(desk["source"], "track a credential file")
    elif problem == "unknown":
        revision = "no-such-branch"
    else:
        revision = "--upload-pack=touch pwned"
    calls_before = ssh_calls(desk)

    result = ops(desk, "prepare", revision, "--source", str(desk["source"]))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "refused:" in result.stderr
    assert ssh_calls(desk) == calls_before


def prepared_through_ops(desk, value: str) -> str:
    revision = commit_change(desk, value)
    result = ops(desk, "prepare", "main", "--source", str(desk["source"]))
    assert result.returncode == 0, result.stdout + result.stderr
    return revision


def accept_on_terminal(desk, candidate: str, answer: str) -> tuple[int, str]:
    """Run ``ops.py accept`` with a pseudo-terminal as stdin, typing ``answer``."""
    leader, follower = pty.openpty()
    process = start_ops(
        desk,
        "accept",
        candidate,
        "--scope",
        "feature",
        "--note",
        "heater page checked in development",
        stdin=follower,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    os.close(follower)
    try:
        os.write(leader, answer.encode() + b"\n")
        output, _ = process.communicate(timeout=120)
    finally:
        os.close(leader)
        if process.poll() is None:
            process.kill()
    return process.returncode, output


def test_candidate_status_shows_identity_evidence_and_what_blocks_deployment(desk):
    revision = prepared_through_ops(desk, "inspected")

    listing = ops(desk, "candidate")
    single = ops(desk, "candidate", revision[:12], "--json")

    assert listing.returncode == 0, listing.stderr
    assert revision[:12] in listing.stdout and desk["first"][:12] in listing.stdout
    assert single.returncode == 0, single.stderr
    report = json.loads(single.stdout)
    assert report["revision"] == revision
    assert report["checks"]["tests"]["status"] == "passed"
    assert report["deployable"] is False
    assert "owner feature acceptance is missing" in report["blockers"]


def test_acceptance_is_recorded_only_after_the_owner_confirms_on_a_terminal(desk):
    revision = prepared_through_ops(desk, "accepted")
    candidate = revision[:12]

    status, output = accept_on_terminal(desk, candidate, candidate)

    assert status == 0, output
    assert f"revision {revision}" in output
    acceptance = candidate_record(desk, revision)["acceptance"]["feature"]
    assert acceptance["note"] == "heater page checked in development"
    report = json.loads(ops(desk, "candidate", candidate, "--json").stdout)
    assert report["deployable"] is True


def test_acceptance_without_a_terminal_or_confirmation_records_nothing(desk):
    revision = prepared_through_ops(desk, "unconfirmed")
    candidate = revision[:12]

    piped = ops(desk, "accept", candidate, "--scope", "feature", "--note", "agent says ok")
    status, output = accept_on_terminal(desk, candidate, "yes")

    assert piped.returncode == 2
    assert "owner's own terminal" in piped.stderr
    assert status == 2, output
    assert "not confirmed" in output
    assert candidate_record(desk, revision)["acceptance"] == {}


def accepted_through_ops(desk, value: str) -> str:
    """Prepare through ops.py; the owner's acceptance is recorded on the Pi directly."""
    revision = prepared_through_ops(desk, value)
    accepted = release(desk, "accept", revision[:12], "--scope", "feature", "--note", "checked")
    assert accepted.returncode == 0, accepted.stderr
    return revision


def stop_calls(desk) -> list[str]:
    return [call for call in systemctl_calls(desk) if not call.startswith("is-active")]


def test_deploys_an_accepted_desktop_commit_and_verifies_both_revisions_and_the_database(desk):
    revision = accepted_through_ops(desk, "deployed")

    result = ops(desk, "deploy", revision[:12])

    assert result.returncode == 0, result.stdout + result.stderr
    assert running(desk) == revision[:12]
    assert f"Deploy of {revision[:12]} succeeded and is ready." in result.stdout
    assert f"app           revision {revision}, database ok" in result.stdout
    assert f"gateway       revision {revision}, status healthy" in result.stdout
    assert "healthy       yes" in result.stdout
    (log,) = (desk["root"] / "jobs").glob("*-deployment-*.log")
    assert "Maintenance window ends" in log.read_text()
    assert PRODUCTION_SECRET not in result.stdout + result.stderr


def test_stale_acceptance_is_refused_before_any_downtime(desk):
    revision = accepted_through_ops(desk, "stale")
    code = desk["root"] / "releases" / revision[:12] / "code" / "candidate.py"
    code.write_text(code.read_text() + "# edited on the Pi after acceptance\n")
    services_before = stop_calls(desk)

    result = ops(desk, "deploy", revision[:12])

    assert result.returncode == 2, result.stdout + result.stderr
    assert "release code changed since preparation" in result.stdout
    assert "owner feature acceptance is stale" in result.stdout
    assert "Refused before any change; production is untouched." in result.stderr
    assert running(desk) == desk["first"][:12]
    assert stop_calls(desk) == services_before


def test_failed_readiness_recovers_compatible_code_and_says_what_to_do_next(desk):
    (desk["source"] / "candidate.py").write_text('DATABASE = "missing_database"\n')
    revision = commit_all(desk["source"], "a release whose app cannot reach its database")
    prepared = ops(desk, "prepare", "main", "--source", str(desk["source"]))
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    assert (
        release(desk, "accept", revision[:12], "--scope", "feature", "--note", "x").returncode == 0
    )

    result = ops(desk, "deploy", revision[:12], "--readiness-timeout", "4")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "app database is unavailable (HTTP 503)" in result.stdout
    assert running(desk) == desk["first"][:12]
    assert f"app           revision {desk['first']}, database ok" in result.stdout
    assert "ops.py recover" in result.stderr
    assert "separate owner decision" in result.stderr


def test_recovery_returns_to_the_previous_release_from_the_desktop(desk):
    revision = accepted_through_ops(desk, "recovered")
    assert ops(desk, "deploy", revision[:12]).returncode == 0

    result = ops(desk, "recover")

    assert result.returncode == 0, result.stdout + result.stderr
    assert running(desk) == desk["first"][:12]
    assert f"app           revision {desk['first']}, database ok" in result.stdout


def test_a_migration_bearing_candidate_needs_its_rehearsal_before_deployment(desk):
    (desk["source"] / "migrations" / "versions" / "0002_new_table.py").write_text(
        'revision = "0002"\ndown_revision = "0001"\n'
    )
    revision = accepted_through_ops(desk, "migrating")
    services_before = stop_calls(desk)

    result = ops(desk, "deploy", revision[:12])

    assert result.returncode == 2, result.stdout + result.stderr
    assert "needs database migrations (0002)" in result.stdout
    assert "valid migration rehearsal is required" in result.stdout
    assert stop_calls(desk) == services_before
    assert running(desk) == desk["first"][:12]


def test_a_lost_desktop_connection_never_interrupts_a_deployment_halfway(desk):
    revision = accepted_through_ops(desk, "detached")
    controls = desk["controls"]
    (controls / "hold-start").touch()
    process = start_ops(
        desk,
        "deploy",
        revision[:12],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        wait_for(controls / "start-waiting", process)
        # Services are stopped: the desktop, its ssh session and the follower on
        # the Pi all vanish.
        for ssh in children(process.pid):
            os.killpg(ssh, signal.SIGKILL)
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
    finally:
        (controls / "hold-start").unlink(missing_ok=True)

    assert wait_until_finished(desk)["state"] == "succeeded"
    assert running(desk) == revision[:12]
    logs = ops(desk, "logs", "release", "-n", "1")
    assert f"deploy {revision[:12]} succeeded" in logs.stdout


def test_stopping_the_watch_leaves_the_deployment_running_on_the_pi(desk):
    revision = accepted_through_ops(desk, "watched")
    controls = desk["controls"]
    (controls / "hold-start").touch()
    # Files, not pipes: the stand-in ssh is a local sh whose orphaned follower
    # would hold a pipe open, where a real ssh connection simply closes.
    out, err = controls / "watch.out", controls / "watch.err"
    with out.open("w") as stdout_file, err.open("w") as stderr_file:
        process = start_ops(desk, "deploy", revision[:12], stdout=stdout_file, stderr=stderr_file)
    try:
        wait_for(controls / "start-waiting", process)
        process.send_signal(signal.SIGINT)
        process.wait(timeout=30)
    finally:
        (controls / "hold-start").unlink(missing_ok=True)
        if process.poll() is None:
            process.kill()
    stdout, stderr = out.read_text(), err.read_text()

    assert process.returncode == 130, stdout + stderr
    assert "Stopped watching; the deployment continues on the Pi" in stderr
    assert "Traceback" not in stderr
    assert wait_until_finished(desk)["state"] == "succeeded"
    assert running(desk) == revision[:12]


def test_a_rehearsed_migration_deploys_after_its_backup_and_keeps_the_rows(desk, tmp_path):
    source = desk["source"]
    (source / "migrations" / "versions" / "0002_next.py").write_text(
        'from alembic import op\nrevision = "0002"\ndown_revision = "0001"\n'
        "def upgrade():\n"
        '    op.execute("ALTER TABLE samples ADD COLUMN active boolean DEFAULT true")\n'
    )
    (source / "migrations" / "rehearsal.sql").write_text(
        "INSERT INTO samples VALUES ('synthetic');\n"
    )
    (source / "migrations" / "rehearsal-verify.sql").write_text(
        "DO $$ BEGIN IF (SELECT count(*) FROM samples WHERE value='synthetic' AND active) <> 1 "
        "THEN RAISE EXCEPTION 'synthetic state lost'; END IF; END $$;\n"
    )
    revision = accepted_through_ops(desk, "migrated")
    candidate = revision[:12]
    sql(desk, "CREATE TABLE samples (value text); INSERT INTO samples VALUES ('preserve me')")

    rehearsed = ops(
        desk,
        "rehearse",
        candidate,
        "--disposable-env",
        str(disposable(desk, tmp_path, "first.env")),
        "--restore-env",
        str(disposable(desk, tmp_path, "second.env")),
    )
    deployed = ops(desk, "deploy", candidate)

    assert rehearsed.returncode == 0, rehearsed.stdout + rehearsed.stderr
    assert "Migration rehearsal and disposable restore passed" in rehearsed.stdout
    assert deployed.returncode == 0, deployed.stdout + deployed.stderr
    assert "Restricted pre-migration backup recorded" in deployed.stdout
    assert running(desk) == candidate
    assert sql(desk, "SELECT value FROM samples WHERE active") == "preserve me"
    (backup,) = (desk["root"] / "backups").glob(f"{candidate}-*.dump")
    assert backup.stat().st_mode & 0o777 == 0o600
    assert "healthy       yes" in deployed.stdout
