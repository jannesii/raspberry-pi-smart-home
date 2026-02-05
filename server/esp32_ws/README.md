# ESP32 WebSocket Service

Standalone WebSocket server for ESP32 device communication, separate from the main Flask app to avoid eventlet conflicts.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Main App (port 5555)          ESP32 WS Service (port 5556) │
│    │                                  │                     │
│    │ Redis pub/sub                    │ WebSocket           │
│    ├─────────────────────────────────►│◄────────────────────┤
│    │   esp32:commands                 │        ESP32        │
│    │◄─────────────────────────────────┤                     │
│    │   esp32:status                   │                     │
│    │   esp32:action_results           │                     │
└─────────────────────────────────────────────────────────────┘
```

## Redis Channels

| Channel | Direction | Description |
|---------|-----------|-------------|
| `esp32:commands` | Main → WS Service | Commands to send to ESP32 |
| `esp32:status` | WS Service → Main | Status updates from ESP32 |
| `esp32:action_results` | WS Service → Main | Command execution results |

## WebSocket Protocol

### 1. Authentication (first message from ESP32)
```json
{"auth": "<api_key>", "device_id": "car_heater_esp32"}
```

### 2. Status Updates (ESP32 → Server)
```json
{
  "timestamp": "2026-02-05 10:30:00",
  "temperature": 22.5,
  "shelly": "{\"output\": true, \"apower\": 1200, ...}",
  "action_results": [
    {"action": "turn_on", "success": true}
  ]
}
```

### 3. Commands (Server → ESP32)
```json
[{"action": "turn_on", "source": "web_ui", "reason": "Manual control"}]
```

## Installation

```bash
# Install to systemd
sudo cp esp32_ws.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable esp32_ws
sudo systemctl start esp32_ws

# Check status
sudo systemctl status esp32_ws
journalctl -u esp32_ws -f
```

## Configuration

Environment variables in `esp32_ws.service`:

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `ESP32_WS_PORT` | `5556` | WebSocket service port |
| `ESP32_WS_HOST` | `127.0.0.1` | Bind address |
| `ESP32_WS_API_KEY` | (none) | API key for ESP32 auth |

## Endpoints

- `ws://host:5556/ws` — WebSocket endpoint for ESP32
- `http://host:5556/health` — Health check with connected devices
- `http://host:5556/` — Service info

## Development

```bash
cd esp32_ws
python main.py
```
