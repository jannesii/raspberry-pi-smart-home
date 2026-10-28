"""Review regressions at the isolated operator command boundary."""

import time

import pytest
import test_release_deployment as deployment
from test_release_deployment import prepared, release, systemctl_calls
from test_release_preparation import commit_all, git

postgres = deployment.postgres
world = deployment.world


@pytest.mark.parametrize("probe", ["service", "http"])
def test_readiness_stalled_probes_obey_one_deadline(world, probe):
    if probe == "http":
        app = world["source"] / "app_standin.py"
        app.write_text(
            app.read_text()
            .replace("import json", "import json\nimport time")
            .replace("    def do_GET(self):", "    def do_GET(self):\n        time.sleep(3)")
        )
        commit_all(world["source"], "slow health endpoint")
    revision = prepared(world)
    script = world["systemctl"].read_text()
    script = script.replace(
        'note(" ".join(sys.argv[1:]))',
        """note(" ".join(sys.argv[1:]))
if action == "start":
    (controls / "probe-start").write_text(str(time.monotonic()))
if action == "is-active" and (controls / "probe-start").exists():
    time.sleep(0.8)
""",
    )
    if probe == "http":
        script = script.replace("    time.sleep(0.8)", "    pass")
    world["systemctl"].write_text(script)
    budget = 0.2 if probe == "service" else 1.2
    result = release(world, "deploy", revision[:12], readiness=budget)
    elapsed = time.monotonic() - float((world["controls"] / "probe-start").read_text())
    assert result.returncode == 4
    assert elapsed < budget + 0.7, result.stdout + result.stderr


@pytest.mark.parametrize("which", ["app", "gateway"])
def test_healthy_json_with_http_503_is_not_ready(world, which):
    source = world["source"]
    if which == "app":
        path = source / "app_standin.py"
        path.write_text(path.read_text().replace('200 if state == "ok" else 503', "503"))
    else:
        path = source / "esp32_ws/gateway_standin.py"
        path.write_text(path.read_text().replace('200 if self.path == "/health" else 404', "503"))
    commit_all(source, "unhealthy HTTP status")
    revision = prepared(world)
    result = release(world, "deploy", revision[:12], readiness=3)
    assert result.returncode == 4, result.stdout + result.stderr
    assert f"{which} health check returned HTTP 503" in result.stderr


@pytest.mark.parametrize("alias", ["parent", "symlink"])
def test_rehearsal_rejects_system_socket_alias_before_connecting(world, tmp_path, alias):
    revision = prepared(world)
    if alias == "parent":
        host = "/tmp/../var/run/postgresql"
    else:
        link = tmp_path / "socket-alias"
        link.symlink_to("/var/run/postgresql", target_is_directory=True)
        host = str(link)
    target = tmp_path / "alias.env"
    target.write_text(
        f"DATABASE_URL=postgresql+psycopg:///release_test_alias?host={host}\nRELEASE_DISPOSABLE=yes\n"
    )
    # Guard before red: interception records any connection attempt and refuses.
    # No SQLAlchemy engine may reach a real socket, even if alias validation is broken.
    guard = world["bin"] / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import sqlalchemy\n"
        f"from pathlib import Path\np=Path({str(tmp_path / 'connected')!r})\n"
        'def refuse(*args, **kwargs):\n    p.touch()\n    raise RuntimeError("guarded connection")\n'
        "sqlalchemy.create_engine=refuse\n"
    )
    second = tmp_path / "second.env"
    second.write_text(
        f"DATABASE_URL=postgresql+psycopg:///release_test_other?host={world['postgres']}\nRELEASE_DISPOSABLE=yes\n"
    )
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(deployment.RELEASE),
            "rehearse",
            revision[:12],
            "--release-root",
            str(world["root"]),
            "--production-env",
            str(world["production_env"]),
            "--production-code",
            str(world["production"]),
            "--disposable-env",
            str(target),
            "--restore-env",
            str(second),
        ],
        env={**os.environ, "PYTHONPATH": str(guard)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "private socket" in result.stderr, result.stderr
    assert not (tmp_path / "connected").exists()
    assert systemctl_calls(world) == []


def test_rewritten_applied_migration_blocks_preparation_rehearsal_and_deployment(world, tmp_path):
    source = world["source"]
    prior = source / "migrations/versions/0001_base.py"
    prior.write_text(prior.read_text() + "# edited applied migration\n")
    (source / "migrations/versions/0002_next.py").write_text(
        'revision = "0002"\ndown_revision = "0001"\n'
    )
    commit_all(source, "rewrite applied migration")
    revision = git(source, "rev-parse", "HEAD")
    result = release(
        world, "prepare", "main", "--source", str(source), "--installer", str(world["installer"])
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "applied migration 0001 changed" in result.stdout
    target = tmp_path / "disposable.env"
    target.write_text(
        f"DATABASE_URL=postgresql+psycopg:///release_test_unused?host={world['postgres']}\nRELEASE_DISPOSABLE=yes\n"
    )
    rehearsal = release(
        world,
        "rehearse",
        revision[:12],
        "--disposable-env",
        str(target),
        "--restore-env",
        str(target),
        "--production-code",
        str(world["production"]),
    )
    assert rehearsal.returncode == 2
    assert "applied migration 0001 changed" in rehearsal.stderr
    deploy = release(world, "deploy", revision[:12])
    assert deploy.returncode == 2
    assert systemctl_calls(world) == []
