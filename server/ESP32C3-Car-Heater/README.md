# ESP32C3 Car Heater Controller

Firmware for Seeed XIAO ESP32-C3 that controls a car block heater via Shelly PM1 relay.

## Features

- **Dual communication channels:**
  - **WebSocket (primary)** — Real-time bidirectional via `wss.jannenkoti.com/ws`
  - **HTTP (fallback)** — Polling-based via `https://jannenkoti.com/api/car_heater/status`
- **Glass and cabin sensors** on one I2C bus: two TMP117 windshield contact
  sensors and an SHT45 cabin temperature/humidity sensor
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
| `I2C_SDA_PIN`, `I2C_SCL_PIN` | Sensor bus pins (`D4`/`D5`); optional address overrides are listed in the example |
| `SHELLY_IP` | Shelly relay address |
| `API_KEY` | Server API key for the HTTP fallback, sent as `x-api-key` |
| `WS_API_KEY` | Nonempty key matching gateway `ESP32_WS_API_KEY` |

The gateway address is set in [WebSocketTask.h](include/io/WebSocketTask.h)
and the HTTP fallback URL in [PosterTask.h](include/io/PosterTask.h).

```bash
pio test -e native                      # sensor logic against a simulated I2C bus
pio run -e seeed_xiao_esp32c3           # build
pio run -e seeed_xiao_esp32c3 -t upload # flash the attached XIAO
pio device monitor -b 115200
```

The [PlatformIO configuration](platformio.ini) supplies the board and library
dependencies. Upload flashes the attached device. Configuration files written
for firmware 2.x still build: the unused `BMP280_I2C_ADDRESS` is ignored.

After flashing, the boot log lists every I2C address that answers and each
sensor's state, for example:

```text
[Sensors] I2C devices answering: 0x44 0x48 0x49
[Sensors] cabin=ok glass_reference=ok glass_comparison=ok
```

## Sensor wiring and addresses

All three Adafruit STEMMA QT boards share one I2C bus. Daisy-chain order does
not matter; identity comes from the address.

| XIAO ESP32C3 | Sensor boards |
| --- | --- |
| `3V3` | `VIN` (prefer 3.3 V; the boards also accept 5 V) |
| `GND` | `GND` |
| `D4` (GPIO6) | `SDA` |
| `D5` (GPIO7) | `SCL` |

| Sensor | Role (`sensors` key) | Address | Board setting |
| --- | --- | --- | --- |
| SHT45 (Adafruit 6174) | `cabin` | `0x44` | Fixed |
| TMP117 (Adafruit 4821) | `glass_reference`: lower clear-glass edge, nearer the centre | `0x48` | Default, `ADDR` jumper open |
| TMP117 (Adafruit 4821) | `glass_comparison`: lower clear-glass edge, farther toward the driver side | `0x49` | Close the `ADDR` jumper on the back |

- Glass positions are candidates above the black border. The whole mount and
  its wiring must stay out of the driver's sightlines; see the
  [defrost strategy](../docs/reviews/car-heater-defrost-strategy.md).
- The two TMP117 boards must have different addresses. With both at `0x48`
  only one answers and `glass_comparison` reports `not_found`. Label each board with
  its role so moving a sensor is a recorded installation change.
- Each board has its own 10 kΩ pull-ups, so three boards give about 3.3 kΩ. That
  suits 100 kHz over the 1–2 m cable route. Before connecting the XIAO, check
  with a multimeter that idle SDA/SCL sit at about 3.3 V, never 5 V.
- The firmware checks identities. A TMP117 must return device ID `0x117`, and the
  SHT45 must return a checksummed serial number. A wrong chip reports
  `wrong_identity`. Missing sensors are probed again every 10 s, and a sensor
  is re-identified after three failed reads in a row.
- Glass contact, bias and lag are installation checks, not firmware checks: see
  the [TMP117 holder](../hardware/tmp117-windshield/README.md) and
  [SHT45 holder](../hardware/sht45-cabin/README.md) guides.

## Compatible versions

| Firmware | Server | Notes |
| --- | --- | --- |
| 3.0.0 | Glass/cabin sensor support (migration `20261013_0010`) | Adds `sensors` and `firmware_version` to every status frame; `temperature` is null when the cabin read fails |
| 2.0.0 | Any | BMP280 cabin `temperature` only; no glass, humidity or freshness |

