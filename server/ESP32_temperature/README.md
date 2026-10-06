# ESP32 temperature and humidity sensor

Arduino firmware for the Adafruit QT Py ESP32-C3, using a DHT sensor and the
[shared WebSocket gateway](../esp32_ws/README.md). Telemetry and control use
WebSocket JSON messages; this firmware has no local HTTP API or HTTP uploader.

## Configure and build

Install PlatformIO. From `server/ESP32_temperature/`:

```bash
cp include/config.h.example include/config.h  # First-time setup only.
```

Edit the ignored `include/config.h` before building. The
[example](include/config.h.example) lists the supported compile-time settings:

| Settings | Purpose |
| --- | --- |
| `DEVICE_LOCATION` | Room/location label in telemetry |
| `DHTPIN`, `DHTTYPE` | Wiring and DHT sensor model |
| `WIFI_SSID`, `WIFI_PASSWORD` | Wi-Fi credentials |
| `WIFI_HOSTNAME`, `MDNS_HOSTNAME` | Network names; use a distinct mDNS name per device |
| `WIFI_STATIC_IP_ENABLED` and related fields | Optional static IP configuration |
| `WS_HOST`, `WS_PORT`, `WS_PATH`, `WS_USE_TLS` | Gateway hostname (no scheme), port, path, and TLS choice |
| `WS_API_KEY` | Nonempty key matching gateway `ESP32_WS_API_KEY` |
| `POST_INTERVAL_SECONDS`, `ALIGN_POSTS_TO_MINUTE` | Reading cadence; names retained despite WebSocket transport |

Replace the example gateway hostname with your deployment. Optional boot log
level and factory-reset pin settings are documented in the example header.

```bash
pio run -e adafruit_qtpy_esp32c3
pio run -e adafruit_qtpy_esp32c3 -t upload
pio device monitor -b 115200
```

The [PlatformIO configuration](platformio.ini) supplies the board and library
dependencies. Upload flashes the attached device. Previously saved NVS settings
can override compile-time defaults; inspect runtime config if changes appear
not to take effect.

## Identity and telemetry

The first WebSocket message authenticates with `auth`, `device_id`,
`device_type: temperature`, `location`, and `firmware_version`. This is an
application message after connection, not an HTTP authentication header.

Device IDs derive from the mDNS hostname (falling back to location), normalized
to lowercase with a `temperature_` prefix. Use `/health` on the gateway to find
the actual ID. Duplicate IDs replace each other's connections.

A measurement contains these fields, plus metrics and WebSocket statistics:

```json
{
  "type": "temperature_reading",
  "device_type": "temperature",
  "device_id": "temperature_kitchen",
  "location": "Kitchen",
  "temperature_c": 22.34,
  "humidity_pct": 45.67,
  "timestamp_ms": 123456,
  "metrics": {},
  "ws_stats": {}
}
```

`timestamp_ms` is device uptime in milliseconds, not Unix time. Failed reads
produce `temperature_error` messages. A status-only frame is also sent after
authentication; it is not a new measurement. The server accepts finite readings
within `-50..80°C` and `0..100%` humidity before persistence.

## Runtime control

RPC requests contain `type: rpc_request`, `request_id`, `action`, and `params`.
Responses contain `type: rpc_response`, the request ID, device identity, `ok`,
and result data or an error.

| Actions | Purpose |
| --- | --- |
| `get_status`, `get_metrics`, `get_logs` | Inspect the device |
| `read_now` | Trigger a reading |
| `get_config`, `update_config` | Read/change runtime configuration |
| `save_config`, `discard_config` | Save to NVS or reload saved configuration |
| `factory_reset` | Clear persisted configuration and restore defaults |
| `task_control` | Control firmware tasks |
| `clear_logs`, `restart_esp` | Clear the log buffer or restart the device |

From `server/`, with the Python environment active, use the
[RPC tester](esp_api_tester.py) against Redis:

```bash
python ESP32_temperature/esp_api_tester.py --device-id temperature_kitchen get_status
python ESP32_temperature/esp_api_tester.py --device-id temperature_kitchen update_config \
  --params-json '{"post_interval_sec":120}'
```

Use the actual device ID and `--redis-url` for a nondefault Redis address.
Changes persist across reboot only after `save_config`. Requests use
`esp32:temperature:commands`; replies use `esp32:temperature:rpc_results`.

## Implementation

[WebSocketTask.cpp](src/WebSocketTask.cpp) owns authentication, telemetry, and
RPC dispatch; [SensorTask.cpp](src/SensorTask.cpp) owns DHT reads;
[AppConfig.cpp](src/AppConfig.cpp) owns runtime/NVS configuration.
[main.cpp](src/main.cpp) starts Wi-Fi, NTP, watchdog, and tasks.

Use ArduinoJson serialization/deserialization for payloads. Keep transport and
measurement liveness separate when diagnosing reconnects or stale readings.
