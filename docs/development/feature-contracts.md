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
- Diagnostics accepts only boolean command acknowledgements; absent/malformed
  results remain `unknown` through HTTP and Redis. Browser command-stage
  updates merge present fields, and a local 30 s timeout never proves failure
  or execution. Restart confirmation never changes the immediate Stop path.
  Latest log snapshots are app-scoped, transient and available to authenticated
  HTTP status polling; old snapshots cannot satisfy a new log request.
- Detailed history retains the original row payloads and adds sample-state
  series using the session-review evidence rules, explicit Helsinki-day UTC
  boundaries (including DST), and the configured sensor gap limit. Failed reads
  are null points; late samples keep sample time, never receipt time as a value.
- WebSocket status runs the same charge-mode, keep-at-temperature, ready-by,
  kFactor, alert, and persistence work as HTTP status.
- Ready-by reaching its target enables Keep at Temperature, persists
  `completed`, and ends that tick before start-time logic can run again.
- Ready-by schedule create/replace/cancel (`/api/car_heater/ready_by/schedule`
  and the `ready_by_schedule` event) require login and browser CSRF on both
  transports. The Heating page sends Helsinki wall time (naive ISO); creation is
  refused while a bounded session is not confirmed stopped; cancel never sends OFF
  from the page (`turn_off=false`).
- Comfort (Keep at Temperature) changes go through
  `KeepAtTempService.apply_changes` from both `PATCH /api/car_heater/comfort`
  and the CSRF-checked `keep_at_temp_settings` event: partial, validated,
  persisted before the in-memory copy, and refused (nothing saved) while a
  bounded session is not confirmed stopped. Stopping comfort never sends OFF.
- `car_heater_installation` is append-only owner configuration; new manual and
  departure sessions copy the newest row into their snapshot. Snapshotting
  tolerates the table being unreadable so a lagging migration never blocks
  heating. Device facts are live reports only; never invent missing ones.
- Calibration and Ready-by tuning changes go through
  `calibration_settings.apply_calibration_changes`/`run_calibration_action`
  from both `/api/car_heater/calibration` and the CSRF-checked
  `kfactor_control`/`ready_by_control` events: partial, range-validated,
  persisted before the in-memory copy (`save_settings`), and enabling
  autonomous calibration is refused while a bounded session is not confirmed
  stopped. Disabling or resetting ends autonomous arming/recording. Keep the
  page's field bounds in `KFACTOR_FIELDS`; calibration never feeds departure
  planning. Preserve DB refresh of persisted config in workers.
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

- Session review (`GET /api/car_heater/sessions/<id>/measurements`) is
  read-only and never calls heater services. Raw frames stay session-independent;
  a review window derived from execution/plan/session evidence selects them.
  Failed reads and gaps over the sensor freshness limit stay gaps, and Shelly-less
  frames add no power points. The +3 °C/ten-minute reference-glass indicator counts
  only `ok` samples, breaks on gaps over a fixed 60 s (not the live freshness
  setting, so candidate times stay reproducible) and is display-only: never a control, comfort or readiness
  input. An already-clear outcome is only an upper bound on clearing time.

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
- Session reads add derived `mode` (start reason) and `remaining_s` (server
  clock), never stored. The Heating view renders heater state only from them
  and fresh telemetry; browser outside-condition payloads share one builder
  carrying the observation time across page render, HTTP and Socket.IO.
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

## Weather observation archive

Code: `app/services/weather/observation_archive.py`, `app/services/weather/fmi_wfs.py`
(shared fetch, namespaces, aligned scheduler),
`app/core/_controller/weather_archive.py`, `app/blueprints/api/car_heater/weather.py`.
Tests: `tests/test_weather_observation_archive.py`, `tests/test_weather_observation_recovery.py`,
`tests/test_weather_archive_migration.py`.

- `ObservationCollector` is app-owned and runs on its own thread, independently
  of sessions, telemetry, browsers and departures. It never reads or refreshes
  the planner's `WeatherService` cache and never calls heater services.
  Fetch/parse/archive failures stay inside the collector and remain visible.
- Each aligned ten-minute slot requests both stations (101486, 137188) for the
  preceding two hours at `timestep=10`. Each station succeeds or fails alone.
  Restart re-collects the current slot; deduplication prevents duplicate rows.
- Keep the source text and per-field time. `NaN` is `missing` with value `None`;
  non-numeric text is `invalid`. Zero and special codes are stored unchanged.
  NaN padding between hours in `r_1h` is not a reading. Units and periods come
  from a table copied from FMI parameter metadata; `property_href` keeps the
  source metadata link. Do not infer meanings from parameter names.
