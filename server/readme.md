# Jannenkoti – Smart Home Dashboard

A comprehensive home automation server for Raspberry Pi (or any Linux host). Real-time dashboard, API, and WebSocket gateway for monitoring and controlling your home devices.

---

## Features

### 🚗 Car Heater Control
- **Remote control** of Shelly-based car block heater
- **Ready-by scheduling** – set when you need the car warm, system calculates optimal start time
- **Keep-at-temperature mode** – maintain cabin temperature automatically
- **kFactor calibration** – learns your car's thermal characteristics for accurate predictions
- **Real-time power monitoring** – voltage, current, energy consumption
- **Weather integration** – displays outside temperature and wind from FMI (Finnish Meteorological Institute)

### 🌡️ Temperature Monitoring
- **Multi-room sensors** via ESP32 nodes (temperature + humidity)
- **BMP pressure sensor** support (barometric pressure, altitude)
- **Historical charts** with date filtering
- **Real-time updates** via WebSocket

### ❄️ Smart AC / Thermostat
- **Tuya/Smart Life integration** – control AC via cloud API
- **Local thermostat loop** – maintains target temperature automatically
- **Sleep schedule** – automatic night mode with configurable times
- **Heating/cooling rate analytics** – tracks °C/h and estimated power consumption
- **Mode control** – cold, dry, fan modes with fan speed adjustment

### 🖨️ 3D Printer Integration
- **Live camera feed** via HLS streaming
- **Print progress tracking** – layer info, time remaining, print speed
- **Printer controls** – pause, resume, stop, home
- **Timelapse capture** – automated frame capture during prints
- **G-code queue management**

### 💡 Philips Hue Lights
- **Time-based routines** – automatic light schedules
- **Bridge integration** via local API

### 🍽️ Sodexo Lunch Menu
- **Daily menu scraping** from Sodexo Frami restaurant
- **Discord webhook** – automatic daily posts to Discord channel

### 🌐 NoVPN Device Bypass
- **Network configuration** for devices that need direct internet access
- **MAC-based device management**
- **DNS bypass options**

### 🔐 Authentication & Security
- **Multi-user support** with admin/root-admin roles
- **Temporary users** with expiration dates
- **API key management** for external integrations
- **Rate limiting** with Redis backend
- **CSRF protection** on all forms

### 💳 YNAB Categorizer
- **Manual categorization queue** for uncategorized YNAB transactions
- **Payee-based category suggestions** from local usage history
- **Explicit apply workflow** (no automatic category changes)
- **Bootstrap learning** from 24 months of categorized history
- **Configurable queue filter mode** (`strict`, `skip_transfers`, `all_uncategorized`)
- **Starting Balance transactions are always excluded** from categorization queue
- **Queue rows show EUR amounts and `DD-MM-YYYY` dates**, row click toggles selection, rows show source/target account context (not raw transaction UUID), selected actions appear in a floating apply bar, and advanced settings are tucked into a collapsible panel (reconciled + queue time limit)
- **Default fallback category** can be configured for no-suggestion payees; category dropdowns prioritize top 10 locally most-used categories and hide hidden YNAB categories
- **Approval view** for unapproved transactions with bulk `Approve selected` / `Approve visible` actions
- **QoL quick actions**: apply suggestions in bulk (High by default, optional Medium), select/clear visible, queue text filter, per-group one-click suggestion apply, recent category chips, and keyboard shortcuts

### 📊 System Features
- **Real-time WebSocket updates** – instant UI refresh without polling
- **Application logging** – error logs stored in database, viewable in UI
- **Mobile-friendly UI** – responsive design for all screen sizes
- **REST API** – full programmatic access to all features

---

## Quick Start

```bash
# Clone and setup
git clone https://github.com/jannesii/raspberry-pi-smart-home.git
cd raspberry-pi-smart-home

# Create virtual environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure (minimum required)
export SECRET_KEY="$(openssl rand -hex 32)"
export DB_PATH="/tmp/jannenkoti.db"
export WEB_USERNAME=admin
export WEB_PASSWORD=changeme

# Run
python run.py
```

Browse to http://127.0.0.1:5555 and log in.

---

## Development Notes

