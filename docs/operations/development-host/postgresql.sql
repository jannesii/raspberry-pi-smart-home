-- Development PostgreSQL role and database (reviewable; not applied by any tool).
--
-- Run once as the local PostgreSQL administrator, then set the password
-- interactively with psql's \password jannenkoti_dev.
--
-- The comment on the database is the marker scripts/dev.py requires before
-- setup or reset touch it; no other database is ever migrated or reset.
\set ON_ERROR_STOP on

CREATE ROLE jannenkoti_dev LOGIN
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE DATABASE jannenkoti_dev OWNER jannenkoti_dev TEMPLATE template0;
COMMENT ON DATABASE jannenkoti_dev IS 'jannenkoti development';
REVOKE ALL ON DATABASE jannenkoti_dev FROM PUBLIC;
