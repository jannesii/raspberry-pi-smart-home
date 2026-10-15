# Feature contracts

Read the section relevant to the code being changed. These notes preserve
non-obvious behavior; implementation and regression tests define the details.
Update or remove notes when that behavior changes. Add new regression cases to
tests instead of growing a chronological list of past bugs here.

## Car heater and kFactor

Code: `app/services/car_heater/`, `app/core/_controller/car_heater*.py`,
`app/blueprints/api/car_heater/`, `app/static/js/car_heater*.js`,
`ESP32C3-Car-Heater/src/`.
Tests: `tests/test_car_heater*.py`, `tests/test_ready_by.py`.

- Browser status uses normalized `CarHeaterStatus`, not raw ESP JSON.
  `source` identifies transport (`WS`/HTTP), not Shelly's internal source.
  Include boolean `autonomous_enabled` in initial and live kFactor status.
- `car_heater_action_result` distinguishes local `stage=queued` from device
  `stage=executed`. Deduplicate logs received through `car_heater_logs` and
  the compatibility `status.logs` field.
- WebSocket status runs the same charge-mode, keep-at-temperature, ready-by,
  kFactor, alert, and persistence work as HTTP status.
- Ready-by reaching its target enables Keep at Temperature, persists
  `completed`, and ends that tick before start-time logic can run again.
- Use `KFactorCalibrator.update_config()` so physics/session/snapshot modules
  stay synchronized. Preserve DB refresh of persisted config in workers.
- Imported explicit IDs can leave PostgreSQL sequences behind. Preserve the
  kFactor controller's sequence realignment and single retry on collisions.
- Glass/cabin `sensors` (firmware 3.0.0+) are ingested by one app-wide
  `CarHeaterSensorTracker` for both transports and persisted to
  `car_heater_sensor_frame` even without Shelly data. Only new samples store
  values (late ones as `stale`, kept for history); omitted sensors (NULL state),
  test-endpoint frames and cached/invalid/stale/out-of-order reports never
  overwrite the live reading. Freshness is judged
  when read (`CAR_HEATER_SENSOR_MAX_AGE_S`). Car sensors accept `-50..100°C`
  (sunlit glass exceeds the room-sensor limit) and humidity `0..100%`. Missing cabin temperature stays
  `None` for kFactor/keep-at-temperature; never substitute 0.
- A status without `is_heater_on` (Shelly unreachable) is unknown, not OFF. The
  gateway forwards car-heater frames carrying `shelly_connected` or `sensors`.

- Heater status, sensor frames and existing heating-session evidence are kept
  permanently. Migration `20261014_0011` removes the known heater retention
  trigger; bootstrap does not recreate it. Household retention remains separate.
  See [preservation operations](../operations/heater-history-preservation.md)
  for owner-controlled discovery, backup/restore and deployed migration in #12.

- `car_heater_session` holds permanent attempt identities and immutable
  installation/version snapshots, independently of command/relay telemetry.
  Creation associates existing heating; it never operates or schedules a heater.
  Outcome saves are authenticated and CSRF-protected, never invoke controls,
  and keep missing departure times unknown. Already-clear observations do not
  imply an exact clearing time. Driver-entered duration is reported evidence,
  not confirmed power-on duration. Dates filter requested departure or observation
  time in Europe/Helsinki. Later capture/review work should reuse these UUIDs.

- `car_heater_photo` stores observation JPEG content in the database with its
  session, so whole-database backups keep the association. Clients choose the
  photo UUID; an identical retry returns the saved row and different content is
  refused. Only that acknowledgement means the image is persisted. Uploads are
  raw bodies with query metadata so CSRF checks never parse large multipart
  bodies. `captured_at` is the actual capture time; `due_at` is the scheduled
  slot (none for manual photos). `car_heater_observation` merges capture windows
  and missed slots without dropping misses or clearing a finish. Capture pages
  never call heater controls except the separate Stop heating request.

## AC, sleep, Hue, and temperatures

Code: `app/services/ac/`, `app/services/hue/`,
`app/static/js/temperatures.js`, `app/templates/temperatures.html`.
Tests: `tests/test_ac*.py`, `tests/test_hue_controller.py`.

- Serialize calls on shared TinyTuya instances. Honor `AC_TUYA_VERSION`,
  timeout/persistence settings, and `AC_TUYA_STATE_SETTLE_S` after commands.
  Retry transient malformed responses (`900`/`904`) only after closing the
  socket; do not retry credential/protocol errors such as `914`.
- Missing/null power DPS is unknown, not OFF. Normalize only present,
  non-null datapoints; startup can fall back to persisted `current_phase`.
