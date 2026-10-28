# Releases

Production runs an exact committed revision from a dedicated release location,
separate from working checkouts
([ADR 0004](../adr/0004-isolate-development-effects-from-household-operation.md)).
This guide covers preparing and accepting a candidate, deploying it, checking
the running release and recovering the previous one. Preparing a candidate never
migrates the production database, restarts a service or switches the running
release. Candidates with database migrations require an exact, successful
PostgreSQL rehearsal and a restricted backup before deployment.

## Release root

Every command needs an explicit release root: `--release-root DIR` or
`JANNENKOTI_RELEASE_ROOT`. The intended location on the Pi is
`/home/jannesi/jannenkoti-releases` (create it when preparing the first release;
it must be outside any Git checkout):

```text
releases/<candidate>/   one candidate: code/, venv/, logs/, release.json, dependencies.txt
current -> releases/…   the running release (set by deployment, never by preparation)
previous -> releases/…  the release kept for code recovery
backups/                production database backups (never pruned)
operation.json          the latest deploy, recover or restart, step by step
operations.log          one JSON line per finished operation
.lock                   held while an operation changes the root
```

Production configuration (`jannenkoti.env`) and persistent state stay outside
`releases/`. A candidate is `git archive` of one commit, so untracked and
ignored files are never copied, and a revision that tracks a `*.env` or
`alembic.ini` file fails preparation.

## Preparing a candidate

```bash
export JANNENKOTI_RELEASE_ROOT=/home/jannesi/jannenkoti-releases
.venv/bin/python scripts/release.py prepare main --source ~/Code/server-worktrees/feature
```

The command:

1. Refuses (exit `2`) when `--source` has uncommitted changes to tracked
   files, or when the revision is unknown or ambiguous (for example a branch
   and a tag with the same name). Untracked files never reach a candidate.
2. Exports the resolved commit to `releases/<first 12 hex>/code` and installs
   `requirements.txt` into a new `venv` there. Nothing is shared with a
   development or production virtualenv. A candidate that is already prepared
   is not rebuilt unless you pass `--replace`, which discards its evidence.
3. Records the code identity (hash of the exported files) and dependency
   identity (hash of the installed `name==version` list in `dependencies.txt`).
4. Runs the required checks with the candidate's own dependencies:
   `ruff check` and the full `pytest` suite. Output is in `logs/`; skips are
   listed there and in the check summary, and do not count as passes of the
   skipped cases.
5. Checks readiness before any downtime: every pinned requirement is
   installed, and the production environment file (`--production-env`, default
   `/home/jannesi/Code/server/jannenkoti.env`) has `DATABASE_URL` (PostgreSQL),
   `SECRET_KEY`, `WEB_USERNAME`, `WEB_PASSWORD` and `DB_PATH`, enables
   `USE_SQLA_READS` and is not set to development. Only setting names are
   reported.
6. Compares migrations with the running code (`--production-code`, default
   `current/code`, or the production checkout before the first release). New
   revisions mean the release needs migrations and a rehearsal on disposable
   PostgreSQL. The comparison uses code, not the database; deployment must
   still confirm the database revision.

It exits `0` when only owner acceptance or the migration rehearsal remain,
`1` when installation, a check or readiness failed, and `3` when another
release operation holds the root. A failed or interrupted preparation stays
inspectable but is never deployable; preparing the revision again replaces it.
`--device-acceptance` makes real-device acceptance required too.

## Acceptance and candidate status

```bash
.venv/bin/python scripts/release.py accept <candidate> --scope feature --note "what you checked"
.venv/bin/python scripts/release.py accept <candidate> --scope device --note "heater relay observed"
.venv/bin/python scripts/release.py status            # all candidates
.venv/bin/python scripts/release.py status <candidate> [--json]
```

Feature acceptance is always required. Record acceptance only after checking
the candidate's revision in development; the command records your statement,
it does not observe devices.

`status` recomputes the code and dependency identities. Checks and acceptance
recorded against different identities are reported as `stale` and block
deployment, as do failed or missing checks, missing acceptance, readiness
problems, unknown migration needs and a missing rehearsal. Evidence is never
carried from one candidate to another, so a new commit needs its own checks and
acceptance.

## Deploying

```bash
.venv/bin/python scripts/release.py deploy <candidate>
```

Before any downtime, `deploy` refuses (exit `2`, nothing changed) unless the
candidate is deployable by `status <candidate>` and the production database
schema revision is exactly the candidate's migration head. It reads that
revision with the candidate's own dependencies and `DATABASE_URL` from
`--production-env`; an unreadable revision counts as unknown, never as
compatible. A candidate needing migrations instead requires a rehearsal bound
to the current baseline; deployment creates its backup before stopping services.

The maintenance window then:

1. stops `jannenkoti.service` and `esp32_ws.service` together and confirms
   both are inactive, so workers of two releases never overlap;
