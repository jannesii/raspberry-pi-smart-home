-- Development PostgreSQL role and database (reviewable; not applied by any tool).
--
-- Review, then run once as the PostgreSQL superuser, choosing a new password:
--   sudo -u postgres psql -v dev_password='<new password>' \
--     -f docs/operations/development-host/postgresql.sql
--
-- The comment on the database is the marker scripts/dev.py requires before
-- setup or reset touch it; no other database is ever migrated or reset.
\set ON_ERROR_STOP on

CREATE ROLE jannenkoti_dev LOGIN PASSWORD :'dev_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE DATABASE jannenkoti_dev OWNER jannenkoti_dev;
COMMENT ON DATABASE jannenkoti_dev IS 'jannenkoti development';

-- Verify the development role cannot read production tables. Expect 0.
-- (Replace jannenkoti if the production database has another name.)
\connect jannenkoti
SELECT count(*) AS production_tables_readable_by_dev
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND has_table_privilege('jannenkoti_dev', c.oid, 'SELECT');
