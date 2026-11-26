-- Production read-only browsing/query role (reviewable; not applied by any tool).
--
-- Run as the PostgreSQL administrator against the application database, naming
-- the read-only role, the role that owns and migrates the application tables,
-- and the database. Repeatable; set the password afterwards with \password.
--
--   psql -X -d jannenkoti -v readonly_role=jannenkoti_readonly \
--        -v app_role=<application owner> -v database=jannenkoti -f readonly.sql
--
-- Read-only is enforced by privileges: the role holds only CONNECT, schema
-- USAGE and SELECT. Its default_transaction_read_only setting is defence in
-- depth; a session can lift it, but still lacks any write privilege.
\set ON_ERROR_STOP on

\if :{?readonly_role}
\else
  \warn 'set -v readonly_role=<name>'
  \quit
\endif
\if :{?app_role}
\else
  \warn 'set -v app_role=<application table owner>'
  \quit
\endif
\if :{?database}
\else
  \warn 'set -v database=<application database>'
  \quit
\endif

BEGIN;

SELECT set_config('jannenkoti.readonly_role', :'readonly_role', true),
       set_config('jannenkoti.app_role', :'app_role', true),
       set_config('jannenkoti.database', :'database', true)
\g /dev/null

DO $$
BEGIN
  IF current_database() <> current_setting('jannenkoti.database') THEN
    RAISE EXCEPTION 'connected to %, expected %; connect to the application database',
      current_database(), current_setting('jannenkoti.database');
  END IF;
  IF current_setting('jannenkoti.readonly_role') = current_setting('jannenkoti.app_role') THEN
    RAISE EXCEPTION 'the read-only role must differ from the application role';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = current_setting('jannenkoti.app_role')) THEN
    RAISE EXCEPTION 'application role % does not exist', current_setting('jannenkoti.app_role');
  END IF;
END
$$;

SELECT format('CREATE ROLE %I LOGIN', :'readonly_role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'readonly_role')
\gexec

ALTER ROLE :"readonly_role" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE :"readonly_role" IN DATABASE :"database" SET default_transaction_read_only = on;
ALTER ROLE :"readonly_role" IN DATABASE :"database" SET statement_timeout = '30s';
ALTER ROLE :"readonly_role" IN DATABASE :"database" SET idle_in_transaction_session_timeout = '60s';

GRANT CONNECT ON DATABASE :"database" TO :"readonly_role";
GRANT USAGE ON SCHEMA public TO :"readonly_role";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"readonly_role";
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO :"readonly_role";
-- Tables and sequences the application role creates later, e.g. by migrations.
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_role" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"readonly_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_role" IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO :"readonly_role";

-- Fail closed if inherited PUBLIC privileges would still let the role write.
DO $$
DECLARE
  reader text := current_setting('jannenkoti.readonly_role');
  writable text;
BEGIN
  IF has_database_privilege(reader, current_database(), 'CREATE') THEN
    RAISE EXCEPTION '% can create schemas; revoke CREATE on the database from PUBLIC', reader;
  END IF;
  SELECT string_agg(quote_ident(nspname), ', ') INTO writable FROM pg_namespace
  WHERE nspname NOT LIKE 'pg\_%' AND nspname <> 'information_schema'
    AND has_schema_privilege(reader, oid, 'CREATE');
  IF writable IS NOT NULL THEN
    RAISE EXCEPTION '% can create in schemas %; revoke CREATE on them from PUBLIC', reader, writable;
  END IF;
  SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ') INTO writable
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'
    AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
    AND (pg_get_userbyid(c.relowner) = reader
      OR has_table_privilege(reader, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      OR (c.relkind = 'S' AND has_sequence_privilege(reader, c.oid, 'USAGE,UPDATE')));
  IF writable IS NOT NULL THEN
    RAISE EXCEPTION '% can modify or owns %; revoke those privileges first', reader, writable;
  END IF;
  SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ') INTO writable
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
    AND pg_get_userbyid(c.relowner) <> current_setting('jannenkoti.app_role');
  IF writable IS NOT NULL THEN
    RAISE WARNING 'future-table grants follow only %; also owned by other roles: %',
      current_setting('jannenkoti.app_role'), writable;
  END IF;
END
$$;

COMMIT;
