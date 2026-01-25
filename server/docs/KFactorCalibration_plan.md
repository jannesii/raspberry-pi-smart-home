# Step-by-step plan: `KFactorCalibrator` for “Ready-by” prediction

## 0) Goal and core idea
**Goal:** predict `time_to_target` (minutes) for cabin temperature reaching `target_temp`.

### 0.1 Fixed physical constants (initial defaults)
These are the current constants you listed in `TODO.md` and should be explicitly carried through the implementation and calibration math:
- `cabin_volume_m3  = 2.8f`
- `heater_power_W   = 1000.0f`
- `air_density_kg_m3 = 1.2f`
- `specific_heat_J_kgK = 1000.0f`

Notes:
- In Python these are plain floats (no `f` suffix), but keep the same *values* and units.
- If Shelly instantaneous power is available (`CarHeaterStatus.instant_power_w`), you can use it as `P` during fitting; otherwise fall back to `heater_power_W`.

Use a **first-order heat model with losses** using outside temperature:
- Heat capacity (fixed): `C = air_density_kg_m3 * cabin_volume_m3 * specific_heat_J_kgK`
- Dynamics (heater ON):
  - `dT/dt = (eta * P / C) - (k_loss / C) * (T - T_out)`
- Dynamics (heater OFF) (optional but useful for validation):
  - `dT/dt = - (k_loss / C) * (T - T_out)`

Store and learn **two parameters**:
- `k_loss` (W/K): “how leaky the car is”
- `eta` (0..1): “effective heating efficiency” (captures warm-up into plastics/glass, airflow, etc.)

This is robust for Ready-by and uses your WeatherService (`t2m`, `ws_10min`).

---

## 1) Inputs and dependencies
### 1.1 Required inputs
- Cabin temperature samples: use the existing car heater status stream:
  - source: `POST /api/car_heater/status` (or `/api/car_heater/status/test`)
  - stored in SQLite table: `car_heater_status`
  - field: `CarHeaterStatus.ambient_temp` (treat as `cabin_temp_C`)
- Heater state + power: from the same stream
  - `CarHeaterStatus.is_heater_on`
  - optional: `CarHeaterStatus.instant_power_w` (preferred over fixed `heater_power_W` when available)
- Outside weather samples: `app/services/weather/weather_service.py:WeatherService.get_latest()` returning `WeatherData`
  - Required: `t2m` (outside temp in °C)
  - Optional: `ws_10min` (wind speed) for smarter bucketing/weighting

### 1.2 Where this fits in this repo (architecture mapping)
Target shape aligned to the existing Flask + Controller + services layout:
- **Service**: implement `KFactorCalibrator` under `app/services/car_heater/` (same area as `CarHeaterService` and `KeepAtTempService`)
  - store instance on the Flask app (similar to `app.car_heater_service`, `app.keep_at_temp_service`)
  - tick it from `app/blueprints/api/car_heater_utils.py:def handle_status_update_request(...)` right after a `CarHeaterStatus` row is recorded
- **DB access**: add DB tables to `app/core/database.py:DatabaseManager._create_tables()` and add Controller methods via a new mixin in `app/core/_controller/`
- **API** (optional, but matches existing patterns): add an API route under `app/blueprints/api/` to fetch “Ready-by” predictions + current active params for UI/debugging

### 1.3 Config
- `auto_calib_start_hhmm`, `auto_calib_stop_hhmm`
- `min_session_minutes` (e.g. 15)
- `max_session_minutes` (e.g. 120)
- `min_temp_rise_C` (e.g. 2–3°C) to ensure the run is informative
- `sample_period_seconds` (your actual logging interval)
- Quality thresholds (RMSE, monotonicity, disturbance rules)
- Bounds:
  - `eta` in `[0.05, 1.0]` (choose conservative lower bound)
  - `k_loss` in `[10, 400]` W/K (tune later, keep wide)

---