- Preserve transition source (`thermostat`, `manual`, `sleep`, `device`) and
  `state_correlation_id` through logs, event notes, and status payloads.
  Rate-limit missing-DPS warnings. Queued persistent-socket frames can cause
  misleading power reads; protocol/sequence logs help diagnose this.
- Use `SleepManager.get_status_payload()` for HTTP and Socket.IO. Override
  end times are formatted values, not booleans. `disable_for` and `sleep_for`
  cancel each other; a scheduled sleep window clears temporary `sleep_for`.
- Keep `window.TEMPERATURES_BOOTSTRAP` and the inline temperatures workspace.
  Partial AC events must preserve omitted control-location/schedule fields.
  Fixed mobile sheets must not have persistently transformed ancestors.
- Hue is optional: guard missing `app.hue_ctrl` with an unavailable response.
  Bridge HTTP calls require explicit `HUE_HTTP_TIMEOUT_S` timeouts.

## ESP32 transport and temperature telemetry

Code/docs: [gateway](../../esp32_ws/README.md),
[temperature firmware](../../ESP32_temperature/README.md).
Tests: `tests/test_esp32_ws_manager.py`, `tests/test_esp32_api.py`,
`tests/test_socket_handlers.py`.

- Gunicorn loads `main:app`; initialize Redis/connection management on that
  path, not solely under `__main__`. Replaced connections need identity-aware
  cleanup so an old socket cannot unregister its replacement.
- Preserve the gateway's hard-kill systemd stop path for stuck long-lived
  WebSockets. Protocol PING diagnostics require the custom server shim;
  route JSON logs cannot observe those frames.
- Redis temperature channels are `esp32:temperature:telemetry`,
  `esp32:temperature:rpc_results`, and `esp32:temperature:commands`. Route
  readings through the main app's `esp32_temphum` persistence/broadcast helper.
- Normalize finite readings before persistence: temperature `-50..80°C`,
  humidity `0..100%`. Keep FMI/thermostat refresh off the subscriber/request
  path. Suppress reconnect status-only `temperature_reading` frames only when
  measurement keys are absent and `metrics`/`ws_stats` identify the frame;
  other malformed readings still reach validation.
- Log first live Redis status at INFO, recurring summaries at DEBUG unless
  `ESP32_REDIS_STATUS_INFO_REPEAT_INTERVAL_S` explicitly enables INFO repeats.
  Build active firmware JSON with ArduinoJson serialization/deserialization.

## YNAB categorizer

Details: [YNAB documentation](../features/ynab.md).
Code: `app/services/ynab/`, `app/core/_controller/ynab_categorizer.py`.
Tests: `tests/test_ynab_categorizer*.py`.

- Persist budget-aware config through Controller (`ynab_categorizer_config`,
  singleton `id=1`). Keep Root-Admin and CSRF guards on browser mutations.
- The single queue merges uncategorized and categorized-but-unapproved items.
  Default `strict` mode skips transfers and split parents; hide reconciled
  items unless enabled (`cleared`, case-insensitive). Exclude Starting Balance
  by normalized payee name and `starting_balance`/`starting-balance` IDs.
- Date limits use transaction dates and configured days/months/years.
  Bulk suggestions default to High confidence; medium requires opt-in.
- Category choices exclude hidden/deleted entries; list the ten locally most
  used first, then alphabetical remainder. Fallback category must be visible.
  Show account context rather than transaction UUIDs in rows.
- Rules in `custom_rules_json` run top-to-bottom, first match wins; payee
  matching uses `normalize_payee()`.
- The current UI submits visible rows through `/review-commit`, approving
  unchanged categories and categorizing/approving changed ones.
- Apply patches both category and approval. Approve-as-is applies to already
  categorized transactions; fetch unapproved items with `type=unapproved`.
- Persisted test mode suppresses remote writes and local learning changes for
  apply/approve/review-commit. Queue and review validation still read YNAB;
  bootstrap still replaces local learning data. It is not an offline mode.

## Medicine calculator

Code: `app/core/medicine_calculator.py`,
`app/core/_controller/medicine_calculator.py`.
Tests: `tests/test_medicine_calculator.py`, `tests/test_medicine_alert_service.py`.

- Page and API require Root-Admin; navigation is through Settings only.
- Store exact `pieces_bought` and each purchase's dose/weekday snapshot.
  Later schedule changes do not recalculate old rows unless explicitly edited.
- Calculate from the latest purchase per normalized medicine name; older rows
  are history, with no implicit stock carryover. Purchase date + 1 is day one;
  only selected weekdays consume stock. Subtract Finnish `joustoaika` as
  calendar days from the run-out date.
