"""Migration release behavior through the approved operator command seam."""

import json
import subprocess
import uuid

import pytest
import test_release_deployment as deployment
from test_release_deployment import (
    assert_no_host_effects,
    prepared,
    release,
    running,
    sql,
    systemctl_calls,
)
from test_release_preparation import commit_all

# Reuse the private socket cluster and explicitly substituted service manager.
postgres = deployment.postgres


@pytest.fixture
def world(tmp_path, postgres):
    fixture = deployment.world.__wrapped__(tmp_path, postgres)
    value = next(fixture)
    # Model a real applied migration: candidate history must match the reference.
    baseline = (
        'from alembic import op\nrevision = "0001"\ndown_revision = None\n'
        'def upgrade():\n    op.execute("CREATE TABLE samples (value text)")\n'
    )
    for code in (value["source"], value["production"]):
        (code / "migrations/versions/0001_base.py").write_text(baseline)
    commit_all(value["source"], "executable baseline")
    try:
        yield value
    finally:
        fixture.close()


def test_rehearsal_refuses_missing_disposable_target_without_host_effects(world):
    revision = prepared(world)
    result = release(world, "rehearse", revision[:12])
    assert result.returncode == 2
    assert "disposable" in result.stderr
    assert systemctl_calls(world) == []


def migration_candidate(world):
    source = world["source"]
    (source / "migrations/env.py").write_text(
        "import os\nfrom alembic import context\nfrom sqlalchemy import create_engine\n"
        'with create_engine(os.environ["DATABASE_URL"]).connect() as conn:\n'
        "    context.configure(connection=conn)\n"
        "    with context.begin_transaction():\n"
        "        context.run_migrations()\n"
    )
    (source / "migrations/versions/0001_base.py").write_text(
        'from alembic import op\nrevision = "0001"\ndown_revision = None\n'
        'def upgrade():\n    op.execute("CREATE TABLE samples (value text)")\n'
    )
    (source / "migrations/versions/0002_next.py").write_text(
        'from alembic import op\nrevision = "0002"\ndown_revision = "0001"\n'
        'def upgrade():\n    op.execute("ALTER TABLE samples ADD COLUMN active boolean DEFAULT true")\n'
    )
    (source / "migrations/rehearsal.sql").write_text("INSERT INTO samples VALUES ('synthetic');\n")
    (source / "migrations/rehearsal-verify.sql").write_text(
        "DO $$ BEGIN IF (SELECT count(*) FROM samples WHERE value='synthetic' AND active) <> 1 "
        "THEN RAISE EXCEPTION 'synthetic state lost'; END IF; END $$;\n"
    )
    commit_all(source, "migration")
    return prepared(world)


def disposable(world, tmp_path, name):
    database = "release_test_" + uuid.uuid4().hex[:12]
    subprocess.run(
        ["createdb", "-h", str(world["postgres"]), database], check=True, capture_output=True
    )
    env = tmp_path / name
    env.write_text(
        f"DATABASE_URL=postgresql+psycopg:///{database}?host={world['postgres']}\nRELEASE_DISPOSABLE=yes\n"
    )
    return env


