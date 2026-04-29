ESP32 Temperature & Humidity WebSocket Sensor
============================================

This firmware runs on ESP32-C3 with the Arduino framework. It reads a DHT
temperature/humidity sensor on a fixed cadence and sends readings to the shared
`esp32_ws` WebSocket service. The device no longer exposes a local HTTP API and
does not POST readings over HTTP.

Features
--------

- DHT temperature and humidity readings using Adafruit DHT + Unified Sensor
- WebSocket-only upstream transport through `esp32_ws`
- API-key authentication on the WebSocket handshake
- Server-routed RPC commands for status, reads, config, logs, metrics, and task control
- Runtime configuration backed by NVS persistence
- Structured log ring buffer with serial mirroring
- Wi-Fi reconnect manager with optional static IP and mDNS hostname
- Task watchdog for the sensor and WebSocket tasks

Architecture
------------

- `src/WebSocketTask.*` - WebSocket connection, auth, telemetry, and RPC handling
- `src/SensorTask.*` - DHT cadence and sensor read lifecycle
- `src/AppConfig.*` - thread-safe configuration with JSON serialization and NVS
- `src/Metrics.*` - counters/gauges included in telemetry and RPC responses
- `src/StructuredLog.*` - ring-buffer logs returned through RPC
- `src/main.cpp` - boot, Wi-Fi, NTP, watchdog, WebSocket task, and sensor task startup

The legacy `Poster.*` and `HttpServerTask.*` sources have been removed; the
active firmware has one server transport path.

Configuration
-------------

Copy `include/config.h.example` to `include/config.h` and set secrets locally.
Do not commit `include/config.h`.

Key macros:

- `DEVICE_LOCATION` - logical room/location name used by the server
- `DHTPIN`, `DHTTYPE` - DHT sensor wiring and model
- `WIFI_SSID`, `WIFI_PASSWORD`, `WIFI_HOSTNAME`, `MDNS_HOSTNAME`
- `WIFI_STATIC_IP_ENABLED` plus static IP/gateway/netmask/DNS fields when needed
- `WS_HOST`, `WS_PORT`, `WS_PATH`, `WS_USE_TLS` - WebSocket upstream
- `WS_API_KEY` - device authentication key for `esp32_ws`
- `POST_INTERVAL_SECONDS`, `ALIGN_POSTS_TO_MINUTE` - measurement cadence
- `DEFAULT_LOG_LEVEL` - optional boot log level
- `FACTORY_RESET_PIN` options - optional hardware NVS reset on boot

Runtime config changes arrive as WebSocket RPC `update_config` commands. Save
them across reboot with `save_config`, discard them with `discard_config`, or
clear NVS with `factory_reset`.

WebSocket Protocol
------------------

The first message from the device authenticates it:

```json
{
  "auth": "<WS_API_KEY>",
  "device_id": "temperature_kitchen",
  "device_type": "temperature",
  "location": "Keittiö",
  "firmware_version": "temperature-ws-v1"
}
```

Successful authentication response:

```json
{
  "status": "authenticated",
  "device_id": "temperature_kitchen",
  "device_type": "temperature"
}
```

Reading telemetry:

```json
{
  "type": "temperature_reading",
  "device_type": "temperature",
  "device_id": "temperature_kitchen",
  "location": "Keittiö",
  "temperature_c": 22.34,
  "humidity_pct": 45.67,
  "timestamp_ms": 123456,
  "metrics": {},
  "ws_stats": {}
}
```

Sensor-error telemetry:

```json
{
  "type": "temperature_error",
  "device_type": "temperature",
  "device_id": "temperature_kitchen",
  "location": "Keittiö",
  "error": "DHT read failed: temp",
  "timestamp_ms": 123456
}
```

RPC request from the server:

```json
{
  "type": "rpc_request",
  "request_id": "req-123",
  "action": "read_now",
  "params": {}
}
```

RPC response from the device:

```json
{
  "type": "rpc_response",
  "request_id": "req-123",
  "device_type": "temperature",
  "device_id": "temperature_kitchen",
  "ok": true,
  "data": {}
}
```

Supported RPC actions:

- `get_status`
- `read_now`
- `get_config`
- `update_config`
- `save_config`
- `discard_config`
- `factory_reset`
- `task_control`
- `get_metrics`
- `get_logs`
- `clear_logs`
- `restart_esp`

JSON Rule
---------

All active firmware JSON must use `#include <ArduinoJson.h>`, `JsonDocument`,
and `serializeJson()`/`deserializeJson()`. Do not add hand-concatenated JSON
strings to active firmware paths.

Build & Flash
-------------

Requires PlatformIO.

```bash
pio run -e adafruit_qtpy_esp32c3
pio run -e adafruit_qtpy_esp32c3 -t upload
pio device monitor -b 115200
```

RPC Tester
----------

Use `esp_api_tester.py` from a machine that can reach Redis:

```bash
python ESP32_temperature/esp_api_tester.py \
  --device-id temperature_kitchen read_now

python ESP32_temperature/esp_api_tester.py \
  --device-id temperature_kitchen update_config \
  --params-json '{"post_interval_sec":120}'
```

The helper publishes to `esp32:temperature:commands` and waits for
`esp32:temperature:rpc_results`.
