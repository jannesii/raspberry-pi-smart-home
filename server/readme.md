# Jannenkoti Smart Home Server

A personal home automation dashboard for Linux and Raspberry Pi. Jannenkoti
collects sensor readings, controls home devices, and provides authenticated
browser tools for monitoring and administration. It is tailored to one home;
device integrations need their own hardware, credentials, and configuration.

## What it does

- **Climate:** live room temperatures and humidity, history charts, FMI weather,
  and local TinyTuya AC control with thermostat and sleep schedules.
- **Car heater:** live power/status, ready-by scheduling, keep-at-temperature,
  battery charge mode, and learned heating-time estimates.
- **3D printer:** live video, progress, and timelapse monitoring. Printer control
  commands such as pause, home, and direct G-code execution are disabled.
- **Administration:** users and API keys, logs, Hue lighting, webhook alerts,
  Sodexo menu posts, and host-specific VPN/DNS bypass controls.
- **Root-Admin tools:** YNAB transaction review and approval, medicine purchase
  and refill tracking, and network-bypass management.

## Architecture

```text
Browser / printer client ── HTTP + Socket.IO ── Flask app :5555
                                                   ├── SQLAlchemy Core ── PostgreSQL
                                                   └── Redis ── esp32_ws :5556
                                                                    │
                                                               WebSocket
                                                                    │
                                                               ESP32 devices
```

The main app uses Python 3.11+, Flask-SocketIO/Eventlet, Jinja2, and vanilla
JavaScript/CSS; there is no frontend build step. ESP32 devices connect to the
separate gateway, which exchanges telemetry and commands with the app through
Redis. Authenticated HTTP fallbacks exist for selected device endpoints.

## Local setup

Install Python 3.11 with virtual environment support and run Redis locally on
port 6379. Linux is needed for host integrations such as systemd journal access
and network bypass. PostgreSQL is required for production; SQLite is available
for local development and tests.

Run these commands from a fresh checkout:

```bash
git clone https://github.com/jannesii/raspberry-pi-smart-home.git
cd raspberry-pi-smart-home/server

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

redis-cli ping  # Expect PONG.

export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export DB_PATH="/tmp/jannenkoti-dev.db"
export WEB_USERNAME="admin"
read -rsp 'Local admin password: ' WEB_PASSWORD
export WEB_PASSWORD
printf '\n'

python run.py
```

Open `http://127.0.0.1:5555` and sign in with those credentials. This example
assumes `DATABASE_URL` is unset and no existing environment file supplies
production credentials. The `/tmp` database is disposable; choose a persistent,
writable path if you want to keep local data.

`run.py` loads `jannenkoti.env` from the working directory, so keep it in
`server/` and run commands there. Exported variables take precedence. Missing
integration credentials can produce startup warnings; those integrations will
not be available. Configured services start with the app, including background
work and device connections.

Session cookies always have the `Secure` flag. If login does not persist over
local HTTP, use a local HTTPS reverse proxy. Production must use HTTPS.

## Configuration

These variables are required at every startup:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Stable, private key for sessions and CSRF |
| `WEB_USERNAME` | Account to create or promote to Root-Admin |
| `WEB_PASSWORD` | Password used when creating that account |
| `DB_PATH` | Writable SQLite path, also required for legacy bootstrap with PostgreSQL |

An existing account keeps its password; changing `WEB_PASSWORD` does not reset
it. Keep credentials in the process environment or ignored `jannenkoti.env`.

Common optional settings:

| Variable | Default / purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL SQLAlchemy URL; otherwise SQLite at `DB_PATH` |
| `PORT` | Local app port, default `5555` |
| `ALLOWED_WS_ORIGINS` | JSON list, default `["http://127.0.0.1:5555"]`; set to your browser origin |
| `API_ALLOWED_ORIGINS` | JSON CORS allowlist, default `[]`; same-origin requests need no entry |
| `RATE_LIMIT_STORAGE_URI` | Rate-limit Redis URL, default `redis://localhost:6379` |
| `REDIS_URL` | ESP32 bridge/command Redis URL, default `redis://localhost:6379` |

