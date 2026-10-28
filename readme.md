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

## Repository layout

The repository root contains the Flask app, ESP32 gateway and both firmware
projects directly. On this host it is `/home/jannesi/Code/server`; existing
service paths stay the same. Fresh clones do not have a surrounding `server/`
subdirectory.

The root was narrowed from `/home/jannesi/Code` without rewriting history.
Older commits retain the previous layout. Sibling projects and other files in
the old root remain on disk outside this checkout; the license and GitHub
automation are retained here.

## Local setup

Install Python 3.11 with virtual environment support and run Redis locally on
port 6379. Linux is needed for host integrations such as systemd journal access
and network bypass. PostgreSQL is required for production; SQLite is available
for local development and tests.

Run these commands from a fresh checkout:

```bash
git clone https://github.com/jannesii/raspberry-pi-smart-home.git
cd raspberry-pi-smart-home

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
the repository root and run commands there. Exported variables take precedence.
Missing integration credentials can produce startup warnings; those integrations will
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
| `REDIS_URL` | Redis for the Socket.IO message queue, ESP32 bridge and heater commands, default `redis://localhost:6379` |
| `RATE_LIMIT_STORAGE_URI` | Rate-limit storage, default `REDIS_URL` |
| `APP_ENV` | Unset or `production`; `development` applies the [development safety contract](docs/operations/development-environment.md) |

Configure only the integrations you use:

