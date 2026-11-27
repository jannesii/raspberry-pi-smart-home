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
   server: apply migrations through `20261021_0019`, then confirm **Settings →
   Diagnostics → Weather collection** shows both stations and the parking-point
   forecast collecting, with history initialized. Open a recent session's
   **Weather evidence**, download one original reply and one window export.
   Then make a whole-database backup and restore it into a separate target only
   (never over production), following the
   [preservation guide](heater-history-preservation.md#3-restore-into-isolation-and-verify):
   compare `weather_fingerprints`, require empty integrity lists and check the
   same session, export and reply in the isolated app. Record both in #12.
7. After mounting the sensors, record the heater, power topology and each sensor
   position in **Settings → Installation & devices** and save. Check that its
   device facts show firmware 3.1.1, relay control ready and each sensor's
   reading. Save again whenever anything is moved: new sessions copy the current
   record, and earlier records stay as history.

See the [14–15 October 2026 commissioning record](bounded-car-heater-commissioning-2026-10-15.md)
for the owner's supervised relay checks on a work desk at about 24.5°C. It records
the subsequent script storage/memory corrections and successful retests, while
keeping full vehicle/collection commissioning in #12 open.

The script endpoint follows Shelly device authentication settings. The existing
firmware uses unauthenticated LAN HTTP; enabling Shelly authentication without a
matching transport adapter fails closed. Keep this relay on the isolated trusted
car LAN; do not expose its RPC/script endpoints to the Internet.

## Using the app

The car-heater pages have three views, **Heating**, **Sessions** and **Settings**,
under one navigation. Times are Europe/Helsinki and 24-hour, whatever the phone's
timezone; missing values show —. Use the phone for everyday heating and capture,
and a desktop for reviewing sessions and charts.

**Heating** leads with the next departure. Edit its date/time (or **Tomorrow**),
**Preview departure**, then **Save departure**; typing and preview never schedule.
The timeline shows heating start, departure and latest finish. Below it, the
heater state reads Start requested, then confirmed ON only from relay evidence.
**Stop heating** submits OFF immediately, without a dialog. Stopping, Stop not
confirmed (with **Request OFF again**) and OFF confirmed remain distinct;
acknowledgements alone never confirm OFF. If device telemetry is absent or older
than 30 seconds, the relay state is unknown even after OFF was requested. Device
report, FMI observation and each sensor reading show their own age.

**Manual heating** takes 30/60/120/180 minutes or a typed duration (default 60,
maximum 180) and **Start timed heating**. It shows the mode, original finish and
remaining time judged by the server clock; a new selection cannot extend it.
While a timed session runs, or until its OFF is confirmed, every other start
(manual, comfort, Ready-by, departure replacement) is unavailable with the
reason shown, and the server refuses it too. Old comfort/calibration/Ready-by
intentions never resume when the timed session ends.

**Comfort heating** (collapsed) keeps cabin air near its target with the
hysteresis saved in **Settings → Heating preferences**. Start, Save target and
Stop are the only actions that save; stopping comfort does not switch the relay
off. **Ready by (cabin target)** (collapsed below it) schedules cabin air to reach
a target by a Helsinki date and time; **Save Ready-by** creates or replaces the
schedule and **Cancel Ready-by** removes it without switching the relay. It uses
the calibrated cabin model and then comfort heating; it does not mean the windows
are clear. Its tuning is in **Settings → Calibration**.

**Observe windshield** (beside Stop during a session, or from a session in
**Sessions**) opens capture. Enable the camera, frame the windshield and **Start
observation**; it photographs now and every 10 minutes while the page stays
visible, and **Capture now** adds a photo. Only **Saved** photos are evidence;
**Not saved** ones stay until **Retry failed uploads** succeeds. **Finish
observation** opens the departure outcome; neither Finish nor saving an outcome
changes heating. **Stop heating** on the capture page only requests OFF.

**Sessions** lists attempts newest first with outcome (or Outcome not recorded)
and photos, filtered by Helsinki date. A session review starts with the outcome
and heating timeline, then glass/cabin temperature, humidity and power charts,
photos with capture times, observation windows and weather evidence with
original-reply downloads. The glass indicator stays experimental.

**Settings** groups **Heating preferences**, **Installation & devices**,
**Calibration** and **Diagnostics**. Diagnostics shows browser/device/relay
health, the latest device telemetry, **View Charts**, command stages (Queued and
Sent are not execution), **Get Logs** and **Restart ESP**/**Restart Shelly**,
which ask for confirmation and never stop the heater, plus weather collection
health and exports. See the readme's
[complete interface table](../../readme.md#complete-car-heater-interface) for every
retained function. The emulated browser checks do not establish iPhone camera,
lens or wake-lock behaviour; check those on the phone during commissioning.

## Interruption notifications

The app sends immediate problem messages to the configured `ALERT_WEBHOOK_URL`.
The ordinary `ALERT_WEBHOOK_BATCH_SECONDS` delay does not apply. Messages include
the session ID, detection time and original heating finish in Europe/Helsinki.
They cover bounded manual, departure and fixed-window sessions:

- A server restart interrupts an unfinished execution; OFF is requested and ON
  never resumes automatically. Delivery begins after the server returns.
- A correlated relay report confirms OFF before the original finish. A five-second
  tolerance avoids warning for the device timer's intentional early cutoff.
- ON remains unconfirmed for 30 seconds, a control command fails, or OFF remains
  unconfirmed for 30 seconds. Failed OFF and missing OFF evidence explicitly warn
  that the relay may still be ON. Existing OFF retries continue unchanged.
- Correlated known relay status is absent for 60 seconds during heating. This
  reports **unknown**, never a proven interruption or OFF. Status returning sends
  a recovery message.
- Fresh relay reports say ON but power above 5 W is not confirmed for 60 seconds
  (low or missing measurements). A subsequent measured draw sends a recovery.
- A departure cannot start for 60 seconds after its scheduled start, or its
  original finish passes without a start. A delayed start later confirmed ON sends
  a follow-up. The original finish is never extended.

Normal starts, normal finishes and driver cancellation stay quiet. A prolonged
unconfirmed OFF still warns even when the driver requested the stop. Once that
OFF is confirmed, a follow-up reports the device evidence. Notification failure
does not change ON/OFF decisions or extend the session. Discord requests run
separately from heater control, so webhook timeouts cannot hold its command lock.
Legacy comfort/Ready-by/calibration modes are outside this bounded-session alert
coverage; their pre-existing alerts remain unchanged.

Migration `20261119_0028` adds the durable notification queue. Alerts are deduplicated
per session/event, with separate episodes for lost status or power and recovery.
A separate sender checks the queue every two seconds, with a five-second webhook
timeout. A certainly unsent request or explicit rate limit gets up to three
attempts, respecting Discord's retry delay and at least 60 seconds between tries.
A missing webhook leaves the message pending. An ambiguous request (including a
sender interrupted by restart) is recorded as unknown and is not automatically
resent, because Discord might already have accepted it. Rejected requests remain
recorded. Delivery outcomes are logged without webhook URLs or credentials.
Discord acceptance proves receipt by Discord, not phone delivery or readership.
Problem alerts mention `@everyone`; recovery messages never mention anyone.
Discord channel/device notification settings still control phone delivery.
Development does not post to the production webhook.

The server cannot notify while the whole Pi or its network is unavailable. An
independent external monitor would be needed for immediate host-outage alerts.
These messages do not replace the relay's local timer and electrical protections.

## Isolated checks

From the repository root:

```sh
.venv/bin/pytest -q tests/test_bounded_heating.py
.venv/bin/pytest -q tests/test_car_heater_sessions_browser.py
.venv/bin/pytest -q tests/test_car_heater_redesign_completion.py
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
