"""The release-preparation command builds an exact, verified candidate offline.

Tests drive ``scripts/release.py`` as a subprocess against a temporary Git
checkout, a temporary release root and a stand-in production environment. The
dependency installer is a controlled stand-in that builds a small virtualenv
reusing this interpreter's packages (no network). A fake ``systemctl`` on PATH
records any service-manager call; preparation must never make one.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import sysconfig
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "release.py"
PRODUCTION_SECRET = "production-secret-value"


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args], env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def tree_digest(path: Path) -> str:
    """Content and layout of a directory, to prove it was not changed."""
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        digest.update(str(item.relative_to(path)).encode())
        if item.is_symlink():
            digest.update(os.readlink(item).encode())
        elif item.is_file():
            digest.update(item.read_bytes())
    return digest.hexdigest()


@pytest.fixture
def world(tmp_path):
    """A source checkout, a stand-in production and controlled host boundaries."""
    source = tmp_path / "source"
    (source / "migrations" / "versions").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / ".gitignore").write_text("*.env\n*.db\n")
    (source / "requirements.txt").write_text("pytest\nruff\n")
    (source / "candidate.py").write_text("VALUE = 1\n")
    (source / "tests" / "test_candidate.py").write_text(
        "import candidate\n\n\ndef test_value():\n    assert candidate.VALUE == 1\n"
    )
    (source / "migrations" / "versions" / "0001_base.py").write_text(
        'revision = "0001"\ndown_revision = None\n'
    )
    git(source, "init", "-q", "-b", "main")
    commit_all(source, "base")
    # Untracked local state that must never reach a release artifact.
    (source / "jannenkoti.env").write_text(f"SECRET_KEY={PRODUCTION_SECRET}\n")
    (source / "local.db").write_text("persistent state")

    production = tmp_path / "production"
    (production / "migrations" / "versions").mkdir(parents=True)
    (production / "migrations" / "versions" / "0001_base.py").write_text(
        'revision = "0001"\ndown_revision = None\n'
    )
    production_env = production / "jannenkoti.env"
    production_env.write_text(
        "DATABASE_URL=postgresql+psycopg://jannenkoti:"
        f"{PRODUCTION_SECRET}@127.0.0.1:5432/jannenkoti\n"
        f"SECRET_KEY={PRODUCTION_SECRET}\n"
        "WEB_USERNAME=owner\n"
        f"WEB_PASSWORD={PRODUCTION_SECRET}\n"
        f"DB_PATH={production / 'legacy.db'}\n"
    )

    root = tmp_path / "releases"
    root.mkdir()
    # A running release #45 would have switched to; preparation must keep it.
    running = root / "releases" / "running"
    running.mkdir(parents=True)
    (running / "marker").write_text("running release")
    (root / "current").symlink_to(running)

    controls = tmp_path / "controls"
    controls.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl_log = controls / "systemctl.log"
    for name in ("systemctl", "sudo"):
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{systemctl_log}"\nexit 0\n')
        tool.chmod(0o755)

    installer = controls / "install-dependencies"
    installer.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import pathlib, subprocess, sys, time
            requirements, venv = sys.argv[1], sys.argv[2]
            controls = pathlib.Path({str(controls)!r})
            with open(controls / "installer.log", "a") as log:
                log.write(f"{{requirements}} {{venv}}\\n")
            if (controls / "fail-install").exists():
                print("simulated dependency installation failure", file=sys.stderr)
                sys.exit(1)
            deadline = time.monotonic() + 60
            while (controls / "hold-install").exists() and time.monotonic() < deadline:
                (controls / "install-waiting").touch()
                time.sleep(0.05)
            subprocess.run([{sys.executable!r}, "-m", "venv", "--without-pip", venv], check=True)
            site = next(pathlib.Path(venv).glob("lib/python*/site-packages"))
            (site / "stand-in-packages.pth").write_text({sysconfig.get_paths()["purelib"]!r})
            (pathlib.Path(venv) / "bin" / "ruff").symlink_to({str(Path(sys.executable).with_name("ruff"))!r})
            """
        )
    )
    installer.chmod(0o755)

    world = {
        "source": source,
        "production": production,
        "production_env": production_env,
        "root": root,
        "controls": controls,
        "installer": installer,
        "bin": bin_dir,
        "systemctl_log": systemctl_log,
    }
    world["production_digest"] = tree_digest(production)
    world["root_current"] = os.readlink(root / "current")
    return world


