# DB Migration Next Steps

Work remaining for the SQLite → Postgres transition (no ORM).

---

## 1) Data migration
- Controller-level SQLite → SQLAlchemy migration helpers exist for 3D/auth/AC/car-heater/ready-by data; keep them working as smoke-checked import paths.
- Write a one-time SQLite → Postgres import script (table-by-table) or compose one from the controller migration helpers.
- Migrate low-risk tables first (logs, api_keys), then car_heater_* tables.
- Validate row counts + spot-check latest rows per table.

---

## 2) Read path expansion (SQLAlchemy Core)
- Extend SQLAlchemy read paths beyond logs (e.g., api_keys, status, sensors).
- Add parity logging for critical endpoints during transition.
- Keep `USE_SQLA_READS=1` gating until verified.

---

## 3) Write path migration
- Start dual-write for logs (SQLite + Postgres).
- Move writes table-by-table once reads are stable.
- Add rollback switch to fall back to SQLite.

---

## 4) Cutover
- Switch runtime reads/writes fully to Postgres.
- Deprecate `DatabaseManager._create_tables()` in production path.
- Keep SQLite only for local/dev unless explicitly needed.

---

## 5) Operations
- Add scheduled `pg_dump` backup + rotation.
- Add health checks (DB connectivity, migration head).
- Document new environment variables and commands.

---

## 6) Alembic hygiene
- Generate new migrations for Postgres schema changes.
- Avoid mixing SQLite-only behavior in new migrations.
