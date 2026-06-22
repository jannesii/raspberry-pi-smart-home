# Jannenkoti Smart Home Server

Jannenkoti is a personal home automation server for Linux and Raspberry Pi
deployments. It combines authenticated browser dashboards, Flask APIs,
Socket.IO updates, PostgreSQL persistence, and a separate Redis-backed
WebSocket gateway for ESP32 devices.

The repository is tailored to one home environment and its devices. Optional
integrations stay disabled when their credentials or host-side services are not
available.

## Current Features

### Climate monitoring and AC automation

- Multi-location ESP32 temperature and humidity telemetry
- Live room workspace with search, sorting, outside-sensor grouping, and
  per-day temperature and humidity charts
- FMI outside weather observations without an API key
- Local TinyTuya AC control using device ID, LAN IP, and local key
- Thermostat target, hysteresis, minimum cycle, stale-reading, and sensor
  selection controls with live partial updates that preserve form state
- Weekly sleep schedules, apply-to-all scheduling, cancellable temporary sleep
  overrides, and early-sleep mode
- Heating and cooling rate estimates based on sensor and AC event history
- BMP pressure, altitude, and temperature persistence/read APIs; the legacy
  BMP polling service is not started by the application bootstrap

### Car heater

- Shelly-backed heater control through the car-heater ESP32
- Live status, power, voltage, current, energy, cabin temperature, and FMI
  weather data
- Ready-by scheduling with learned start-time prediction
- Keep-at-temperature and battery charge modes
- kFactor passive and autonomous calibration with persisted sessions,
  parameters, cooldowns, and prediction outcomes
- Historical charts, command state, and ESP log snapshots
- WebSocket-first device commands and telemetry with authenticated HTTP
  fallback endpoints

### 3D printer dashboard

- HLS live video with captured-image fallback
- Printer temperatures, state, layer progress, remaining time, speed, and
  timelapse status
- Timelapse configuration and G-code history/autocomplete

Printer actions such as pause, resume, stop, home, and direct G-code execution
are currently disabled in the server handler.

### Administration and integrations

- Session authentication with regular, admin, and Root-Admin roles
- Temporary users with expiration times
- API key creation, one-time token display, verification, and revocation
- Database-backed application errors plus admin log browsing and live journal
  streaming
- Runtime logging-level controls
- Philips Hue local bridge routines and manual color application
- Scheduled Sodexo Frami menu posts to Discord
- MAC-based VPN and DNS bypass management for the host's `novpn-master`
  service
- Batched alert webhooks for car-heater and medicine notifications

### Root-Admin tools

- YNAB transaction review with local payee learning, ordered custom rules,
  queue filters, fallback categories, bulk categorize/approve operations, and
  a persisted test mode
- Medicine purchase tracking with per-purchase dosing snapshots, refill-date
  calculations, and bounded daily webhook alerts
- Network VPN/DNS bypass management

## Architecture

```text
Browser
  | HTTP + authenticated Socket.IO
  v
Main Flask app :5555 ---- SQLAlchemy Core ---- PostgreSQL
  |       |
  |       +---- Redis pub/sub ---- esp32_ws :5556 ---- ESP32 devices
  |
  +---- optional local/cloud APIs: TinyTuya, Hue, FMI, YNAB, Discord

Printer/timelapse client ---- authenticated Socket.IO ---- Main Flask app
```

The two real-time paths have different responsibilities:

- The main Flask-SocketIO server handles authenticated browser views and the
  printer/timelapse client.
- Temperature and car-heater ESP32 devices authenticate to the standalone
  `esp32_ws` service. Redis carries commands, telemetry, and command results
  between that service and the main app.
- API-key-protected HTTP endpoints remain available only where implemented as
  device or integration fallbacks.

## Requirements

- Python 3.11 or newer
- Redis at `redis://localhost:6379` for the default Socket.IO queue, rate-limit
  storage, and ESP32 bridge
