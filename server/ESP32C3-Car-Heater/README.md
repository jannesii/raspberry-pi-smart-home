# Car Heater V3 (ESP32C3)

Wi‑Fi–controlled car heater built on a Seeed XIAO ESP32C3 with a Shelly relay and a BMP280 temperature/pressure sensor.  
The firmware exposes a web UI, schedules heating so the cabin is ready at a given time, and auto‑calibrates its heating model over time.

---

## Features

- **Heater control**
  - Thermostat with configurable target temperature and hysteresis.
  - Deadzone time window where automatic heating is suppressed.
  - Manual heater toggle from the web UI.

- **Ready‑By scheduling**
  - “Heat to X °C by time T” using a physics‑based `HeatingCalculator`.
  - Uses current ambient temperature and a tunable `kFactor` to estimate warm‑up time.
  - Displays estimated start time and progress on the Ready‑By page.

- **kFactor calibration**
  - Manual calibration runs from the **kFactor** page: start now or schedule a calibration.
  - Auto‑calibration window (configurable time range and max target temperature) that can run unattended when new ambient bands are seen.
  - Records of past calibrations (ambient, target, warm‑up seconds, kFactor) stored in NVS.
  - `ReadyByTask` and the UI use a **derived kFactor** based on these records for smarter warm‑up estimates.

- **Web UI**
  - Responsive pages designed for phones (incl. iPhone safe‑area insets).
  - Live status with colored badges for heater/deadzone/task state.
  - “Ready By” planner with date/time/temperature inputs.
  - kFactor calibration dashboard with history and automation controls.
  - Logs view streaming new log lines in real time.

- **Under the hood**
  - Async web server (`ESPAsyncWebServer`) + WebSocket updates.
  - Persistent configuration via NVS (`Preferences`).
  - LittleFS filesystem for gzipped web assets.
  - Watchdog task, LED patterns for status, and basic NVS stats reporting.

---

## Project layout

High‑level structure (see `docs/ARCHITECTURE.md` for more detail):

- `src/core/`
  - `Config` – loads/saves runtime settings in NVS (target temp, hysteresis, deadzone, Ready‑By and auto‑calibration settings, kFactor).
  - `LogManager` – in‑memory log buffer with NVS persistence hooks and WebSocket forwarding.
  - `TimeKeeper` – time management, local/UTC formatting, “truly valid” time tracking.
  - `WatchDog` – monitors heater task and system health.

- `src/heating/`
  - `Thermostat` – simple hysteresis controller.
  - `HeaterTask` – FreeRTOS task that polls Shelly state, runs the thermostat, and drives the heater relay.
  - `HeatingCalculator` – physics‑based warm‑up estimator.
  - `ReadyByTask` – schedules heating so the cabin is ready by a target time, using `HeatingCalculator` and a kFactor.
  - `KFactorCalibrator` / `KFactorCalibrationManager` – manages calibration runs, auto‑calibration, and records.

- `src/io/`
  - `wifihelper` – Wi‑Fi connect helpers (static IP, DNS).
  - `ShellyHandler` – HTTP/REST‑style controller for the Shelly relay.
  - `measurements` – BMP280 sensor initialization and reading, with basic filtering.
  - `LedManager` – LED patterns via FreeRTOS queue/timer.
  - `WebSocketHub` – central WebSocket endpoint (`/ws`) used by all pages for live updates.

- `src/ui/`
  - `WebInterface` – registers HTTP routes, serves gzipped HTML/JS/CSS from LittleFS, and implements REST endpoints.

- `include/`
  - Mirrors the `src/` tree (`core/`, `heating/`, `io/`, `ui/`) with public headers.
  - `core/staticconfig.h` – compile‑time wiring and Wi‑Fi constants (SSID/password, IPs, pins, Shelly address).

- `web/`
  - `web/src/` – web UI sources:
    - `index.html` – Status/config page.
    - `readyby.html` – Ready‑By planner.
    - `logs.html` – Log viewer.
    - `calibrate.html` – kFactor calibration UI.
    - `static/` – shared JS (`index.js`, `readyby.js`, `logs.js`, `calibrate.js`) and `styles.css`.
  - `web/dist/` – gzipped assets uploaded to LittleFS (see `platformio.ini:data_dir`).
  - `web/README.md` – notes on web asset build flow.

- `scripts/`
  - `build_web.sh` – compresses `web/src` into `web/dist` (`*.gz`) before uploading filesystem.

- `docs/`
  - `ARCHITECTURE.md` – quick overview of module responsibilities and layout.

---

## Building and flashing

