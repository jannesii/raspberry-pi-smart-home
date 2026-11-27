#!/usr/bin/env python3
"""Encrypted recovery backups of Pi production, and their isolated restoration.

    recovery_backup.py create [--backup-root DIR] [--production-env FILE]
                              [--manifest FILE] [--recipient PUBLIC_KEY]
    recovery_backup.py status [--backup-root DIR] [--json]
    recovery_backup.py restore BACKUP_DIR --target-env FILE --restore-dir DIR
                               [--gnupg-home DIR]

``create`` (daily on the Pi from ``jannenkoti-backup.timer``, or on demand)
takes one consistent PostgreSQL dump with exact row counts from the same
snapshot, online copies of the SQLite files named by ``DB_PATH`` and
``VILLENKOTI_DB_PATH``, and the configuration, secrets and persistent files the
manifest lists. It streams them as one tar archive into ``gpg``, encrypted to
the recovery public key: the Pi never holds the decryption key. Plaintext
staging is private to the attempt and removed afterwards, including leftovers
of a killed attempt at the next start. A backup becomes visible in ``daily/``
only once complete, by one directory rename; failed attempts never replace
the last success in ``status.json``. The newest backup of each of the 14 most
recent days is kept. Nothing outside ``daily/`` is pruned, so the release
pre-migration backups under the release root are never touched.

``restore`` runs on the desktop, which holds the private key. It restores into
an explicitly isolated, empty ``recovery_test_`` database and a new directory,
never over production paths, verifies the row counts, Alembic revision and
file checksums recorded at backup time, and writes development-mode overrides
so a restored app runs with household effects disabled.

See docs/operations/recovery-backups.md. Exit status: 0 done (status: a fresh
success), 1 failed (status: failed, stale or none), 2 refused before any change,
3 another backup operation holds the backup root.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

DEFAULT_BACKUP_ROOT = "/home/jannesi/jannenkoti-backups"
DEFAULT_PRODUCTION_ENV = "/home/jannesi/Code/server/jannenkoti.env"
DEFAULT_RECIPIENT = "/home/jannesi/.config/jannenkoti-backup/recovery-public.asc"
DEFAULT_MANIFEST = str(
    Path(__file__).resolve().parents[1] / "docs/operations/production-host/recovery-files.txt"
)
FORMAT = 1
RETAIN_DAYS = 14
STALE_AFTER = timedelta(hours=36)
DUMP_TIMEOUT = 1800
SQLITE_SETTINGS = ("DB_PATH", "VILLENKOTI_DB_PATH")
RESTORE_PREFIX = "recovery_test_"
BACKUP_ID = re.compile(r"^\d{8}T\d{6}Z$")
ID_FORMAT = "%Y%m%dT%H%M%SZ"  # UTC start time, matching BACKUP_ID
# Retention days follow the schedule's zone, not the process's TZ.
RETENTION_ZONE = ZoneInfo("Europe/Helsinki")
ARTIFACT, RECORD = "backup.tar.gpg", "backup.json"

OK, FAILED, REFUSED, BUSY = 0, 1, 2, 3


class Failure(Exception):
    """The operation failed; the message is safe to show and record."""


class Refused(Exception):
    """The request is unsafe or unusable; nothing was changed."""


class Busy(Exception):
    """Another backup operation holds the backup root."""


def now() -> datetime:
    return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    """Replace ``path`` atomically, so readers never see a partial record."""
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def lock_held(root: Path) -> bool:
    """Whether a running ``create`` holds the backup root (status never waits)."""
    try:
        with (root / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    return False


@contextlib.contextmanager
def locked(root: Path):
    handle = (root / ".lock").open("a")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy("another backup operation holds the backup root") from None
        yield
    finally:
        handle.close()


def terminate_cleanly(signum, frame):
    # systemd stops the unit with SIGTERM; unwinding runs the staging cleanup.
    raise SystemExit(FAILED)


# --- Inputs -------------------------------------------------------------------


def read_manifest(path: Path) -> list[tuple[bool, Path]]:
    """``required PATH`` / ``optional PATH`` lines; a directory includes its files."""
    entries = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        raise Failure(f"recovery manifest {path} is unreadable") from None
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, value = line.partition(" ")
        value = value.strip()
        if kind not in ("required", "optional") or not value.startswith("/"):
            raise Failure(f"recovery manifest line {number} is not 'required|optional /path'")
        entries.append((kind == "required", Path(value)))
    return entries


def collect_files(entries: list[tuple[bool, Path]]) -> tuple[list[Path], list[str], list[str]]:
    """Regular files to archive, absent optional entries and skipped special files."""
    files: list[Path] = []
    absent: list[str] = []
    skipped: list[str] = []
    for required, entry in entries:
        if entry.is_symlink() or not entry.exists():
            if required:
                raise Failure(f"required recovery file {entry} is missing")
            absent.append(str(entry))
            continue
        candidates = [entry] if entry.is_file() else sorted(entry.rglob("*"))
        for path in candidates:
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                skipped.append(str(path))
            elif path.is_file():
                files.append(path)
    return files, absent, skipped


def environment_values(path: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    if not path.is_file():
        raise Failure(f"production environment {path} is missing")
    return {key: value or "" for key, value in dotenv_values(path).items()}


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_count(conn, name: str) -> int:
    from sqlalchemy import text

    schema, _, table = name.partition(".")
    return conn.execute(
        text(f"SELECT count(*) FROM {quote_identifier(schema)}.{quote_identifier(table)}")
    ).scalar_one()


def alembic_revision(conn) -> list[str]:
    from sqlalchemy import text

    return sorted(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())


def dump_database(url: str, target: Path) -> dict:
    """Dump with ``pg_dump`` inside one exported snapshot, counting rows in it.

    The counts and the Alembic revision therefore describe exactly the dumped
    state, which lets a restore prove it recovered every row.
    """
    import release_database as db
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    try:
        engine = db.connection(url)
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
            conn.begin(),
        ):
            conn.execute(text("SET TRANSACTION READ ONLY"))
            snapshot = conn.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            tables = conn.execute(
                text(
                    "SELECT n.nspname, c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relkind IN ('r', 'p') "
                    "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
                    "AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1, 2"
                )
            ).all()
            counts = {
                f"{schema}.{table}": table_count(conn, f"{schema}.{table}")
                for schema, table in tables
            }
            alembic = alembic_revision(conn) if "public.alembic_version" in counts else None
            db.run(
                ["pg_dump", "--format=custom", f"--snapshot={snapshot}", "--file", str(target)],
                url=url,
                timeout=DUMP_TIMEOUT,
            )
    except db.DatabaseFailure as exc:
        raise Failure(f"database dump failed: {exc}") from None
    except Exception as exc:
        raise Failure(f"database dump failed: {type(exc).__name__}") from None
    return {
        "database": make_url(url).database,
        "identity": db.identity(url),
        "alembic": alembic,
        "tables": counts,
    }


def copy_sqlite(source: Path, target: Path) -> None:
    """Online copy with SQLite's backup API: consistent even while the app writes."""
    if not source.is_file():
        raise Failure(f"SQLite database {source} is missing")
    try:
        with (
            contextlib.closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src,
            contextlib.closing(sqlite3.connect(target)) as dst,
        ):
            src.backup(dst)
            check = dst.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        raise Failure(f"SQLite copy of {source} failed: {exc}") from None
    if check != "ok":
        raise Failure(f"SQLite copy of {source} failed its integrity check")


