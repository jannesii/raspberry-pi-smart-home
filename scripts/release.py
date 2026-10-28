#!/usr/bin/env python3
"""Prepare, deploy, inspect and recover exact production releases.

    .venv/bin/python scripts/release.py prepare <revision> --release-root DIR
    .venv/bin/python scripts/release.py status [<candidate>] [--live] --release-root DIR
    .venv/bin/python scripts/release.py accept <candidate> --scope feature --note TEXT ...
    .venv/bin/python scripts/release.py deploy <candidate> --release-root DIR
    .venv/bin/python scripts/release.py recover --release-root DIR
    .venv/bin/python scripts/release.py rehearse <candidate> --disposable-env FILE --restore-env FILE ...
    .venv/bin/python scripts/release.py restore --backup NAME --acknowledge-data-loss ...
    .venv/bin/python scripts/release.py restart --release-root DIR
    .venv/bin/python scripts/release.py prune [--keep N] --release-root DIR

Preparation exports one committed revision into its own release directory,
installs its dependencies into a separate virtualenv there, runs the required
checks with them and records the evidence. It never migrates the production
database, restarts services or switches the running release, and it never copies
production configuration or state into the candidate.

Only ``deploy``, ``recover`` and ``restart`` stop and start the production
services, through the service manager, and only while holding the release-root
lock. Rehearsed migrations require a restricted backup before deployment. The
release root may also be given as ``JANNENKOTI_RELEASE_ROOT``. See
docs/operations/releases.md.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("release")

RELEASE_ROOT_VAR = "JANNENKOTI_RELEASE_ROOT"
DEFAULT_PRODUCTION_ENV = Path("/home/jannesi/Code/server/jannenkoti.env")
# Production still runs from this checkout until the first release is deployed.
LEGACY_PRODUCTION_CODE = Path("/home/jannesi/Code/server")
DEFAULT_KEEP = 3

# ATTENTION: production may not be serving a verified release; act on the guidance.
OK, FAILED, REFUSED, BUSY, ATTENTION = 0, 1, 2, 3, 4

# The production units; both run from ``<release root>/current`` (see
# docs/operations/systemd). Stopped together, so no old worker overlaps a new one.
APP_SERVICE = "jannenkoti.service"
GATEWAY_SERVICE = "esp32_ws.service"
SERVICES = (APP_SERVICE, GATEWAY_SERVICE)
DEFAULT_SERVICE_MANAGER = "sudo systemctl"
DEFAULT_APP_URL = "http://127.0.0.1:5555"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:5556"
DEFAULT_READINESS_TIMEOUT = 60.0

# The full suite is always required: on one Pi it is cheaper than deciding
# per release whether a change touches shared behavior.
REQUIRED_CHECKS = {
    "lint": ["-m", "ruff", "check", "--no-cache", "."],
    "tests": ["-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider"],
}
ACCEPTANCE_SCOPES = ("feature", "device")
REQUIRED_PRODUCTION_SETTINGS = (
    "DATABASE_URL",
    "SECRET_KEY",
    "WEB_USERNAME",
    "WEB_PASSWORD",
    "DB_PATH",
)
# Child processes get process basics only, never the caller's app settings.
PASSED_VARIABLES = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")
PASSED_PREFIXES = ("PIP_",)
PASSED_PROXIES = ("http_proxy", "https_proxy", "no_proxy")
CACHE_DIRECTORIES = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"})
CREDENTIAL_FILE = re.compile(r"(^|/)([^/]*\.env|alembic\.ini)$")
MIGRATION_REVISION = re.compile(r"""^revision(?:\s*:\s*str)?\s*=\s*["']([^"']+)["']""", re.M)
DOWN_REVISION = re.compile(r"^down_revision(?:\s*:[^=]*)?\s*=\s*(.+)$", re.M)

_LIST_DISTRIBUTIONS = (
    "import importlib.metadata as m, re\n"
    "names = set()\n"
    "for d in m.distributions():\n"
    "    name = d.metadata['Name']\n"
    "    if name:\n"
    "        names.add(re.sub(r'[-_.]+', '-', name).lower() + '==' + d.version)\n"
    "print('\\n'.join(sorted(names)))\n"
)


class Refused(Exception):
    """The request is unsafe or ambiguous; nothing was changed."""


class Busy(Exception):
    """Another release operation holds the release root."""


# --- Small helpers -----------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _child_env(**extra: str) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name in PASSED_VARIABLES
        or name.startswith(PASSED_PREFIXES)
        or name.lower() in PASSED_PROXIES
    }
    env.update(extra)
    return env


def _git(source: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(source), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@contextlib.contextmanager
def _locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / ".lock").open("a")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy(f"another release operation is using {root}")
        yield
    finally:
        handle.close()


def _write_record(directory: Path, record: dict) -> None:
    temporary = directory / ".release.json.tmp"
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, directory / "release.json")


def _read_record(directory: Path) -> dict | None:
    try:
        return json.loads((directory / "release.json").read_text())
    except (OSError, ValueError):
        return None


def _candidates_dir(root: Path) -> Path:
    return root / "releases"


def _active_releases(root: Path) -> dict[Path, str]:
    """Directories of the running ("current") and "previous" releases."""
    active = {}
    for name in ("current", "previous"):
        link = root / name
        if link.is_symlink() or link.exists():
            active[link.resolve()] = name
    return active


# --- Identities --------------------------------------------------------------


def code_identity(code: Path) -> str | None:
    if not code.is_dir():
        return None
    digest = hashlib.sha256()
    for directory, subdirs, files in os.walk(code):
        subdirs[:] = sorted(d for d in subdirs if d not in CACHE_DIRECTORIES)
        for name in sorted(files):
            path = Path(directory, name)
            relative = path.relative_to(code).as_posix()
            if path.is_symlink():
                payload = b"link:" + os.readlink(path).encode()
            else:
                mode = b"x" if os.access(path, os.X_OK) else b"-"
                payload = mode + hashlib.sha256(path.read_bytes()).digest()
            digest.update(relative.encode() + b"\0" + payload + b"\0")
    return "sha256:" + digest.hexdigest()


def installed_distributions(venv: Path) -> list[str] | None:
    python = venv / "bin" / "python"
    if not python.exists():
        return None
    result = subprocess.run(
        [str(python), "-I", "-c", _LIST_DISTRIBUTIONS],
        capture_output=True,
        text=True,
        env=_child_env(PYTHONDONTWRITEBYTECODE="1"),
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def dependency_identity(distributions: list[str] | None) -> str | None:
    if distributions is None:
        return None
    return "sha256:" + hashlib.sha256("\n".join(distributions).encode()).hexdigest()


# --- Readiness ---------------------------------------------------------------


def dependency_problems(code: Path, distributions: list[str] | None) -> list[str]:
    if distributions is None:
        return ["candidate dependencies cannot be listed"]
    installed = dict(item.split("==", 1) for item in distributions)
    problems = []
    try:
        lines = (code / "requirements.txt").read_text().splitlines()
    except OSError:
        return ["requirements.txt is missing from the candidate"]
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-") or ";" in line:
            continue
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)", line)
        if not match:
            continue
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        spec = match.group(2).replace(" ", "")
        if name not in installed:
            problems.append(f"requirement {name} is not installed")
        elif spec.startswith("==") and "," not in spec and installed[name] != spec[2:]:
            problems.append(f"requirement {name} needs {spec[2:]}, installed {installed[name]}")
    return problems


def configuration_problems(production_env: Path) -> list[str]:
    """Check the production settings a release needs, by key name only."""
    from dotenv import dotenv_values

    if not production_env.is_file():
        return [f"production environment file {production_env} is not readable"]
    try:
        values = {k: v for k, v in dotenv_values(production_env).items() if v}
    except OSError:
        return [f"production environment file {production_env} is not readable"]
    problems = [
        f"production setting {name} is missing"
        for name in REQUIRED_PRODUCTION_SETTINGS
        if not values.get(name, "").strip()
    ]
    database_url = values.get("DATABASE_URL", "")
    if database_url and not database_url.startswith("postgresql"):
        problems.append("production setting DATABASE_URL must use PostgreSQL")
    if values.get("USE_SQLA_READS", "").strip().lower() not in ("1", "true", "yes", "on"):
        # The app reaches DATABASE_URL, and its readiness check, only with this set.
        problems.append("production setting USE_SQLA_READS must be enabled")
    if values.get("APP_ENV", "").strip().lower() not in ("", "production"):
        problems.append("production setting APP_ENV must be production or unset")
    return problems


def migration_revisions(code: Path) -> set[str] | None:
    versions = code / "migrations" / "versions"
    if not versions.is_dir():
        return None
    revisions = set()
    for path in versions.glob("*.py"):
        match = MIGRATION_REVISION.search(path.read_text(errors="ignore"))
        if match:
            revisions.add(match.group(1))
    return revisions


def migration_requirement(code: Path, reference: Path) -> dict:
    candidate = migration_revisions(code) or set()
    production = migration_revisions(reference)
    result = {"reference": str(reference)}
    if production is None:
        return {**result, "required": None, "new": [], "missing": []}
    new = sorted(candidate - production)
    return {
        **result,
        "required": bool(new),
        "new": new,
        "missing": sorted(production - candidate),
    }


def historical_migration_problems(code: Path, reference: Path) -> list[str]:
    """Applied migration files are immutable, even when revision IDs are retained."""

    def history(directory):
        result = {}
        for path in (directory / "migrations/versions").glob("*.py"):
            match = MIGRATION_REVISION.search(path.read_text(errors="ignore"))
            if match:
                revision = match.group(1)
                if revision in result:
                    raise Refused("duplicate migration revision " + revision)
                result[revision] = path.read_bytes()
        return result

    candidate, baseline = history(code), history(reference)
    return [
        f"applied migration {revision} changed or is missing from candidate"
        for revision, payload in baseline.items()
        if candidate.get(revision) != payload
    ]


def migration_heads(code: Path) -> set[str] | None:
    """Revisions no other migration of ``code`` builds on: the schema it expects."""
    versions = code / "migrations" / "versions"
    if not versions.is_dir():
        return None
    revisions, parents = set(), set()
    for path in versions.glob("*.py"):
        text = path.read_text(errors="ignore")
        match = MIGRATION_REVISION.search(text)
        if not match:
            continue
        revisions.add(match.group(1))
        down = DOWN_REVISION.search(text)
        if down:
            parents.update(re.findall(r"""["']([^"']+)["']""", down.group(1)))
    return revisions - parents


# --- Evaluation --------------------------------------------------------------


def _evidence_status(entry: dict | None, code: str | None, dependencies: str | None) -> str:
    if not entry:
        return "missing"
    if entry.get("code") != code or entry.get("dependencies") != dependencies:
        return "stale"
    return entry["status"]


def _process_alive(pid: object) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def evaluate(directory: Path, production_env: Path, production_code: Path) -> dict:
    """Recompute the candidate's identities and whether it may be deployed."""
    record = _read_record(directory) or {}
    code = directory / "code"
    venv = directory / "venv"
    state = record.get("state", "unknown")
    if state == "preparing" and not _process_alive(record.get("pid")):
        state = "incomplete"
    blockers: list[str] = []
    # Steps that follow preparation (owner acceptance, rehearsal) rather than failures.
    awaiting: list[str] = []

    if state == "failed":
        blockers.append(f"preparation failed: {record.get('failure', 'unknown reason')}")
    elif state == "preparing":
        blockers.append("preparation is still in progress")
    elif state != "prepared":
        blockers.append("preparation did not complete; prepare the revision again")

    report = {
        "candidate": directory.name,
        "state": state,
        "revision": record.get("revision"),
        "requested": record.get("requested"),
        "source": record.get("source"),
        "created_at": record.get("created_at"),
        "code_dir": str(code),
        "venv_dir": str(venv),
        "failure": record.get("failure"),
    }
    if state != "prepared":
        report.update(deployable=False, blockers=blockers)
        return report

    distributions = installed_distributions(venv)
    current_code = code_identity(code)
    current_dependencies = dependency_identity(distributions)

    code_matches = current_code == record.get("code_identity")
    dependencies_matches = current_dependencies == record.get("dependency_identity")
    if not code_matches:
        blockers.append("release code changed since preparation")
    if not dependencies_matches:
        blockers.append("installed dependencies changed since preparation")
    dependency_issues = dependency_problems(code, distributions)
    blockers += dependency_issues
    config_issues = configuration_problems(production_env)
    blockers += config_issues

    checks = {}
    for name in record.get("required_checks", []):
        entry = record.get("checks", {}).get(name)
        status = _evidence_status(entry, current_code, current_dependencies)
        checks[name] = {"status": status, "summary": (entry or {}).get("summary")}
        if status != "passed":
            blockers.append(f"required check {name} is {status}")

    acceptance = {}
    for scope in ACCEPTANCE_SCOPES:
        entry = record.get("acceptance", {}).get(scope)
        required = scope in record.get("required_acceptance", [])
        status = _evidence_status(entry, current_code, current_dependencies)
        acceptance[scope] = {
            "required": required,
            "status": status,
            "note": (entry or {}).get("note"),
            "at": (entry or {}).get("at"),
        }
        if required and status != "accepted":
            blockers.append(f"owner {scope} acceptance is {status}")
            awaiting.append(blockers[-1])

    migrations = migration_requirement(code, production_code)
    blockers.extend(historical_migration_problems(code, production_code))
    rehearsal = _evidence_status(
        record.get("evidence", {}).get("migration-rehearsal"), current_code, current_dependencies
    )
    evidence = record.get("evidence", {}).get("migration-rehearsal", {})
    if rehearsal == "passed" and migrations["required"]:
        import release_database as db

        try:
            baseline = db.revisions(db.database_url(production_env))
            if evidence.get("baseline") != baseline or evidence.get(
                "reference_code"
            ) != code_identity(production_code / "migrations"):
                rehearsal = "stale"
        except db.DatabaseFailure:
            rehearsal = "unknown"
    migrations["rehearsal"] = rehearsal if migrations["required"] is not False else "not required"
    if migrations["required"] is None:
        blockers.append(
            f"migration requirement unknown: no migrations found under {production_code}"
        )
    elif migrations["missing"]:
        blockers.append("candidate lacks production migrations " + ", ".join(migrations["missing"]))
    if migrations["required"] is not False and rehearsal != "passed":
        blockers.append(f"migration rehearsal on disposable PostgreSQL is {rehearsal}")
        awaiting.append(blockers[-1])

    report.update(
        code={"identity": current_code, "matches": code_matches},
        dependencies={
            "identity": current_dependencies,
            "packages": len(distributions or []),
            "matches": dependencies_matches,
            "problems": dependency_issues,
        },
        configuration={"ready": not config_issues, "problems": config_issues},
        checks=checks,
        acceptance=acceptance,
        migrations=migrations,
        deployable=not blockers,
        awaiting=awaiting,
        blockers=blockers,
    )
    return report