This is a standard PlatformIO project, targeting the Seeed XIAO ESP32C3.

### Prerequisites

- PlatformIO CLI installed (`pip install platformio` or via VS Code extension).
- ESP32 toolchain managed by PlatformIO (auto‑installed on first build).

### Build firmware

```bash
pio run
```

### Build web assets

Whenever you change anything under `web/src`:

```bash
scripts/build_web.sh
```

This regenerates `web/dist/*.gz`, which are what LittleFS serves.

### Upload firmware and filesystem

1. Connect the XIAO ESP32C3 over USB.
2. From the project root:

```bash
pio run -t upload        # flash firmware
pio run -t uploadfs      # upload LittleFS (web/dist)
```

After flashing, the device advertises itself as `http://car-heater.local/` via mDNS (see `initMDNS()` in `src/main.cpp`), or you can browse to its static IP as configured in `staticconfig.h`.

---

## Configuration

### Compile‑time (wiring/network)

Edit `include/core/staticconfig.h`:

- Wi‑Fi SSID/password and static IP/gateway/subnet/DNS.
- I2C pins and BMP280 address.
- Shelly IP address.
- LED pin and active‑high/low behavior.

Rebuild and flash after changing these.

### Runtime (via web UI)

On the **Status** page:

- Target temperature and hysteresis.
- Heater task delay (polling/decision interval).
- Deadzone start/end times.
- Heater/deadzone/task on/off state (via buttons).

On the **Ready By** page:

- Schedule a Ready‑By event with:
  - Date and time (device local time, converted to UTC).
  - Target temperature.
- See:
  - Whether a schedule is active.
  - Current ambient temperature.
  - Estimated warm‑up duration and start time.

On the **kFactor** page:

- See current kFactor and basic heater physics parameters.
- Manually start/schedule a calibration run with a chosen target temperature.
- Enable **automation**:
  - Auto‑calibration window (start/end local time).
  - Maximum calibration target temperature cap.
  - Auto‑cal runs only when:
    - Time is synchronized and within the window.
    - Heater isn’t already heating.
    - There is no Ready‑By target in the last 2 hours before its deadline.
    - A new ambient temperature “band” (5 °C bucket) without a record is detected.

Calibration runs are exclusive: they temporarily disable the normal heater task, drive the heater directly to the target, compute an observed kFactor, store a record in NVS, and update `Config`’s kFactor value.

---

## WebSocket & web UI behavior

All pages connect to the shared WebSocket endpoint `/ws`:

- **Status page** (`index.html`)
  - Receives `temp_update` messages with:
    - Current temperature, heater state, deadzone state, heater task enabled flag, current time, and time‑sync status.
  - Uses colored badges to reflect state, and keeps the nav temperature pill updated.

- **Ready By page** (`readyby.html`)
  - Receives `ready_by_update` messages:
    - Schedule presence, target time, ambient temp, estimated warm‑up, and recommended start time.
  - Also watches `temp_update` for live nav temperature updates.

- **Logs page** (`logs.html`)
  - Receives `log_append` messages and prepends lines to the log view.
  - Reacts to `time_sync` messages by triggering a sync when needed.
  - Also updates the nav temperature from `temp_update`.

- **kFactor page** (`calibrate.html`)
  - Receives `calibration_update` messages:
    - Calibration state (idle/scheduled/running), current ambient/target, elapsed time, suggested k, active settings, and recent calibration records.
  - Uses these to keep the calibration panel and nav temperature in sync.

---

## Logging and diagnostics

- **LogManager**
  - Provides `append()` for structured log lines.
  - Maintains an in‑memory buffer exposed via `/api/logs` and the logs page.
  - Broadcasts new lines over WebSocket, so the logs page updates in real time.

- **Serial output**
  - On boot: prints configuration, NVS stats, and initialization status for subsystems (timekeeper, log manager, etc.).
  - Ready‑By and calibration flows log key transitions (start/finish, schedule changes, early target reached).

---

## Safety & behavior notes

- The device will not start Ready‑By or calibration flows until time is “truly valid” (NTP/time sync via web UI).
- Auto‑calibration never runs in the last 2 hours before an active Ready‑By target time.
- Auto‑cal target is capped by `autoCalibTargetCapC` to avoid excessive high‑temperature runs.
- Heater automation can be disabled entirely via the status page or config, in which case only manual toggling affects the heater.

---

## Future ideas

Some natural extensions if you want to grow this project:

- OTA firmware updates via web UI.
- Multiple “profiles” for different vehicles or heaters.
- Export/import of calibration history as JSON.
- More advanced UI for kFactor visualization across ambient bands.