# --- Encryption ---------------------------------------------------------------


@contextlib.contextmanager
def gnupg_home():
    """An empty, private GnuPG home: the Pi encrypts without any keyring."""
    home = Path(tempfile.mkdtemp(prefix="jk-backup-gpg-"))
    try:
        yield home
    finally:
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "all"], capture_output=True)
        shutil.rmtree(home, ignore_errors=True)


def recipient_fingerprint(recipient: Path, home: Path) -> str:
    if not recipient.is_file():
        raise Failure(f"recovery public key {recipient} is missing")
    result = subprocess.run(
        ["gpg", "--batch", "--homedir", str(home), "--with-colons", "--show-keys", str(recipient)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    fingerprints = [
        line.split(":")[9] for line in result.stdout.splitlines() if line.startswith("fpr:")
    ]
    if result.returncode or not fingerprints:
        raise Failure(f"recovery public key {recipient} is unreadable")
    return fingerprints[0]


def add_bytes(archive: tarfile.TarFile, name: str, content: bytes, mode: int = 0o600) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    info.mtime = int(now().timestamp())
    archive.addfile(info, io.BytesIO(content))


def encrypt_archive(output: Path, recipient: Path, home: Path, write) -> None:
    """Stream a tar archive built by ``write(archive)`` through ``gpg --encrypt``."""
    command = [
        "gpg",
        "--batch",
        "--no-tty",
        "--quiet",
        "--homedir",
        str(home),
        "--trust-model",
        "always",
        "--recipient-file",
        str(recipient),
        "--output",
        str(output),
        "--encrypt",
    ]
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        with tarfile.open(fileobj=process.stdin, mode="w|") as archive:
            write(archive)
        process.stdin.close()
    except BrokenPipeError:
        pass
    finally:
        try:
            process.communicate(timeout=DUMP_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise Failure("encryption timed out") from None
    if process.returncode or not output.is_file() or not output.stat().st_size:
        raise Failure("encryption failed")


# --- create -------------------------------------------------------------------


def read_status(root: Path) -> dict:
    try:
        return json.loads((root / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def record_attempt(root: Path, attempt: dict, success: dict | None) -> None:
    status = read_status(root)
    status["last_attempt"] = attempt
    if success is not None:
        status["last_success"] = success
    write_json(root / "status.json", status)
    with (root / "runs.log").open("a") as log:
        log.write(json.dumps(attempt, sort_keys=True) + "\n")


def completed_backups(root: Path) -> list[Path]:
    daily = root / "daily"
    if not daily.is_dir():
        return []
    return sorted(
        path
        for path in daily.iterdir()
        if path.is_dir() and BACKUP_ID.match(path.name) and (path / RECORD).is_file()
    )


def backup_time(path: Path) -> datetime:
    return datetime.strptime(path.name, ID_FORMAT).replace(tzinfo=UTC)


def prune(root: Path) -> list[str]:
    """Keep the newest backup of each of the most recent ``RETAIN_DAYS`` Helsinki days."""
    kept_days: dict[object, Path] = {}
    for backup in sorted(completed_backups(root), reverse=True):
        day = backup_time(backup).astimezone(RETENTION_ZONE).date()
        if day not in kept_days and len(kept_days) < RETAIN_DAYS:
            kept_days[day] = backup
    keep = set(kept_days.values())
    removed = []
    for backup in completed_backups(root):
        if backup not in keep:
            shutil.rmtree(backup)
            removed.append(backup.name)
    return removed


def build_backup(args, work: Path, identifier: str, started: datetime) -> dict:
    staging = private_dir(work / "staging")
    values = environment_values(Path(args.production_env))
    import release_database as db

    try:
        url = db.database_url(Path(args.production_env))
    except db.DatabaseFailure as exc:
        raise Failure(str(exc)) from None
    files, absent, skipped = collect_files(read_manifest(Path(args.manifest)))

    with gnupg_home() as home:
        fingerprint = recipient_fingerprint(Path(args.recipient), home)
        dump = staging / "postgresql.dump"
        database = dump_database(url, dump)

        sqlite_parts = []
        for setting in SQLITE_SETTINGS:
            source = values.get(setting)
            if source:
                copy = staging / f"{setting}.sqlite3"
                copy_sqlite(Path(source), copy)
                sqlite_parts.append({"setting": setting, "source": source, "copy": copy})

        manifest = {
            "format": FORMAT,
            "id": identifier,
            "created_at": stamp(started),
            "host": socket.gethostname(),
            "database": database | {"part": "database/postgresql.dump"},
            "sqlite": [],
            "files": [],
            "absent": absent,
            "skipped": skipped,
        }
        manifest["database"]["sha256"] = sha256_file(dump)

        def write(archive: tarfile.TarFile) -> None:
            archive.add(dump, "database/postgresql.dump", recursive=False)
            for part in sqlite_parts:
                name = f"sqlite/{part['setting']}.sqlite3"
                archive.add(part["copy"], name, recursive=False)
                manifest["sqlite"].append(
                    {
                        "setting": part["setting"],
                        "source": part["source"],
                        "part": name,
                        "sha256": sha256_file(part["copy"]),
                    }
                )
            for path in files:
                try:
                    content = path.read_bytes()
                    mode = path.stat().st_mode & 0o777
                except OSError:
                    raise Failure(f"recovery file {path} is unreadable") from None
                name = "files" + str(PurePosixPath(path))
                add_bytes(archive, name, content, mode)
                manifest["files"].append(
                    {
                        "path": str(path),
                        "part": name,
                        "mode": oct(mode),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            # Last, so it lists every part with the checksum actually archived.
            add_bytes(archive, "manifest.json", json.dumps(manifest, indent=2).encode())

        artifact = work / ARTIFACT
        encrypt_archive(artifact, Path(args.recipient), home, write)
    shutil.rmtree(staging)
    artifact.chmod(0o600)
    with artifact.open("rb") as handle:
        os.fsync(handle.fileno())
    return {
        "format": FORMAT,
        "id": identifier,
        "status": "complete",
        "created_at": stamp(started),
        "finished_at": stamp(now()),
        "host": manifest["host"],
        "encryption": {"tool": "gpg", "recipient_fingerprint": fingerprint},
        "artifact": ARTIFACT,
        "size": artifact.stat().st_size,
        "sha256": sha256_file(artifact),
        "database": {
            "database": database["database"],
            "identity": database["identity"],
            "alembic": database["alembic"],
            "tables": len(database["tables"]),
        },
        "sqlite": [part["setting"] for part in sqlite_parts],
        "files": [entry["path"] for entry in manifest["files"]],
        "absent": absent,
        "skipped": skipped,
    }


def command_create(args) -> int:
    root = private_dir(Path(args.backup_root))
    with locked(root):
        incomplete = private_dir(root / "incomplete")
        daily = private_dir(root / "daily")
        # A killed attempt leaves plaintext staging behind; nothing there is ever
        # a usable backup, so remove it before starting.
        for leftover in incomplete.iterdir():
            shutil.rmtree(leftover, ignore_errors=True)
            print(f"Removed incomplete attempt {leftover.name}", file=sys.stderr)

        started = now()
        # Names have one-second resolution; never reuse a completed backup's name.
        while (daily / started.strftime(ID_FORMAT)).exists():
            time.sleep(0.2)
            started = now()
        identifier = started.strftime(ID_FORMAT)
        work = private_dir(incomplete / identifier)
        attempt = {"id": identifier, "started_at": stamp(started)}
        # Visible while running, and afterwards if the process is killed outright.
        record_attempt(root, attempt | {"result": "running"}, None)
        try:
            record = build_backup(args, work, identifier, started)
            write_json(work / RECORD, record)
            os.rename(work, daily / identifier)
            descriptor = os.open(daily, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException as exc:
            shutil.rmtree(work, ignore_errors=True)
            if isinstance(exc, Failure):
                reason = str(exc)
            elif isinstance(exc, SystemExit | KeyboardInterrupt):
                reason = "interrupted"
            else:
                reason = f"unexpected {type(exc).__name__}"
            try:
                record_attempt(
                    root,
                    attempt | {"finished_at": stamp(now()), "result": "failed", "error": reason},
                    None,
                )
            except OSError as record_error:
                print(f"Recording the failed attempt also failed: {record_error}", file=sys.stderr)
            if isinstance(exc, Failure):
                print(f"Backup {identifier} failed: {reason}", file=sys.stderr)
                return FAILED
            raise
        prune_error = None
        try:
            removed = prune(root)
        except OSError as exc:
            # The backup itself is complete; report retention separately.
            removed, prune_error = [], f"pruning failed: {exc.strerror or type(exc).__name__}"
        success = {
            "id": identifier,
            "finished_at": record["finished_at"],
            "path": str(daily / identifier),
            "size": record["size"],
            "sha256": record["sha256"],
        }
        record_attempt(
            root,
            attempt
            | {"finished_at": record["finished_at"], "result": "success", "pruned": removed}
            | ({"prune_error": prune_error} if prune_error else {}),
            success,
        )
    print(f"Backup {identifier} complete: {daily / identifier} ({record['size']} bytes)")
    if removed:
        print(f"Pruned {len(removed)} older backup(s): {', '.join(removed)}")
    if prune_error:
        print(f"Backup retention needs attention: {prune_error}", file=sys.stderr)
        return FAILED
    return OK


# --- status -------------------------------------------------------------------


def command_status(args) -> int:
    root = Path(args.backup_root)
    status = read_status(root)
    attempt, success = status.get("last_attempt"), status.get("last_success")
    problems = []
    age = None
    if not success:
        problems.append("no successful backup recorded")
    else:
        try:
            age = now() - datetime.fromisoformat(success["finished_at"])
        except (KeyError, TypeError, ValueError):
            problems.append("latest success record is unreadable")
        else:
            if age > STALE_AFTER:
                hours = STALE_AFTER // timedelta(hours=1)
                problems.append(f"latest successful backup is older than {hours} hours")
    result = attempt.get("result") if isinstance(attempt, dict) else None
    if result == "running":
        if not lock_held(root):
            problems.append(f"latest attempt {attempt.get('id')} did not finish (killed?)")
    elif attempt and result != "success":
        problems.append(f"latest attempt {attempt.get('id')} failed: {attempt.get('error')}")
    elif attempt and attempt.get("prune_error"):
        problems.append(attempt["prune_error"])
    summary = {
        "healthy": not problems,
        "problems": problems,
        "last_attempt": attempt,
        "last_success": success,
        "age_hours": round(age.total_seconds() / 3600, 1) if age is not None else None,
        "completed": [path.name for path in completed_backups(root)],
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Backups: {'healthy' if not problems else 'attention'}")
        if success and age is not None:
            print(f"Latest success: {success.get('id')} ({summary['age_hours']} h ago)")
        if attempt:
            print(f"Latest attempt: {attempt.get('id')} {result}")
        print(f"Completed backups retained: {len(summary['completed'])}")
        for problem in problems:
            print(f"Problem: {problem}")
    return OK if not problems else FAILED


# --- restore ------------------------------------------------------------------


def restore_url(target_env: Path) -> str:
    """The isolated target's URL, refusing anything production-like or ambiguous."""
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url

    if not target_env.is_file():
        raise Refused(f"target environment {target_env} is missing")
    values = dotenv_values(target_env)
    if values.get("RECOVERY_RESTORE_TARGET") != "isolated":
        raise Refused("target environment must set RECOVERY_RESTORE_TARGET=isolated")
    url = values.get("DATABASE_URL") or ""
    if not url.startswith("postgresql"):
        raise Refused("target environment needs a PostgreSQL DATABASE_URL")
    parsed = make_url(url)
    host = parsed.host or str(parsed.query.get("host") or "")
    if not (host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")):
        raise Refused("restore target must be a local socket or loopback database")
    if not (parsed.database or "").startswith(RESTORE_PREFIX):
        raise Refused(f"restore target database name must start with {RESTORE_PREFIX}")
    return url


def safe_extract(stream, destination: Path) -> None:
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                raise Failure(f"archive member {member.name!r} is not allowed")
            archive.extract(member, destination, filter="data")


def decrypt(artifact: Path, destination: Path, home: str | None) -> None:
    command = ["gpg", "--batch", "--quiet", "--decrypt", str(artifact)]
    if home:
        command[1:1] = ["--homedir", home]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        safe_extract(process.stdout, destination)
    except BaseException as exc:
        process.kill()
        if isinstance(exc, tarfile.TarError):
            raise Failure("decryption failed: is the recovery private key available?") from None
        raise
    finally:
        process.stdout.close()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if process.returncode:
        raise Failure("decryption failed: is the recovery private key available?")


def verify_restored_database(url: str, expected: dict) -> None:
    import release_database as db

    try:
        with db.connection(url).connect() as conn:
            for name, count in expected["tables"].items():
                actual = table_count(conn, name)
                if actual != count:
                    raise Failure(f"restored {name} has {actual} rows, backup recorded {count}")
            if expected["alembic"] is not None and alembic_revision(conn) != expected["alembic"]:
                raise Failure("restored Alembic revision differs from the backup")
    except Failure:
        raise
    except Exception as exc:
        raise Failure(f"restored database could not be verified: {type(exc).__name__}") from None


def command_restore(args) -> int:
    import release_database as db
    from sqlalchemy.engine import make_url

    backup = Path(args.backup)
    restore_dir = Path(args.restore_dir)
    try:
        record = json.loads((backup / RECORD).read_text())
    except (OSError, ValueError):
        raise Refused(f"{backup} is not a completed backup (no {RECORD})") from None
    artifact = backup / ARTIFACT
    if record.get("status") != "complete" or not artifact.is_file():
        raise Refused(f"{backup} is not a completed backup")
    if sha256_file(artifact) != record.get("sha256"):
        raise Refused(f"{artifact} does not match its record: incomplete or corrupted copy")
    url = restore_url(Path(args.target_env))
    source = record.get("database") or {}
    if db.identity(url) == source.get("identity") or make_url(url).database == source.get(
        "database"
    ):
        raise Refused("restore target is the backed-up production database")
    existed = restore_dir.exists()
    if existed and (not restore_dir.is_dir() or any(restore_dir.iterdir())):
        raise Refused(f"restore directory {restore_dir} must be new or empty")

    private_dir(restore_dir)
    staging = private_dir(restore_dir / ".staging")
    try:
        decrypt(artifact, staging, args.gnupg_home)
        manifest = json.loads((staging / "manifest.json").read_text())
        expected = manifest["database"]
        parts = [expected] + manifest["sqlite"] + manifest["files"]
        for part in parts:
            path = staging / part["part"]
            if not path.is_file() or sha256_file(path) != part["sha256"]:
                raise Failure(f"archive part {part['part']} is missing or corrupted")
        try:
            db.require_empty(url)
            db.run(
                [
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "--dbname",
                    make_url(url).database,
                    str(staging / expected["part"]),
                ],
                url=url,
                timeout=DUMP_TIMEOUT,
            )
        except db.DatabaseFailure as exc:
            raise Failure(f"database restore failed: {exc}") from None
        verify_restored_database(url, expected)

        sqlite_dir = private_dir(restore_dir / "sqlite")
        overrides = ["APP_ENV=development", f"DATABASE_URL={url}"]
        for part in manifest["sqlite"]:
            target = sqlite_dir / f"{part['setting']}.sqlite3"
            os.replace(staging / part["part"], target)
            overrides.append(f"{part['setting']}={target}")
        if (staging / "files").is_dir():
            os.replace(staging / "files", restore_dir / "files")
        os.replace(staging / "manifest.json", restore_dir / "manifest.json")
        overrides_file = restore_dir / "isolated-overrides.env"
        descriptor = os.open(overrides_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(
                "# Development mode keeps household effects disabled for the restored app.\n"
                + "\n".join(overrides)
                + "\n"
            )
    except BaseException:
        # Restored data is sensitive: remove it, but keep a directory the owner made.
        if existed:
            for child in restore_dir.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        else:
            shutil.rmtree(restore_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    rows = sum(expected["tables"].values())
    print(f"Restored backup {manifest['id']} into {make_url(url).database} and {restore_dir}")
    print(
        f"verified: {len(expected['tables'])} tables, {rows} rows, "
        f"Alembic {expected['alembic']}, {len(manifest['sqlite'])} SQLite file(s), "
        f"{len(manifest['files'])} file(s) with matching checksums"
    )
    print(f"Development-mode overrides: {overrides_file}")
    return OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="take an encrypted recovery backup")
    create.add_argument("--backup-root", default=DEFAULT_BACKUP_ROOT)
    create.add_argument("--production-env", default=DEFAULT_PRODUCTION_ENV)
    create.add_argument("--manifest", default=DEFAULT_MANIFEST)
    create.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    create.set_defaults(handler=command_create)

    status = commands.add_parser("status", help="show the latest backup results")
    status.add_argument("--backup-root", default=DEFAULT_BACKUP_ROOT)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=command_status)

    restore = commands.add_parser("restore", help="restore a backup into isolated targets")
    restore.add_argument("backup")
    restore.add_argument("--target-env", required=True)
    restore.add_argument("--restore-dir", required=True)
    restore.add_argument("--gnupg-home")
    restore.set_defaults(handler=command_restore)

    args = parser.parse_args(argv)
    os.umask(0o077)
    signal.signal(signal.SIGTERM, terminate_cleanly)
    try:
        return args.handler(args)
    except Refused as exc:
        print(f"Recovery {args.command} refused: {exc}", file=sys.stderr)
        return REFUSED
    except Failure as exc:
        print(f"Recovery {args.command} failed: {exc}", file=sys.stderr)
        return FAILED
    except Busy as exc:
        print(f"Recovery {args.command} not started: {exc}", file=sys.stderr)
        return BUSY


if __name__ == "__main__":
    sys.exit(main())