# --- Commands ----------------------------------------------------------------


def _release_root(args: argparse.Namespace) -> Path:
    raw = args.release_root or os.environ.get(RELEASE_ROOT_VAR)
    if not raw:
        raise Refused(f"pass --release-root or set {RELEASE_ROOT_VAR}")
    root = Path(raw).expanduser().absolute()
    probe = root if root.exists() else root.parent
    if probe.is_dir() and _git(probe, "rev-parse", "--show-toplevel").returncode == 0:
        raise Refused(f"release root {root} must be outside Git checkouts")
    return root


def _production_code(args: argparse.Namespace, root: Path) -> Path:
    if args.production_code:
        return args.production_code
    current = root / "current" / "code"
    return current if current.is_dir() else LEGACY_PRODUCTION_CODE


def _resolve_revision(source: Path, revision: str) -> str:
    toplevel = _git(source, "rev-parse", "--show-toplevel")
    if toplevel.returncode != 0:
        raise Refused(f"{source} is not a Git checkout")
    if revision.startswith("-"):
        raise Refused(f"revision {revision!r} is not a revision name")
    dirty = _git(source, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise Refused(
            f"{source} has uncommitted changes; commit or stash them so the candidate "
            "is exactly a committed revision"
        )
    resolved = _git(source, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if resolved.returncode != 0:
        raise Refused(f"revision {revision!r} does not name a commit in {source}")
    if "ambiguous" in resolved.stderr:
        raise Refused(f"revision {revision!r} is ambiguous; name the branch, tag or commit")
    return resolved.stdout.strip()


def _export(source: Path, revision: str, code: Path) -> None:
    with tempfile.TemporaryFile() as archive:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(source), "archive", "--format=tar", revision],
            stdout=archive,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("git archive failed")
        archive.seek(0)
        code.mkdir()
        with tarfile.open(fileobj=archive) as tar:
            tracked_credentials = [n for n in tar.getnames() if CREDENTIAL_FILE.search(n)]
            if tracked_credentials:
                raise RuntimeError(
                    "the revision tracks credential files: " + ", ".join(tracked_credentials)
                )
            tar.extractall(code, filter="data")


def _run_logged(command: list[str], log: Path, cwd: Path, timeout: float) -> int:
    with log.open("w") as output:
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=_child_env(PYTHONDONTWRITEBYTECODE="1"),
                timeout=timeout,
                check=False,
            ).returncode
        except subprocess.TimeoutExpired:
            output.write(f"\n[release] timed out after {timeout:.0f}s\n")
            return 124


