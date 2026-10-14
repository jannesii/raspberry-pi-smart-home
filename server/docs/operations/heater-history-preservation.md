# Preserve surviving heater evidence before collection

Issue #4 prepares the software and isolated checks. The owner performs the
following deployed inspection, preservation and migration under #12 before
first-frost collection. These commands are instructions, not evidence that
production has been inspected or changed. Use the shared [domain terms](../../GLOSSARY.md).

Raw heater status, glass/cabin sensor frames and existing kFactor heating
sessions/results are evidence. Keep them permanently. The session identities,
photos and departure outcomes introduced by later tickets must follow the same
policy; this ticket does not introduce those tables or photo storage.

## 1. Discover without starting the app

From `server/`, use the configured backend: `DATABASE_URL` takes precedence over
`DB_PATH`. Supply connection settings through the owner's environment or secret
manager; do not paste passwords into commands, issues, reports or logs. The
script does not load `.env` files or import/initialize the application.

```sh
umask 077
.venv/bin/python scripts/heater_history.py discover > heater-discovery-before.json
```

Discovery opens SQLite in read-only mode (the file must already exist), or a
PostgreSQL read-only, repeatable-read transaction. It lists every visible
`car_heater_*` table, row counts and available text timestamp bounds, plus all
user triggers and, for PostgreSQL, their function definitions. Missing tables
are absent from the report; a zero count means an existing empty table. Text
bounds preserve stored ISO-text semantics, not normalized UTC instants across
mixed formats. Inspect the schema/search path for the configured PostgreSQL
role; use a role that can read every applicable heater table and catalog entry.

Check the report for surviving dates, status readings, sensor frames and heating
sessions. Inspect trigger/function definitions for heater deletions, including
shared functions and differently named triggers. Inspect pg_cron/pgAgent jobs
if installed, systemd timers, crontabs, backup jobs and other external cleanup
scripts separately: the report cannot establish that no external deletion job
exists. The repository-defined SQLite trigger
`cleanup_car_heater_status_after_insert` deletes status rows older than 30 days
on an insert. No PostgreSQL heater retention is defined in repository migrations;
the deployed catalog must still be checked. Household temperature/humidity,
pressure, AC and image retention are outside the heater policy and remain intact.

Do not insert test telemetry, start the app or apply migrations during discovery.
Rows already deleted cannot be recovered by this migration; locate older backups
if the surviving range is incomplete. Keep reports private: trigger/function
source may contain deployment-specific information.

## 2. Preserve before migrating

Pause heater telemetry writers and cleanup jobs for the change window so no
old rule deletes evidence between backup and migration. Record the source
backend, discovery counts/date range, current Alembic revision, application
revision, backup timestamp and checksum. Preserve the configured PostgreSQL
backend and the required legacy `DB_PATH` file separately; backing up only the
legacy file does not back up PostgreSQL evidence.

SQLite uses the backup API, including committed WAL content, and checks the
snapshot's integrity. Both source and destination are explicit paths; the
command refuses to overwrite an existing destination and creates it mode 0600.

```sh
.venv/bin/python scripts/heater_history.py sqlite-copy /path/to/source.db /private/backup/heater-before.db
sha256sum /private/backup/heater-before.db
```

For PostgreSQL, configure private libpq service entries and `.pgpass` (mode
0600), or the equivalent owner secret mechanism. `heater_source` below is a
placeholder service name for the deployed source. Do not pass the SQLAlchemy
`postgresql+psycopg://` URL to libpq utilities. Use PostgreSQL client versions
compatible with the source server.

```sh
pg_dump --dbname='service=heater_source' --format=custom --no-owner --no-acl --file=/private/backup/heater-before.dump
pg_restore --list /private/backup/heater-before.dump > /private/backup/heater-before.contents
sha256sum /private/backup/heater-before.dump
```

This is a whole-database backup so related heating sessions/results and their
relationships stay together. `--no-owner --no-acl` makes isolated restoration
portable; capture roles/grants separately for disaster recovery. A database dump
does not include future photo files: once capture is implemented, archive its
storage with the database and session associations. Keep an off-host copy,
restrict access, and retain dated snapshots rather than replacing the sole copy.

