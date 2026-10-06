# ESP32C3 Car Heater Controller

Firmware for Seeed XIAO ESP32-C3 that controls a car block heater via Shelly PM1 relay.

## Features

- **Dual communication channels:**
  - **WebSocket (primary)** — Real-time bidirectional via `wss.jannenkoti.com/ws`
  - **HTTP (fallback)** — Polling-based via `https://jannenkoti.com/api/car_heater/status`
- **BMP280 temperature sensor** for cabin temperature
- **Shelly PM1 integration** for power monitoring and relay control
- **Command deduplication** — Avoids double-execution from both channels
- **Auto-reconnect** with exponential backoff

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  ESP32C3                                                    │
│    ├── WebSocketTask ─── WSS (instant commands) ──────────► │
│    │     └── Heartbeat every 30s                           │
│    │     └── Auto-reconnect with backoff                   │
│    │                                                        │
│    └── PosterTask ─── HTTPS POST every 5-10s ─────────────► │
│          └── Fallback if WebSocket disconnected            │
│          └── Command deduplication via WebSocketTask       │
└─────────────────────────────────────────────────────────────┘
```

## Configure and build

Install PlatformIO. From `server/ESP32C3-Car-Heater/`:

```bash
cp include/core/staticconfig.h.example include/core/staticconfig.h  # First-time setup only.
```

Edit the ignored `include/core/staticconfig.h` before building. The
[example](include/core/staticconfig.h.example) lists the required compile-time
settings:

| Settings | Purpose |
| --- | --- |
| `WIFI_SSID`, `WIFI_PASSWORD` | Wi-Fi credentials |
| `WIFI_STATIC_IP_OCTETS` and the other `*_OCTETS` | Static IP, gateway, subnet, and DNS |
| `I2C_SDA_PIN`, `I2C_SCL_PIN`, `BMP280_I2C_ADDRESS` | Cabin sensor wiring |
| `SHELLY_IP` | Shelly relay address |
| `API_KEY` | Server API key for the HTTP fallback, sent as `x-api-key` |
| `WS_API_KEY` | Nonempty key matching gateway `ESP32_WS_API_KEY` |

The gateway address is set in [WebSocketTask.h](include/io/WebSocketTask.h)
and the HTTP fallback URL in [PosterTask.h](include/io/PosterTask.h).

```bash
pio run -e seeed_xiao_esp32c3
pio run -e seeed_xiao_esp32c3 -t upload
pio device monitor -b 115200
```

The [PlatformIO configuration](platformio.ini) supplies the board and library
dependencies. Upload flashes the attached device.

## WebSocket Protocol

### Authentication (first message)
```json
{"auth": "<WS_API_KEY>", "device_id": "car_heater_esp32", "firmware_version": "2.0.0"}
```

### Status Updates (ESP32 → Server)
```json
{
  "timestamp": "2026-02-05 10:30:00",
  "temperature": 22.5,
  "shelly": "{\"output\": true, \"apower\": 1200, ...}",
  "ws_stats": {"connected": true, "uptime_ms": 3600000, ...}
}
```

### Commands (Server → ESP32)
```json
[{"action": "turn_on", "source": "web_ui", "reason": "Manual control"}]
```

### Supported Commands
- `turn_on` — Turn heater ON
- `turn_off` — Turn heater OFF
- `get_logs` — Request device logs
- `esp_restart` — Restart ESP32
- `shelly_restart` — Reboot Shelly relay

## Dependencies

- [ArduinoJson](https://github.com/bblanchon/ArduinoJson) — JSON parsing
- [WebSockets](https://github.com/Links2004/arduinoWebSockets) — WebSocket client with SSL
- [Adafruit BMP280](https://github.com/adafruit/Adafruit_BMP280_Library) — Temperature sensor