def _summary(log: Path) -> str:
    lines = [line.strip() for line in log.read_text(errors="ignore").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def _install_dependencies(installer: list[str], code: Path, venv: Path, logs: Path) -> None:
    logger.info("Installing candidate dependencies into %s", venv)
    status = _run_logged(
        [*installer, str(code / "requirements.txt"), str(venv)],
        logs / "install.log",
        cwd=code,
        timeout=3600,
    )
    if status != 0:
        logger.warning("Dependency installation failed with status %s", status)
        raise RuntimeError(
            f"dependency installation failed (status {status}); see {logs / 'install.log'}"
        )


def _default_installer() -> list[str]:
    """Build a fresh virtualenv and install the pinned requirements into it."""
    script = (
        "import subprocess, sys\n"
        "requirements, venv = sys.argv[1], sys.argv[2]\n"
        "subprocess.run([sys.executable, '-m', 'venv', venv], check=True)\n"
        "subprocess.run([venv + '/bin/python', '-m', 'pip', 'install',\n"
        "    '--disable-pip-version-check', '--requirement', requirements], check=True)\n"
    )
    return [sys.executable, "-c", script]


def prune(root: Path, keep: int) -> list[str]:
    """Remove older inactive candidates; never the running or previous release.

    Only candidate directories with a release record are considered, so backups
    and anything else under the release root are left alone.
    """
    if keep < 1:
        raise Refused("--keep must be at least 1")
    protected = _active_releases(root)
    candidates = []
    for directory in sorted(_candidates_dir(root).glob("*")):
        record = _read_record(directory)
        if record is None or directory.resolve() in protected or record.get("backups"):
            continue
        candidates.append((record.get("created_at", ""), directory))
    candidates.sort(reverse=True)
    removed = []
    for _created, directory in candidates[keep:]:
        logger.info("Removing retained candidate %s", directory.name)
        shutil.rmtree(directory)
        removed.append(directory.name)
    return removed


def command_prepare(args: argparse.Namespace) -> int:
    root = _release_root(args)
    source = args.source.absolute()
    installer = [str(args.installer)] if args.installer else _default_installer()
    if args.keep < 1:
        raise Refused("--keep must be at least 1")
    with _locked(root):
        revision = _resolve_revision(source, args.revision)
        logger.info("Resolved %s in %s to %s", args.revision, source, revision)
        directory = _candidates_dir(root) / revision[:12]
        if directory.exists():
            existing = _read_record(directory) or {}
            if existing.get("backups"):
                raise Refused(
                    "candidate has recovery backups; preserve its record and prepare another revision"
                )
            if directory.resolve() in _active_releases(root):
                raise Refused(f"candidate {directory.name} is the running or previous release")
            if existing.get("state") == "prepared" and not args.replace:
                raise Refused(
                    f"candidate {directory.name} is already prepared; inspect it with status "
                    "or pass --replace to rebuild it and discard its evidence"
                )
            logger.info("Replacing earlier candidate %s", directory.name)
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        record = {
            "state": "preparing",
            "pid": os.getpid(),
            "revision": revision,
            "requested": args.revision,
            "source": str(source),
            "created_at": _now(),
            "required_checks": list(REQUIRED_CHECKS),
            "required_acceptance": ["feature", "device"] if args.device_acceptance else ["feature"],
            "checks": {},
            "acceptance": {},
            "evidence": {},
        }
        _write_record(directory, record)
        code, venv, logs = directory / "code", directory / "venv", directory / "logs"
        logs.mkdir()
        try:
            _export(source, revision, code)
            _install_dependencies(installer, code, venv, logs)
            distributions = installed_distributions(venv)
            if distributions is None:
                raise RuntimeError("the installed environment has no working Python")
            (directory / "dependencies.txt").write_text("\n".join(distributions) + "\n")
            identities = {
                "code": code_identity(code),
                "dependencies": dependency_identity(distributions),
            }
            record["code_identity"] = identities["code"]
            record["dependency_identity"] = identities["dependencies"]
            record["tree"] = _git(source, "rev-parse", f"{revision}^{{tree}}").stdout.strip()
            for name, check in REQUIRED_CHECKS.items():
                log = logs / f"{name}.log"
                logger.info("Running required check %s", name)
                status = _run_logged([str(venv / "bin" / "python"), *check], log, code, 3600)
                result = "passed" if status == 0 else "failed"
                logger.info("Required check %s %s", name, result)
                record["checks"][name] = {
                    "status": result,
                    "summary": _summary(log),
                    "at": _now(),
                    **identities,
                }
        except Exception as exc:
            logger.warning("Preparation of %s failed: %s", directory.name, exc)
            record.update(state="failed", failure=str(exc))
            record.pop("pid", None)
            _write_record(directory, record)
            print_report(evaluate(directory, args.production_env, _production_code(args, root)))
            return FAILED
        record["state"] = "prepared"
        record.pop("pid", None)
        _write_record(directory, record)
        prune(root, args.keep)
    report = evaluate(directory, args.production_env, _production_code(args, root))
    print_report(report)
    unmet = set(report["blockers"]) - set(report.get("awaiting", []))
    return FAILED if unmet else OK


def command_status(args: argparse.Namespace) -> int:
    root = _release_root(args)
    production_code = _production_code(args, root)
    if args.candidate:
        directory = _candidates_dir(root) / args.candidate
        if _read_record(directory) is None:
            raise Refused(f"no candidate {args.candidate} under {root}")
        report = evaluate(directory, args.production_env, production_code)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_report(report)
        return OK
    reports = [
        evaluate(directory, args.production_env, production_code)
        for directory in sorted(_candidates_dir(root).glob("*"))
        if _read_record(directory) is not None
    ]
    reports.sort(key=lambda r: r.get("created_at") or "")
    protected = {path.name: name for path, name in _active_releases(root).items()}
    production = production_report(root)
    live = None
    if args.live:
        current = production["current"]
        live = readiness(_operations(args), current["revision"] if current else None)
    exit_status = FAILED if live is not None and not live["ready"] else OK
    if args.json:
        payload = {"candidates": reports, "releases": protected, "production": production}
        if live is not None:
            payload["live"] = live
        print(json.dumps(payload, indent=2, sort_keys=True))
        return exit_status
    print_production(production, live)
    if not reports:
        print(f"No candidates under {root}")
    for report in reports:
        label = protected.get(report["candidate"], "")
        print(
            f"{report['candidate']}  {report['state']:<10} "
            f"{'deployable' if report['deployable'] else 'blocked':<10} "
            f"{report.get('created_at') or ''} {label}".rstrip()
        )
    return exit_status


def command_accept(args: argparse.Namespace) -> int:
    root = _release_root(args)
    directory = _candidates_dir(root) / args.candidate
    with _locked(root):
        record = _read_record(directory)
        if record is None:
            raise Refused(f"no candidate {args.candidate} under {root}")
        if record.get("state") != "prepared":
            raise Refused(f"candidate {args.candidate} is not prepared; acceptance not recorded")
        code = code_identity(directory / "code")
        dependencies = dependency_identity(installed_distributions(directory / "venv"))
        if code != record.get("code_identity") or dependencies != record.get("dependency_identity"):
            raise Refused(f"candidate {args.candidate} changed since preparation; prepare it again")
        record.setdefault("acceptance", {})[args.scope] = {
            "status": "accepted",
            "note": args.note,
            "at": _now(),
            "code": code,
            "dependencies": dependencies,
        }
        _write_record(directory, record)
        logger.info("Recorded owner %s acceptance for %s", args.scope, args.candidate)
    print_report(evaluate(directory, args.production_env, _production_code(args, root)))
    return OK


# --- Deployment --------------------------------------------------------------

# Runs with a release's own interpreter; the URL arrives in the environment so
# it never appears in a process listing, and errors are reported without it.
_DATABASE_REVISIONS = (
    "import json, os\n"
    "try:\n"
    "    from sqlalchemy import create_engine, text\n"
    "    from sqlalchemy.pool import NullPool\n"
    "    engine = create_engine(os.environ['RELEASE_DATABASE_URL'], poolclass=NullPool,\n"
    "                           connect_args={'connect_timeout': 10})\n"
    "    with engine.connect() as conn:\n"
    "        rows = conn.execute(text('SELECT version_num FROM alembic_version')).scalars()\n"
    "        print(json.dumps({'revisions': sorted(rows)}))\n"
    "except Exception as exc:\n"
    "    reason = str(getattr(exc, 'orig', None) or exc).strip().splitlines()\n"
    "    detail = type(exc).__name__ + (': ' + reason[0][:200] if reason else '')\n"
    "    print(json.dumps({'error': detail}))\n"
)
OPERATION_FILE = "operation.json"
OPERATION_LOG = "operations.log"


@dataclasses.dataclass(frozen=True)
class Operations:
    """How to reach the production services; substituted as a whole in tests."""

    service_manager: list[str]
    app_url: str
    gateway_url: str
    readiness_timeout: float


def _operations(args: argparse.Namespace) -> Operations:
    explicit = args.service_manager and args.app_url and args.gateway_url
    if "PYTEST_CURRENT_TEST" in os.environ and not explicit:
        # Tests must never reach the host's service manager or production ports.
        raise Refused("under tests pass --service-manager, --app-url and --gateway-url")
    if args.readiness_timeout <= 0:
        raise Refused("--readiness-timeout must be positive")
    return Operations(
        service_manager=shlex.split(args.service_manager or DEFAULT_SERVICE_MANAGER),
        app_url=(args.app_url or DEFAULT_APP_URL).rstrip("/"),
        gateway_url=(args.gateway_url or DEFAULT_GATEWAY_URL).rstrip("/"),
        readiness_timeout=args.readiness_timeout,
    )


def _service(ops: Operations, action: str, *units: str, timeout: float = 120) -> int:
    quiet = action == "is-active"
    if not quiet:
        logger.info("Service manager: %s %s", action, " ".join(units))
    try:
        result = subprocess.run(
            [*ops.service_manager, action, *units],
            stdout=subprocess.DEVNULL if quiet else None,
            env=_child_env(),
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Service manager %s failed: %s", action, exc)
        return 1
    if result.returncode != 0 and not quiet:
        logger.warning("Service manager %s exited with status %s", action, result.returncode)
    return result.returncode


_HTTP_PROBE = """
import json, sys, urllib.error, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    try:
        response = opener.open(sys.argv[1], timeout=float(sys.argv[2]))
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status, raw = response.code, response.read(65536)
    body = json.loads(raw or b"{}")
    print(json.dumps([status, body if isinstance(body, dict) else {}]))
except Exception:
    print(json.dumps([None, {}]))
"""


def _http_json(url: str, timeout: float = 5) -> tuple[int | None, dict]:
    # A socket inactivity timeout alone does not bound slow headers/body streams.
    # Bound the entire HTTP exchange in a child, including error-body reads.
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _HTTP_PROBE, url, str(timeout)],
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        status, body = json.loads(result.stdout)
        return status, body
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, {}


def readiness(ops: Operations, expected: str | None, deadline: float | None = None) -> dict:
    """Probe the running services once. Reads only; no device or automation effects.

    Each process reports the revision it was started from, so a running process
    of the wrong release, or a unit pointing elsewhere, is not ready.
    """
    deadline = deadline if deadline is not None else time.monotonic() + ops.readiness_timeout
    problems: list[str] = []
    services = {}
    for unit in SERVICES:
        remaining = deadline - time.monotonic()
        active = remaining > 0 and _service(ops, "is-active", unit, timeout=remaining) == 0
        services[unit] = "active" if active else "inactive"
        if services[unit] != "active":
            problems.append(f"{unit} is not active")
    probes = {}
    for name, url, field, healthy in (
        ("app", ops.app_url + "/healthz", "database", "ok"),
        ("gateway", ops.gateway_url + "/health", "status", "healthy"),
    ):
        remaining = deadline - time.monotonic()
        status, body = _http_json(url, timeout=min(5, remaining)) if remaining > 0 else (None, {})
        if status is None:
            probes[name] = None
            problems.append(f"{name} health check at {url} is unreachable")
            continue
        probes[name] = {"revision": body.get("revision"), field: body.get(field), "http": status}
        if status != 200:
            logger.info("%s health check returned HTTP %s", name, status)
            problems.append(f"{name} health check returned HTTP {status}")
        if body.get("revision") != expected:
            problems.append(
                f"{name} runs revision {body.get('revision') or 'unknown'}, expected {expected}"
            )
        if body.get(field) != healthy:
            problems.append(f"{name} {field} is {body.get(field) or 'unknown'} (HTTP {status})")
    return {"ready": not problems, "services": services, **probes, "problems": problems}


def wait_ready(ops: Operations, expected: str | None) -> dict:
    deadline = time.monotonic() + ops.readiness_timeout
    last_complete = None

    def expired(result):
        return {
            **result,
            "ready": False,
            "problems": [*result["problems"], "readiness deadline expired"],
        }

    while True:
        result = readiness(ops, expected, deadline)
        if time.monotonic() >= deadline:
            # A final interrupted probe did not establish newer service/HTTP
            # state. Keep the last completed observation and explicitly report
            # expiry; stale observations never count as successful readiness.
            return expired(last_complete or result)
        if result["ready"]:
            return result
        last_complete = result
        time.sleep(min(1, max(0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            return expired(result)


def _database_revisions(venv: Path, production_env: Path) -> tuple[set[str] | None, str]:
    from dotenv import dotenv_values

    url = ""
    if production_env.is_file():
        url = (dotenv_values(production_env).get("DATABASE_URL") or "").strip()
    if not url:
        return None, f"DATABASE_URL is not readable from {production_env}"
    python = venv / "bin" / "python"
    if not python.exists():
        return None, f"{venv} has no Python"
    try:
        result = subprocess.run(
            [str(python), "-I", "-c", _DATABASE_REVISIONS],
            capture_output=True,
            text=True,
            env=_child_env(PYTHONDONTWRITEBYTECODE="1", RELEASE_DATABASE_URL=url),
            timeout=60,
            check=False,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return None, "the database query timed out"
    except (ValueError, IndexError):
        return None, "the database query did not run"
    if "error" in payload:
        return None, payload["error"]
    return set(payload["revisions"]), ""


def database_compatibility(directory: Path, production_env: Path) -> tuple[bool | None, str]:
    """Whether the production schema is exactly the one ``directory``'s code expects.

    ``None`` means compatibility is unknown, which never counts as compatible.
    """
    heads = migration_heads(directory / "code")
    if not heads:
        return None, f"release {directory.name} has no migration history"
    revisions, error = _database_revisions(directory / "venv", production_env)
    if revisions is None:
        return None, f"cannot read the database schema revision ({error})"
    expected = ", ".join(sorted(heads))
    if revisions == heads:
        return True, f"database schema {expected} matches release {directory.name}"
    actual = ", ".join(sorted(revisions)) or "no revision"
    return False, f"database schema is {actual}; release {directory.name} expects {expected}"


def _link_target(root: Path, name: str) -> Path | None:
    link = root / name
    return link.resolve() if link.is_symlink() and link.exists() else None


def _point(root: Path, name: str, target: Path | None) -> None:
    link = root / name
    if target is None:
        if link.is_symlink():
            link.unlink()
        return
    temporary = root / f".{name}.link"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(Path(_candidates_dir(root).name) / target.name)
    os.replace(temporary, link)


def _revision_of(directory: Path | None) -> str | None:
    return (_read_record(directory) or {}).get("revision") if directory else None


class _Operation:
    """The inspectable record of one deploy, recover or restart."""

    def __init__(self, root: Path, name: str, target: Path, replaced: Path | None):
        self.root = root
        self.record = {
            "operation": name,
            "state": "started",
            "pid": os.getpid(),
            "candidate": target.name,
            "revision": _revision_of(target),
            "replaced": replaced.name if replaced else None,
            "started_at": _now(),
            "steps": [],
        }
        self._save()

    def step(self, message: str) -> None:
        logger.info("%s", message)
        self.record["steps"].append(f"{_now()} {message}")
        self._save()

    def finish(self, state: str, problems=(), guidance=()) -> None:
        self.record.update(
            state=state, finished_at=_now(), problems=list(problems), guidance=list(guidance)
        )
        self.record.pop("pid", None)
        self._save()
        with (self.root / OPERATION_LOG).open("a") as log:
            log.write(json.dumps(self.record, sort_keys=True) + "\n")
        operation = self.record["operation"].capitalize()
        if state == "restored":
            print("Named database backup restored; services remain stopped.")
            return
        if state == "succeeded":
            print(f"{operation} of {self.record['candidate']} succeeded and is ready.")
            return
        print(
            f"{operation} of {self.record['candidate']} did not succeed ({state}).",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        for line in guidance:
            print(line, file=sys.stderr)

    def _save(self) -> None:
        temporary = self.root / f".{OPERATION_FILE}.tmp"
        temporary.write_text(json.dumps(self.record, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, self.root / OPERATION_FILE)


def _cutover(
    root: Path,
    ops: Operations,
    op: _Operation,
    target: Path,
    previous: Path | None,
    migration=None,
) -> list[str]:
    """Stop both services, select ``target``, start them and wait for readiness.

    The links change only after both services report inactive, so workers of two
    releases never run together. Returns the problems; empty means ready.
    """
    op.step(f"Maintenance window starts: stopping {' and '.join(SERVICES)}")
    stopped = _service(ops, "stop", *SERVICES) == 0
    still_active = [unit for unit in SERVICES if _service(ops, "is-active", unit) == 0]
    if not stopped or still_active:
        op.step("Services did not stop; starting them again without switching the release")
        _service(ops, "start", *reversed(SERVICES))
        return [
            f"services did not stop ({', '.join(still_active) or 'service manager failed'}); "
            f"{target.name} was not selected"
        ]
    if migration:
        import release_database as db

        op.step("Services stopped; applying rehearsed database migrations")
        try:
            migration()
        except db.DatabaseFailure as exc:
            op.record["migration_failed"] = True
            op.step("Migration failed or partially applied; services remain stopped")
            return [str(exc)]
    _point(root, "current", target)
    _point(root, "previous", previous)
    op.step(f"Selected {target.name} ({_revision_of(target)}); starting the services")
    _service(ops, "start", *reversed(SERVICES))
    result = wait_ready(ops, _revision_of(target))
    op.record["readiness"] = result
    if result["ready"]:
        op.step(f"Maintenance window ends: {target.name} is ready")
    else:
        op.step("Readiness failed: " + "; ".join(result["problems"]))
    return result["problems"]


def _inspect_guidance(root: Path) -> list[str]:
    return [
        "Inspect: release.py status --live, "
        f"{root / OPERATION_FILE}, and journalctl -u {APP_SERVICE} -u {GATEWAY_SERVICE} -n 200.",
        "Inspect the operation record for migration and backup state. Restoring a database backup is a "
        "separate owner decision that discards later writes; never use it to undo a code failure.",
    ]


def command_restore(args: argparse.Namespace) -> int:
    if not args.acknowledge_data_loss:
        raise Refused(
            "restoration discards later writes; pass --acknowledge-data-loss only after an explicit owner decision"
        )
    import release_database as db

    root = _release_root(args)
    ops = _operations(args)
    with _locked(root):
        if Path(args.backup).name != args.backup:
            raise Refused("name one retained backup without a path")
        matches = []
        for directory in _candidates_dir(root).glob("*"):
            for entry in (_read_record(directory) or {}).get("backups", []):
                if entry["name"] == args.backup:
                    matches.append((directory, entry))
        if len(matches) != 1:
            raise Refused("backup is missing or ambiguous in retained release records")
        directory, entry = matches[0]
        backup = root / "backups" / args.backup
        if (
            not backup.is_file()
            or backup.is_symlink()
            or hashlib.sha256(backup.read_bytes()).hexdigest() != entry["sha256"]
        ):
            raise Refused("backup identity cannot be established")
        url = db.database_url(args.production_env)
        if entry.get("database_identity") != db.identity(url):
            raise Refused("backup belongs to a different database target")
        op = _Operation(root, "restore", directory, _link_target(root, "current"))
        op.record["backup"] = args.backup
        op.step(
            "Owner acknowledged loss of later writes; stopping services for backup " + args.backup
        )
        if _service(ops, "stop", *SERVICES) or any(
            _service(ops, "is-active", unit) == 0 for unit in SERVICES
        ):
            op.finish("failed", ["services did not stop; no data restored"])
            return ATTENTION
        try:
            db.replace(url, backup)
            if db.revisions(url) != entry["baseline"]:
                raise db.DatabaseFailure("restored baseline differs from recorded backup")
        except db.DatabaseFailure as exc:
            op.finish(
                "restore-failed",
                [str(exc)],
                ["Services remain stopped; establish database state before restarting."],
            )
            return ATTENTION
        op.finish(
            "restored",
            guidance=[
                "Database restored; services remain stopped. Select compatible code with recover, or fix forward before restarting."
            ],
        )
        print("Restored named backup; later writes were discarded. Services remain stopped.")
        return OK


def command_rehearse(args: argparse.Namespace) -> int:
    if not args.disposable_env or not args.restore_env:
        raise Refused("pass disposable --disposable-env and --restore-env targets")
    import release_database as db

    root = _release_root(args)
    with _locked(root):
        directory = _candidates_dir(root) / args.candidate
        record = _read_record(directory)
        if not record or record.get("state") != "prepared":
            raise Refused("prepare the exact candidate before rehearsal")
        report = evaluate(directory, args.production_env, _production_code(args, root))
        if not report["code"]["matches"] or not report["dependencies"]["matches"]:
            raise Refused("candidate changed since preparation")
        historical = historical_migration_problems(directory / "code", _production_code(args, root))
        if historical:
            raise Refused("; ".join(historical))
        production = db.database_url(args.production_env)
        first = db.disposable_url(args.disposable_env, production)
        second = db.disposable_url(args.restore_env, production)
        if first == second:
            raise Refused("use two distinct disposable PostgreSQL databases")
        # Check both targets and the baseline before the first database mutation.
        db.require_empty(first)
        db.require_empty(second)
        baseline = db.revisions(production)
        expected = migration_heads(_production_code(args, root))
        if set(baseline) != expected or len(baseline) != 1:
            raise Refused("production baseline does not match the reference release")
        code = directory / "code"
        seed, verify = code / "migrations/rehearsal.sql", code / "migrations/rehearsal-verify.sql"
        if not seed.is_file() or not verify.is_file():
            raise Refused(
                "candidate needs committed synthetic rehearsal.sql and rehearsal-verify.sql"
            )
        evidence = {
            "code": report["code"]["identity"],
            "dependencies": report["dependencies"]["identity"],
            "baseline": baseline,
            "reference_code": code_identity(_production_code(args, root) / "migrations"),
            "at": _now(),
            "status": "failed",
        }
        record.setdefault("evidence", {})["migration-rehearsal"] = evidence
        _write_record(directory, record)
        try:
            db.migrate(directory, first, baseline[0])
            db.run(["psql", "-X", "--set", "ON_ERROR_STOP=1", "--file", str(seed)], url=first)
            with tempfile.TemporaryDirectory() as temporary:
                backup = Path(temporary) / "rehearsal.dump"
                db.dump(first, backup)
                db.restore(second, backup)
                if db.signature(first) != db.signature(second):
                    raise db.DatabaseFailure("restored synthetic state differs")
            for url in (first, second):
                db.migrate(directory, url)
                db.run(["psql", "-X", "--set", "ON_ERROR_STOP=1", "--file", str(verify)], url=url)
                if set(db.revisions(url)) != migration_heads(code):
                    raise db.DatabaseFailure("migration did not establish candidate schema")
            if db.signature(first) != db.signature(second):
                raise db.DatabaseFailure("restored migration outcome differs")
            evidence["status"] = "passed"
        finally:
            _write_record(directory, record)
    print("Migration rehearsal and disposable restore passed for " + args.candidate)
    return OK


def command_deploy(args: argparse.Namespace) -> int:
    root = _release_root(args)
    ops = _operations(args)
    with _locked(root):
        directory = _candidates_dir(root) / args.candidate
        if _read_record(directory) is None:
            raise Refused(f"no candidate {args.candidate} under {root}")
        current, previous = _link_target(root, "current"), _link_target(root, "previous")
        if current == directory.resolve():
            raise Refused(f"candidate {args.candidate} is already the running release")
        # Every check before the maintenance window; production is untouched on refusal.
        report = evaluate(directory, args.production_env, _production_code(args, root))
        migrations = report.get("migrations") or {}
        if migrations.get("required") and migrations.get("rehearsal") != "passed":
            raise Refused(
                f"candidate {args.candidate} needs database migrations "
                f"({', '.join(migrations['new'])}); valid migration rehearsal is required"
            )
        if not report["deployable"]:
            raise Refused(
                f"candidate {args.candidate} is not deployable:\n  - "
                + "\n  - ".join(report["blockers"])
            )
        migrating = bool(migrations.get("required"))
        if migrating:
            import release_database as db

            detail = "rehearsal matches the current database baseline"
            compatible = True
        else:
            compatible, detail = database_compatibility(directory, args.production_env)
        if not compatible:
            state = "unknown" if compatible is None else "not established"
            raise Refused(f"database compatibility {state}: {detail}")
        logger.info("Pre-deployment checks passed: %s", detail)

        migration = None
        if migrating:
            url = db.database_url(args.production_env)
            baseline = db.revisions(url)
            record = _read_record(directory)
            if baseline != record.get("evidence", {}).get("migration-rehearsal", {}).get(
                "baseline"
            ):
                raise Refused("database baseline changed after rehearsal validation")
            backup_dir = root / "backups"
            backup_dir.mkdir(mode=0o700, exist_ok=True)
            backup_dir.chmod(0o700)
            name = args.candidate + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".dump"
            backup = backup_dir / name
            # A backup failure is before the maintenance window and before any migration.
            db.dump(url, backup)
            if db.revisions(url) != baseline:
                raise Refused(
                    "database baseline changed during backup; inspect retained dump before proceeding"
                )
            record = _read_record(directory)
            record.setdefault("backups", []).append(
                {
                    "name": name,
                    "baseline": baseline,
                    "created_at": _now(),
                    "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                    "database_identity": db.identity(url),
                }
            )
            _write_record(directory, record)

            def migration():
                if db.revisions(url) != baseline:
                    raise db.DatabaseFailure(
                        "database baseline changed after backup; refusing migration"
                    )
                db.migrate(directory, url)
                compatible, reason = database_compatibility(directory, args.production_env)
                if not compatible:
                    raise db.DatabaseFailure(reason)

        op = _Operation(root, "deploy", directory, current)
        if migrating:
            op.record["backup"] = name
            op.step("Restricted pre-migration backup recorded: " + name)
        problems = _cutover(root, ops, op, directory, current or previous, migration)
        if op.record.get("migration_failed"):
            op.finish(
                "migration-failed",
                problems,
                [
                    "No automatic code recovery, downgrade or data restore was attempted.",
                    "Establish database state and fix forward, or separately authorize restore "
                    "of the named backup; restoration discards later writes.",
                    "Services remain stopped. Inspect operation.json and retained backups.",
                ],
            )
            return ATTENTION
        if not problems:
            op.finish("succeeded")
            return OK
        return _recover_after_failure(root, ops, op, args, problems, current, previous)


def _recover_after_failure(
    root: Path,
    ops: Operations,
    op: _Operation,
    args: argparse.Namespace,
    problems: list[str],
    restore: Path | None,
    restore_previous: Path | None,
) -> int:
    """Return to the release that ran before, only when the schema still fits it."""
    if restore is None:
        op.finish(
            "failed",
            problems,
            [
                "No earlier release exists to recover to: production ran from the legacy "
                "checkout. Reinstall the saved legacy unit files and restart them (see "
                "docs/operations/releases.md), or fix the candidate and deploy again.",
                *_inspect_guidance(root),
            ],
        )
        return ATTENTION
    compatible, detail = database_compatibility(restore, args.production_env)
    op.step(f"Automatic recovery check: {detail}")
    if not compatible:
        state = "unknown" if compatible is None else "not established"
        op.finish(
            "failed",
            problems,
            [
                f"Automatic recovery to {restore.name} was not attempted: database "
                f"compatibility {state}. Production still selects {op.record['candidate']}.",
                "Establish the database state before choosing between fixing forward and "
                "release.py recover.",
                *_inspect_guidance(root),
            ],
        )
        return ATTENTION
    recovery = _cutover(root, ops, op, restore, restore_previous)
    if not recovery:
        op.finish(
            "recovered",
            problems,
            [
                f"Production runs the previous release {restore.name} again and is ready. "
                f"{op.record['candidate']} is kept for inspection.",
                *_inspect_guidance(root),
            ],
        )
        return FAILED
    op.finish(
        "recovery-failed",
        problems + [f"recovery to {restore.name}: {problem}" for problem in recovery],
        ["Production is not verified ready.", *_inspect_guidance(root)],
    )
    return ATTENTION


def command_recover(args: argparse.Namespace) -> int:
    root = _release_root(args)
    ops = _operations(args)
    with _locked(root):
        current, previous = _link_target(root, "current"), _link_target(root, "previous")
        if previous is None:
            raise Refused(f"no previous release is retained under {root}")
        compatible, detail = database_compatibility(previous, args.production_env)
        if not compatible:
            state = "unknown" if compatible is None else "not established"
            raise Refused(
                f"recovery to {previous.name} refused: database compatibility {state}: "
                f"{detail}. Running code against a schema it does not expect could make "
                "things worse; restoring a database backup is a separate owner decision "
                "(see docs/operations/releases.md)"
            )
        op = _Operation(root, "recover", previous, current)
        problems = _cutover(root, ops, op, previous, current)
        if not problems:
            op.finish("succeeded")
            return OK
        op.finish(
            "failed", problems, ["Production is not verified ready.", *_inspect_guidance(root)]
        )
        return ATTENTION


def command_restart(args: argparse.Namespace) -> int:
    root = _release_root(args)
    ops = _operations(args)
    with _locked(root):
        current = _link_target(root, "current")
        if current is None:
            raise Refused(f"no release is deployed under {root}")
        compatible, detail = database_compatibility(current, args.production_env)
        if not compatible:
            raise Refused("restart refused: database compatibility is not established: " + detail)
        op = _Operation(root, "restart", current, current)
        problems = _cutover(root, ops, op, current, _link_target(root, "previous"))
        if not problems:
            op.finish("succeeded")
            return OK
        op.finish(
            "failed", problems, ["Production is not verified ready.", *_inspect_guidance(root)]
        )
        return ATTENTION


def production_report(root: Path) -> dict:
    report: dict = {"root": str(root)}
    for name in ("current", "previous"):
        target = _link_target(root, name)
        report[name] = (
            {"candidate": target.name, "revision": _revision_of(target)} if target else None
        )
    try:
        last = json.loads((root / OPERATION_FILE).read_text())
    except (OSError, ValueError):
        last = None
    if last and last.get("state") == "started" and not _process_alive(last.get("pid")):
        last["state"] = "interrupted"
    report["last_operation"] = last
    return report


def print_production(report: dict, live: dict | None) -> None:
    print(f"Production ({report['root']})")
    for name in ("current", "previous"):
        entry = report[name]
        detail = f"{entry['candidate']} revision {entry['revision']}" if entry else "none"
        print(f"  {name:<14}{detail}")
    last = report["last_operation"]
    if last:
        print(
            f"  {'last':<14}{last['operation']} {last['candidate']} {last['state']} "
            f"{last.get('finished_at') or last.get('started_at')}"
        )
        for problem in last.get("problems") or []:
            print(f"    - {problem}")
    if live is None:
        return
    services = ", ".join(f"{unit} {state}" for unit, state in live["services"].items())
    print(f"  {'services':<14}{services}")
    for name, field in (("app", "database"), ("gateway", "status")):
        probe = live.get(name)
        detail = f"revision {probe['revision']}, {field} {probe[field]}" if probe else "unreachable"
        print(f"  {name:<14}{detail}")
    print(f"  {'ready':<14}{'yes' if live['ready'] else 'no'}")
    for problem in live["problems"]:
        print(f"    - {problem}")


def command_prune(args: argparse.Namespace) -> int:
    root = _release_root(args)
    with _locked(root):
        removed = prune(root, args.keep)
    print(f"Removed {len(removed)} candidate(s): {', '.join(removed) or 'none'}")
    return OK


def print_report(report: dict) -> None:
    print(f"Candidate {report['candidate']} ({report['state']})")
    print(f"  revision      {report.get('revision')} (requested {report.get('requested')})")
    print(f"  code          {report['code_dir']}")
    if "code" in report:
        dependencies = report["dependencies"]
        print(f"  dependencies  {dependencies['packages']} packages {dependencies['identity']}")
        for name, check in report["checks"].items():
            print(f"  check {name:<8}{check['status']}  {check.get('summary') or ''}".rstrip())
        for scope, entry in report["acceptance"].items():
            if entry["required"] or entry["status"] != "missing":
                print(f"  accept {scope:<7}{entry['status']}  {entry.get('note') or ''}".rstrip())
        migrations = report["migrations"]
        if migrations["required"] is None:
            needs = "unknown"
        elif migrations["required"]:
            needs = "required: " + ", ".join(migrations["new"])
        else:
            needs = "none required"
        print(f"  migrations    {needs} (rehearsal {migrations['rehearsal']})")
        print(f"  configuration {'ready' if report['configuration']['ready'] else 'not ready'}")
    print(f"  deployable    {'yes' if report['deployable'] else 'no'}")
    for blocker in report["blockers"]:
        print(f"    - {blocker}")


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--release-root", help=f"release directory (or {RELEASE_ROOT_VAR})")
    common.add_argument("--production-env", type=Path, default=DEFAULT_PRODUCTION_ENV)
    common.add_argument(
        "--production-code",
        type=Path,
        help="running code to compare migrations with (default: the current release)",
    )
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    prepare_parser = commands.add_parser("prepare", parents=[common], help="prepare a candidate")
    prepare_parser.add_argument("revision", help="branch, tag or commit to release")
    prepare_parser.add_argument("--source", type=Path, default=Path.cwd())
    prepare_parser.add_argument("--installer", type=Path, help=argparse.SUPPRESS)
    prepare_parser.add_argument("--device-acceptance", action="store_true")
    prepare_parser.add_argument("--replace", action="store_true")
    prepare_parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    prepare_parser.set_defaults(handler=command_prepare)

    operations = argparse.ArgumentParser(add_help=False)
    operations.add_argument(
        "--service-manager", help=f"service manager command (default: {DEFAULT_SERVICE_MANAGER})"
    )
    operations.add_argument("--app-url", help=f"app base URL (default: {DEFAULT_APP_URL})")
    operations.add_argument(
        "--gateway-url", help=f"gateway base URL (default: {DEFAULT_GATEWAY_URL})"
    )
    operations.add_argument(
        "--readiness-timeout", type=float, default=DEFAULT_READINESS_TIMEOUT, help="seconds"
    )

    status_parser = commands.add_parser(
        "status", parents=[common, operations], help="inspect candidates and production"
    )
    status_parser.add_argument("candidate", nargs="?")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument(
        "--live", action="store_true", help="probe the running services' revision and readiness"
    )
    status_parser.set_defaults(handler=command_status)

    deploy_parser = commands.add_parser(
        "deploy", parents=[common, operations], help="switch production to a prepared candidate"
    )
    deploy_parser.add_argument("candidate")
    deploy_parser.set_defaults(handler=command_deploy)

    rehearse_parser = commands.add_parser("rehearse", parents=[common])
    rehearse_parser.add_argument("candidate")
    rehearse_parser.add_argument("--disposable-env", type=Path)
    rehearse_parser.add_argument("--restore-env", type=Path)
    rehearse_parser.set_defaults(handler=command_rehearse)

    restore_parser = commands.add_parser("restore", parents=[common, operations])
    restore_parser.add_argument("--backup", required=True)
    restore_parser.add_argument("--acknowledge-data-loss", action="store_true")
    restore_parser.set_defaults(handler=command_restore)

    recover_parser = commands.add_parser(
        "recover", parents=[common, operations], help="return production to the previous release"
    )
    recover_parser.set_defaults(handler=command_recover)

    restart_parser = commands.add_parser(
        "restart", parents=[common, operations], help="restart the running release and verify it"
    )
    restart_parser.set_defaults(handler=command_restart)

    accept_parser = commands.add_parser("accept", parents=[common], help="record owner acceptance")
    accept_parser.add_argument("candidate")
    accept_parser.add_argument("--scope", choices=ACCEPTANCE_SCOPES, required=True)
    accept_parser.add_argument("--note", required=True, help="what was observed")
    accept_parser.set_defaults(handler=command_accept)

    prune_parser = commands.add_parser("prune", parents=[common], help="remove old candidates")
    prune_parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    prune_parser.set_defaults(handler=command_prune)

    import release_database

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[release] %(message)s")
    try:
        return args.handler(args)
    except Refused as exc:
        print(f"Release {args.command} refused: {exc}", file=sys.stderr)
        return REFUSED
    except release_database.DatabaseFailure as exc:
        print(f"Release {args.command} failed: {exc}", file=sys.stderr)
        return FAILED
    except Busy as exc:
        print(f"Release {args.command} not started: {exc}", file=sys.stderr)
        return BUSY


if __name__ == "__main__":
    sys.exit(main())
