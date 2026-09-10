# Feature contracts

Read the section relevant to the code being changed. These notes preserve
non-obvious behavior; implementation and regression tests define the details.
Update or remove notes when that behavior changes. Add new regression cases to
tests instead of growing a chronological list of past bugs here.

## Car heater and kFactor

Code: `app/services/car_heater/`, `app/core/_controller/car_heater*.py`,
`app/blueprints/api/car_heater/`, `app/static/js/car_heater*.js`.
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
