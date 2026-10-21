# Bounded car-heater setup and acceptance

This is the software slice for #7. Deployment, migration, flashing and physical
shutdown verification belong to #12; this implementation does not perform them.
Do not treat simulation results as confirmation of installed hardware behavior.

## Compatible components

Use the server containing migration `20261014_0013`, ESP32C3-Car-Heater firmware
**3.1.1**, and the repository's `shelly/bounded_heating.js` (**bounded-v1**) together.
Firmware 3.0.0 can still report sensor data but cannot authorize bounded heating.
Firmware 3.1.1 deliberately rejects bare/replayed ON, including old-server ON;
update the server before using it. Older sensor firmware remains a separate target.

The server requires fresh relay-local capability, confirmed OFF and both boot
identifiers. Missing script, clock, storage, configuration or relay reachability
blocks timed ON. The relay's local flip-back timer is armed once with remaining
original allowance, rather than rearmed on every retry. A three-second early
margin covers up to two seconds of clock lag plus whole-second rounding. Delivery
expiry is judged two seconds early, so lag does not extend command lifetime. A script restart requests
OFF; a relay restart starts OFF and persists the old session as stopped. Recovery
never attempts to resume an old timed session.

## Owner-controlled commissioning (#12)

1. Back up the permanent history and apply `alembic upgrade head` with the approved
   production configuration. Verify migration `20261014_0013` is current. Record
   the server Git revision and installation context for the measurement experiment.
2. On the actual Shelly model/firmware, verify scripting and the `HTTPServer`,
   `KVS.GetMany/Set` and `Switch.Set(toggle_after)` APIs exist. Configure switch 0
   with `initial_state=off`, `auto_on=false`, `auto_off=false`, and disable
   `autorecover_voltage_errors` when supported. Keep existing electrical and
   over-temperature protections. Remove competing Shelly schedules, cloud/MQTT
   automations, button actions and other scripts that can turn this heater ON.
   These external controllers are outside the app's service guard.
3. Install `ESP32C3-Car-Heater/shelly/bounded_heating.js` in the Shelly script editor.
   Enable it at boot and record its script ID. Start with the heater unplugged.
   Do not delete its `car_heater_bound_v1` persistent key after use: it rejects
   replayed commands. Clearing the key/factory reset requires recommissioning.
   GET `/script/<id>/bounded` must report `protocol=1`, `ready=true`, a boot token
   and `relay_on=false`. A valid relay clock (SNTP) is also required before ON.
4. Copy `include/core/staticconfig.h.example` to the ignored `staticconfig.h` if
   needed, fill the existing network/API configuration and set
   `SHELLY_BOUNDED_SCRIPT_ID` to that ID. Build with
   `pio run -e seeed_xiao_esp32c3` from `ESP32C3-Car-Heater/`. Only the owner flashes
   with `pio run -e seeed_xiao_esp32c3 -t upload`. Confirm firmware 3.1.1, both
   sensors/telemetry paths, relay reachability and the capability report.
5. Use a safe test load for physical acceptance. Start a short timed run through
   the authenticated Heating page. Record the session UUID, original finish,
   relay ON and measured power. Disconnect the router and stop the server; verify
   physical relay/load OFF by the original finish with no successful remote OFF.
   Repeat with server/ESP32/Shelly reboot, cable reconnect, duplicated transport
   delivery, delayed ON, cancellation, failed OFF and script restart/stop. Verify
   initial OFF after a Shelly power cycle and no timer extension or old-mode resume.
   Record actual Shelly model/firmware, timings and power evidence in #12 before
   declaring the installation commissioned. No cold-range validation is claimed.
6. Before first-frost measurements, verify weather evidence on the deployed
   server: apply migrations through `20261017_0018`, then confirm **Settings &
   diagnostics → Weather collection** shows both stations and the parking-point
   forecast collecting, with history initialized. Open a recent session's
   **Weather evidence**, download one original reply and one window export.
   Then make a whole-database backup and restore it into a separate target only
   (never over production), following the
   [preservation guide](heater-history-preservation.md#3-restore-into-isolation-and-verify):
   compare `weather_fingerprints`, require empty integrity lists and check the
   same session, export and reply in the isolated app. Record both in #12.

See the [14–15 October 2026 commissioning record](bounded-car-heater-commissioning-2026-10-15.md)
for the owner's supervised relay checks on a work desk at about 24.5°C. It records
the subsequent script storage/memory corrections and successful retests, while
keeping full vehicle/collection commissioning in #12 open.

The script endpoint follows Shelly device authentication settings. The existing
firmware uses unauthenticated LAN HTTP; enabling Shelly authentication without a
matching transport adapter fails closed. Keep this relay on the isolated trusted
car LAN; do not expose its RPC/script endpoints to the Internet.

## Using the app

Open **Heating**, pick 30/60/120/180 minutes or type a positive duration (default
60, maximum 180), and press **Start timed heating**. The heater state under the
next departure reads Start requested, then confirmed ON only from relay evidence.
Manual heating shows the mode, original finish (Helsinki time) and remaining time
judged by the server clock; a new selection cannot extend it, and other starts
show why they are unavailable. **Stop heating**, beside **Observe windshield**,
submits OFF immediately. Stopping, Stop not confirmed (with **Request OFF again**)
and OFF confirmed remain distinct; acknowledgements alone never confirm OFF. If
device telemetry is absent or older than 30 seconds, the relay state is unknown
even after OFF was requested. Device report, FMI observation and each sensor
reading show their own age; missing values show —.

**Review this heating session** opens the permanent session and its request,
execution, relay, draw and OFF milestones. Saving a departure outcome still does
not control heating. Old comfort/calibration/Ready-by intentions do not resume
when the timed session ends; select comfort explicitly after confirmed shutdown.

## Isolated checks

From the repository root:

```sh
.venv/bin/pytest -q tests/test_bounded_heating.py
.venv/bin/pytest -q tests/test_car_heater_sessions_browser.py
node --test tests/device/bounded_relay.test.cjs
```

From `ESP32C3-Car-Heater/`:

```sh
pio test -e native
pio run -e seeed_xiao_esp32c3
```

The relay-script tests execute the shipped JavaScript with fake time and simulated
local Switch/KVS APIs. Router loss makes the simulated ESP32 network and relay RPC
unavailable; shutdown comes from the simulated native relay timer, with no remote
OFF. Shared firmware executor tests exercise the same code used by HTTP and WS,
including request failure, lifetime, original finish, reboot token and retryable
OFF. The model assumes Shelly's documented local timer and startup configuration;
only the physical commissioning tests can validate those assumptions on the load.

API references: [local switch timer and startup configuration](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch/),
[persistent KVS limits](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/KVS/),
[script endpoint API](https://shelly-api-docs.shelly.cloud/gen2/Scripts/APIs/HTTPServer/).
