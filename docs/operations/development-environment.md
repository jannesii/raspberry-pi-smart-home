# Development environment

One on-demand development instance can run beside production on the Pi
([ADR 0004](../adr/0004-isolate-development-effects-from-household-operation.md)).
It runs one selected feature worktree with its own dependencies, PostgreSQL
database and role, Redis server, configuration and writable files, and is
browsed at `https://dev.jannenkoti.com` through the existing Cloudflare Tunnel.

## Daily use

Run from any checkout; the selected worktree is what runs:

```bash
dev() { ~/Code/server/.venv/bin/python ~/Code/server-worktrees/<any>/scripts/dev.py "$@"; }
dev setup --worktree ~/Code/server-worktrees/<feature>   # select, install, migrate, sample data
dev start          # background app on 127.0.0.1:5557 plus its own Redis
dev status         # worktree, branch @ commit, state, URLs
dev stop
dev reset --yes    # stopped instance only: back to the sample state
```

Every command reads `~/.config/jannenkoti-dev/dev.env` unless `--env-file` is
given, and `/home/jannesi/Code/server/jannenkoti.env` only to detect shared
targets (`--production-env`). Commands print setting names and paths, never
values.

| Exit | Meaning |
| --- | --- |
| `0` | Done; for `status`, running and answering |
| `1` | Operation failed (`status`: running but unhealthy); see the named log |
| `2` | Refused: unsafe, ambiguous or missing target; nothing was changed |
| `3` | `status`: not running |
| `4` | Another `setup`/`start`/`stop`/`reset` is in progress |

- **setup** refuses the production checkout, non-worktrees and worktrees that
  predate this contract (no `app/environment.py` or
  `app/development_sample.py`); merge the development environment branch into
  older feature branches first. It installs the worktree's `requirements.txt`
  into `$DEV_STATE_DIR/venv` (skipped while the file is unchanged), applies
  migrations and adds the sample data. It keeps existing development data.
- **start** refuses a second instance, a busy port or dependencies changed
  since setup. It runs a private `redis-server` (no persistence) on the
  `REDIS_URL` port, then one Eventlet Gunicorn worker, and waits for `/login`.
  Logs: `$DEV_STATE_DIR/instance/app.log`, `redis.log`, `setup.log`.
- **stop** signals only processes `start` recorded (matched by PID and start
  time), so it never stops production.
- **reset** needs `--yes` and a stopped instance. It drops and recreates the
  development database's `public` schema, deletes `$DEV_STATE_DIR` contents
  except `venv/` and `instance/`, migrates and reseeds. Redis is never
  persisted, so nothing queued earlier can be replayed.