def release(world, *args: str, timeout: float = 180, popen: bool = False):
    command = [
        sys.executable,
        str(RELEASE),
        *args,
        "--release-root",
        str(world["root"]),
        "--production-env",
        str(world["production_env"]),
        "--production-code",
        str(world["production"]),
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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)


def prepare(world, revision: str = "main", *extra: str, **kwargs):
    return release(
        world,
        "prepare",
        revision,
        "--source",
        str(world["source"]),
        "--installer",
        str(world["installer"]),
        *extra,
        **kwargs,
    )


def status(world, candidate: str) -> dict:
    result = release(world, "status", candidate, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def assert_production_untouched(world) -> None:
    assert tree_digest(world["production"]) == world["production_digest"]
    assert os.readlink(world["root"] / "current") == world["root_current"]
    assert not (world["root"] / "previous").exists()
    assert not world["systemctl_log"].exists()


def test_prepares_the_exact_committed_revision_without_touching_production(world):
    first = git(world["source"], "rev-parse", "HEAD")
    (world["source"] / "candidate.py").write_text("VALUE = 1\nSECOND = True\n")
    second = commit_all(world["source"], "second")

    result = prepare(world, first)

    assert result.returncode == 0, result.stderr
    report = status(world, first[:12])
    assert report["revision"] == first
    assert report["requested"] == first
    assert report["state"] == "prepared"
    assert report["checks"]["tests"]["status"] == "passed"
    assert report["checks"]["lint"]["status"] == "passed"
    code = Path(report["code_dir"])
    assert code.is_relative_to(world["root"])
    assert (code / "candidate.py").read_text() == "VALUE = 1\n"
    assert not (code / "jannenkoti.env").exists()
    assert not (code / "local.db").exists()
    assert not (code / ".git").exists()
    installed_into = (world["controls"] / "installer.log").read_text().split()[-1]
    assert Path(installed_into) == Path(report["venv_dir"])
    assert Path(installed_into).is_relative_to(Path(report["code_dir"]).parent)
    assert report["dependencies"]["identity"].startswith("sha256:")
    assert report["dependencies"]["packages"] > 0
    # Owner acceptance is still pending, so the candidate is not yet deployable.
    assert report["deployable"] is False
    assert any("feature acceptance" in blocker for blocker in report["blockers"])
    assert second != first
    for path in world["root"].rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert PRODUCTION_SECRET not in path.read_text(errors="ignore"), path
    assert PRODUCTION_SECRET not in result.stdout + result.stderr
    assert_production_untouched(world)


def candidate_dirs(world) -> list[Path]:
    return [p for p in (world["root"] / "releases").glob("*") if p.name != "running"]


@pytest.mark.parametrize("problem", ["dirty", "ambiguous", "unknown"])
def test_refuses_dirty_or_ambiguous_input_instead_of_packaging_it(world, problem):
    source = world["source"]
    revision = "main"
    if problem == "dirty":
        (source / "candidate.py").write_text("VALUE = 2  # uncommitted\n")
        expected = "uncommitted changes"
    elif problem == "ambiguous":
        git(source, "tag", "release")
        (source / "candidate.py").write_text("VALUE = 1\nNEXT = 1\n")
        commit_all(source, "next")
        git(source, "branch", "release")
        revision = "release"
        expected = "ambiguous"
    else:
        revision = "no-such-branch"
        expected = "does not name a commit"

    result = prepare(world, revision)

    assert result.returncode == 2, result.stdout + result.stderr
    assert expected in result.stderr
    assert candidate_dirs(world) == []
    assert not (world["controls"] / "installer.log").exists()
    assert_production_untouched(world)


def test_requires_an_explicit_release_root(world):
    env = {k: v for k, v in os.environ.items() if k != "JANNENKOTI_RELEASE_ROOT"}
    result = subprocess.run(
        [sys.executable, str(RELEASE), "status"], env=env, capture_output=True, text=True
    )

    assert result.returncode == 2
    assert "--release-root" in result.stderr


def test_failed_dependency_installation_is_not_a_deployable_release(world):
    (world["controls"] / "fail-install").touch()

    result = prepare(world)

    assert result.returncode == 1
    candidate = git(world["source"], "rev-parse", "HEAD")[:12]
    report = status(world, candidate)
    assert report["state"] == "failed"
    assert "dependency installation failed" in report["failure"]
    assert report["deployable"] is False
    listing = release(world, "status")
    assert f"{candidate}  failed" in listing.stdout
    assert "blocked" in listing.stdout
    assert_production_untouched(world)


def test_failed_check_blocks_deployment(world):
    (world["source"] / "tests" / "test_candidate.py").write_text(
        "def test_regression():\n    assert False\n"
    )
    revision = commit_all(world["source"], "broken")

    result = prepare(world)

    assert result.returncode == 1
    report = status(world, revision[:12])
    assert report["state"] == "prepared"
    assert report["checks"]["tests"]["status"] == "failed"
    assert "1 failed" in report["checks"]["tests"]["summary"]
    assert "required check tests is failed" in report["blockers"]
    assert_production_untouched(world)


def test_reports_dependency_and_configuration_readiness_before_downtime(world):
    (world["source"] / "requirements.txt").write_text("pytest\nruff\nnot-installed-anywhere==1.0\n")
    revision = commit_all(world["source"], "new dependency")
    world["production_env"].write_text(
        world["production_env"].read_text().replace("WEB_USERNAME=owner\n", "")
    )
    world["production_digest"] = tree_digest(world["production"])

    result = prepare(world)

    assert result.returncode == 1
    report = status(world, revision[:12])
    assert "requirement not-installed-anywhere is not installed" in report["blockers"]
    assert report["configuration"]["ready"] is False
    assert "production setting WEB_USERNAME is missing" in report["blockers"]
    assert PRODUCTION_SECRET not in json.dumps(report) + result.stdout + result.stderr
    assert_production_untouched(world)


def accept(world, candidate: str, scope: str, note: str = "stand-in acceptance in a test"):
    return release(world, "accept", candidate, "--scope", scope, "--note", note)


def test_owner_acceptance_makes_a_verified_candidate_deployable(world):
    revision = git(world["source"], "rev-parse", "HEAD")
    assert prepare(world, "main", "--device-acceptance").returncode == 0
    candidate = revision[:12]

    assert accept(world, candidate, "feature").returncode == 0
    partial = status(world, candidate)
    assert partial["acceptance"]["feature"]["status"] == "accepted"
    assert partial["deployable"] is False
    assert "owner device acceptance is missing" in partial["blockers"]

    assert accept(world, candidate, "device", "stand-in heater, no real device").returncode == 0
    report = status(world, candidate)
    assert report["acceptance"]["device"]["note"] == "stand-in heater, no real device"
    assert report["migrations"]["required"] is False
    assert report["deployable"] is True, report["blockers"]
    assert_production_untouched(world)


@pytest.mark.parametrize("change", ["dependencies", "code"])
def test_changed_code_or_dependencies_invalidate_earlier_evidence(world, change):
    candidate = git(world["source"], "rev-parse", "HEAD")[:12]
    assert prepare(world).returncode == 0
    assert accept(world, candidate, "feature").returncode == 0
    before = status(world, candidate)
    assert before["deployable"] is True, before["blockers"]

    if change == "dependencies":
        site = next(Path(before["venv_dir"]).glob("lib/python*/site-packages"))
        dist = site / "surprise_package-1.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: surprise-package\nVersion: 1.0\n"
        )
        expected = "installed dependencies changed since preparation"
    else:
        (Path(before["code_dir"]) / "candidate.py").write_text("VALUE = 1\nPATCHED = True\n")
        expected = "release code changed since preparation"

    after = status(world, candidate)

    assert after["deployable"] is False
    assert expected in after["blockers"]
    assert after["checks"]["tests"]["status"] == "stale"
    assert after["acceptance"]["feature"]["status"] == "stale"
    refused = accept(world, candidate, "feature")
    assert refused.returncode == 2
    assert "changed since preparation" in refused.stderr