## 3. Restore into isolation and verify

Restore to a **fresh isolated target**, never over the running database. Backups
made before the change retain the old triggers; migrate the restored copy
**before inserting telemetry or starting the app**.

```sh
.venv/bin/python scripts/heater_history.py sqlite-copy /private/backup/heater-before.db /private/restore-check/heater.db
```

For PostgreSQL the owner creates an empty isolated database and configures a
`heater_restore_check` libpq service for it, then runs:

```sh
pg_restore --dbname='service=heater_restore_check' --no-owner --no-acl --exit-on-error /private/backup/heater-before.dump
```

Use the restored target's `DATABASE_URL` for migration and discovery. Alembic
does **not** read `DB_PATH`: for SQLite set an explicit URL such as
`sqlite:////private/restore-check/heater.db`; `DB_PATH` selects only the legacy
app bootstrap file and the discovery fallback when no `DATABASE_URL` is set.
For PostgreSQL, select the restored PostgreSQL database explicitly and use a
separate isolated `DB_PATH` for app bootstrap. Save a restored discovery report
and compare table counts/date bounds
with the source backup. See the migration environment caveat below. Run an
isolated app with mocked/disabled device, weather and other background
integrations. Authenticate, request `/api/car_heater/history?date=YYYY-MM-DD`
for a known surviving date, and open the car-heater chart's date/history view
for the same day. Confirm original timestamps and measurements, then verify
again after new synthetic telemetry and a process restart. Never run test
telemetry or automated tests against the deployed database or live heaters.

## 4. Apply the reviewed migration under #12

Review `20261014_0011_permanent_heater_history.py` and every intervening unapplied
revision. It drops only the known heater cleanup trigger on SQLite/PostgreSQL,
keeps all records and leaves unrelated triggers and shared functions intact.
Resolve any differently named heater-deletion rule found during discovery with
an owner-reviewed migration/job change before resuming collection; this revision
cannot remove unknown deployment-specific jobs safely.

Use the existing credential-free `alembic.ini.example` configuration. The current
migration loader reads `../jannenkoti.env` with **override enabled**. The owner
must ensure it cannot replace the intended target `DATABASE_URL`, especially
when validating a restored database. Never print the effective secret URL.

```sh
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/python scripts/heater_history.py discover > heater-discovery-after.json
```

Before resuming writers, compare surviving heater counts/date bounds and confirm
that applicable heater deletion is gone. Household retention should still be
present. Restart through the existing single-worker Eventlet deployment and
verify the authenticated history API and browser chart for a surviving old date.
The local bootstrap no longer creates the heater cleanup trigger, but does not
upgrade existing databases: apply the migration to an old SQLite file before
starting a new app against it. For a legacy file without Alembic bookkeeping,
follow the repository's baseline/adoption procedure; do not blindly stamp head.
After a preservation backup and schema review, the trigger-only SQLite removal
can also be applied explicitly by the owner:

```sql
DROP TRIGGER IF EXISTS cleanup_car_heater_status_after_insert;
```

Record the inspected backend, backup/restore evidence, applied revisions,
remaining external-job checks and browser/history verification in #12.
Downgrading this revision deliberately does not recreate destructive retention;
application rollback keeps evidence permanent. Prefer rolling software back
without replacing the database; restoring a snapshot loses records collected
since that snapshot and requires a separately approved recovery plan.

## Isolated automated evidence

```sh
.venv/bin/pytest -q tests/test_car_heater_history_preservation.py
```

Checks exercise read-only discovery, old authenticated history after new
telemetry and fresh-process SQLite bootstrap, migration preservation, unrelated
household cleanup, non-overwriting SQLite backup/restore and restored history.
When PostgreSQL 15 binaries are available, a private Unix-socket-only cluster
also exercises PostgreSQL trigger removal, discovery and `pg_dump`/`pg_restore`
followed by the same authenticated history path. No deployed credentials, data,
weather service or devices are involved. PostgreSQL checks skip if those local
binaries are unavailable. Browser rendering remains a separate manual check.
