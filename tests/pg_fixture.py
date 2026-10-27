"""Private PostgreSQL cluster for tests, using whichever server version is installed.

Unlike the older ``isolated_postgres`` fixture (hardcoded to version 15 and a
fixed port), this discovers the newest ``/usr/lib/postgresql/*/bin`` and picks
a free port so parallel worktrees do not collide. It listens only on a private
Unix socket, never on a network interface or a deployed database.
"""

from __future__ import annotations

import glob
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.engine import URL


def _binaries() -> Path | None:
    found = sorted(
        (Path(p).parent for p in glob.glob("/usr/lib/postgresql/*/bin/initdb")),
        key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0,
    )
    return found[-1] if found else None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def private_postgres(tmp_path):
    """Yield a SQLAlchemy URL for a throwaway cluster; skip when no server exists."""
    binaries = _binaries()
    if binaries is None:
        pytest.skip("No PostgreSQL server binaries under /usr/lib/postgresql")
    cluster = tmp_path / "pg"
    sock = Path(tempfile.mkdtemp(prefix="announce-pg-"))
    port = _free_port()
    subprocess.run(
        [str(binaries / "initdb"), "-D", str(cluster), "-A", "trust"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            str(binaries / "pg_ctl"),
            "-D",
            str(cluster),
            "-l",
            str(tmp_path / "pg.log"),
            "-o",
            f"-k {sock} -p {port} -c listen_addresses=''",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield URL.create(
            "postgresql+psycopg", database="postgres", query={"host": str(sock), "port": str(port)}
        )
    finally:
        subprocess.run(
            [str(binaries / "pg_ctl"), "-D", str(cluster), "-m", "immediate", "-w", "stop"],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(sock, ignore_errors=True)
