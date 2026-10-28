# Release preparation

Production runs an exact committed revision from a dedicated release location,
separate from working checkouts
([ADR 0004](../adr/0004-isolate-development-effects-from-household-operation.md)).
This guide covers preparing and accepting a candidate. Deployment, migration
backups and recovery are not automated yet; preparing a candidate never
migrates the production database, restarts a service or switches the running
release.

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
   `SECRET_KEY`, `WEB_USERNAME`, `WEB_PASSWORD` and `DB_PATH` and is not set
   to development. Only setting names are reported.
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

## Acceptance and status

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

## Cleanup

Each successful preparation keeps the newest three candidates (`--keep N`).
`prune --keep N` removes older ones explicitly. Pruning never removes the
`current` or `previous` release, `backups/`, or directories without a release
record. To reclaim more space, remove failed candidates with `prune` or delete
their `releases/<candidate>` directory after checking `status`.

## Verification

[`tests/test_release_preparation.py`](../../tests/test_release_preparation.py)
runs the command against temporary Git checkouts, a temporary release root and
a stand-in production environment, with a stand-in dependency installer (no
network) and a recording `systemctl`. The real `pip` installation and a full
suite inside a real candidate are exercised only by an actual preparation.
