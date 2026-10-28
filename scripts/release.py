#!/usr/bin/env python3
"""Prepare and inspect exact production release candidates.

    .venv/bin/python scripts/release.py prepare <revision> --release-root DIR
    .venv/bin/python scripts/release.py status [<candidate>] --release-root DIR
    .venv/bin/python scripts/release.py accept <candidate> --scope feature --note TEXT ...
    .venv/bin/python scripts/release.py prune [--keep N] --release-root DIR

Preparation exports one committed revision into its own release directory,
installs its dependencies into a separate virtualenv there, runs the required
checks with them and records the evidence. It never migrates the production
database, restarts services or switches the running release, and it never copies
production configuration or state into the candidate. The release root may also
be given as ``JANNENKOTI_RELEASE_ROOT``. See docs/operations/releases.md.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("release")

RELEASE_ROOT_VAR = "JANNENKOTI_RELEASE_ROOT"
DEFAULT_PRODUCTION_ENV = Path("/home/jannesi/Code/server/jannenkoti.env")
# Production still runs from this checkout until the first release is deployed.
LEGACY_PRODUCTION_CODE = Path("/home/jannesi/Code/server")
DEFAULT_KEEP = 3

OK, FAILED, REFUSED, BUSY = 0, 1, 2, 3

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
    rehearsal = _evidence_status(
        record.get("evidence", {}).get("migration-rehearsal"), current_code, current_dependencies
    )
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
        if record is None or directory.resolve() in protected:
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
    if args.json:
        print(json.dumps({"candidates": reports, "releases": protected}, indent=2, sort_keys=True))
        return OK
    if not reports:
        print(f"No candidates under {root}")
    for report in reports:
        label = protected.get(report["candidate"], "")
        print(
            f"{report['candidate']}  {report['state']:<10} "
            f"{'deployable' if report['deployable'] else 'blocked':<10} "
            f"{report.get('created_at') or ''} {label}".rstrip()
        )
    return OK


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

    status_parser = commands.add_parser("status", parents=[common], help="inspect candidates")
    status_parser.add_argument("candidate", nargs="?")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=command_status)

    accept_parser = commands.add_parser("accept", parents=[common], help="record owner acceptance")
    accept_parser.add_argument("candidate")
    accept_parser.add_argument("--scope", choices=ACCEPTANCE_SCOPES, required=True)
    accept_parser.add_argument("--note", required=True, help="what was observed")
    accept_parser.set_defaults(handler=command_accept)

    prune_parser = commands.add_parser("prune", parents=[common], help="remove old candidates")
    prune_parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    prune_parser.set_defaults(handler=command_prune)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[release] %(message)s")
    try:
        return args.handler(args)
    except Refused as exc:
        print(f"Release {args.command} refused: {exc}", file=sys.stderr)
        return REFUSED
    except Busy as exc:
        print(f"Release {args.command} not started: {exc}", file=sys.stderr)
        return BUSY


if __name__ == "__main__":
    sys.exit(main())
