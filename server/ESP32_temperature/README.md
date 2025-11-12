ESP32 Temperature & Humidity Poster (FreeRTOS + HTTP Control)
=============================================================

This project runs on ESP32 (Arduino framework) and periodically reads a DHT sensor, then posts measurements as JSON to an upstream HTTP/HTTPS endpoint. It uses FreeRTOS tasks for concurrency and exposes a lightweight HTTP API for runtime configuration and task control.


Features
--------

- DHT sensor readouts (temperature, humidity) using Adafruit DHT + Unified Sensor
- Configurable posting cadence (interval + optional epoch alignment) with NTP time sync and fallback cadence
- TLS (HTTPS) posting with configurable Root CA or insecure mode for development
- Runtime configuration via HTTP API (Wi‑Fi credentials, upstream host/path/port, TLS flags, API key, device location)
- Task status endpoint and task control (suspend, resume, restart)
- NVS-backed configuration persistence with HTTP save/discard endpoints and optional factory-reset button


Architecture
------------

- `src/AppConfig.*` — Thread-safe, runtime configuration store
  - Loads defaults from `include/config.h` macros at boot
  - Exposes getters/setters with a mutex and JSON (de)serialization helpers
- `src/Poster.*` — Upstream HTTP(S) client
  - Builds JSON body and posts to configured host/path/port
  - Respects `use_tls` and `https_insecure`; uses `kHttpsRootCA` when validating
- `src/SensorTask.*` — FreeRTOS task for reading DHT and posting
  - Immediate read on boot, then cadence defined by `post_interval_sec` and `align_to_minute`
  - Uses wall-clock alignment when time is available; otherwise falls back to interval-based scheduling
  - Gentle recovery on DHT failures (re-init sensor) and posts error JSON
- `src/HttpServerTask.*` - HTTP server (port 80) exposing JSON endpoints
  - `/status` (GET): runtime status and task metrics
  - `/read` (GET): take an immediate DHT reading and return it
  - `/config` (GET/POST): view/update configuration
  - `/task` (POST): control tasks (suspend/resume/restart)
- `src/main.cpp` — Minimal bootstrap
  - Serial, Wi‑Fi connect (using AppConfig defaults), NTP setup
  - Starts HTTP server and sensor tasks


Configuration
-------------

Default configuration is defined in `include/config.h`. A template is provided at `include/config.h.example` — copy it to `include/config.h` and fill your values. The file `include/config.h` is intentionally `.gitignore`d to avoid committing secrets. At boot, `AppConfig` loads these defaults and then overlays any values previously saved in NVS (non-volatile storage).

Runtime changes made through the HTTP API remain in RAM until persisted. To keep changes across reboots:

- `POST /config/save` — write the current in-memory configuration to NVS.
- `POST /config/discard` — throw away unsaved edits and reload the last persisted configuration (or defaults if nothing has been saved yet).
- `POST /config/factory_reset` — clear NVS and reload compile-time defaults; the response includes the fresh defaults and recommends a reboot.

`GET /config` now includes a `persisted` flag so clients can tell whether values originate from NVS or only from compile-time defaults. You can also opt into a hardware-assisted factory reset by defining `FACTORY_RESET_PIN` (and optional level/mode/hold macros) in `config.h`. Holding that pin in the active state for `FACTORY_RESET_HOLD_MS` during boot clears NVS and restarts the device.

Key macros (examples):

- Device metadata
  - `DEVICE_LOCATION` — Logical location string (e.g., "kitchen")
- DHT sensor
  - `DHTPIN` — GPIO pin for the DHT sensor
  - `DHTTYPE` — DHT model (e.g., `DHT22`)
- Wi‑Fi
  - `WIFI_SSID`, `WIFI_PASSWORD`
- Upstream server
  - `HTTP_SERVER_HOST` — Hostname only (no scheme)
  - `HTTP_SERVER_PORT` — Usually 80 (HTTP) or 443 (HTTPS)
  - `HTTP_SERVER_PATH` — API path (e.g., "/api/esp32_temphum")
  - `HTTP_USE_TLS` — 1 to use TLS; 0 for plain HTTP
  - `HTTPS_INSECURE` — 1 to disable certificate validation (development only)
  - `kHttpsRootCA` — PEM-encoded Root CA used for TLS validation when not insecure
- API key
  - `API_KEY` — Sent as `Authorization: Bearer <API_KEY>` when present
- Posting cadence
  - `POST_INTERVAL_SECONDS` — Interval between automatic posts (seconds)
  - `ALIGN_POSTS_TO_MINUTE` — 1 to align to epoch boundaries (cron-like), 0 for relative timing

Runtime updates via the HTTP API override the in-memory config until reboot. Persist them with `POST /config/save` if you need them to survive power cycles.


HTTP API
--------

All endpoints are on port 80 (plain HTTP) and return JSON unless otherwise stated.

