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

## Configuration

Edit `include/core/staticconfig.h`:

```cpp
// WiFi credentials
#define WIFI_SSID "your_ssid"
#define WIFI_PASSWORD "your_password"

// Shelly PM1 IP address
#define SHELLY_IP "192.168.x.x"

// HTTP API key (for /api/car_heater/status)
#define API_KEY "sk_..."

// WebSocket API key (for wss.jannenkoti.com/ws)
#define WS_API_KEY "your_ws_api_key"
```

## Building

```bash
# Using PlatformIO CLI
pio run

# Upload to device
pio run --target upload

# Monitor serial output
pio device monitor
```

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