def test_rehearsal_restores_representative_state_and_binds_baseline(world, tmp_path):
    revision = migration_candidate(world)
    first = disposable(world, tmp_path, "first.env")
    second = disposable(world, tmp_path, "second.env")
    result = release(
        world,
        "rehearse",
        revision[:12],
        "--disposable-env",
        str(first),
        "--restore-env",
        str(second),
        "--production-code",
        str(world["production"]),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = release(world, "status", revision[:12], "--json")
    candidate = json.loads(report.stdout)
    assert candidate["migrations"]["rehearsal"] == "passed"
    assert candidate["deployable"]
    assert systemctl_calls(world) == []


def rehearse(world, tmp_path, revision):
    return release(
        world,
        "rehearse",
        revision[:12],
        "--disposable-env",
        str(disposable(world, tmp_path, "first.env")),
        "--restore-env",
        str(disposable(world, tmp_path, "second.env")),
        "--production-code",
        str(
            world["root"] / "current" / "code"
            if (world["root"] / "current").exists()
            else world["production"]
        ),
    )


def test_migration_deploy_preserves_rows_and_creates_restricted_backup(world, tmp_path):
    revision = migration_candidate(world)
    assert rehearse(world, tmp_path, revision).returncode == 0
    sql(world, "CREATE TABLE samples (value text); INSERT INTO samples VALUES ('preserve me')")
    result = release(world, "deploy", revision[:12])
    assert result.returncode == 0, result.stdout + result.stderr
    assert running(world) == revision[:12]
    assert sql(world, "SELECT value FROM samples WHERE active") == "preserve me"
    record = json.loads((world["root"] / "releases" / revision[:12] / "release.json").read_text())
    backup = world["root"] / "backups" / record["backups"][-1]["name"]
    assert backup.is_file()
    assert backup.stat().st_mode & 0o777 == 0o600
    assert record["backups"][-1]["baseline"] == ["0001"]
    assert_no_host_effects(world, result.stdout, result.stderr)


def test_backup_failure_never_stops_services_or_migrates(world, tmp_path):
    revision = migration_candidate(world)
    assert rehearse(world, tmp_path, revision).returncode == 0
    broken = world["bin"] / "pg_dump"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    result = release(world, "deploy", revision[:12])
    assert result.returncode == 1
    assert systemctl_calls(world) == []
    assert sql(world, "SELECT version_num FROM alembic_version") == "0001"


def test_partial_migration_stops_without_automatic_recovery(world, tmp_path):
    revision = migration_candidate(world)
    assert rehearse(world, tmp_path, revision).returncode == 0
    # Real production-like schema drift makes ALTER fail, although synthetic rehearsal passed.
    sql(world, "CREATE TABLE samples(value text, active boolean)")
    result = release(world, "deploy", revision[:12])
    assert result.returncode == 4, result.stdout + result.stderr
    assert "No automatic code recovery" in result.stderr
    assert not any(call.startswith("start ") for call in systemctl_calls(world))
    operation = json.loads((world["root"] / "operation.json").read_text())
    assert operation["state"] == "migration-failed"
    assert operation["backup"]


def test_restore_requires_named_backup_and_data_loss_acknowledgement(world):
    result = release(world, "restore", "--backup", "unknown.dump")
    assert result.returncode == 2
    assert "later writes" in result.stderr
    assert systemctl_calls(world) == []


@pytest.mark.parametrize("fault", ["baseline", "reference", "failed-seed", "production-target"])
def test_invalid_rehearsal_cannot_authorize_migration(world, tmp_path, fault):
    revision = migration_candidate(world)
    first, second = (
        disposable(world, tmp_path, "first.env"),
        disposable(world, tmp_path, "second.env"),
    )
    if fault == "production-target":
        first = world["production_env"]
    if fault == "failed-seed":
        # Rehearsal must execute the committed validation, not accept a claimed pass.
        source = world["source"]
        (source / "migrations/rehearsal-verify.sql").write_text(
            "SELECT missing_column FROM samples;\n"
        )
        commit_all(source, "bad verification")
        revision = prepared(world)
    result = release(
        world,
        "rehearse",
        revision[:12],
        "--disposable-env",
        str(first),
        "--restore-env",
        str(second),
        "--production-code",
        str(world["production"]),
    )
    if fault in ("failed-seed", "production-target"):
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        if fault == "baseline":
            sql(world, "UPDATE alembic_version SET version_num='unexpected'")
        else:
            baseline = world["production"] / "migrations/versions/0001_base.py"
            baseline.write_text(baseline.read_text() + "# baseline changed\n")
    result = release(world, "deploy", revision[:12])
    assert result.returncode == 2, result.stdout + result.stderr
    assert systemctl_calls(world) == []


def test_named_restore_discards_later_writes_and_leaves_services_stopped(world, tmp_path):
    revision = migration_candidate(world)
    assert rehearse(world, tmp_path, revision).returncode == 0
    sql(world, "CREATE TABLE samples(value text); INSERT INTO samples VALUES('original')")
    assert release(world, "deploy", revision[:12]).returncode == 0
    sql(world, "INSERT INTO samples(value) VALUES('later')")
    record = json.loads((world["root"] / "releases" / revision[:12] / "release.json").read_text())
    backup = record["backups"][-1]["name"]
    result = release(
        world,
        "restore",
        "--backup",
        backup,
        "--acknowledge-data-loss",
        "--service-manager",
        str(world["systemctl"]),
        "--app-url",
        world["app_url"],
        "--gateway-url",
        world["gateway_url"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "later writes were discarded" in result.stdout
    assert sql(world, "SELECT value FROM samples") == "original"
    assert sql(world, "SELECT version_num FROM alembic_version") == "0001"
    assert json.loads((world["root"] / "operation.json").read_text())["state"] == "restored"


def test_readiness_failure_after_migration_refuses_incompatible_code_recovery(world, tmp_path):
    first = prepared(world)
    assert release(world, "deploy", first[:12]).returncode == 0
    revision = migration_candidate(world)
    (world["source"] / "candidate.py").write_text('DATABASE = "missing_database"\n')
    commit_all(world["source"], "unready application")
    revision = prepared(world)
    assert rehearse(world, tmp_path, revision).returncode == 0
    sql(world, "CREATE TABLE samples(value text)")
    result = release(world, "deploy", revision[:12], readiness=0.5)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "compatibility not established" in result.stderr
    assert running(world) == revision[:12]
    assert sql(world, "SELECT version_num FROM alembic_version") == "0002"
    recovered = release(world, "recover")
    assert recovered.returncode == 2
    assert "restoring a database backup is a separate owner decision" in recovered.stderr


def test_restart_refuses_after_schema_changes_outside_selected_release(world):
    revision = prepared(world)
    assert release(world, "deploy", revision[:12]).returncode == 0
    sql(world, "UPDATE alembic_version SET version_num='unknown_after_failure'")
    calls = systemctl_calls(world)
    result = release(world, "restart")
    assert result.returncode == 2
    assert "database compatibility" in result.stderr
    assert systemctl_calls(world) == calls