Deploy and migrate the server before flashing 3.0.0. Older servers ignore
`sensors`, and their gateway drops frames sent while Shelly is unreachable.

## WebSocket Protocol

### Authentication (first message)
```json
{"auth": "<WS_API_KEY>", "device_id": "car_heater_esp32", "firmware_version": "3.0.0"}
```

### Status Updates (ESP32 → Server)

The HTTP fallback POST body has the same fields without `ws_stats`.

```json
{
  "timestamp": "2026-02-05 10:30:00",
  "temperature": 22.5,
  "firmware_version": "3.0.0",
  "sensors": {
    "cabin": {"status": "ok", "temperature_c": 22.5, "humidity_pct": 41.2, "age_ms": 0},
    "glass_reference": {"status": "cached", "temperature_c": -1.25, "age_ms": 5000, "error": "read_failed"},
    "glass_comparison": {"status": "missing", "temperature_c": null, "age_ms": null, "error": "not_found"}
  },
  "shelly": "{\"output\": true, \"apower\": 1200, ...}",
  "ws_stats": {"connected": true, "uptime_ms": 3600000, ...}
}
```

When Shelly is unreachable the frame has `"shelly_connected": false` instead
of `shelly`; sensor readings are still sent and stored.

- `timestamp` is the device's UTC wall time. A sample was taken `age_ms`
  before it.
- `status`:
  - `ok`: read for this frame.
  - `cached`: this read failed; the entry repeats the last valid value and its
    age.
  - `missing`: no valid value since boot. The entry sends explicit `null`s.
- `error` values: `not_found`, `wrong_identity`, `read_failed`, `crc_mismatch`,
  `out_of_range` (SHT45 outside −40…125 °C) and `not_ready` (TMP117 before its
  first conversion). SHT45 humidity is clipped to 0–100 %, as the datasheet
  recommends.
- `temperature` keeps its earlier meaning, the current cabin temperature. It
  is `null` unless the cabin sensor was read for this frame.

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
- [CarSensors](lib/CarSensors/) — in-repo SHT45/TMP117 drivers and status JSON (no vendor sensor library)

## Bounded manual heating (firmware 3.1.1)

Use firmware **3.1.1**, the current **bounded-v1** script at
`shelly/bounded_heating.js`, and the server's bounded execution service together.
See [commissioning and upgrade steps](../docs/operations/bounded-car-heater.md).
The corrected script uses named callbacks to avoid Shelly's nested-function
limit. Fresh ON tolerates up to two seconds of clock lag, while the relay timer
finishes three seconds early to preserve the original deadline. Expiry also
uses a conservative clock allowance. OFF remains independent of clock validation.

The Shelly script must also include the KVS startup-loader correction: Gen 3
devices can return `KVS.GetMany.items` as an object keyed by the storage key.
The loader accepts that response and the array format shown in API examples,
preserving the stopped session and replay guard after script/device restart.
Update the existing script in place and retain `car_heater_bound_v1`; this
script-only correction does not require another ESP32 firmware flash. With the
relay OFF, restart the updated script and verify that `/script/<id>/bounded`
still reports the previous session UUID/finish, `reason=shelly_restart`, and
`relay_on=false`. Physical commissioning must then check replay rejection.

The current script also reduces temporary allocations in control replies. Clock
and relay checks return scalars, so full component-status objects are no longer
retained by the control handler while its reply reads status and builds JSON.
This addresses the allocation pattern found when a duplicate ON stopped a Gen 3
script with `out_of_memory`; its native relay timer still shut the load down.
Install this script update in place without clearing KVS. The Node tests check
duplicate bursts and response semantics, but do not model the device's 7 KB
scripting heap. Repeat the physical duplicate test and record running memory
and unchanged native timer start/duration before accepting the memory correction.

HTTP and WebSocket results now include a rejection `note`, also persisted by the
server. Serial `[BoundedRelay]` lines show the action, session, clock, issue,
expiry, finish, and rejection reason. Shelly HTTP response notes identify device
rejections without logging credentials or entire payloads. Build checks do not
establish physical shutdown behavior; flashing and commissioning remain in #12.
