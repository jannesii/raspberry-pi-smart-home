# Development environment

One on-demand development instance can run beside production on the Pi
([ADR 0004](../adr/0004-isolate-development-effects-from-household-operation.md)).
This guide covers the safe startup contract. Setup, lifecycle, reset and
synthetic data are not automated yet.

## Starting

```bash
.venv/bin/python scripts/dev.py run --env-file ~/.config/jannenkoti-dev/dev.env
```

The command runs one Eventlet Gunicorn worker on `127.0.0.1:$PORT` (default
`5557`) in the foreground from the checkout that contains the script. Stop it
with Ctrl+C. It never runs the production restart scripts or `systemctl`.

The app receives only the development env file plus `PATH`, `HOME`, locale,
`TZ` and `TMPDIR`. Nothing else is inherited from the calling shell, and the
checkout's `jannenkoti.env` is never read in development. `APP_ENV` is forced
to `development`.

`--production-env` (default `/home/jannesi/Code/server/jannenkoti.env`) is read
only to detect shared targets. If it cannot be read, the launch is refused.

## Required development settings

| Setting | Requirement |
| --- | --- |
| `DATABASE_URL` | Dedicated PostgreSQL database with its own role. Must not use the production database or the production role. Apply migrations before starting. |
| `REDIS_URL` | A separate Redis server, never the production instance (`localhost:6379` when production sets none). A different `/N` database number is not enough: Pub/Sub ignores database numbers. |
| `RATE_LIMIT_STORAGE_URI` | Optional. Defaults to `REDIS_URL` and has the same Redis restriction. |
| `DEV_STATE_DIR` | Existing absolute directory for development's writable files |
| `DB_PATH`, `VILLENKOTI_DB_PATH` | Inside `DEV_STATE_DIR` and not the production paths. KFactor test-mode output also goes to `DEV_STATE_DIR`. |
| `SECRET_KEY`, `WEB_PASSWORD` | Different from production |
| `WEB_USERNAME` | The dedicated development login, seeded as Root-Admin |
| `PORT` | Optional, default `5557`; must not be `5555` or `5556` |

Unix-socket directories, this host's name and its LAN addresses count as
`localhost` when comparing servers with production.

Set `USE_SQLA_READS=1` so reads use the development PostgreSQL. When a setting
is missing or ambiguous, the launcher exits with status `2` and lists the
setting names, never their values. The app makes the same self-contained checks
when `APP_ENV=development` is started without the launcher.

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
limits all use `REDIS_URL`. Authentication, Root-Admin checks and CSRF behave
the same as in production.

## Verification

[`tests/test_development_launch.py`](../../tests/test_development_launch.py)
runs the command against disposable PostgreSQL, separate Redis servers and a
stand-in production environment; without those binaries its cases are skipped.