- PostgreSQL for production
- SQLite for tests and lightweight local development
- A Linux host for systemd, journal streaming, HLS paths, and network-bypass
  integration

## Quick Start

```bash
git clone https://github.com/jannesii/raspberry-pi-smart-home.git
cd raspberry-pi-smart-home

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Ensure Redis is running before using real-time features.
redis-cli ping

# Minimal local development configuration. Omitting DATABASE_URL uses SQLite.
export SECRET_KEY="$(openssl rand -hex 32)"
export DB_PATH="/tmp/jannenkoti-dev.db"
export WEB_USERNAME="admin"
export WEB_PASSWORD="change-this-password"

python run.py
```

Open `http://127.0.0.1:5555`.

`run.py` also loads `jannenkoti.env` from the repository root. The file is
ignored by Git and can be used instead of shell exports.

Session cookies are always marked `Secure`. Use HTTPS for normal deployments;
browser behavior for secure cookies on plain HTTP localhost can vary.

## PostgreSQL Setup

Production runtime data should use `DATABASE_URL`. `DB_PATH` remains required
because the legacy SQLite manager is instantiated during startup, but it is not
the production runtime database when `DATABASE_URL` is set.

```bash
cp alembic.ini.example alembic.ini

export DATABASE_URL="postgresql+psycopg://user:password@localhost/jannenkoti"
export DB_PATH="/tmp/jannenkoti-legacy.db"

alembic upgrade head
python run.py
```

`alembic.ini` is intentionally ignored so local credentials cannot be
committed. Keep the database URL in the environment; the migration environment
prefers `DATABASE_URL` over the example URL.

`WEB_USERNAME` and `WEB_PASSWORD` are required at every startup. The configured
username is created as Root-Admin when absent and promoted to Root-Admin if it
already exists.

## Configuration

Environment values may be exported by the process manager or placed in
`jannenkoti.env`.

### Core

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | required | Flask session and CSRF signing key |
| `WEB_USERNAME` | required | Startup Root-Admin username |
| `WEB_PASSWORD` | required | Startup Root-Admin password when creating the user |
| `DB_PATH` | required | Legacy bootstrap path and SQLite development database |
| `DATABASE_URL` | SQLite from `DB_PATH` | PostgreSQL SQLAlchemy URL |
| `PORT` | `5555` | Local development server port |
| `ALLOWED_WS_ORIGINS` | `["http://127.0.0.1:5555"]` | JSON Socket.IO origin allowlist |
| `API_ALLOWED_ORIGINS` | `[]` | JSON CORS allowlist for `/api/*` |
| `RATE_LIMIT_STORAGE_URI` | `redis://localhost:6379` | Flask-Limiter storage |
| `RATE_LIMIT_WHITELIST` | `[]` | JSON list of exact IPs, wildcards, or CIDRs |
| `ALLOW_API_KEY_QUERY_PARAM` | `false` | Legacy query-string API key support |

`REDIS_URL` configures the ESP32 bridge and car-heater command publisher. The
main Socket.IO message queue currently uses local Redis directly.

### Optional integrations

| Integration | Variables |
|---|---|
| AC / TinyTuya | `AC_DEV_ID`, `AC_IP`, `AC_LOCAL_KEY`, `AC_TUYA_VERSION`, `AC_TUYA_TIMEOUT_S`, `AC_TUYA_PERSIST`, `AC_TUYA_RETRY_ATTEMPTS`, `AC_TUYA_RETRY_DELAY_S`, `AC_TUYA_STATE_SETTLE_S`, `WINTER_MODE` |
| Thermostat | `THERMOSTAT_LOCATION`, `ROOM_THERMAL_CAPACITY_J_PER_K` |
| Philips Hue | `HUE_BRIDGE_IP`, `HUE_USERNAME`, `HUE_HTTP_TIMEOUT_S` |
| YNAB | `YNAB_API_KEY`, `YNAB_BUDGET_ID`, `YNAB_HTTP_TIMEOUT_S`, `YNAB_HTTP_RETRIES` |
| Sodexo Discord post | `SODEXO_WEBHOOK_URL`, `SODEXO_POST_HOUR`, `SODEXO_POST_MINUTE` |
| Shared alerts | `ALERT_WEBHOOK_URL`, `ALERT_WEBHOOK_BATCH_SECONDS` |
| ESP32 bridge | `REDIS_URL`, `ESP32_WS_API_KEY`, `ESP32_WS_HOST`, `ESP32_WS_PORT` |
| Network bypass | `LEASES_FILE`, `STATIC_LEASES_CACHE`, `NOVPN_RESTART_TIMEOUT_S` |

