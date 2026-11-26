"""Read-only production database access and the bounded query command.

Both seams use a disposable PostgreSQL cluster: the reviewed provisioning SQL is
applied as the administrator, and assertions log in as the restricted role over
password-authenticated loopback TCP, as VS Code and the query command do through
their SSH tunnels. The cluster never listens on a non-loopback interface.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROVISIONING = ROOT / "docs/operations/production-host/readonly.sql"
READONLY = "jannenkoti_readonly"
READONLY_PASSWORD = "synthetic-readonly-secret"
APP = "jannenkoti_app"
APP_PASSWORD = "synthetic-application-secret"
DATABASE = "jannenkoti"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _server_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(Path("/usr/lib/postgresql").glob(f"*/bin/{name}"))
    if not candidates:
        pytest.skip("PostgreSQL server binaries unavailable")
    return str(candidates[-1])


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    """A cluster with trusted admin sockets and password-only loopback TCP."""
    initdb, pg_ctl = _server_binary("initdb"), _server_binary("pg_ctl")
    work = tmp_path_factory.mktemp("readonly-postgres")
    data = work / "data"
    subprocess.run(
        [
            initdb,
            "-D",
            str(data),
            "--auth-local=trust",
            "--auth-host=scram-sha-256",
            "--no-locale",
            "-E",
            "UTF8",
        ],
        check=True,
        capture_output=True,
    )
    port = _free_port()
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-l",
            str(work / "postgres.log"),
            "-o",
            f"-k {work} -p {port} -c listen_addresses=127.0.0.1 -c fsync=off",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    value = {"socket": work, "port": port}
    admin(value, f"CREATE ROLE {APP} LOGIN PASSWORD '{APP_PASSWORD}'", database="postgres")
    admin(value, f"CREATE DATABASE {DATABASE} OWNER {APP}", database="postgres")
    as_app(value, "CREATE TABLE readings (id serial PRIMARY KEY, value text)")
    as_app(value, "INSERT INTO readings (value) VALUES ('first'), ('second')")
    provision(value)
    admin(value, f"ALTER ROLE {READONLY} PASSWORD '{READONLY_PASSWORD}'")
    try:
        yield value
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
            check=False,
            capture_output=True,
        )


def admin(cluster, statement: str, database: str = DATABASE) -> str:
    result = subprocess.run(
        [
            "psql",
            "-X",
            "-h",
            str(cluster["socket"]),
            "-p",
            str(cluster["port"]),
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            statement,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def as_app(cluster, statement: str) -> str:
    return admin(cluster, f"SET ROLE {APP}; {statement}")


def provision(cluster) -> None:
    subprocess.run(
        [
            "psql",
            "-X",
            "-h",
            str(cluster["socket"]),
            "-p",
            str(cluster["port"]),
            "-d",
            DATABASE,
            "-v",
            f"readonly_role={READONLY}",
            "-v",
            f"app_role={APP}",
            "-v",
            f"database={DATABASE}",
            "-f",
            str(PROVISIONING),
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def as_readonly(cluster, *statements: str) -> subprocess.CompletedProcess:
    """Log in exactly as browsing does: role name and password over loopback TCP.

    Each statement is a separate psql command and so its own transaction.
    """
    commands = [part for statement in statements for part in ("-c", statement)]
    return subprocess.run(
        [
            "psql",
            "-X",
            "-h",
            "127.0.0.1",
            "-p",
            str(cluster["port"]),
            "-U",
            READONLY,
            "-d",
            DATABASE,
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            *commands,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": READONLY_PASSWORD, "PGPASSFILE": os.devnull},
    )


def test_readonly_role_reads_application_tables(cluster):
    result = as_readonly(cluster, "SELECT string_agg(value, ',' ORDER BY id) FROM readings")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "first,second"


@pytest.mark.parametrize(
    "mutation",
    [
        "INSERT INTO readings (value) VALUES ('forbidden')",
        "UPDATE readings SET value = 'forbidden'",
        "DELETE FROM readings",
        "TRUNCATE readings",
        "SELECT nextval('readings_id_seq')",
        "SELECT setval('readings_id_seq', 100)",
        "CREATE TABLE intruder (id int)",
        "ALTER TABLE readings ADD COLUMN intruder int",
        "DROP TABLE readings",
        "CREATE SCHEMA intruder",
        "CREATE INDEX intruder ON readings (value)",
    ],
)
def test_readonly_role_cannot_mutate_even_after_lifting_session_read_only(cluster, mutation):
    # Lifting the session default proves the database privileges, not the
    # user-overridable read-only default, deny the change.
    result = as_readonly(
        cluster,
        "SET default_transaction_read_only = off",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
        mutation,
    )
    assert result.returncode != 0
    assert "permission denied" in result.stderr or "must be owner" in result.stderr
    assert admin(cluster, "SELECT count(*) FROM readings") == "2"
    assert admin(cluster, "SELECT last_value FROM readings_id_seq") == "2"


def test_readonly_role_reads_tables_the_application_creates_later(cluster):
    as_app(cluster, "CREATE TABLE later_rows (id serial PRIMARY KEY, note text)")
    as_app(cluster, "INSERT INTO later_rows (note) VALUES ('after provisioning')")
    read = as_readonly(cluster, "SELECT note FROM later_rows")
    assert read.returncode == 0, read.stderr
    assert read.stdout.strip() == "after provisioning"
    write = as_readonly(
        cluster,
        "SET default_transaction_read_only = off",
        "INSERT INTO later_rows (note) VALUES ('x')",
    )
    assert write.returncode != 0
    assert "permission denied" in write.stderr


def test_readonly_role_has_no_administrative_attributes(cluster):
    attributes = admin(
        cluster,
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit "
        f"FROM pg_roles WHERE rolname = '{READONLY}'",
    )
    assert attributes == "f|f|f|f|f|f"
    memberships = admin(
        cluster,
        "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
        f"WHERE r.rolname = '{READONLY}'",
    )
    assert memberships == "0"


def test_provisioning_is_repeatable(cluster):
    provision(cluster)
    result = as_readonly(cluster, "SELECT count(*) FROM readings")
    assert result.returncode == 0, result.stderr


# --- Query command -----------------------------------------------------------

QUERY = ROOT / "scripts/db_query.py"

# Stand-in for OpenSSH at the transport boundary: it honours `-L` by forwarding
# loopback TCP, records its arguments and never contacts a real host.
FAKE_SSH = r"""#!/usr/bin/env python3
import json, os, socket, sys, threading
args = sys.argv[1:]
with open(os.environ["FAKE_SSH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_SSH_FAIL"):
    sys.stderr.write("ssh: Could not resolve hostname raspi\n")
    sys.exit(255)
bind, lport, host, port = args[args.index("-L") + 1].split(":")
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((bind, int(lport)))
server.listen()
def pipe(a, b):
    try:
        while data := a.recv(65536):
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
while True:
    client, _ = server.accept()
    upstream = socket.create_connection((host, int(port)))
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()
"""


def write_credentials(path: Path, **values: str) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(0o600)


def production_url(cluster, user: str = READONLY, password: str = READONLY_PASSWORD) -> str:
    return f"postgresql://{user}:{password}@127.0.0.1:{cluster['port']}/{DATABASE}"


@pytest.fixture
def operator(tmp_path, cluster):
    """An operator environment: private credentials and a substituted SSH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    credentials = tmp_path / "config" / "database.env"
    credentials.parent.mkdir(mode=0o700)
    write_credentials(
        credentials, PRODUCTION_DATABASE_URL=production_url(cluster), PRODUCTION_SSH_HOST="raspi"
    )
    return {
        "credentials": credentials,
        "ssh_log": tmp_path / "ssh.log",
        "port": cluster["port"],
        "env": {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path / "home"),
            "FAKE_SSH_LOG": str(tmp_path / "ssh.log"),
        },
    }


