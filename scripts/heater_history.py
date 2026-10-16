"""Read-only heater evidence discovery and non-overwriting SQLite recovery.

Observation photo content and original weather replies live in the database, so
these backups include them; discovery recomputes photo and reply checksums and
reports photos without a session and broken weather provenance links.

Does not import the application, bootstrap tables or load environment files.
PostgreSQL backup/restore uses pg_dump/pg_restore; see the operating guide.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url


def discover(database_url: str) -> dict:
    """Count surviving evidence and inspect triggers in a read-only snapshot."""
    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend == "sqlite":
        path = Path(url.database or "").resolve(strict=True)
        engine = create_engine(
            "sqlite://",
            creator=lambda: sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True),
        )
    elif backend == "postgresql":
        engine = create_engine(url)
    else:
        raise ValueError("Discovery supports SQLite and PostgreSQL only")
    report = {"backend": backend, "tables": {}, "triggers": []}
    try:
        with engine.connect() as conn, conn.begin():
            if backend == "postgresql":
                conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            else:
                conn.exec_driver_sql("PRAGMA query_only=ON")
            inspector = inspect(conn)
            for name in sorted(inspector.get_table_names()):
                if not name.startswith(("car_heater_", "weather_")):
                    continue
                table = Table(name, MetaData(), autoload_with=conn)
                time_col = next(
                    (
                        table.c[key]
                        for key in (
                            "timestamp",
                            "captured_at",
                            "received_at",
                            "started_at",
                            "created_at",
                            "observed_at",
                            "retrieved_at",
                            "first_retrieved_at",
                        )
                        if key in table.c
                    ),
                    None,
                )
                summary = {"count": conn.scalar(select(func.count()).select_from(table))}
                if time_col is not None:
                    summary.update(
                        oldest=conn.scalar(select(func.min(time_col))),
                        newest=conn.scalar(select(func.max(time_col))),
                    )
                report["tables"][name] = summary
            if "car_heater_photo" in report["tables"]:
                report["photo_integrity"] = _photo_integrity(conn)
            if "weather_reply" in report["tables"]:
                report["weather_integrity"] = _weather_integrity(conn)
            if backend == "sqlite":
                rows = conn.execute(
                    text(
                        "SELECT name, tbl_name AS table_name, sql AS definition "
                        "FROM sqlite_master WHERE type='trigger' ORDER BY name"
                    )
                )
            else:
                rows = conn.execute(
                    text("""
                    SELECT t.tgname AS name, n.nspname AS schema_name,
                           c.relname AS table_name, pg_get_triggerdef(t.oid) AS definition,
                           pg_get_functiondef(t.tgfoid) AS function_definition
                    FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE NOT t.tgisinternal AND n.nspname NOT IN ('pg_catalog','information_schema')
                    ORDER BY n.nspname,c.relname,t.tgname
                """)
                )
            report["triggers"] = [dict(row) for row in rows.mappings()]
            # This transaction performs only reads; commit cannot mutate data.
        return report
    finally:
        engine.dispose()


def _photo_integrity(conn) -> dict:
    """Recompute stored photo checksums and find photos without a session.

    Reads one image at a time so restored archives can be verified on a Pi.
    """
    photos = Table("car_heater_photo", MetaData(), autoload_with=conn)
    sessions = Table("car_heater_session", MetaData(), autoload_with=conn)
    result = {"checked": 0, "mismatched": [], "orphaned": []}
    ids = conn.execute(select(photos.c.id).order_by(photos.c.id)).scalars().all()
    for photo_id in ids:
        row = conn.execute(
            select(photos.c.sha256, photos.c.content, sessions.c.id.label("session"))
            .outerjoin(sessions, sessions.c.id == photos.c.session_id)
            .where(photos.c.id == photo_id)
        ).one()
        result["checked"] += 1
        if hashlib.sha256(bytes(row.content)).hexdigest() != row.sha256:
            result["mismatched"].append(photo_id)
        if row.session is None:
            result["orphaned"].append(photo_id)
    return result


def _weather_integrity(conn) -> dict:
    """Recompute original-reply checksums and find broken provenance links.

    Reads one compressed reply at a time to keep memory bounded on a Pi.
    """
    replies = Table("weather_reply", MetaData(), autoload_with=conn)
    retrievals = Table("weather_retrieval", MetaData(), autoload_with=conn)
    readings = Table("weather_observation", MetaData(), autoload_with=conn)
    checked = 0
    mismatched: list[int] = []
    for reply_id in conn.execute(select(replies.c.id).order_by(replies.c.id)).scalars().all():
        row = conn.execute(
            select(replies.c.sha256, replies.c.content).where(replies.c.id == reply_id)
        ).one()
        checked += 1
        try:
            content = gzip.decompress(bytes(row.content))
        except (OSError, EOFError):
            content = None
        if content is None or hashlib.sha256(content).hexdigest() != row.sha256:
            mismatched.append(reply_id)
    without_reply = (
        conn.execute(
            select(retrievals.c.id)
            .outerjoin(replies, replies.c.id == retrievals.c.reply_id)
            .where(retrievals.c.reply_id.is_not(None), replies.c.id.is_(None))
            .order_by(retrievals.c.id)
        )
        .scalars()
        .all()
    )
    without_retrieval = (
        conn.execute(
            select(readings.c.id)
            .outerjoin(retrievals, retrievals.c.id == readings.c.retrieval_id)
            .where(retrievals.c.id.is_(None))
            .order_by(readings.c.id)
        )
        .scalars()
        .all()
    )
    return {
        "replies_checked": checked,
        "mismatched_replies": mismatched,
        "retrievals_without_reply": without_reply,
        "readings_without_retrieval": without_retrieval,
    }


def sqlite_copy(source: Path, destination: Path) -> None:
    """Snapshot an existing SQLite database into a new owner-only file.

    The same command restores to a fresh path. SQLite's backup API includes WAL
    data and never runs schema bootstrap or retention triggers.
    """
    source = source.resolve(strict=True)
    destination = destination.resolve()
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with (
            closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as original,
            closing(sqlite3.connect(destination)) as copy,
        ):
            original.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Snapshot integrity check failed")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="Read DATABASE_URL, or SQLite DB_PATH, without bootstrap")
    copy = sub.add_parser("sqlite-copy", help="Back up or restore into a new file")
    copy.add_argument("source", type=Path)
    copy.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "discover":
            url = os.environ.get("DATABASE_URL")
            if not url:
                path = Path(os.environ["DB_PATH"]).resolve(strict=True)
                url = f"sqlite:///{path}"
            print(json.dumps(discover(url), indent=2, default=str))
        else:
            sqlite_copy(args.source, args.destination)
            print("SQLite snapshot completed and integrity checked")
    except Exception as exc:
        # Driver exceptions can contain connection credentials. Do not print them.
        parser.exit(
            1, f"Operation failed ({type(exc).__name__}); check configuration and access.\n"
        )


if __name__ == "__main__":
    main()
