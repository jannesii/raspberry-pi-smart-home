# Jannenkoti Smart Home Server

A personal home automation dashboard for Linux and Raspberry Pi. Jannenkoti
collects sensor readings, controls home devices, and provides authenticated
browser tools for monitoring and administration. It is tailored to one home;
device integrations need their own hardware, credentials, and configuration.

## What it does

- **Climate:** live room temperatures and humidity, history charts, FMI weather,
  and local TinyTuya AC control with thermostat and sleep schedules.
- **Car heater:** live power/status, two windshield glass temperatures and
  cabin temperature/humidity with freshness, ready-by scheduling,
  keep-at-temperature, learned heating-time estimates, permanent heating attempts
  and departure outcomes in Heating / Sessions / Settings.
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
The device firmware is maintained here too: `ESP32_temperature/` and
`ESP32C3-Car-Heater/` are separate PlatformIO projects with their own local
configuration and builds.

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

## Car-heater UI design preview

Compare three throwaway layouts with synthetic heating, session and photo states:

```sh
python3 scripts/run_car_heater_ui_prototype.py
```

Open `http://127.0.0.1:8765/car_heater?variant=A`. See the
[prototype guide](docs/reviews/car-heater-ui-prototype.md) for the layout comparison
and phone access. This preview runs separately from the Flask app and uses no
live devices or database.

## TMP117 windshield holder

A parametric PETG taped base, screwed airflow cover, removable board retainers,
two cable clamps, fit coupons, STEP/STL exports and inspection reports
are available in [the holder guide](hardware/tmp117-windshield/README.md). The
exports are measurement-gated drafts; measure the delivered PCB, connected cable
and installation stack before a final print.

## SHT45 cabin holder

A ventilated PETG housing for the Adafruit 6174 PTFE module, with a separate
taped console adapter, removable board retainers, two BT3 cable clamps and fit
coupons, is available in [the cabin holder guide](hardware/sht45-cabin/README.md).
Its installed STEP assembly and printable STLs are measurement-gated drafts;
confirm the delivered board, both plugs, bearing lands, fasteners and trim/tape
fit before final release.

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
| ESP32 | `ESP32_WS_LOG_LEVEL` (default `INFO`); [gateway setup](esp32_ws/README.md), [temperature firmware](ESP32_temperature/README.md), and [car-heater firmware](ESP32C3-Car-Heater/README.md) |
| Car-heater sensors | `CAR_HEATER_SENSOR_MAX_AGE_S` (default `60`): glass/cabin samples older than this are shown and stored as stale, never current. Requires car-heater firmware 3.0.0+ and migration `20261013_0010` |
| Sodexo | `SODEXO_WEBHOOK_URL`, `SODEXO_POST_HOUR`, `SODEXO_POST_MINUTE` |
| Alerts | `ALERT_WEBHOOK_URL`, `ALERT_WEBHOOK_BATCH_SECONDS` |

See [app/config.py](app/config.py) for core parsing/defaults and
[service initialization](app/services/__init__.py) plus individual services for
integration options. AC behavior and other non-obvious feature rules are in
[feature contracts](docs/development/feature-contracts.md).

Heater readings are permanent evidence. Before first-frost collection, follow
the [heater history discovery, backup/restore and migration guide](docs/operations/heater-history-preservation.md)
under issue #12; software checks in #4 do not change the deployed database.

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
- [ESP32 gateway](esp32_ws/README.md), [temperature firmware](ESP32_temperature/README.md), and [car-heater firmware](ESP32C3-Car-Heater/README.md)
- [YNAB categorizer](docs/features/ynab.md)

## License

[MIT](../LICENSE)

## Car-heater session evidence

Open **Car Heater → Sessions** to browse attempts newest first, filter by
requested departure/session date in Europe/Helsinki, or record an existing
manual/comfort/ready-by attempt. Recording evidence never operates the heater
or changes its plan. Existing control behavior remains in Heating; calibration,
charts, logs and restart tools remain reachable in Settings. Battery-charge
controls are removed from the interface; legacy backend support remains.