- `scripts/restart.sh` and `restart-no-log.sh` restart production and refuse
  to run from any checkout except `/home/jannesi/Code/server`, and refuse
  everywhere once production runs from a release
  ([release guide](releases.md#failure-and-recovery)).

`run --env-file FILE` still runs the app of the checkout that contains the
script in the foreground, without the lifecycle (Ctrl+C stops it).

## Sample data

Setup and reset create the `WEB_USERNAME` login as Root-Admin and three
synthetic sensor locations named `Kehitys …` with fixed values
([`app/development_sample.py`](../../app/development_sample.py)). Production
accounts, schedules and data are never copied. Add a fixture there only when a
feature needs one, and keep it idempotent because setup runs it again.

## Browser access

Pages show a sticky **Kehitysympäristö** banner with the branch and commit, and
tab titles start with `[DEV]`. Development uses its own cookies
(`jannenkoti_dev_session`, `jannenkoti_dev_remember`, both `Secure` and
`HttpOnly`) because browsers share cookies between ports of one host. Socket.IO
accepts `https://dev.jannenkoti.com` and `http://127.0.0.1:$PORT` /
`http://localhost:$PORT` unless `ALLOWED_WS_ORIGINS` is set. Authentication,
Root-Admin checks, CSRF and the explicit API-key device exemptions are the same
as in production; the hostname is internet-reachable, so use a strong
development password.

## Provisioning (owner, once)

Nothing here is applied automatically. Review each step first.

1. PostgreSQL: review and run
   [`development-host/postgresql.sql`](development-host/postgresql.sql) as the
   PostgreSQL superuser with a new password. It creates the `jannenkoti_dev`
   role and database, adds the marker comment that setup and reset require, and
   prints how many production tables the role can read (expect `0`).
2. Configuration: `mkdir -p ~/.config/jannenkoti-dev ~/.local/state/jannenkoti-dev`,
   copy [`development-host/dev.env.example`](development-host/dev.env.example)
   to `~/.config/jannenkoti-dev/dev.env`, `chmod 600` it and replace every
   `CHANGE_ME` with values that differ from production.
3. Redis: no host service is needed; `start` runs development's own server on
   the `REDIS_URL` port (`6380` in the example).
4. Tunnel: the owner reports an existing ingress
   `dev.jannenkoti.com → http://localhost:5557` and its CNAME. Revalidate
   rather than recreate it: confirm the rule in the tunnel's active
   configuration (for example `cloudflared tunnel ingress rule
   https://dev.jannenkoti.com`), then `dev start` and open
   `https://dev.jannenkoti.com` on a computer and on the phone, log in, and
   check the banner. Record that result as commissioning evidence; automated
   tests do not reach the tunnel.

No systemd unit is needed: the instance runs on demand until `dev stop` or a
reboot.

## Required development settings

| Setting | Requirement |
| --- | --- |
| `DATABASE_URL` | The provisioned development database and role. Must not use the production database or role. |
| `REDIS_URL` | `redis://127.0.0.1:<port>` on a free port, never production's (`localhost:6379` when production sets none). `start` runs this server. A different `/N` database alone is not isolation: Pub/Sub ignores database numbers. |
| `RATE_LIMIT_STORAGE_URI` | Optional. Defaults to `REDIS_URL` and has the same Redis restriction. |
| `DEV_STATE_DIR` | Existing absolute directory; empty for the first setup, which marks it |
| `DB_PATH`, `VILLENKOTI_DB_PATH` | Inside `DEV_STATE_DIR` and not the production paths. KFactor test-mode output also goes to `DEV_STATE_DIR`. |
| `SECRET_KEY`, `WEB_PASSWORD` | Different from production |
| `WEB_USERNAME` | The dedicated development login, seeded as Root-Admin |
| `PORT` | Optional, default `5557` (the tunnel target); must not be `5555` or `5556` |
| `USE_SQLA_READS` | Forced to `1` by setup, reset and `start` |

Unix-socket directories, this host's name and its LAN addresses count as
`localhost` when comparing servers with production. When a setting is missing
or ambiguous, commands exit `2` and list setting names, never values. The app
makes the same checks when `APP_ENV=development` is started without the tool,
and never reads a checkout's `jannenkoti.env` in development.

## Effects in development

Credential-like values in the env file are not consent to use them. Startup
logs `<integration> disabled in development` for each one it keeps off:

- **Household control and automations:** AC thermostat and the Hue routine are
  not started. AC status reports `503`.
- **Notifications:** the alert webhook, alert batcher, Sodexo posts and medicine
  reminders are off. Sending or recording an alert returns not delivered; it is
  never reported as successful.
- **Scheduled external work and finance:** the weather observation and forecast
  archive is off. YNAB is off and its API reports `503`.
- **Host configuration:** network-bypass (NoVPN) changes are refused. The API
  returns `409 unavailable_in_development` and the settings page shows an
  error. The shared `devices.conf` is never rewritten and `novpn-master` is
  never restarted. Reading the device list still works.
- **Car heater:** commands and automations publish only to the development
  Redis. No gateway runs there, so commands stay queued and no device acts.
  On-demand weather forecast reads still reach FMI.

Browser Socket.IO, the ESP32 telemetry bridge, heater command publishing and rate
limits all use `REDIS_URL`.

## Verification

[`tests/test_development_launch.py`](../../tests/test_development_launch.py)
covers the startup contract and
[`tests/test_development_lifecycle.py`](../../tests/test_development_lifecycle.py)
the commands, invoked from outside the selected worktree, against disposable
PostgreSQL, Redis servers, a stand-in production environment and a substitute
dependency installer. Without those binaries the cases are skipped; the
browser flow is skipped without Playwright. The real `pip` installation path,
the tunnel and phone access are checked during commissioning.