## 2) Database schema (minimum viable)
In this project, tables are created/seeded in `app/core/database.py` (no separate migration system),
so add these to `DatabaseManager._create_tables()` following the existing `tables = { ... }` pattern.

Prefer `TEXT` ISO timestamps like the rest of the DB (`car_heater_status.timestamp`, `ac_events.timestamp`, etc.).

Create these tables (or equivalent models) with car-heater-prefixed names to avoid collisions:

### 2.1 `car_heater_kfactor_session`
Fields:
- `id` (PK)
- `start_ts`, `end_ts` (ISO `TEXT`)
- `auto_window_date`, `auto_window_start`, `auto_window_stop`
- `heater_mode` (should be `ON_continuous`)
- `outside_t_mean`, `outside_t_min`, `outside_t_max`
- `wind_mean` (nullable)
- `cabin_t_start`, `cabin_t_end`, `cabin_t_max`
- `duration_s`, `sample_count`
- `flags_json` (disturbances / reasons)
- `quality_score` (0..1)
- `accepted` (bool)

### 2.2 `car_heater_kfactor_result`
Fields:
- `id` (PK)
- `session_id` (FK)
- `model_version` (string)
- `k_loss_W_per_K`
- `eta`
- `rmse_C`
- `r2` (optional)
- `confidence` (optional; can be derived later)
- `promoted` (bool)
- `created_ts`

### 2.3 `car_heater_kfactor_active_params`
Fields:
- `id` (single-row, `CHECK (id = 1)` like other settings tables)
- `k_loss_W_per_K`
- `eta`
- `updated_ts`
- `source` (e.g. “weighted_update”, “manual_override”)

Telemetry storage note (important for this repo):
- Do **not** add a separate `calibration_samples` table initially. You already persist the raw signal in `car_heater_status`.
- For session fitting, query the `car_heater_status` rows between `start_ts..end_ts` via Controller methods (same style as `get_car_heater_status_between(...)`).

---

## 3) Class responsibilities
Implement class `KFactorCalibrator` that:
1. Watches time-of-day and decides whether calibration is allowed.
2. Detects “good” heater-on segments suitable for calibration.
3. Builds a session dataset (cabin temp + outside temp aligned by time).
4. Computes a **quality score** and decides accept/reject.
5. Fits `(k_loss, eta)` using the session.
6. Saves session + result in DB.
7. Updates “active params” using a weighted strategy.
8. Avoids unnecessary calibrations with “do we need this?” logic.

---

## 4) State machine design (simple and reliable)
### 4.1 States
- `IDLE`
- `ARMED` (inside auto window, waiting for eligible start)
- `RECORDING` (collecting samples for current session)
- `EVALUATING` (after stop, compute quality and fit)
- `COOLDOWN` (avoid repeated calibrations in one window)

### 4.2 Transitions
- `IDLE -> ARMED` when now is within `[start, stop)` window
- `ARMED -> RECORDING` when heater becomes ON and stays ON for `grace_period` (e.g. 2–3 samples)
- `RECORDING -> EVALUATING` when heater turns OFF OR max duration reached OR disturbance detected
- `EVALUATING -> COOLDOWN` always
- `COOLDOWN -> ARMED` after `cooldown_minutes` if still in window
- `ARMED/COOLDOWN -> IDLE` when window ends

---

## 5) Session building (data you store and fit)
### 5.1 Sample alignment
For each cabin temp sample `ts`:
- Fetch latest weather once every N minutes OR cache it with timestamp.
- Attach `outside_temp = weather.t2m.value` (use closest in time)
- Attach `wind = weather.ws_10min.value` if available

### 5.2 Eligibility checks (start)
Start session only if:
- Within auto window
- Heater is ON
- There wasn’t a recently accepted session for similar conditions (see §8)
- Cabin temp isn’t already near target/steady (optional)

### 5.3 Stop conditions
Stop when:
- Heater OFF
- Duration > `max_session_minutes`
- Disturbance detected (see next section)