def query(operator, *args: str, stdin: str | None = None, **env: str):
    return subprocess.run(
        [sys.executable, str(QUERY), "--credentials", str(operator["credentials"]), *args],
        capture_output=True,
        text=True,
        input=stdin,
        timeout=60,
        env={**operator["env"], **env},
    )


def ssh_calls(operator) -> list[list[str]]:
    log = operator["ssh_log"]
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_production_query_reads_through_the_ssh_tunnel(operator):
    result = query(operator, "production", "SELECT id, value FROM readings ORDER BY id")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["id\tvalue", "1\tfirst", "2\tsecond"]
    assert "(2 rows)" in result.stderr
    (call,) = ssh_calls(operator)
    assert call[-1] == "raspi"
    assert call[call.index("-L") + 1].endswith(f":127.0.0.1:{operator['port']}")
    assert "BatchMode=yes" in call and "ExitOnForwardFailure=yes" in call


def test_production_query_cannot_write_even_when_the_sql_lifts_read_only(operator, cluster):
    result = query(
        operator,
        "production",
        "SET transaction_read_only = off; INSERT INTO readings (value) VALUES ('no')",
    )
    assert result.returncode == 1
    assert "permission denied" in result.stderr
    assert admin(cluster, "SELECT count(*) FROM readings WHERE value = 'no'") == "0"