2. applies required rehearsed migrations (if any), then points `current` at the candidate and `previous` at the release that ran
   before (the first deployment has no previous release);
3. starts both services, which run from `current` (see the units below);
4. waits up to `--readiness-timeout` seconds (default 60) for readiness.

Expect the app and the device gateway to be unavailable for the stop, the start
and the readiness wait, typically well under a minute. Devices reconnect to the
gateway on their own; queued heater commands are not replayed by the release.

The service manager is `sudo systemctl` (`--service-manager` substitutes it).
The app and gateway are probed at `--app-url` (default `http://127.0.0.1:5555`)
and `--gateway-url` (default `http://127.0.0.1:5556`).

## Readiness

A release is ready only when, within the timeout:

- both units report active (not sufficient on its own);
- the app's `/healthz` reports the release's revision and `database: ok`; the
  app answers it after a trivial database query;
- the gateway's `/health` reports the same revision and `status: healthy`.

Each process reports the revision recorded beside the code it was started from,
so a stale process or a unit that still points elsewhere is not ready. The
probes only read; they trigger no device command, notification or automation.

```bash
.venv/bin/python scripts/release.py status            # current, previous, last operation
.venv/bin/python scripts/release.py status --live     # plus a readiness probe; exit 1 if not ready
```

## Failure and recovery

| Exit | Meaning |
| --- | --- |
| `0` | Ready on the requested release |
| `1` | Preparation/database operation failed, or deployment failed readiness and compatible code was recovered |
| `2` | Refused before any change |
| `3` | Another release operation holds the release root |
| `4` | Production is not verified ready; follow the printed guidance |

When readiness fails, `deploy` returns to the previous release automatically
only if the database schema is still exactly what that release expects; the
candidate stays under `releases/` for inspection. If compatibility is unknown,
or no previous release exists (the first deployment), it stops with exit `4`,
leaves `current` on the failed candidate and prints what to inspect. It never
changes the schema, downgrades the database or restores a backup.

```bash
.venv/bin/python scripts/release.py recover    # return to the previous release
.venv/bin/python scripts/release.py restart    # restart the current release and verify it
```

`recover` swaps `current` and `previous` with the same stop, start and
readiness sequence, after the same database compatibility check; it refuses
(exit `2`) when compatibility is unknown or not established. Restoring a
database backup is a separate owner decision: it discards writes made after the
backup and is never part of code recovery.

To diagnose, read `operation.json` in the release root (steps, readiness
problems, guidance), run `status --live`, and read
`journalctl -u jannenkoti.service -u esp32_ws.service -n 200`.

Only `deploy`, `recover` and `restart` stop or start production services, and
only one release operation runs at a time. The legacy `scripts/restart.sh` and
`scripts/restart-no-log.sh` run only from the production checkout
(`/home/jannesi/Code/server`) while no release is deployed; from any other
checkout, or once `current` exists, they refuse (exit `2`) and point to
`release.py restart`. Development commands never restart production.

## First cutover (owner-authorized)

Production still runs from `/home/jannesi/Code/server` with its `.venv`. The
release-layout units are in
[`systemd/jannenkoti.service.example`](systemd/jannenkoti.service.example) and
[`systemd/esp32_ws.service.example`](systemd/esp32_ws.service.example): both
run from `current`, keep the app's single Eventlet worker and the gateway's
sync worker with 16 threads, and keep using the existing environment files
outside the release. Nothing here has been installed. When you decide to switch:

1. Prepare and accept a schema-compatible candidate; `status <candidate>` must
   say deployable. Its `requirements.txt` must include the gateway's
   dependencies (`flask-sock`).
2. Check persistent state lives outside the release: `DB_PATH` and
   `VILLENKOTI_DB_PATH` must be absolute paths outside `releases/`. KFactor
   test-mode output keeps its absolute path in the old checkout.
3. Save the legacy units: copy `/etc/systemd/system/jannenkoti.service` and the
   file that `/etc/systemd/system/esp32_ws.service` links to (in the production
   checkout) somewhere outside both checkouts.
4. Install the two example units as `/etc/systemd/system/jannenkoti.service`
   and `/etc/systemd/system/esp32_ws.service` (replace the symlink), keep the
   `jannenkoti.service.d` drop-in, and run `sudo systemctl daemon-reload`.
   The running services keep their old code until they are stopped.
5. Run `release.py deploy <candidate>` and confirm `status --live`.

If that first deployment fails, there is no previous release: reinstall the
saved legacy units, run `sudo systemctl daemon-reload` and restart both
services from the production checkout.

## Cleanup

Each successful preparation keeps the newest three candidates (`--keep N`).
`prune --keep N` removes older ones explicitly. Pruning never removes the
`current` or `previous` release, `backups/`, or directories without a release
record. To reclaim more space, remove failed candidates with `prune` or delete
their `releases/<candidate>` directory after checking `status`.

