# ESP32 WebSocket gateway

This separate Flask-Sock process connects ESP32 devices to the main app through
Redis. It handles both car-heater and temperature firmware without sharing the
main app's Eventlet runtime. Implementation: [main.py](main.py).

## Run locally

Install the [main server dependencies](../readme.md#local-setup), then the
additional gateway dependencies. From `server/`, with its environment active:

```bash
python -m pip install -r esp32_ws/requirements.txt
redis-cli ping
export REDIS_URL='redis://localhost:6379'
export ESP32_WS_HOST='127.0.0.1'
export ESP32_WS_PORT='5556'
read -rsp 'Shared device key: ' ESP32_WS_API_KEY
export ESP32_WS_API_KEY
printf '\n'
python esp32_ws/main.py
```

Set the same nonempty key in device firmware. The gateway currently accepts
all keys when `ESP32_WS_API_KEY` is empty; do not leave it empty on a reachable
service. This key is independent of main-app database-backed API keys.

The Python entry point does not load an environment file. Export variables
for local runs; systemd loads the file named in its unit. Redis must be running
before startup because the manager connects when the module is imported.

## Deployment and configuration

The [supplied systemd unit](esp32_ws.service) uses the main virtual environment,
Gunicorn with one worker/four threads, and `localhost:5556`. Edit its user,
group, working directory, executable, and environment-file paths for your host,
then install it from `server/esp32_ws/`:

```bash
sudo cp esp32_ws.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now esp32_ws
```

| Setting | Default | Applies to |
| --- | --- | --- |
| `REDIS_URL` | `redis://localhost:6379` | Gateway Redis connection |
| `ESP32_WS_API_KEY` | Empty; verification disabled | First-message device authentication |
| `ESP32_WS_HOST` | `0.0.0.0` | Direct Python entry point only |
| `ESP32_WS_PORT` | `5556` | Direct Python entry point only |
| `ESP32_WS_STATUS_LOG_INTERVAL_S` | `30` | Recurring gateway status summaries |

Gunicorn's bind address comes from the unit's `--bind`, not the host/port
variables. Provide a device-reachable WebSocket reverse proxy for a loopback
bind; use WSS when crossing an untrusted network. Keep the gateway separate
from main-app Socket.IO routing.

The unit deliberately uses `KillSignal=SIGKILL` and `TimeoutStopSec=1` to avoid
hanging shutdowns on long-lived sockets. A restart drops device connections.

## Protocol and routing

Connect to `/ws` and send an authentication JSON object within ten seconds:

```json
{"auth":"<shared-key>","device_id":"temperature_kitchen","device_type":"temperature"}
```

The response contains `status: authenticated`, `device_id`, and `device_type`.
Use unique, stable IDs; reconnecting with the same ID replaces the old socket.
Omitting `device_type` selects `car_heater`.

| Redis channel | Direction | Payload role |
| --- | --- | --- |
| `esp32:status` | Gateway → main app | Car-heater status |
| `esp32:action_results` | Gateway → main app | Car-heater execution results |
| `esp32:commands` | Main app → gateway | Legacy command, wrapped in an array for devices |
| `esp32:temperature:telemetry` | Gateway → main app | Temperature readings/errors |
| `esp32:temperature:rpc_results` | Gateway → caller | Temperature RPC response |
| `esp32:temperature:commands` | Caller → gateway | Temperature RPC request |

Temperature commands select a `device_id` or `target_device_id`; without a
selector they go to all connected temperature devices. The selector is removed
before forwarding and `type` defaults to `rpc_request`. Legacy `esp32:commands`
currently broadcasts to all connected devices, not just the car heater.
Commands for disconnected devices are dropped; Redis pub/sub is not a durable queue.

For temperature payloads and RPC actions see the
[firmware guide](../ESP32_temperature/README.md). The main app validates and
persists measurements; status-only reconnect frames are filtered by the gateway.

## Diagnostics

```bash
curl --fail http://127.0.0.1:5556/health
journalctl -u esp32_ws -f --no-pager
```

`/` reports service information. `/health` reports connected devices and their
message/status/transport-ping counters. These endpoints have no authentication;
keep diagnostic access restricted. `status: healthy` is not an end-to-end
check of current Redis connectivity or main-app persistence.

The custom WebSocket server logs protocol PING/PONG frames below the JSON route.
Check both transport activity and fresh measurements when diagnosing a stale
sensor. From `server/`, isolated regression tests are
`.venv/bin/pytest -q tests/test_esp32_ws_manager.py tests/test_esp32_api.py`.
