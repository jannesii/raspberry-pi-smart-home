# Database Next Steps

The app now uses SQLAlchemy Core for runtime database access. Production
should run against PostgreSQL via `DATABASE_URL`; SQLite remains useful for
tests, local development, and one-time legacy import sources.

---

## 1) Legacy data import
- Keep controller migration helpers idempotent for 3D/auth/AC/car-heater/ready-by data.
- Add a one-time import runner that composes the helpers against `DB_PATH` → `DATABASE_URL`.
- Validate row counts, latest timestamps, admin users, API keys, and car-heater/kFactor samples.
- Realign PostgreSQL serial sequences after importing rows with explicit `id` values.

---

## 2) Production cutover cleanup
- Remove obsolete phase-in flags once confirmed unused in deployment (`USE_SQLA_READS`, log mirror notes).
- Keep `DatabaseManager` only for legacy import/local compatibility until startup no longer instantiates it.
- Make `DATABASE_URL` required for production service units and keep `DB_PATH` documented as a legacy bootstrap value.
- Smoke-check critical pages after every cleanup: auth, car heater, temperatures, logs, YNAB, Hue.

---

## 3) Alembic and schema hygiene
- Generate Alembic migrations for all new PostgreSQL schema changes.
- Keep timestamp/date columns as ISO text when SQLite test compatibility matters.
- Avoid adding SQLite-only migration behavior to PostgreSQL migrations.
- Add a migration-head check to deployment or health diagnostics.

---

## 4) Operations
- Add scheduled `pg_dump` backups with retention and restore documentation.
- Add health checks for PostgreSQL connectivity, migration head, Redis, and socket bridge freshness.
- Document recovery steps for sequence drift and failed imports.
- Track slow or high-volume queries before adding indexes.

---

## 5) Test coverage
- Keep repo-root pytest bootstrap working without manual `PYTHONPATH`.
- Add focused controller tests for PostgreSQL-compatible SQLAlchemy paths where regressions are likely.
- Run import helpers against temporary SQLite/PostgreSQL fixtures before relying on them for production data.

---

## 6) Documentation
- Keep README, AGENTS.md, and docs/technical-guide.md aligned on the database architecture.
- Mark SQLite references as test/local/legacy unless they describe an intentional runtime fallback.
- Keep operational commands paste-ready and environment-variable names exact.