def test_a_new_revision_does_not_inherit_earlier_acceptance(world):
    first = git(world["source"], "rev-parse", "HEAD")[:12]
    assert prepare(world).returncode == 0
    assert accept(world, first, "feature").returncode == 0
    (world["source"] / "candidate.py").write_text("VALUE = 1\nFOLLOW_UP = True\n")
    second = commit_all(world["source"], "follow-up")[:12]

    assert prepare(world).returncode == 0

    assert status(world, first)["deployable"] is True
    report = status(world, second)
    assert report["acceptance"]["feature"]["status"] == "missing"
    assert report["deployable"] is False
    again = prepare(world)
    assert again.returncode == 2
    assert "already prepared" in again.stderr
    assert status(world, second)["state"] == "prepared"


def test_identifies_candidates_that_need_migrations_and_rehearsal(world):
    (world["source"] / "migrations" / "versions" / "0002_new_table.py").write_text(
        'revision = "0002"\ndown_revision = "0001"\n'
    )
    candidate = commit_all(world["source"], "migration")[:12]

    result = prepare(world)

    assert result.returncode == 0, result.stdout
    assert accept(world, candidate, "feature").returncode == 0
    report = status(world, candidate)
    assert report["migrations"]["required"] is True
    assert report["migrations"]["new"] == ["0002"]
    assert report["migrations"]["rehearsal"] == "missing"
    assert report["deployable"] is False
    assert "migration rehearsal on disposable PostgreSQL is missing" in report["blockers"]
    assert "migrations    required: 0002" in result.stdout
    assert_production_untouched(world)


