# Database operations

## Connections and migrations

Follow [production setup](../../readme.md#production) to configure PostgreSQL
and apply `alembic upgrade head` from `server/`. `DATABASE_URL` is the SQLAlchemy
connection URL. `DB_PATH` must also identify a writable SQLite file because
bootstrap still constructs the legacy manager.

[migrations/env.py](../../migrations/env.py) loads `../jannenkoti.env` with
`override=True`, then chooses `DATABASE_URL` before the `alembic.ini` URL.
This differs from `run.py`, which loads `server/jannenkoti.env` when launched
from `server/`. Check the parent environment file before migration commands;
an exported connection can otherwise be overridden.

The real `alembic.ini` is ignored. Copy [the example](../../alembic.ini.example)
once and keep actual credentials in the environment.

For a schema change, update [schema.py](../../app/core/schema.py), generate a
revision, and review its upgrade/downgrade operations before applying it:

```bash
# From server/, with the virtual environment active and DATABASE_URL set.
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