| Integration | Starting configuration / reference |
| --- | --- |
| AC | `AC_DEV_ID`, `AC_IP`, `AC_LOCAL_KEY`; set `AC_TUYA_VERSION` to match the device |
| Hue | `HUE_BRIDGE_IP`, `HUE_USERNAME` |
| YNAB | `YNAB_API_KEY`, `YNAB_BUDGET_ID`; [review workflow](docs/features/ynab.md) |
| ESP32 | `ESP32_WS_LOG_LEVEL` (default `INFO`); [gateway setup](esp32_ws/README.md), [temperature firmware](ESP32_temperature/README.md), and [car-heater firmware](ESP32C3-Car-Heater/README.md) |
| Car-heater sensors | `CAR_HEATER_SENSOR_MAX_AGE_S` (default `60`): glass/cabin samples older than this are shown and stored as stale, never current. Requires car-heater firmware 3.0.0+ and migration `20261013_0010` |
| Weather archive | `WEATHER_ARCHIVE_ENABLED` (default `true`): archive FMI station observations every ten minutes and the parking-point forecast every thirty minutes. `WEATHER_FORECAST_POINT` (default `62.8000,22.8260`): forecast point as `lat,lon`; an invalid value leaves forecast collection off. Needs outbound HTTPS to `opendata.fmi.fi` and migrations `20261016_0016`/`20261016_0017`/`20261017_0018`; see [weather observation archive](#weather-observation-archive) and [parking-point forecast archive](#parking-point-forecast-archive) |
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
and apply migrations before starting the app. From the repository root with the
virtual environment active:

```bash
cp alembic.ini.example alembic.ini  # First-time setup only.
export DATABASE_URL='postgresql+psycopg://user:password@localhost/jannenkoti'
alembic upgrade head

gunicorn --worker-class eventlet --workers 1 --bind 127.0.0.1:5555 run:app
```

Replace the example database credentials with your own. `alembic.ini` is
ignored; its tracked example is credential-free. The migration loader reads the
same repository-root `jannenkoti.env` as the service, wherever Alembic is run
from. An exported `DATABASE_URL` takes precedence over that file, then the
`alembic.ini` URL is the fallback.

Run the app behind an HTTPS reverse proxy with WebSocket support and set
`ALLOWED_WS_ORIGINS` to the public origin. Use **one main-app worker** because
background services hold in-process state. Install and run the ESP32 gateway
as its own process; review the paths in its supplied systemd unit for your host.

Browser mutations use session authentication and CSRF protection. YNAB,
medicine, and network-bypass operations require Root-Admin. Device HTTP APIs
accept `Authorization: Bearer <token>` or `X-API-Key`; query-string keys are
disabled by default. Endpoint definitions live in [app/blueprints/api/](app/blueprints/api/).

## Development

From the repository root, using the virtual environment created above:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check app tests run.py esp32_ws
.venv/bin/ruff format --check app tests run.py esp32_ws
.venv/bin/pre-commit run --config .pre-commit-config.yaml --all-files
```

Tests use SQLite through SQLAlchemy Core. The sensor-location tests also start
a disposable PostgreSQL cluster on a private Unix socket when `initdb` and
`pg_ctl` are installed (on `PATH` or under `/usr/lib/postgresql/`); otherwise
the PostgreSQL cases are skipped. Run these as a non-root user. Keep validation
isolated from live devices and production data. The pre-commit configuration is
at the repository root. Install the hook with `.venv/bin/pre-commit install`.

To run the app itself for browser testing, start the isolated development
instance with `scripts/dev.py`; see the
[development environment guide](docs/operations/development-environment.md).
Never start a second copy with the production `jannenkoti.env`.

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

[MIT](LICENSE)

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
are available for bounded sessions. Older telemetry is preserved, without
fabricating historical attempts or outcomes.

### Observation photos

From a session, choose **Observe windshield** on the phone in its holder inside
the car. Open the app's **HTTPS** address (browsers block cameras otherwise),
choose **Enable camera** and allow access. The page tries the ultra-wide rear
camera when the browser lists one and otherwise uses the regular rear camera;
the **Camera** menu switches lens and is remembered on that phone. Frame the
driver's viewing area inside the guide, then choose **Start observation**: it
takes a photo now and every 10 minutes while the page stays visible. Use the
agreed 30–60-minute window before departure and the existing holder/lighting.

- Keep the page open and the screen on. The page requests a screen wake lock
  where supported; otherwise set Auto-Lock to Never. Locked or background
  capture is not possible.
- A hidden page, stopped camera or withdrawn permission pauses capture. A
  scheduled photo more than 2 minutes late is recorded as **missed**; missed
  intervals never get catch-up photos. **Capture now** takes an extra manual photo.
- Each photo shows **Saved** only after the server confirms it is stored.
  **Not saved** photos stay on the page until **Retry failed uploads** succeeds;
  they are not evidence and are lost if you leave. Finishing without them
  records those scheduled slots as missed (upload failed).
- **Finish observation** stops capture, records the observation window and opens
  the outcome form. **Stop heating** on the same page only requests OFF and
  keeps capturing. Neither Finish nor saving an outcome changes heating.

Session review lists photos with actual capture time, scheduled time and lens,
plus each observation window and its missed or unsaved scheduled photos. An
already-clear first photo bounds clearing time; it does not give an exact time.
Photos are JPEG, at most 8 MB each and 200 per session, stored in the database
with the session. A reverse proxy in front of the app must accept 8 MB request
bodies. See the [backup/restore guide](docs/operations/heater-history-preservation.md).
Actual iPhone ultra-wide, wake-lock and framing behaviour is a commissioning check.

### Measurement review

A session review opens with the departure outcome and the planned/actual heating
timeline, then charts the reference glass, comparison glass and cabin
temperature together, with cabin humidity and heater power in separate charts.
Photos and session facts sit beside the charts on desktop and follow them on a
phone. Each chart has a legend with units, a text summary and a per-minute value
table; late readings appear as hollow markers. Missing, invalid or repeated sensor reads, reporting gaps over
`CAR_HEATER_SENSOR_MAX_AGE_S` and frames without Shelly data are drawn as gaps,
never as zero.

The review window runs from 30 minutes before heating started (device start
request, else planned start, else the recorded session time) until 15 minutes
after the latest recorded finish, departure, outcome, observation or photo
(the 180-minute limit when none is recorded), at most 6 hours after the start.
Raw readings stay in the permanent history whether or not a session covers them.

The **experimental glass indicator** reports when the reference glass first
held at or above +3 °C for ten minutes of valid, on-time readings. Missing,
invalid, repeated or late readings, gaps over 60 s and any reading below +3 °C
restart the dwell. These rules are fixed (definition 1), so recomputing from the
permanent readings gives the same candidate time. It is a candidate time for later comparison with observed clearing:
it never stops heating, hands off to comfort or confirms defrost readiness.
Detailed diagnostic charts are under **Settings → Diagnostics → View Charts**.

### Diagnostics and restarts

Diagnostics keeps connection health, latest device telemetry, command stages,
logs and detailed history away from everyday Heating controls. Browser live
connection and HTTP fallback are shown separately from the device's last report
and relay connection. Missing or stale relay evidence stays unknown.

**Restart ESP** and **Restart Shelly** open a confirmation dialog. Cancel or
Escape sends nothing and returns focus to the restart button. **Stop heating**
in Heating stays immediate and independent. Queued means the server accepted
intent; Sent means delivery, not execution. Only an explicit device
acknowledgement reports success or failure. After 30 seconds without a result,
the display says Unknown; later acknowledgements can resolve it. A restart
acknowledgement does not establish relay OFF or successful reconnection.
Command stages are transient and start empty after a server restart.

**Get Logs** waits up to 12 seconds for a new snapshot, including through HTTP
fallback. Copy and Clear affect only the displayed snapshot. Log snapshots
are held in app memory and disappear on server restart; they are not permanent
measurement evidence. Dismissing a dialog never cancels a device command.

Retained advanced actions map to these replacements:

| Earlier action | Replacement in Settings → Diagnostics |
| --- | --- |
| Command Status (ON/OFF, logs, both restarts) | Command stages, with queued/sent/acknowledged/unknown labels |
| Latest device details and connection state | Device/relay/browser health and latest telemetry, with Helsinki report time |
| Get Logs, Copy, Clear | Get Logs dialog with receipt/source, timeout and failure feedback |
| Restart ESP / Restart Shelly | Confirmed restart dialogs with Cancel/Escape and focus restoration |
| View Charts, previous/next day, Today | Detailed measurements dialog, date picker and the same day navigation |
| Chart series toggles and energy summary | Saved series visibility and recorded energy-counter change in Wh |
| Mixed detailed metrics plot | Glass/cabin/device temperatures together; separate humidity (%), power (W), voltage (V) and current (A), with summaries and value tables |

The history date is a Europe/Helsinki day regardless of browser timezone,
including clock-change days. All plots use actual sample times and cover the
first to last recorded sample; failed reads and gaps over
`CAR_HEATER_SENSOR_MAX_AGE_S` break lines. True zero remains zero; unavailable
evidence stays missing. Late samples use hollow markers. The history API keeps
its existing `rows`, `sensor_rows` and `energy_today_wh`, adding `sensor_series`
(sample state/times and explicit failed reads), UTC `start`/`end` boundaries and
`max_gap_seconds`. No schema migration or firmware change is required for #17.
Other slices' retained controls, including legacy Ready-by, remain reachable.

Session list/detail/create and outcome endpoints are authenticated under
`/api/car_heater/sessions`; browser mutations require the normal CSRF token.
Photo upload/retrieval (`PUT`/`GET .../photos/<uuid>`) and observation windows
(`PUT .../observations/<uuid>`) use the same authentication and CSRF rules.
`GET .../sessions/<uuid>/measurements` returns the read-only review data.
Migration `20261014_0012` adds permanent session storage; `20261015_0015` adds
photos and observation windows. Follow the existing
backup/migration deployment procedure before using the workflow; a restart
alone does not apply the migration. No automated retention deletes sessions.

Optional isolated browser checks use Playwright without production devices:

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/pytest -q tests/test_car_heater_sessions.py tests/test_car_heater_photos.py tests/test_car_heater_session_review.py tests/test_car_heater_sessions_browser.py
```

The browser driver runs separately from the Eventlet-patched app host. These
checks cover phone/desktop save/reload, date filtering, keyboard save,
loading/save failures and reachable advanced tools. Check the visual feel and
touch behavior on the actual iPhone before collection.

### Bounded manual car heating

**Car Heater → Heating** follows the approved layout: the next departure, heater
state, device freshness and start/departure/latest-finish timeline lead; manual
heating, comfort tools and conditions sit beside them (below them on a phone).
Manual heating takes a preset or typed duration (default 60 minutes, maximum 180)
and shows its mode, original finish and remaining time. **Stop heating** appears
under the heater state during a session or whenever OFF is not confirmed. It asks
no confirmation; Stopping, Stop not confirmed and OFF confirmed stay distinct, and
only matching relay evidence confirms shutdown. Other mode starts show why they
are unavailable until then. Device report, weather observation and each
measurement show their own freshness; missing values show —.
Timed starts require ESP32C3-Car-Heater **3.1.1**, a compatible **bounded-v1** Shelly
script and migration **20261014_0013**. Older/unsupported devices cannot authorize
bounded heating. Both transports preserve the same session identity and deadline.
See [setup and physical acceptance](docs/operations/bounded-car-heater.md) before
owner-controlled deployment/flashing in #12. Review the permanent session to see
request, relay, actual draw and shutdown milestones; outcomes remain independent.

### Comfort heating and Settings

**Heating → Comfort heating** (collapsed by default) keeps cabin air near a
target using the saved hysteresis. It is separate from departure defrost and
never means the windows are clear. Editing the target saves nothing until
**Save target** or **Start comfort heating**; the shown state and saved target
come from the server's persisted reply. Starting is unavailable, with the reason
shown, while a timed session runs or until its OFF is confirmed, and the server
refuses such a start too. A timed start switches comfort off; it never resumes on
its own. **Stop comfort heating** leaves the relay as it is; use **Stop heating**
if the heater reports ON. The legacy cabin-target **Ready by** stays inside this
section until it is retired.

**Settings** groups **Heating preferences** (comfort hysteresis, with an
explanation and an explicit **Save preferences**), **Installation & devices**,
**Calibration** and **Diagnostics** (telemetry, command status, charts, logs and
weather collection). Installation &
devices records the heater, power topology and the three sensor positions. Each
save is kept as history and copied into heating sessions created afterwards;
earlier sessions keep their own snapshot. Below it, firmware, relay capability
and each sensor's state show only what the devices last reported; sensor models
and I2C addresses are labelled as firmware defaults because the device does not
report them. The record needs migration **20261021_0019**; without it heating
still works and the page says the record cannot be read.

**Settings → Calibration** is labelled experimental. It tunes the cabin-air
warm-up model used by comfort estimates and the legacy Ready-by schedule; the
departure duration table never uses it. It shows the model state, active heat
loss and efficiency, last session, calibration window, cooldowns, model version
and when settings were last saved (Helsinki time; "defaults, never saved" until
then). The calibration window and Ready-by tuning are always shown; the 28 model
settings sit in the initially collapsed **Advanced model tuning**, with expert
fitting controls collapsed inside it, each with its explanation and range.
Edits save nothing until **Save calibration settings**, which sends only changed
fields; out-of-range values are refused and a failed save keeps the saved
values. **Turn on autonomous calibration** (confirmed first) lets the server
heat briefly by itself inside the window. A timed session switches it off and
blocks turning it on, in the page and at the server, until OFF is confirmed.
**Reset model settings to defaults** (confirmed) keeps Ready-by settings,
recorded sessions and fitted parameters and leaves autonomous calibration off;
**Reset cooldown** never starts heating. **Recorded calibration data** loads
recent sessions, bucket coverage and statistics when opened.

### Departure defrost planning

In **Car Heater → Heating**, edit **Next departure** in Europe/Helsinki time.
A new departure defaults to tomorrow at 08:00; reloading a saved plan restores
its saved date and time.
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

### Weather observation archive

While the server runs, it archives FMI observations from Seinäjoki Pelmaa
(101486) and Ilmajoki Seinäjoki airport (137188) every ten minutes. This happens
with or without heating, a connected DEFA cable, a browser or telemetry. Each
request covers an aligned two-hour window, so delayed readings arrive later.
The archive keeps:

- every returned reading at its own source time, with unit and aggregation
  period (hourly precipitation stays hourly), and the original source text
- received missing values (`NaN`), recorded as missing rather than zero
- each later correction as a new revision; earlier values are never overwritten
- every attempt, with its retrieval time and outcome
- each original successful reply, compressed (about 4 KB, roughly 430 MB a year
  for both stations) and stored once per checksum

Station readings stay separate. The departure planner keeps using its own
cached temperature observation. Collection runs on its own thread, so a slow
or failing FMI request or archive write, including history recovery, cannot
delay Stop or change a plan.

**History recovery.** On its first start the archive also fetches the
preceding 24 hours for each station. An archive upgraded from live-only
collection fetches the 24 hours before its first live collection instead,
as far as the repair limit allows. After an outage, any stretch of the last
seven days without a successful reply is re-requested in six-hour windows
aligned to UTC, at most six per station per ten-minute slot, through the same
parser and archive. A station whose live request fails gets no recovery
requests in that slot. Older gaps are never repaired and stay visible.
Backfilled readings keep their actual retrieval time and purpose
(`initialization` or `repair`); a changed value is a new revision beside the
original. A restart resumes from the recorded history start and the
retrieval history; it never re-initializes.

**Settings → Diagnostics → Weather collection** shows each station's last
collection and last failure. It also shows which fields had values, which came
back empty and which the station did not return. A missing field is a source
limitation; a failed or stale collector is shown separately. It also shows
the station's history state and its gaps over the last 30 days: stretches
waiting for repair (failed or never collected), source times FMI answered
without any value, and gaps older than the repair limit that were not repaired.
**Download original reply** retrieves the source XML explicitly.

Authenticated read-only API (ranges are ISO timestamps with offsets, at most
seven days apart; default the last 24 hours):

- `GET /api/car_heater/weather/observations?start=&end=&fmisid=&as_of=`: all
  revisions, each linked to its `retrieval_id` and `reply_id`, with
  `purpose`/`backfill`. `as_of` keeps only revisions retrieved by then, which
  is what an earlier decision could have seen.
- `GET /api/car_heater/weather/retrievals?start=&end=`: collection attempts
- `GET /api/car_heater/weather/health`: per-station collection health, with
  `history` progress, pending repairs and recent gaps
- `GET /api/car_heater/weather/coverage?start=&end=&fmisid=`: per-station
  segments: `collected`, `source_empty`, `failed`, `not_collected`,
  `unrepaired` or `before_history`
- `GET /api/car_heater/weather/replies/<id>`: the original reply as an XML
  attachment, verified against its checksum. Other responses never embed it.

Apply migration **20261016_0016** with the normal backup/migration procedure
before relying on the archive. The tables are covered by the whole-database
backup in the [preservation guide](docs/operations/heater-history-preservation.md),
and `scripts/heater_history.py discover` reports `weather_integrity`.
Apply migration **20261017_0018** after 0017 before starting the recovering
collector. It labels existing retrievals `live` and adds per-station progress;
it creates no progress rows, so the next start initializes the history.
Downgrading drops progress and the purpose label but keeps backfilled
readings. Exports are described under
[weather evidence and exports](#weather-evidence-and-exports). Run the isolated checks with:

```sh
.venv/bin/pytest -q tests/test_weather_observation_archive.py tests/test_weather_observation_recovery.py tests/test_weather_archive_migration.py
```

### Parking-point forecast archive

Every thirty minutes the server also requests FMI's HARMONIE point forecast
for the parking point (default 62.8000 N, 22.8260 E). It asks for Temperature,
DewPoint, Humidity, WindSpeedMS, WindDirection, WindGust, TotalCloudCover,
RadiationLW, RadiationGlobal, Precipitation1h and WeatherSymbol3 at an hourly
step. Each check asks for 24 hours back to 10 days ahead, far wider than any
run. FMI's default reply is a 50-hour window that moves with the clock and cuts
runs short. FMI fills hours outside the run with `NaN`. That filler is counted
in the retrieval summary (`outside_run_points`) but not stored, so the archive
keeps exactly the hours the run holds. The archive keeps:

- each distinct forecast version received, with model (procedure), model
  origin (analysis time), source result time, requested and returned location,
  and the request configuration
- every returned hourly value at its own valid time, with FMI's parameter name,
  source text, unit and the original metadata link. `NaN` is kept as missing
  and true zero stays zero. RadiationLW is FMI's "long wave radiation"; its
  direction is not stated by FMI and is not inferred
- every check, with its retrieval time and outcome. A version received again
  adds a receipt, not new values. Changed values or a new result time for the
  same origin become the next revision; earlier versions are never altered
- each original successful reply, compressed (about 30 KB, roughly 530 MB a
  year)

Only versions actually received are kept. Runs FMI published and replaced
between checks, or while the server was down, are not reconstructed. The
forecasts API lists every stretch without a successful check as `unobserved`.
A field FMI returned without values is shown apart from a field it did not
return and from a failed check. Forecasts are evidence only: the departure
planner keeps its observed-temperature source, and no forecast or radiation
field controls heating. A different point or configuration is a separate
source and never merges with earlier evidence.

**Settings → Diagnostics → Weather collection** shows the forecast's last
check and last failure, plus the model run, hours received and any stretches
in the last 24 hours without a successful check. It also shows which fields
had values, which came back empty and which FMI did not return.

Authenticated read-only API (same range rules as above):

- `GET /api/car_heater/weather/forecasts?start=&end=&source_id=`: versions
  received in the range, each with its receipts and `reply_id`s but no values,
  plus `unobserved` stretches
- `GET /api/car_heater/weather/forecasts/<id>`: one version with its complete
  returned hourly horizon
- `GET /api/car_heater/weather/retrievals?kind=forecast`: forecast checks
- `GET /api/car_heater/weather/health`: the `forecast` block holds forecast
  collection health
- Original replies use `GET /api/car_heater/weather/replies/<id>` as above

Apply migration **20261016_0017** after 0016 with the normal backup/migration
procedure. It makes `weather_retrieval.requested_start/end` nullable; forecast
checks archived before the wide window was introduced have none. Discovery reports
`weather_integrity.forecast_links`. Downgrading drops the forecast tables but
keeps forecast retrievals and replies; back up first. Run the isolated checks with:

```sh
.venv/bin/pytest -q tests/test_weather_forecast_archive.py tests/test_weather_forecast_migration.py
```

### Weather evidence and exports

Each session page has a **Weather evidence** section below Measurements. It
uses the same window as Measurements; weather is collected continuously and
belongs to no session, and the session's saved planning, start and departure
weather never changes. For each station it shows readings in the window, how
many FMI corrected or the archive recovered later, any gaps (repair pending,
FMI answered without values, or older than the repair limit) and the original
replies. For the parking-point forecast it shows the version held when heating
began, versions received during the window and stretches without a successful
check. Collection failures, a stale collector and automatic repair are
explained in the same place. Links download the window's exports.

**Settings → Diagnostics → Weather collection → Export archived weather**
exports other dates (Europe/Helsinki calendar days): station observations for
up to 7 days, forecast values for up to 2 days, or coverage and gaps for up to
7 days, as CSV or JSON. Rows keep source text, units, revisions (`current`),
received missing values (empty value, `status=missing`), retrieval times,
`purpose`/`backfill`, the station position and the `reply_id`; forecast rows
add model, origin, result time, requested and returned location and receipts.
Coverage rows list each station's collected stretches and gaps (repair
pending, FMI answered without values, older than the repair limit) and the
forecast's unchecked stretches, so time with no evidence is visible in CSV too.
Coverage is judged from the archive at export time (`checked_at`), including
for an `as_of` export. Text cells starting with `=`, `+`, `-` or `@` that are not numbers
get a leading `'` in CSV only.

Authenticated read-only API:

- `GET /api/car_heater/sessions/<id>/weather`: the window, per-station coverage,
  reading counts, health and reply ids, forecast versions and export links.
  It never embeds readings or reply content.
- `GET /api/car_heater/weather/export?kind=observation|forecast|coverage&format=csv|json`
  with `start`/`end` ISO timestamps or `start_date`/`end_date`, plus optional
  `fmisid` or `source_id` and `as_of`.

Original replies stay explicit downloads (`/weather/replies/<id>`, checksum
verified). Backup and isolated restore of all weather tables, with
`weather_fingerprints` to compare a restore with its source, are in the
[preservation guide](docs/operations/heater-history-preservation.md). No
migration is needed. Run the isolated checks with:

```sh
.venv/bin/pytest -q tests/test_weather_evidence_export.py tests/test_weather_evidence_restore.py
.venv/bin/pytest -q tests/test_car_heater_sessions_browser.py -k weather
```