## Verification

[`tests/test_release_preparation.py`](../../tests/test_release_preparation.py)
runs preparation against temporary Git checkouts, a temporary release root and
a stand-in production environment, with a stand-in dependency installer (no
network) and a recording `systemctl`.
[`tests/test_release_deployment.py`](../../tests/test_release_deployment.py)
deploys, recovers and restarts temporary releases against disposable PostgreSQL
with a recording service manager that runs each candidate's stand-in app and
gateway as isolated processes from `current`.
[`tests/test_release_health.py`](../../tests/test_release_health.py) covers the
real app and gateway health endpoints. The real `pip` installation, a full suite
inside a real candidate, the real units and `systemctl` are exercised only by an
actual release.


## Migration rehearsal and backup

A migration-bearing candidate must include two reviewed, committed synthetic SQL
fixtures: `migrations/rehearsal.sql` seeds representative application rows at the
production baseline; `migrations/rehearsal-verify.sql` checks preserved state at
the candidate head. Verification must raise an SQL error when an invariant fails
(for example in a PostgreSQL DO block); merely printing rows is not a check.
Fixtures are trusted candidate code, never production data. If executable
Alembic infrastructure or these fixtures are missing, rehearsal refuses safely.
Do not claim migration release readiness until suitable fixtures are committed.

Provision two **empty disposable PostgreSQL databases** on a private Unix socket,
with names starting `release_test_`. Each separate environment file contains
`DATABASE_URL` and `RELEASE_DISPOSABLE=yes`. The tool rejects TCP and system
socket targets, production database names, nonempty targets and identical URLs.
Disposable provisioning is an explicit operator step; the tool never creates a
cluster, installs software or drops a rehearsal database for reuse.

```bash
.venv/bin/python scripts/release.py rehearse <candidate> \
  --disposable-env /path/to/rehearsal.env \
  --restore-env /path/to/restore-check.env
```

Rehearsal checks the actual production Alembic baseline against the reference
code, creates that baseline using candidate migrations, seeds synthetic state,
dumps it with real `pg_dump`, restores it into the second disposable database,
and compares logical dumps. Both databases are migrated and the committed
verification SQL runs against both. New connections verify persisted state.
This is representative synthetic coverage, not a clone or proof about all
possible production rows. One linear Alembic baseline is supported; ambiguous
or multiple heads refuse rather than guessing. Applied migration files must remain
byte-for-byte identical to the reference history; changed or missing historical
migrations block preparation, rehearsal and deployment even when IDs are retained. Keep the databases for inspection
and remove their disposable cluster manually when finished.

Evidence records candidate code/dependencies, baseline revisions and reference
migration identity. Changed dependencies, candidate code, reference migrations or production
baseline invalidate the rehearsal. Normal candidate status reports the result.

`deploy` creates a consistent custom-format PostgreSQL dump before downtime,
with directory mode 0700 and file mode 0600. Failure blocks before stopping
services or applying migrations. The backup filename, checksum, database target
identity and baseline are retained in the candidate record; credentials are not
recorded. Backups and candidate records owning backups are never automatically
pruned or replaced. Review disk space and remove obsolete copies explicitly only
when another recovery basis exists.

After both services stop, the baseline is checked again, migrations run with the
candidate interpreter, and the resulting revision must match the candidate.
Readiness uses one monotonic deadline across service probes, HTTP headers/bodies
and retry pauses, and health responses must use HTTP 200. Database subprocesses
have a 180-second bound, connection timeouts, a 120-second
statement timeout and a 10-second lock timeout. Normal readiness remains bounded.
A migration failure, including a possibly partial application, keeps services
stopped and forbids automatic code recovery. After a successful migration but
failed readiness, the prior release is recovered only if the actual database
revision matches its expected head. Unknown or incompatible state requires
inspection and a fix-forward or explicit restoration decision.

## Explicit database restoration

Restoration is destructive: **all writes since the named backup can be lost**.
`deploy`, `recover`, `restart` and generic acceptance never authorize restoration.
After a separate explicit owner decision, name a retained backup and acknowledge
the loss of later writes:

```bash
.venv/bin/python scripts/release.py restore \
  --backup <exact-backup-name.dump> --acknowledge-data-loss
```

The command verifies the retained checksum and original database target, stops
both services, recreates the application's public schema and restores the dump.
It verifies the saved baseline and leaves services stopped. It does not silently
select older code or restart automation. Inspect status, then use compatible code
recovery or fix forward. A failed restore may already have changed data: services
remain stopped and the operation record states the failure. This workflow assumes
the application's ordinary public schema; bespoke external schemas/roles require
a separately reviewed restore procedure. Backups contain sensitive household data
and must remain private. Real host provisioning, migration and restoration still
require owner authorization; automated checks only use disposable targets.