def test_operations_cannot_interleave_and_interrupted_preparation_is_not_deployable(world):
    candidate = git(world["source"], "rev-parse", "HEAD")[:12]
    hold = world["controls"] / "hold-install"
    hold.touch()
    running = prepare(world, popen=True)
    try:
        deadline = time.monotonic() + 30
        while not (world["controls"] / "install-waiting").exists():
            assert running.poll() is None, "preparation exited early"
            assert time.monotonic() < deadline, "installer never started"
            time.sleep(0.05)

        competing = prepare(world)
        assert competing.returncode == 3
        assert "another release operation" in competing.stderr
        assert release(world, "prune").returncode == 3
        assert status(world, candidate)["state"] == "preparing"
    finally:
        os.killpg(running.pid, signal.SIGKILL)
        running.wait(timeout=10)

    interrupted = status(world, candidate)
    assert interrupted["state"] == "incomplete"
    assert interrupted["deployable"] is False

    hold.unlink()
    assert prepare(world).returncode == 0
    assert status(world, candidate)["state"] == "prepared"
    assert_production_untouched(world)


def test_retains_a_bounded_number_of_candidates_and_never_active_releases(world):
    backups = world["root"] / "backups"
    backups.mkdir()
    (backups / "production-before-0002.dump").write_text("recovery copy")
    prepared = []
    for number in range(3):
        (world["source"] / "candidate.py").write_text(f"VALUE = 1\nBUILD = {number}\n")
        prepared.append(commit_all(world["source"], f"build {number}")[:12])
        assert prepare(world, "main", "--keep", "5").returncode == 0
    previous = world["root"] / "previous"
    previous.symlink_to(world["root"] / "releases" / prepared[0])

    result = release(world, "prune", "--keep", "1")

    assert result.returncode == 0, result.stderr
    remaining = sorted(p.name for p in candidate_dirs(world))
    assert remaining == sorted([prepared[0], prepared[2]])
    assert (world["root"] / "releases" / "running" / "marker").exists()
    assert (backups / "production-before-0002.dump").read_text() == "recovery copy"
    assert status(world, prepared[0])["state"] == "prepared"
    previous.unlink()
    assert_production_untouched(world)