- Daily webhook batches use latest purchases. Successful/no-eligible dates
  are complete; failed batches have a cooldown and per-date attempt limit.

## Bounded car-heater execution (protocol bounded-v1)

- `POST/GET/DELETE /api/car_heater/bounded` is authenticated; mutations require
  browser CSRF. Manual duration is positive and at most 180 minutes (default 60).
  Sessions retain immutable original finish and permanent #5 identity. Execution
  JSON is separate from installation snapshots and driver outcomes.
- Timed ON requires a fresh relay-local capability and confirmed OFF. The same
  envelope (session UUID, issue time, 30-second delivery lifetime, original finish,
  Shelly boot token and ESP32 boot token) crosses HTTP and WS. Firmware 3.1.1 uses
  one `RelayExecutor`; OFF bypasses lifetime/cooldown and remains retryable.
- The Shelly bounded-v1 script persists a high-water mark before ON, arms
  `Switch.Set(toggle_after)` once, and never rearms duplicates. Its native relay
  timer runs without router/server access or a running script. Shelly/script
  restart requests OFF and never restores ON; required startup configuration is
  checked before authorization. ESP32 reboot rejects commands from its prior boot.
- Server restart requests OFF with the existing UUID/finish. Expiry or an
  unconfirmed start past its delivery lifetime requests OFF. Lost/failed OFF stays
  pending/failed-or-unknown; retries are spaced five seconds apart, while explicit
  Stop remains immediate. Only matching relay-local stopped evidence confirms
  shutdown. Stale relay evidence is shown unknown after 30 seconds.
- Start suspends and persists disabled comfort, calibration and charge mode and
  cancels old Ready-by schedules. Starts through those public services are blocked
  until shutdown is confirmed. Finish never automatically resumes these modes.
  Comfort remains available when explicitly selected outside bounded heating.
- Execution milestones distinguish request, HTTP delivery, device execution,
  observed relay ON/OFF, powered draw and confirmed shutdown; energy counter
  endpoints are retained. Existing raw telemetry remains permanent. The test
  status endpoint cannot update the bounded session or acknowledge its commands.
- Software simulations do not establish deployed relay timer/reboot behavior or
  cold-range suitability. No Shelly cold-ambient block is added. Owner-controlled
  setup and physical acceptance are in #12; see
  [bounded heating setup](../operations/bounded-car-heater.md).

## Departure defrost planning

- `/api/car_heater/departure` GET/POST/DELETE and POST `/departure/preview`
  share authenticated browser CSRF controls. Preview does not persist or actuate;
  explicit Save creates a permanent defrost session and revision. A partial unique
  index permits one current plan; replacement retains historical plans/sessions.
  Schema and Alembic revision `20261015_0014` must be deployed together.
- Use existing FMI observations: 90 minutes at/above 0 °C, 120 at −5 °C,
  150 at −10 °C, 180 at/below −15 °C. Linear interpolation is rounded up to
  whole minutes. An observation is fresh at 0–1800 seconds inclusive, based
  on its aware observation timestamp. Missing, nonfinite, outside −80…60 °C,
  invalid-time, future or stale observations select 180 minutes and zero grace.
- Departure uses Europe/Helsinki wall time or an explicit ISO offset. Reject
  spring gaps and require fold 0/1 for repeated autumn wall time. Store UTC ISO
  strings. Grace is 0–30 minutes, capped by unused allowance: preheat plus grace
  never exceeds 180 minutes. Saved start/departure/finish do not drift with later
  weather, device availability, delivery, recovery or zero off-state power.
- The app-owned heater loop and shared HTTP/WS telemetry path tick the planner.
  Execution reuses the saved session and #7's original-finish envelope, relay-local
  enforcement, mode suspension and confirmed-OFF guards. An execution attempt is
  durable before ON; restart requests OFF, never another ON allowance. Cabin or
  experimental glass readings cannot shorten a defrost session or start comfort.
- Cancel pending plans without actuator calls. Cancellation after a start requests
  bounded OFF; Stop remains independent and retryable. Active bounded heating
  blocks replacement until confirmed stopped. Save from the page compares the
  expected preview and rejects a changed timeline. Field edits/blur do not save.
- Retain planning, start and departure weather source/station, observation time,
  capture time and fallback reason. Start/departure ticks read cached observations
  without network waits. Capture after downtime is labelled with its actual time.
  Session review exposes revisions, reasons, late requests and execution evidence;
  a schedule or command request is never a readiness or relay-state confirmation.