Each attempt has a permanent UUID, creation/observation times, optional requested
departure and reported heating duration, and an immutable installation/version
snapshot. Add sensor/heater placement notes when recording the attempt. Firmware
uses the latest stored device report, with its report time, unless the driver
records an installed version explicitly. Missing versions stay unknown. Set
`CAR_HEATER_SOFTWARE_VERSION` (defaults to the application package's `1.0.0`)
and `CAR_HEATER_SOFTWARE_REVISION` to the deployed version/commit for useful
software provenance. These may also be supplied in Flask configuration.

Select an attempt to save **Clear**, **Wipers enough**, **Partial scrape** or
**Full scrape**. Optional details include frost/ice type, other windows, interior
fog/frost, actual observation and confirmed departure time. “Already clear when
observation began” preserves that fact without assigning an exact clearing time.
Leave confirmed departure blank for unplugging or telemetry gaps. Outcomes can
be edited; saving them does not start/stop heating. Command/relay milestones
are available for bounded sessions; photos are not yet available. Older telemetry is preserved,
without fabricating historical attempts or outcomes.

Session list/detail/create and outcome endpoints are authenticated under
`/api/car_heater/sessions`; browser mutations require the normal CSRF token.
Migration `20261014_0012` adds permanent session storage. Follow the existing
backup/migration deployment procedure before using the workflow; a restart
alone does not apply the migration. No automated retention deletes sessions.

Optional isolated browser checks use Playwright without production devices:

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/pytest -q tests/test_car_heater_sessions.py tests/test_car_heater_sessions_browser.py
```

The browser driver runs separately from the Eventlet-patched app host. These
checks cover phone/desktop save/reload, date filtering, keyboard save,
loading/save failures and reachable advanced tools. Check the visual feel and
touch behavior on the actual iPhone before collection.

### Bounded manual car heating

The Heating page supports a selected duration (default 60 minutes, maximum 180),
a stable latest finish and explicit Stop pending/failed-or-unknown/confirmed states.
Timed starts require ESP32C3-Car-Heater **3.1.1**, a compatible **bounded-v1** Shelly
script and migration **20261014_0013**. Older/unsupported devices cannot authorize
bounded heating. Both transports preserve the same session identity and deadline.
See [setup and physical acceptance](docs/operations/bounded-car-heater.md) before
owner-controlled deployment/flashing in #12. Review the permanent session to see
request, relay, actual draw and shutdown milestones; outcomes remain independent.

### Departure defrost planning

In **Car Heater → Heating**, edit **Next departure** in Europe/Helsinki time.
**Tomorrow** selects the next Helsinki calendar date, including when the phone
uses another timezone. **Preview departure** shows start, departure, latest
finish, outside observation and any fallback reason. Only **Save departure**
persists a revision; typing, blur and Preview do not schedule heating. Reload
shows the saved plan. If weather changes the previewed timeline before Save,
preview again. Each replacement keeps the previous session and its evidence.

Initial preheat settings use FMI observations, without a forecast or cabin
temperature model:

| Outside temperature | Preheat |
| --- | --- |
| 0 °C or warmer | 90 minutes |
| −5 °C | 120 minutes |
| −10 °C | 150 minutes |
| −15 °C or colder | 180 minutes |

Between anchors, interpolate linearly at six minutes per degree below zero
and round up to the next whole minute (−2.5 °C → 105; −0.1 °C → 91).
These are initial experiment durations, not validated window-clearing times;
warm weather does not skip heating. A valid outside observation must be finite,
between −80 and +60 °C, and **0–30 minutes old, inclusive**, measured from its
timezone-aware observation time. A recent cache retrieval cannot make an old
observation fresh. Missing, invalid, future-dated or older observations select
an explicit **180-minute fallback with no grace**.

Request 0–30 minutes of departure grace within the original 180-minute limit:
150 minutes before departure permits 30 afterward; 180 before permits none.
Saving freezes this timeline. Late device availability or delayed execution
reduces available preheat and never moves the finish. A late request is recorded
separately from relay confirmation and actual power. After an execution attempt,
server recovery requests OFF and never replays ON. Cabin/glass temperature does
not shorten this mode; finish does not hand off to comfort.

**Cancel upcoming departure** cancels a pending plan without controlling current
heating. **Stop heating** requests OFF for active heating; confirmation may remain
pending or unknown. If a cancellation races with a start, it requests OFF under
the bounded contract. Active bounded heating must be confirmed stopped before
a replacement plan can be saved. Spring clock-gap times are rejected; for a
repeated autumn time, choose its occurrence under **Clock changes and departure
grace**. Timelines show dates and timezone abbreviations across midnight/DST.

**Review departure session** opens permanent revisions, planning/start/departure
weather snapshots with source, observation and actual capture times, execution
milestones and outcomes. Start/departure snapshots use available cached weather
without blocking actuation on a network request; delayed snapshots retain their
actual capture time. They do not silently change the saved finish.

Apply migration **20261015_0014** using the normal backup/migration deployment
procedure before enabling this workflow; restarting alone does not migrate.
It needs the same bounded-v1 device setup as manual heating. The app-owned
heater loop runs the saved plan without an open browser or incoming telemetry.
Scheduling API: authenticated `GET/POST/DELETE /api/car_heater/departure` and
`POST /api/car_heater/departure/preview`; mutations require browser CSRF.
Optional `expected_preview` on Save rejects changes to the reviewed timeline.
Only one upcoming departure is supported; this adds no recurring schedule.

Run isolated planner, migration and phone/desktop checks with:

```sh
.venv/bin/pytest -q tests/test_departure_heating.py tests/test_departure_migration.py tests/test_car_heater_sessions_browser.py
```