def assert_secret_free(result, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in result.stdout
        assert secret not in result.stderr
    assert "Traceback" not in result.stderr


def test_production_refuses_application_credentials(operator):
    # Misconfiguring routine queries with the application login must fail closed.
    write_credentials(
        operator["credentials"],
        PRODUCTION_DATABASE_URL=production_url_for(operator, APP, APP_PASSWORD),
    )
    result = query(operator, "production", "SELECT value FROM readings")
    assert result.returncode == 2
    assert "not the read-only role" in result.stderr
    assert result.stdout == ""
    assert_secret_free(result, APP_PASSWORD)


def production_url_for(operator, user: str, password: str) -> str:
    return f"postgresql://{user}:{password}@127.0.0.1:{operator['port']}/{DATABASE}"


def test_production_never_falls_back_to_application_or_libpq_credentials(operator, tmp_path):
    write_credentials(
        operator["credentials"], DEVELOPMENT_DATABASE_URL="postgresql://x:y@127.0.0.1/x"
    )
    pgpass = tmp_path / "pgpass"
    pgpass.write_text(f"*:*:*:{APP}:{APP_PASSWORD}\n")
    pgpass.chmod(0o600)
    result = query(
        operator,
        "production",
        "SELECT 1",
        DATABASE_URL=production_url_for(operator, APP, APP_PASSWORD),
        PGUSER=APP,
        PGPASSWORD=APP_PASSWORD,
        PGPASSFILE=str(pgpass),
    )
    assert result.returncode == 2
    assert "PRODUCTION_DATABASE_URL is not configured" in result.stderr
    assert ssh_calls(operator) == []
    assert_secret_free(result, APP_PASSWORD)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("postgresql://jannenkoti_readonly@127.0.0.1:5432/jannenkoti", "password"),
        ("postgresql://postgres:pw@127.0.0.1:5432/jannenkoti", "administrator"),
        ("postgresql://jannenkoti_readonly:pw@192.168.1.50:5432/jannenkoti", "loopback"),
        ("mysql://jannenkoti_readonly:pw@127.0.0.1/jannenkoti", "PostgreSQL"),
    ],
)
def test_production_refuses_unsafe_connection_settings(operator, url, reason):
    write_credentials(operator["credentials"], PRODUCTION_DATABASE_URL=url)
    result = query(operator, "production", "SELECT 1")
    assert result.returncode == 2
    assert reason in result.stderr
    assert ssh_calls(operator) == []