---

## 6) Disturbance detection (no extra sensors)
Reject or stop a session if any occurs:
- **Non-monotonic rise early:** for the first X minutes while heater ON, cabin temp should generally increase.
  - If you see repeated drops > `drop_threshold` (e.g. 0.5°C) across multiple samples, flag disturbance.
- **Spike outliers:** a single jump/drop beyond plausible rate (sensor glitch).
- **Outside temp discontinuity:** if outside temp changes too abruptly (weather update artifact). Clamp or smooth.
- **Short cycling:** heater toggles OFF/ON during the session.

Store flags in `flags_json` even when rejecting (valuable debugging).

---

## 7) Quality scoring (accept only good calibrations)
Compute a `quality_score` (0..1) from components:
- `duration_score` (0 if < min; saturates at ideal duration)
- `deltaT_score` (requires at least `min_temp_rise_C`)
- `smoothness_score` (penalize noisy data/outliers)
- `continuity_score` (heater ON continuous)
- `fit_score` (based on RMSE once fit is done)

Acceptance rule:
- must pass hard gates: `duration >= min`, `deltaT >= min`, no critical disturbances
- and `quality_score >= threshold` (e.g. 0.6)

---

## 8) “No unnecessary calibrations” policy (important)
Implement a `should_calibrate_now(weather, last_results, recent_prediction_errors)` rule.

### 8.1 Condition buckets
Bucket by outside temperature and wind:
- `t_bucket = round(outside_temp_C / 2) * 2` (2°C bins)
- `wind_bucket = round(wind_m_s / 2) * 2` (optional)
Use `(t_bucket, wind_bucket)` as a key.

### 8.2 Skip if already covered
Skip starting a new session if:
- There is an **accepted** calibration in the last `N days` for the same bucket, AND
- model prediction error recently is within tolerance (see 8.3)

### 8.3 Prediction error trigger (optional but powerful)
After each normal heating run, compute and store:
- `time_to_target_error = actual - predicted`
If `abs(error)` exceeds threshold (e.g. 5–10 minutes) for several runs, allow calibration even if bucket is covered.

### 8.4 Cooldown inside window
Once a calibration is accepted in the current window/day:
- enter `COOLDOWN` for e.g. 60–180 minutes
This prevents “calibration spam”.

---

## 9) Parameter fitting plan (robust and simple)
### 9.1 Use fixed heat capacity
Compute once:
- `C = air_density_kg_m3 * cabin_volume_m3 * specific_heat_J_kgK`

### 9.2 Fit parameters from heater ON session
Fit `k_loss` and `eta` by minimizing error between observed cabin temps and model simulation.

Plan for the fitter:
1. Use initial guesses:
   - `eta_init = 0.6` (or from prior)
   - `k_loss_init` derived from approximate slope:
     - Estimate early slope `dT/dt` and solve rough bounds.
2. Use bounds:
   - `eta ∈ [0.05, 1.0]`
   - `k_loss ∈ [10, 400]` W/K
3. Use robust objective:
   - minimize RMSE; optionally use Huber loss to reduce outlier impact
4. Smoothing:
   - smooth outside temp input (moving average) to avoid weather jitter

Store fit metrics (`rmse_C`, maybe `r2`).

---

## 10) Updating the “active” parameters
Do NOT just overwrite. Do a **weighted update**:
- weight = `quality_score`
- also weight down if bucket is rare/outlier or conditions are extreme

Update rule:
- `param_new = (1 - alpha) * param_old + alpha * param_fit`
- where `alpha = clamp(quality_score * base_alpha, alpha_min, alpha_max)`
  - e.g. `base_alpha=0.3`, `alpha_min=0.05`, `alpha_max=0.5`

Optionally maintain separate params per bucket:
- If you want fastest improvement: keep `k_loss` as function of outside temp bucket (table), and keep `eta` global.
- Store all in DB; choose best at runtime by nearest bucket.