- GET `/status`
  - Returns Wi-Fi state, IP, heap usage, uptime, and task list with state/stack watermark/priority.

- GET `/read`
  - Takes a fresh DHT reading and returns JSON like:
    { "ok": true, "location": "...", "temperature_c": 22.34, "humidity_pct": 45.67 }
  - On failure:
    { "ok": false, "location": "...", "error": "DHT read failed: temp" }

- GET `/config`
  - Returns current runtime configuration plus `persisted` flag indicating whether NVS has data. Sensitive fields (Wi‑Fi password, API key) are included for full visibility — protect network access accordingly.

- POST `/config`
  - Updates any subset of configuration. Body: JSON object. Example:
    {
      "device_location": "kitchen",
      "server_host": "example.com",
      "server_port": 443,
      "server_path": "/api/esp32_temphum",
      "use_tls": true,
      "https_insecure": false,
      "api_key": "sk_abc",
      "wifi_ssid": "MyWiFi",
      "wifi_password": "secret",
      "post_interval_sec": 300,
      "align_to_minute": true
    }
  - If SSID/password change, the device attempts to reconnect immediately.

- POST `/task`
  - Controls tasks. Body: { "name": "SensorPostTask" | "HttpServerTask", "action": "suspend" | "resume" | "restart" }
  - Example:
    { "name": "SensorPostTask", "action": "restart" }
  - Warning: Suspending `HttpServerTask` makes the API unreachable until it is resumed by other means.

- POST `/config/save`
  - Persists the current configuration to NVS. Response includes the active configuration snapshot.

- POST `/config/discard`
  - Restores the last saved configuration (or compile-time defaults if nothing has been saved). Wi-Fi credentials reload immediately if they change.

- POST `/config/factory_reset`
  - Clears all persisted values and restores defaults. Useful for onboarding a new network or wiping secrets. A reboot is recommended afterward.


Posting Format
--------------

- Endpoint: `http(s)://<server_host>:<server_port><server_path>`
- Headers: `Content-Type: application/json`, optional `Authorization: Bearer <API_KEY>`
- Body (example):
  { "location": "kitchen", "temperature_c": 22.34, "humidity_pct": 45.67 }
- Error posts:
  { "location": "kitchen", "error": "DHT read failed: temp" }


Build & Flash
-------------

- Requires PlatformIO
- Board: `adafruit_qtpy_esp32c3`

Commands:

- Build: `pio run -e adafruit_qtpy_esp32c3`
- Upload: `pio run -e adafruit_qtpy_esp32c3 -t upload`
- Monitor: `pio device monitor -b 115200`


Usage Flow
----------

1. Set defaults in `include/config.h` (Wi‑Fi, upstream server, TLS, API key, device location, DHT pin/type).
2. Build and flash the firmware.
3. Check the serial monitor for the assigned IP address.
4. Query API:
   - `GET http://<esp-ip>/status`
   - `GET http://<esp-ip>/read`
   - `GET http://<esp-ip>/config`
   - `POST http://<esp-ip>/config` (update runtime config)
   - `POST http://<esp-ip>/task` (control tasks)
5. The device posts a reading immediately on boot, then according to the configured cadence (default: 60s, aligned to wall-clock minutes once time is synced).

Optional polling-only mode: suspend the posting task and poll via HTTP
- Suspend auto-posting: `POST /task` with `{ "name": "SensorPostTask", "action": "suspend" }`
- Periodically fetch readings from your server using `GET /read`


Security Notes
--------------

- The embedded HTTP API is plain HTTP and unauthenticated.
  - Do not expose the device to untrusted networks.
  - The `/config` endpoint includes sensitive fields; protect access.
- Persisting secrets to NVS makes them survive reboots; remember to factory reset (`POST /config/factory_reset` or the hardware button, if configured) before decommissioning a device.
- `use_tls` / `https_insecure` only affect upstream POSTs. They do not secure the embedded HTTP server.
- For stronger protection, consider:
  - Placing the device on a trusted VLAN
  - Adding simple token-based auth to the endpoints
  - Switching to a TLS-capable embedded server (not provided here)


Extending
--------

- Add new tasks following the `SensorTask` pattern; export task handles for status/control.
- Extend `/status` to include additional metrics.
- Add NVS persistence to `AppConfig` for runtime changes to survive reboots.
- Enhance `/task` with priority changes or stack diagnostics if needed.


Libraries
---------

- Adafruit DHT sensor library
- Adafruit Unified Sensor
- ArduinoJson (for HTTP API payloads)
- WebServer (embedded HTTP)
- WiFi / WiFiClientSecure (ESP32)

Dependencies are declared in `platformio.ini` and fetched automatically by PlatformIO.


Maintenance
-----------

Whenever the code changes in ways that affect behavior, configuration, endpoints, or usage, update this README to keep it authoritative. This repository is maintained with the principle: documentation is part of the code.


Roadmap / Ideas
---------------

See IDEAS.md for a prioritized list of potential features, improvements, and future work.