@pytest.mark.parametrize("mode", [0o640, 0o604])
def test_refuses_credential_files_readable_by_others(operator, mode):
    operator["credentials"].chmod(mode)
    result = query(operator, "production", "SELECT 1")
    assert result.returncode == 2
    assert "private" in result.stderr
    assert ssh_calls(operator) == []


def test_refuses_credential_files_inside_the_repository(operator):
    inside = ROOT / ".scratch-credentials-test.env"
    try:
        inside.write_text(operator["credentials"].read_text())
        inside.chmod(0o600)
        result = subprocess.run(
            [sys.executable, str(QUERY), "--credentials", str(inside), "production", "SELECT 1"],
            capture_output=True,
            text=True,
            env=operator["env"],
            timeout=60,
        )
    finally:
        inside.unlink(missing_ok=True)
    assert result.returncode == 2
    assert "outside the repository" in result.stderr


@pytest.mark.parametrize("target", [None, "prod", "staging"])
def test_requires_an_explicit_known_target(operator, target):
    result = query(operator, *([target] if target else []), "SELECT 1")
    assert result.returncode == 2
    assert ssh_calls(operator) == []


def test_reports_ssh_tunnel_failure_without_credentials(operator):
    result = query(operator, "production", "SELECT 1", FAKE_SSH_FAIL="1")
    assert result.returncode == 1
    assert "SSH tunnel through raspi failed" in result.stderr
    assert "Could not resolve hostname" in result.stderr
    assert_secret_free(result, READONLY_PASSWORD)


def test_reports_wrong_password_without_echoing_secrets(operator):
    write_credentials(
        operator["credentials"],
        PRODUCTION_DATABASE_URL=production_url_for(operator, READONLY, "wrong-secret-value"),
    )
    result = query(operator, "production", "SELECT 1")
    assert result.returncode == 1
    assert "cannot connect" in result.stderr
    assert "password authentication failed" in result.stderr
    assert_secret_free(result, "wrong-secret-value", READONLY_PASSWORD)


def test_reports_sql_errors_with_their_state_and_no_secrets(operator):
    result = query(operator, "production", "SELECT * FROM missing_table")
    assert result.returncode == 1
    assert "query failed (42P01)" in result.stderr
    assert 'relation "missing_table" does not exist' in result.stderr
    assert_secret_free(result, READONLY_PASSWORD)


def test_statement_timeout_cancels_long_queries(operator):
    started = time.monotonic()
    result = query(operator, "production", "--timeout", "0.5", "SELECT pg_sleep(20)")
    assert time.monotonic() - started < 15
    assert result.returncode == 1
    assert "cancelled by the --timeout limit" in result.stderr


def test_timeout_bounds_the_whole_query_even_when_the_sql_lifts_it(operator):
    started = time.monotonic()
    result = query(
        operator,
        "production",
        "--timeout",
        "1",
        "SET statement_timeout = 0; SELECT pg_sleep(0.6); SELECT pg_sleep(0.6); SELECT pg_sleep(20)",
    )
    assert time.monotonic() - started < 15
    assert result.returncode == 1
    assert "cancelled by the --timeout limit" in result.stderr