---

## 11) How Ready-by prediction uses the calibrated model
Given current cabin temp `T0`, outside `Tout`, target `Ttarget`, heater power `P`:
- Compute steady-state:
  - `Tss = Tout + (eta * P) / k_loss`
- If `Ttarget >= Tss - margin` (e.g. 0.5°C):
  - return “not reachable” or switch to best-effort ETA capped.
- Else compute time to target with the first-order solution:
  - `T(t) = Tout + (eta*P)/k_loss + (T0 - Tss)*exp(-(k_loss/C)*t)`
  - solve for t:
    - `t = -(C/k_loss) * ln( (Ttarget - Tss) / (T0 - Tss) )`
Use guards for domain/log safety.

Store predicted vs actual for feedback (§8.3).

---

## 12) Public API of `KFactorCalibrator` (what other code calls)
Define methods:
- `tick(car_status: CarHeaterStatus)`  
  Called from `handle_status_update_request(...)` when a new `car_heater_status` row is recorded (use `ambient_temp`, `is_heater_on`, and optionally `instant_power_w`).
- `get_active_params(now_ts, weather)`  
  Returns current best `(k_loss, eta)` (global or bucketed).
- `should_calibrate(now_ts, weather)`  
  Exposes decision (useful for logs/UI).
- `finalize_session(reason)`  
  Ends recording, evaluates, fits, writes DB.
- `record_prediction_outcome(run_id, predicted_eta_minutes, actual_eta_minutes, context)`  
  Enables drift detection.

---

## 13) Logging and observability (so it’s maintainable)
Log events with clear reasons:
- “Session started: inside window, heater ON stable”
- “Session rejected: duration too short”
- “Session rejected: disturbance detected (non-monotonic)”
- “Session accepted: quality=0.78 rmse=0.42 k_loss=… eta=…”
- “Skipped calibration: bucket covered + low error”

---

## 14) Milestone implementation order (recommended)
1. Add DB tables to `app/core/database.py` + dataclasses in `app/core/models.py` + Controller mixin methods in `app/core/_controller/`.
2. Add `KFactorCalibrator` under `app/services/car_heater/` and initialize it in `app/services/__init__.py:init_services(app)` (store on `app`).
3. Wire `KFactorCalibrator.tick(...)` into `app/blueprints/api/car_heater_utils.py:handle_status_update_request(...)` after recording `CarHeaterStatus`.
4. Update `test_car_heater.py` to add a **KFactor calibration simulation mode** (same style as the existing temperature simulation):
   - generate a realistic heater-on segment using the model + the fixed constants (`cabin_volume_m3`, `heater_power_W`, `air_density_kg_m3`, `specific_heat_J_kgK`)
   - send `/api/car_heater/status/test` payloads with `temperature` + `shelly` power to drive the calibrator, **status db writes are ok (tables that have only 1 row), AVOID OTHER DB WRITES** (test endpoint must stay “no persistence” end-to-end)
   - optionally simulate outside temp/wind as constants (or slowly varying) so calibration bucketing is exercised
   - include a “known truth” `(k_loss, eta)` so you can compare fitted vs expected
5. Implement state machine + session boundaries + DB writes (no fitting yet).
6. Implement quality gates + disturbance flags.
7. Implement fitter for `(k_loss, eta)` and “active params” weighted update.
8. Add bucket logic, skip rules, and prediction error feedback.
9. (Future) add humidity-based classifier (frost vs normal) once you have a reliable humidity signal.

---

## 15) Future-smart upgrades (optional, keep in backlog)
- Bucket `k_loss` by wind bucket using `ws_10min` (wind often changes losses).
- Add “frost start” classifier later using humidity; maintain separate `eta` for frosty starts.
- Learn “sensor bias drift” by comparing long-term residuals; auto-correct offset.
- Add a second cabin sensor (front/back) to detect stratification and improve fits.