The standalone ESP32 service uses its own environment file and process. See
[esp32_ws/README.md](esp32_ws/README.md).

TinyTuya calls are serialized across the thermostat and web handlers. Transient
malformed responses are retried after reconnecting; persistent mode is
recommended when the thermostat uses a short poll interval. Partial status
responses preserve the last known power state instead of reporting a false
transition. After an app-issued power command, contradictory TinyTuya power
status is ignored for `AC_TUYA_STATE_SETTLE_S` seconds (default 120) unless the
device confirms the new state sooner.

## Access and API Security

- Browser pages and browser-oriented APIs use Flask-Login session cookies.
- Admin pages cover users, logs, logging controls, API keys, and timelapse
  configuration.
- Root-Admin guards protect YNAB, medicine, and network-bypass operations.
- External device endpoints use `Authorization: Bearer <token>` or
  `X-API-Key: <token>`.
- API-key query parameters are rejected unless
  `ALLOW_API_KEY_QUERY_PARAM=1`.
- CSRF protects normal browser mutations. API-key device ingress and selected
  legacy routes are explicitly exempt.
- Cross-origin `/api/*` access is disabled unless the origin is listed in
  `API_ALLOWED_ORIGINS`.
- Security headers are applied to all responses.

The HTTP API is feature-specific rather than a complete public API for every
dashboard capability. Route definitions under `app/blueprints/api/` are the
authoritative endpoint inventory.

## Development

```bash
# Tests
.venv/bin/pytest -q

# Lint and formatting checks
.venv/bin/ruff check app tests run.py esp32_ws
.venv/bin/ruff format --check app tests run.py esp32_ws

# Repository hooks
.venv/bin/pre-commit run --all-files
```

Tests use SQLite through the same SQLAlchemy Core interfaces used with
PostgreSQL in production.

For production, run the main app with one Eventlet Gunicorn worker behind an
HTTPS reverse proxy. Long-lived background services and in-process singleton
state are not designed for multiple main-app workers. Run `esp32_ws` as its
separate Gunicorn/systemd service.

## Repository Layout

```text
app/
  blueprints/       HTML, API, auth, and Villenkoti routes
  core/             Controller mixins, dataclasses, schema, database engines
  services/         AC, car heater, Hue, weather, YNAB, alerts, Redis bridge
  sockets/          Browser/printer Socket.IO handlers
  static/           Vanilla JavaScript and CSS
  templates/        Jinja2 templates
esp32_ws/           Standalone ESP32 WebSocket-to-Redis service
ESP32_temperature/  PlatformIO temperature-sensor firmware
migrations/         Alembic migrations
tests/              Pytest suite
```

## Documentation

- [Technical Guide](docs/technical-guide.md)
- [ESP32 WebSocket Service](esp32_ws/README.md)
- [Temperature ESP32 Firmware](ESP32_temperature/README.md)
- [YNAB Categorizer](docs/ynab_categorizer.md)
- [Database Next Steps](docs/db_next_steps.md)
- [Development Guidelines](AGENTS.md)

## License

MIT License - Copyright 2025-2026 Janne Siirtola