- `scripts/restart.sh` activates `.venv` and runs `pre-commit run --all-files`; the service restarts only if checks pass.
- YNAB categorizer is Root-Admin only and configured via `YNAB_API_KEY` + `YNAB_BUDGET_ID`.
- YNAB HTTP behavior can be tuned with `YNAB_HTTP_TIMEOUT_S` and `YNAB_HTTP_RETRIES`.
- KFactor cooldown persistence uses a dedicated `car_heater_kfactor_cooldown` table.
- KFactor calibrator weather parsing reads `WeatherValue.value` for t2m and ws_10min.
- Car heater settings modules export UI helpers via `window.CarHeaterSettings` inside IIFEs.
- KFactor snapshots load the last session summary from the database on startup.
- KFactor calibrator restores passive recording alongside autonomous mode, grace-sample debouncing, overnight windows, and power fallback.
- KFactor active params selection now honors bucketed parameters with any-wind fallback and bucket coverage gating.
- KFactor snapshots now include legacy UI fields: live_session, recent_sessions, bucket_coverage, statistics, and expanded constants.
- KFactor uses separate autonomous/passive cooldowns and records prediction outcomes in the DB.
- kFactor auto-calibration toggle now reads a consistent `autonomous_enabled` flag in initial and Socket.IO status payloads.
- kFactor socket control parses boolean values explicitly to avoid `"false"` becoming `True`.
- kFactor calibrator refreshes config from DB during tick/snapshot to keep multi-worker state in sync.
- Disabling kFactor autonomous mode now cancels active autonomous state and syncs config updates across submodules.
- kFactor controller auto-recovers from PostgreSQL sequence drift on insert (realigns `id` sequence and retries once).
- Car heater "Get Logs" now streams ESP32 log snapshots to a floating UI window via `car_heater_logs` (WS + HTTP fallback).
- Car heater settings include a kFactor cooldown reset button.
- Live kFactor session panel updates asynchronously via Socket.IO status events.
- Logging control now logs handler level updates when applied.
- kFactor session rejection logs now include specific rejection reasons and thresholds.
- kFactor autonomous sessions use separate minimum duration and informativeness thresholds.
- Logs can be mirrored to Postgres by setting `USE_SQLA_LOG_WRITES=1` (requires `DATABASE_URL`).
- Added a controller helper to delete duplicate log rows in Postgres (`delete_duplicate_logs_postgres`).
- Chart modal outdoor temperature range selection now supports multiple location aliases (case-insensitive, substring match).
- Temperature tiles now support multiple outside locations for grouping and outside-range stats.
- SQLAlchemy Core schema + engine are wired in parallel (phase-in for Alembic).
- Alembic migrations are staged (baseline + kFactor result FK) and now target Postgres (SQLite-specific logic removed).
- SQLite connections (sqlite3 + SQLAlchemy/Alembic) enable the foreign_keys PRAGMA.
- Logs reads can be served via SQLAlchemy when `USE_SQLA_READS=1` (Postgres) while writes remain on SQLite.
- Security headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) are applied by default, with CDN allowlist for Chart.js/HLS/Socket.IO.
- API CORS is now opt-in via `API_ALLOWED_ORIGINS`; same-origin browser use works without it, and cross-origin API use should be explicitly allowlisted.
- API key query-string auth is disabled by default; use `Authorization: Bearer ...` or `X-API-Key`, and only set `ALLOW_API_KEY_QUERY_PARAM=1` for legacy compatibility.
- Hue bridge HTTP calls now honor `HUE_HTTP_TIMEOUT_S` (default `5.0`) to avoid hanging worker threads on slow bridge responses.
- Legacy-to-SQLAlchemy controller migration helpers are available for 3D, auth, AC, car heater, and ready-by data (`migrate_*_to_pg`).
- `scripts/restart.sh` retries `pre-commit run --all-files` once before aborting.
- AC controller methods migrated to SQLAlchemy Core (AC event logging, thermostat configuration).
- Hue exposes an API key protected `POST /hue/set_current_color` endpoint to apply the current time-slot color immediately.
- Hue service initialization now fails safe when `HUE_BRIDGE_IP` or `HUE_USERNAME` is missing.
- Test bootstrap now works from the repo root without manually exporting `PYTHONPATH`.
- `esp32_ws.service` uses `KillSignal=SIGKILL` with a 1 second stop timeout so Gunicorn-backed WebSocket restarts are immediate instead of waiting on long-lived connections.
- ESP32 WebSocket ingress now logs throttled status summaries in `esp32_ws` and matching Redis-bridge summaries in `jannenkoti`, plus low-level WS ping receipts in `esp32_ws`, to verify live device traffic.
- `esp32_ws` now initializes its Redis manager at module import so Gunicorn `main:app` runs the Redis publish/subscribe bridge instead of only the `__main__` code path.
- Car heater UI now consumes normalized live status over Socket.IO from the Redis bridge, with `GET /api/car_heater/status` as an HTTP fallback returning the same payload shape.
- Car heater Details now show websocket-originated status with `source=WS`, and live timestamps render with seconds in 12-hour format.
- Car heater command events now distinguish `queued` vs ESP `executed` results, and the ESP sends an immediate follow-up status frame after websocket command execution so the UI updates without waiting for the next periodic status tick.

---

## Documentation

- [Technical Guide](docs/technical-guide.md) – deployment, configuration, API reference
- [Guide](docs/guide.md) – operational commands, migrations, backups
- [DB Next Steps](docs/db_next_steps.md) – remaining work for the Postgres transition
- [AGENTS.md](AGENTS.md) – AI development guidelines

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11, Flask, Flask-SocketIO, Eventlet |
| Database | SQLite (auto-migrated) |
| Frontend | Vanilla JS (ES6+), CSS3 Grid/Flexbox |
| Real-time | Socket.IO |
| Auth | Flask-Login, session cookies, CSRF |

---

## License

MIT License – © 2025–2026 Janne Siirtola
