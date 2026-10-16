"""Alembic reads the service's environment file without overriding exported targets."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path):
    """An isolated copy of the migration setup with its own jannenkoti.env."""
    root = tmp_path / "server"
    root.mkdir()
    shutil.copytree(
        ROOT / "migrations", root / "migrations", ignore=shutil.ignore_patterns("__pycache__")
    )
    (root / "app").symlink_to(ROOT / "app")
    # Point the ini fallback at a local placeholder so no server is ever contacted.
    ini = (ROOT / "alembic.ini.example").read_text()
    placeholder = f"sqlite:///{tmp_path / 'ini_placeholder.db'}"
    (root / "alembic.ini").write_text(
        "\n".join(
            f"sqlalchemy.url = {placeholder}" if line.startswith("sqlalchemy.url") else line
            for line in ini.splitlines()
        )
    )
    (root / "jannenkoti.env").write_text(f"DATABASE_URL=sqlite:///{tmp_path / 'from_file.db'}\n")
    return root


def alembic_current(root, **env):
    base = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "USE_SQLA_READS")}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=root,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_migrations_use_the_service_environment_file(checkout):
    result = alembic_current(checkout)
    assert result.returncode == 0, result.stderr
    # The URL came from server/jannenkoti.env, not the alembic.ini placeholder.
    assert (checkout.parent / "from_file.db").exists()
    assert not (checkout.parent / "ini_placeholder.db").exists()


def test_an_exported_database_url_wins_over_the_environment_file(checkout):
    target = checkout.parent / "restore_check.db"
    result = alembic_current(checkout, DATABASE_URL=f"sqlite:///{target}")
    assert result.returncode == 0, result.stderr
    assert target.exists()
    assert not (checkout.parent / "from_file.db").exists()
