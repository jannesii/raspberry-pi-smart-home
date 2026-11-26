# Database operations

## Connections and migrations

Follow [production setup](../../readme.md#production) to configure PostgreSQL
and apply `alembic upgrade head` from the repository root. `DATABASE_URL` is the SQLAlchemy
connection URL. `DB_PATH` must also identify a writable SQLite file because
bootstrap still constructs the legacy manager.

[migrations/env.py](../../migrations/env.py) loads the repository-root
`jannenkoti.env` (the service's environment file) without overriding the process
environment, then chooses `DATABASE_URL` before the `alembic.ini` URL. Export
`DATABASE_URL` to target another database, such as an isolated restore.

The real `alembic.ini` is ignored. Copy [the example](../../alembic.ini.example)
once and keep actual credentials in the environment.

For a schema change, update [schema.py](../../app/core/schema.py), generate a
revision, and review its upgrade/downgrade operations before applying it:

```bash
# From the repository root, with the virtual environment active and DATABASE_URL set.
alembic revision --autogenerate -m "describe schema change"
alembic heads
alembic current
```

Autogeneration is a draft, not proof that a migration preserves data. Validate
on a disposable database representative of production. SQLite controller tests
do not establish that PostgreSQL DDL or sequence behavior is correct.

## Legacy imports

[MigrationMixin](../../app/core/_controller/migrations.py) provides
`migrate_3d_to_pg`, `migrate_auth_to_pg`, `migrate_ac_to_pg`,
`migrate_car_heater_to_pg`, and `migrate_ready_by_to_pg`.
They read `self.db_path` and write through `self._sa_engine`, using upserts.
They cover selected tables, not a complete database backup or universal importer.

Before an import, preserve a source backup and validate against an isolated
target. Review each helper's table list and returned `migrated`/`errors` counts.
Compare row counts, latest timestamps, and account permissions afterward.
Repeated imports can update existing target rows; idempotent does not mean
read-only. Do not initialize the full app merely to inspect source data.

Explicit imported IDs can leave PostgreSQL serial sequences behind. The
kFactor controller has targeted realignment/retry behavior; other imported
tables still need their sequences checked before new inserts.

## Runtime constraints

[Engine management](../../app/core/sqlalchemy_engine.py) caches engines and
uses bounded PostgreSQL pooling. Keep `NullPool` limited to SQLite or one-shot
migration paths; repeated PostgreSQL socket creation can conflict with Eventlet.
Timestamp columns declared as text must be compared with ISO strings.

## Read-only browsing and queries

Routine production inspection uses the dedicated `jannenkoti_readonly` login,
never the application or administrator credentials
([ADR 0005](../adr/0005-operate-pi-production-from-desktop-development.md)).
PostgreSQL privileges enforce the boundary: the role has only `CONNECT`, schema
`USAGE` and `SELECT`, including on tables the application role creates later.
Its read-only session default is defence in depth only. PostgreSQL stays on
the Pi's loopback interface; VS Code and the query command reach it through SSH.
Production writes are separate maintenance operations.

### Provisioning on the Pi

Review [readonly.sql](production-host/readonly.sql), then run it as the
PostgreSQL administrator. First find the role that owns the application tables.
Migrations must keep running as that role, because future-table grants follow it.

```fish
ssh raspi
sudo -u postgres psql -X -d jannenkoti -Atc "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'"
```

Copy the SQL to the Pi, substitute the owner found above for `APP_ROLE`, and set
a new password at the prompt. The script is repeatable. It stops if `PUBLIC`
privileges would still let the role create in any schema or modify objects,
and warns about tables owned by other roles.

```fish
scp /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home/docs/operations/production-host/readonly.sql raspi:/tmp/readonly.sql
ssh -t raspi 'sudo -u postgres psql -X -d jannenkoti -v readonly_role=jannenkoti_readonly -v app_role=APP_ROLE -v database=jannenkoti -f /tmp/readonly.sql && sudo -u postgres psql -X -d postgres -c "\password jannenkoti_readonly"; rm -f /tmp/readonly.sql'
```

Password logins must be accepted on loopback TCP. The Debian default
`host all all 127.0.0.1/32 scram-sha-256` line in `pg_hba.conf` is enough.
Keep `listen_addresses` at `localhost`.

### Browsing in VS Code

Use the installed Database Client extension (`cweijan.vscode-postgresql-client2`).
Create a PostgreSQL connection named `jannenkoti production (read-only)`:

| Field | Value |
| --- | --- |
| Host / Port | `127.0.0.1` / `5432` (as seen from the Pi) |
| Database | `jannenkoti` |
| Username | `jannenkoti_readonly` |
| Save password | `Never`: the extension prompts for the password on connect |
| SSH tunnel | Enabled, with the values of the `raspi` alias (`ssh -G raspi`): host `192.168.1.50`, port `22`, user `jannesi`, and that alias's private key |

Leave the password field empty. Never use the application login for this
connection. The role defaults to a 30 s `statement_timeout`; lift it with
`SET statement_timeout` in that session for a deliberately longer read. Its `SELECT` results are live production data. Edits, DDL and
data changes fail with `permission denied`.

### Query command

[scripts/db_query.py](../../scripts/db_query.py) runs bounded queries for the
owner and coding agents. Store credentials once, outside the repository:

```fish
install -d -m 700 ~/.config/jannenkoti-ops
install -m 600 /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home/docs/operations/production-host/database.env.example ~/.config/jannenkoti-ops/database.env
```

Set the read-only password in `PRODUCTION_DATABASE_URL`. Point
`DEVELOPMENT_DATABASE_URL` at the local development database (the
`DATABASE_URL` of `~/.config/jannenkoti-dev/dev.env`) or another disposable local
database. The command refuses a file that others can read, a symlink, or a
file inside the repository. It reads no other file or environment variable for
credentials.

```fish
set -l repo /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
"$repo/.venv/bin/python" "$repo/scripts/db_query.py" production "SELECT count(*) FROM sensor_locations"
"$repo/.venv/bin/python" "$repo/scripts/db_query.py" production --max-rows 20 --timeout 5 < /home/jannesi/query.sql
"$repo/.venv/bin/python" "$repo/scripts/db_query.py" development "UPDATE sensor_locations SET name = 'Kehitys' WHERE id = 1"
```

- **Target** is required. `production` opens a temporary `ssh -L` tunnel through
  `PRODUCTION_SSH_HOST` (default `raspi`) in batch mode. It runs read-only
  transactions and rolls them back. Before running the SQL, it refuses
  credentials that are superuser, can create, can modify or own tables, or can
  assume other roles. `development` connects directly to a loopback address or
  socket and commits. It refuses remote hosts and the production role or
  database name, because a loopback port could itself forward to the Pi.
- **Bounds.** `--timeout` (default 15 s) sets `statement_timeout` and also
  cancels the whole run client-side, even if the SQL lifts that setting.
  `--max-rows` (100), `--max-bytes` (64 KiB) and `--max-cell` (500 characters)
  bound printed output; printing stops at the byte limit, including any later
  result sets. Rows are tab-separated with a header. Tabs, newlines and backslashes in
  values are escaped, and `NULL` is shown literally. The row count or a
  `truncated: printed N of M rows` notice goes to stderr. The timeout bounds
  server work; results within it are still transferred before printing.
- **Errors** show the SQLSTATE and PostgreSQL's primary message, or the failing
  SSH step, without connection URLs, passwords or tracebacks.

| Exit | Meaning |
| --- | --- |
| `0` | Query completed; output may be truncated as reported. |
| `1` | Connection, tunnel, timeout or SQL failure. |
| `2` | Refused target, limits, credential file or credentials. |

Automated tests ([test_database_query.py](../../tests/test_database_query.py))
apply the provisioning SQL to a disposable cluster and log in as the role.
They substitute the SSH transport. Commission the real Pi separately: after
provisioning, run `SELECT current_user` and one write attempt through the
command, then connect once from VS Code. Expect `jannenkoti_readonly`,
`permission denied` and a password prompt respectively.