def test_row_limit_truncates_and_reports_it(operator):
    result = query(
        operator, "production", "--max-rows", "3", "SELECT n FROM generate_series(1, 500) n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["n", "1", "2", "3"]
    assert "truncated: printed 3 of 500 rows" in result.stderr


def test_byte_limit_truncates_wide_output_and_reports_it(operator):
    result = query(
        operator,
        "production",
        "--max-bytes",
        "1024",
        "--max-cell",
        "300",
        "SELECT repeat('x', 1000) AS wide FROM generate_series(1, 50)",
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.encode()) <= 1024
    lines = result.stdout.splitlines()
    assert lines[0] == "wide"
    assert all(line == "x" * 299 + "…" for line in lines[1:])
    assert "truncated: printed 3 of 50 rows" in result.stderr


def test_out_of_range_limits_are_refused(operator):
    for flag, value in (("--timeout", "0"), ("--max-rows", "0"), ("--max-bytes", "10")):
        result = query(operator, "production", flag, value, "SELECT 1")
        assert result.returncode == 2
    assert ssh_calls(operator) == []


def test_reads_sql_from_stdin_and_escapes_control_characters(operator):
    result = query(operator, "production", stdin="SELECT E'a\\tb\\nc' AS text, NULL AS missing")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["text\tmissing", "a\\tb\\nc\tNULL"]


# --- Writable development target ---------------------------------------------


@pytest.fixture
def development(operator, cluster):
    name = "dev_" + os.urandom(4).hex()
    admin(cluster, f"CREATE ROLE {name} LOGIN PASSWORD 'dev-secret'", database="postgres")
    admin(cluster, f"CREATE DATABASE {name} OWNER {name}", database="postgres")
    write_credentials(
        operator["credentials"],
        PRODUCTION_DATABASE_URL=production_url(cluster),
        DEVELOPMENT_DATABASE_URL=(
            f"postgresql://{name}:dev-secret@/{name}?host={cluster['socket']}&port={cluster['port']}"
        ),
    )
    return name


def test_development_target_permits_writes_without_ssh(operator, cluster, development):
    created = query(
        operator,
        "development",
        "CREATE TABLE notes (body text); INSERT INTO notes VALUES ('kept')",
    )
    assert created.returncode == 0, created.stderr
    assert "INSERT 0 1" in created.stderr
    assert admin(cluster, "SELECT body FROM notes", database=development) == "kept"
    read = query(operator, "development", "SELECT body FROM notes")
    assert read.stdout.splitlines() == ["body", "kept"]
    assert ssh_calls(operator) == []


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://dev:pw@192.168.1.50:5432/jannenkoti",
        "postgresql://dev:pw@raspi:5432/jannenkoti",
        f"postgresql://{READONLY}:pw@127.0.0.1:5432/jannenkoti_dev",
        # A loopback forward to the Pi with application credentials.
        f"postgresql://{APP}:{APP_PASSWORD}@127.0.0.1:5432/{DATABASE}",
        "postgresql://dev:pw@/jannenkoti_dev?host=/run/postgresql&port=abc",
    ],
)
def test_development_refuses_remote_production_or_invalid_targets(operator, cluster, url):
    write_credentials(
        operator["credentials"],
        PRODUCTION_DATABASE_URL=production_url(cluster),
        DEVELOPMENT_DATABASE_URL=url,
    )
    result = query(operator, "development", "SELECT 1")
    assert result.returncode == 2, result.stderr
    assert "refused" in result.stderr
    assert ssh_calls(operator) == []
    assert_secret_free(result, APP_PASSWORD)


def test_malformed_production_url_is_a_refused_configuration(operator):
    write_credentials(
        operator["credentials"],
        PRODUCTION_DATABASE_URL="not a url",
        DEVELOPMENT_DATABASE_URL="postgresql://dev:pw@127.0.0.1/jannenkoti_dev",
    )
    for target in ("production", "development"):
        result = query(operator, target, "SELECT 1")
        assert result.returncode == 2
        assert "not a database URL" in result.stderr


def test_byte_limit_stops_before_later_result_sets(operator):
    result = query(
        operator,
        "production",
        "--max-bytes",
        "300",
        "SELECT repeat('x', 100) FROM generate_series(1, 10); SELECT 'later' AS small",
    )
    assert result.returncode == 0, result.stderr
    assert "later" not in result.stdout
    assert "later result sets not printed" in result.stderr


def test_provisioning_refuses_schemas_that_let_the_role_create(cluster):
    admin(
        cluster,
        f"CREATE SCHEMA scratch AUTHORIZATION {APP}; GRANT CREATE ON SCHEMA scratch TO PUBLIC",
    )
    try:
        with pytest.raises(subprocess.CalledProcessError) as refused:
            provision(cluster)
        assert "can create in schemas scratch" in refused.value.stderr
    finally:
        admin(cluster, "DROP SCHEMA scratch")
    provision(cluster)