- Readings are keyed by station, parameter and source time. A differing value or
  status inserts the next `revision`; never update or delete earlier revisions.
  Every attempt is a `weather_retrieval` row. Successful replies are stored
  gzip-compressed once per SHA-256 of the original bytes; a failed or malformed
  reply stores no content. FMI stamps each reply, so nearly every retrieval
  stores its own reply (about 4 KB compressed); reading revisions are the main
  deduplication.
- APIs are authenticated GETs. Observation/retrieval/health JSON references
  replies by id; only `/weather/replies/<id>` returns content, after a checksum
  check. Health distinguishes received-but-empty fields from failed, stale (no
  attempt for 25 minutes) or not-running collection.
- Recovery runs after a successful live request, on the collector thread.
  `weather_collection_progress` fixes `history_start` (24 h before the first
  attempt, or before the earliest pre-existing retrieval: `origin=upgraded`)
  and `initialized_at`. Coverage derives only from retrievals: a window spans
  `[requested_start, requested_end + step)`. A source time an ok reply
  answered is `collected` when any field has a valid value there, otherwise
  `source_empty` (not re-requested); unanswered time is `failed`/
  `not_collected` (repairable) or `unrepaired` (older than seven days). Live
  requests for all stations precede any recovery. Repairs use UTC-aligned
  six-hour windows, oldest first, budgeted per slot, stopping at the first
  failure. Retrieval `purpose` is `live`, `initialization` (the history the
  progress row set out to fill, including an upgrade's) or `repair`; `as_of`
  filters by retrieval time.

## Parking-point forecast archive

Code: `app/services/weather/forecast_archive.py` (`ForecastPoint` owns the
`lat,lon` source identity),
`app/core/_controller/weather_forecast.py`, `app/blueprints/api/car_heater/weather.py`.
Tests: `tests/test_weather_forecast_archive.py`, `tests/test_weather_forecast_migration.py`.

- `ForecastCollector` has its own thread, with the same isolation as
  observations. It requests HARMONIE point timevaluepair for one point at
  `timestep=60` every aligned thirty-minute slot. It requests a window far
  wider than any run (`WINDOW_BEFORE`/`WINDOW_AFTER`), because FMI's default
  window moves hourly and would turn every check into a new revision. NaN filler
  outside the run's first..last real value is counted (`outside_run_points`),
  not stored; missing values inside the run are kept. Retrievals use
  `kind="forecast"` with `source_id` = the `lat,lon` point.
- Analysis time (`om:parameter` analysisTime), `resultTime`, each value's valid
  time and the local retrieval time are separate fields. Units, periods and
  statistics come from a table copied from FMI forecast metadata, and
  `property_href` keeps the link. Never infer RadiationLW direction.
- Series sharing procedure, analysis time, result time and returned location
  form one version. Values are stored once per SHA-256 of their source text
  (`weather_forecast_content`). A version is unique by source, request JSON,
  model/times, location and content. A reply matching the origin's latest
  revision adds only a `weather_forecast_receipt`; anything else, including
  content reverting to an earlier revision, inserts the next `revision`.
  Never update or delete versions, values or receipts. Bump
  `METADATA_VERSION` with any `PARAMETER_META` change: it is part of the
  stored request, so new units start a new configuration, not a revision.
- `unobserved` is derived from successful forecast retrievals (gap > 1.5 ×
  interval). It never claims a run was received. Forecast health is stale
  after 2.5 × interval and summarises the last 24 hours' `unobserved_24h`.
  Version JSON never embeds reply content.

## Weather evidence, exports and restore

Code: `app/blueprints/api/car_heater/weather.py` (`/sessions/<id>/weather`,
`/weather/export`), `app/static/js/car_heater_session_weather.js`,
`scripts/heater_history.py`. Tests: `tests/test_weather_evidence_export.py`,
`tests/test_weather_evidence_restore.py`, the weather browser flows.

- Session weather is read-only and derives its window from `session_window`,
  the Measurements window. It reports coverage, reading counts, health, reply
  ids and forecast versions for that window without assigning rows to the
  session or touching its snapshots. "Held at anchor" is what the latest
  successful check at or before the heating anchor received.
- Exports are authenticated GETs bounded to 7 days (observations by source
  time, coverage) or 2 days (forecasts by valid time); date ranges count
  Helsinki calendar days. They keep every revision and never embed reply
  content. Coverage reflects the archive at export time, also for `as_of`.
- Reply and version ids are bounded integers. Discovery's `weather_fingerprints`
  must match between a source and its restore; integrity lists must be empty.

## Spoken announcements

- Code: `app/services/announcements/` (service, content, media, synthesis,
  transport, access), `AnnouncementMixin`, `announcements_api.py`,
  `announcement_media.py`. Tests: `tests/test_announcement*.py`,
  `tests/announcement_support.py` (doubles only for Piper and Cast).
- `AnnouncementService.tick()` is the processing interface. Handlers only
  validate and record requests. Synthesis, probes and Cast calls run in `tick()`.
  Content preparation, media preparation and delivery stay separate; only static
  owner text exists. A processor-only lock serializes ticks; short state locks
  protect preview snapshots and finishing identities. No request shares a lock
  held over synthesis or provider waits. Terminal history deletion uses atomic
  Controller guards plus an in-memory finishing guard until restoration and
  occurrence settlement end. Stops recorded before a tick precede previews.
- Cast probes distinguish receiver readiness from media readiness. For a fresh
  Default Media Receiver connection, an explicit `MEDIA_STATUS` response with an
  empty session list establishes idle; no usable reply within the bounded wait
  remains unknown. Volume restoration rechecks the retained ownership listener
  after probing. Unknown state leaves volume unchanged without claiming newer
  owner intent, and loss of volume ownership also prevents music restoration.
- Playback launches the Default Media Receiver first when another Cast app
  (e.g. Spotify) is running: PyChromecast would otherwise send the load to that
  app, which discards it unfetched. A failed launch is `launch_failed` with
  `certain_not_played`.
- Optional Spotify connect, enable (including refresh) and device discovery are
  queued to a feature-local worker independent of tick. OAuth callback validates
  the session/state/owner then redirects immediately. Settings status exposes
  `operation` (`connecting`, `saving`, `loading_devices`, or null), safe `reason`,
  cached `available_devices`, and `devices_checked` for an empty successful read.
  `GET /spotify/devices` queues a refresh and
  returns 202 with status; clients poll `/spotify` or `/status` for completion.
  Disable/disconnect are immediate local changes. New settings intent invalidates
  older queued/in-flight results; provider work stages credentials/settings and
  publishes only if its generation remains current. Device/settings results
  preserve credentials refreshed by concurrent playback since the job snapshot.
  All settings and playback token reads share one refresh owner, which publishes
  rotated credentials before subsequent provider I/O. A concurrent caller sees
  `refresh_pending` rather than waiting or rotating the same token again; media
  evidence stays unknown, and settings can be retried. Settings errors remain
  visible across unrelated playback successes until the settings action succeeds
  or the owner disables/disconnects. Device reads do not replace
  queued settings changes. Queues and OAuth codes are process-local; restart
  drops unfinished settings work and the owner can retry. No general job system
  or automatic replay is added.
- One asset per announcement: generated chime, then 0.5 s silence, then speech at
  the speech sample rate. Cache keys hash text, language, voice, engine and chime;
  eviction never touches attempt rows, which keep only `media_key`.
- Attempt `status`: `requested` → `dispatching` (intent committed before any Cast
  effect) → `dispatched` → `playing` → `finished`/`failed`/`unknown`/`stopped`.
  Updates are conditional on the expected status. On construction, the service
  marks `dispatching`/`dispatched`/`playing` attempts `unknown`
  (`interrupted_by_restart`) and never replays them or touches volume.
- Dispatch only to an idle probe; a configured `device_id` must match. Attempts
  whose `speaker_set` overlaps an active or earlier queued attempt wait. Each
  attempt has `dispatch_deadline` that queueing never extends. Volume restore
  requires the speaker to still be idle and at the applied level.
- Media capability: `GET /announcement-media/<token>.wav` without login or
  cookies. It stores a token hash in memory and serves one asset until 10 minutes
  pass or the attempt ends. Unknown and expired tokens both return 404. Logs carry
  neither tokens (security header debug path is redacted) nor reminder text.
- Root-Admin + CSRF: `/api/announcements/status`, `previews` (POST, GET, audio),
  `tests` (GET/POST with idempotent `request_id`), `tests/<id>` and
  `tests/<id>/stop`, `schedules` (GET/POST idempotent `request_id`),
  `schedules/<id>` (GET, PUT with optional `revision`, DELETE),
  `schedules/<id>/pause|resume`, `occurrences`, `occurrences/<id>`.
  Page: `/settings/announcements`.
- Schedules (`AnnouncementScheduleMixin`, `occurrences.py`, `schedules.py`):
  one-time due times are Helsinki wall time (`due_local` + `due_fold`), stored
  as UTC ISO. Recurring rules live in `recurrence` JSON
  (`{"weekdays": [ISO 1-7], "times": ["HH:MM"]}`, Helsinki wall time; no due
  columns). Deletion is soft (`status='deleted'`). `next_due_at` is the only
  materialization cursor; `instance_after()`/`next_instance()` give instances.
- Each tick materializes every passed instance (bounded batch), so downtime
  leaves one occurrence per missed instance and the expiry check closes stale
  ones `missed` before dispatch. Spring-gap times are instances at the
  pre-change offset, inserted closed `missed`/`dst_gap` (fallback
  `expired_undelivered`); autumn repeats use the first occurrence only.
  API items add `instance_local`; gap upcoming items add `local` + `missed`.
- An occurrence is created when due, unique on `(schedule_id, instance_due_at)`,
  and snapshots the schedule. `status` (waiting/announcing/closed), `delivery`
  (attempt evidence) and `disposition` (missed/cancelled; later owner actions)
  are independent. `fallback_reason` (failed/partial/unknown/
  expired_undelivered) is durable fallback eligibility; cancellation never sets
  it. `expires_at` is fixed at creation; waiting never moves it.
- Occurrence attempts are inserted already `dispatching` in the same
  transaction that moves the occurrence to `announcing`, conditional on it
  still waiting, without disposition and unexpired. Pause/delete updates lose
  or win that race as a whole. Restart: in-flight attempts become unknown, the
  next tick settles the occurrence (fallback `unknown`); waiting occurrences
  continue inside their window; expired ones close as `missed`.
- Ordinary occurrences wait on quiet hours, `playing`/`unknown` probes, a
  10 s idle debounce after media was last seen busy (in memory; restart forgets
  it), unreachable speakers and overlapping active work. Urgent ones sort first,
  may dispatch on `playing`/`unknown` (recorded as `interrupted_media`,
  `media_restore='unavailable'`) and never bypass quiet hours.
- Fallback (`fallback.py`, `AnnouncementFallbackMixin`): each tick opens one
  `announcement_fallback` per occurrence with `fallback_reason` (unique
  `occurrence_id`); the message is composed once (reminder text, reason or
  waited-for context, link `ANNOUNCEMENT_PUBLIC_ORIGIN` +
  `/settings/announcements?occurrence=<id>`) and sent at once through
  `ALERT_WEBHOOK_URL` with `allowed_mentions={"parse": []}`, never the batcher.
  A numbered `sending` attempt row is committed before each request. Confirmed
  failures (HTTP error, 429, never sent) retry after 60 s/300 s or the server's
  `retry_after`, at most 3 requests; 4xx other than 408/429 stop at once.
  Timeouts after sending and restarts mid-request are `unknown` and never
  resent. Missing webhook → `unavailable`. Accepted is not read/acknowledged.
- `alert_webhook.deliver_webhook()` returns `WebhookResult` (accepted/rejected/
  rate_limited/failed/not_configured/unknown) and never logs the URL;
  `send_alert_webhook()` keeps its payload (mentions allowed) and boolean.
- Repeats: schedules carry `max_announcements` (1–5) and
  `repeat_interval_minutes` (1–120, required iff more than one); API adds
  `repeats{fit, offsets_minutes}` (announcement k at k × interval, before
  expiry). `_settle` returns a delivered occurrence to `waiting` with
  `next_attempt_purpose='repeat'` at previous `intent_at` + interval (or now if
  later: no burst), else closes it (group `partial` is never repeated or retried); a delivered occurrence that expires while
  waiting closes without disposition or fallback.
- Retries: only a `failed` attempt with `certain_not_played` (TransportError
  certain) is retried, at most `max_automatic_retries` (2) after
  `retry_delay_s` (60 s) and only before `expires_at`; `retries_started` is
  counted apart from `announcements_started`. Unknown outcomes are never
  retried. No retry left → closed `failed` (fallback `failed`, also when the
  retry window expires while held).
- Done/Skip (`POST occurrences/<id>/done|skip`): one `announcement_acknowledgement`
  per occurrence (unique; keeps prior status/delivery/disposition), disposition
  `done`/`skipped`, waiting → closed, announcing keeps current speech without
  follow-ups. Same action again → 200, other → 409 `already_acknowledged`. A
  `pending` fallback becomes `cancelled`/`acknowledged` (also when opened or
  about to send after the acknowledgement); sending/sent/unknown stay. Never a
  medicine dose, BP value or refill change.
- Stop speaking (`POST occurrences/<id>/stop`, 202): sets the active attempt's
  `stop_requested_at` (actual `stop_outcome` from the speaker) and disposition
  `stopped` (waiting follow-up → closed); 409 `not_speaking` before any speech.
  Never acknowledges and never sets or cancels a fallback. Item `actions` lists
  what applies now.