The main Socket.IO message queue uses `redis://localhost:6379` directly;
changing `REDIS_URL` does not move that queue.

Configure only the integrations you use:

| Integration | Starting configuration / reference |
| --- | --- |
| AC | `AC_DEV_ID`, `AC_IP`, `AC_LOCAL_KEY`; set `AC_TUYA_VERSION` to match the device |
| Hue | `HUE_BRIDGE_IP`, `HUE_USERNAME` |
| YNAB | `YNAB_API_KEY`, `YNAB_BUDGET_ID`; [review workflow](docs/features/ynab.md) |
| ESP32 | [Gateway setup](esp32_ws/README.md) and [temperature firmware](ESP32_temperature/README.md) |
| Sodexo | `SODEXO_WEBHOOK_URL`, `SODEXO_POST_HOUR`, `SODEXO_POST_MINUTE` |
| Alerts | `ALERT_WEBHOOK_URL`, `ALERT_WEBHOOK_BATCH_SECONDS` |

See [app/config.py](app/config.py) for core parsing/defaults and
[service initialization](app/services/__init__.py) plus individual services for
integration options. AC behavior and other non-obvious feature rules are in
[feature contracts](docs/development/feature-contracts.md).

## Production

Provision a PostgreSQL database and user, keep the required settings above,
and apply migrations before starting the app. From `server/` with the virtual
environment active:

```bash
cp alembic.ini.example alembic.ini  # First-time setup only.
export DATABASE_URL='postgresql+psycopg://user:password@localhost/jannenkoti'
alembic upgrade head

gunicorn --worker-class eventlet --workers 1 --bind 127.0.0.1:5555 run:app
```

Replace the example database credentials with your own. `alembic.ini` is
ignored; its tracked example is credential-free. The migration loader currently
also reads `../jannenkoti.env` with override enabled: ensure that file does not
supply a conflicting `DATABASE_URL`. It does not load `server/jannenkoti.env`
in the same way as `run.py`; export the migration connection explicitly.

Run the app behind an HTTPS reverse proxy with WebSocket support and set
`ALLOWED_WS_ORIGINS` to the public origin. Use **one main-app worker** because
background services hold in-process state. Install and run the ESP32 gateway
as its own process; review the paths in its supplied systemd unit for your host.

Browser mutations use session authentication and CSRF protection. YNAB,
medicine, and network-bypass operations require Root-Admin. Device HTTP APIs
accept `Authorization: Bearer <token>` or `X-API-Key`; query-string keys are
disabled by default. Endpoint definitions live in [app/blueprints/api/](app/blueprints/api/).

## Development

From `server/`, using the virtual environment created above:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check app tests run.py esp32_ws
.venv/bin/ruff format --check app tests run.py esp32_ws
.venv/bin/pre-commit run --config .pre-commit-config.yaml --all-files
python3 scripts/check_agent_instructions.py
```

Tests use SQLite through SQLAlchemy Core. Keep validation isolated from live
devices and production data. The server's pre-commit configuration is inside
`server/`, so pass it explicitly as shown.

[AGENTS.md](AGENTS.md) describes the code layout, working conventions, and
validation expectations. Pre-commit and CI enforce its size budget. Keep this
README focused on orientation and setup; update feature documentation for
implementation details and troubleshooting.

## Further reading

- [Feature contracts](docs/development/feature-contracts.md): behavior to preserve when editing
- [Documentation index](docs/README.md): architecture, database operations, and feature guides
- [ESP32 gateway](esp32_ws/README.md) and [temperature firmware](ESP32_temperature/README.md)
- [YNAB categorizer](docs/features/ynab.md)

## License

[MIT](../LICENSE)
