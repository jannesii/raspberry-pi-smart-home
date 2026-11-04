/**
 * Car heater Heating and Settings views: live status, freshness, Diagnostics
 * command stages and maintenance commands over Socket.IO with HTTP fallback.
 */

let statusPollTimer = null;

// ============================================
// Utility Functions
// ============================================

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('en-GB', {
      timeZone: 'Europe/Helsinki', hourCycle: 'h23',
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return '—';
  }
}

function fmtNum(v, unit = '', digits = 1) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${n.toFixed(digits)}${unit}`;
}

// ============================================
// Heating View: power, device and weather freshness
// ============================================

// Matches the server's stale relay-evidence limit; the departure planner's
// weather freshness limit is 30 minutes.
const DEVICE_REPORT_MAX_AGE_S = 30;
const WEATHER_MAX_AGE_S = 1800;
let latestDeviceReport = null;
let latestDeviceReportMs = 0;
let latestWeather = null;

function fmtHelsinkiTime(ts, withSeconds = false) {
  const d = new Date(ts);
  if (!ts || Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-GB', {
    timeZone: 'Europe/Helsinki', hourCycle: 'h23', hour: '2-digit', minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}),
  });
}

function fmtHeaterReportAge(seconds) {
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))} s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  return `${(seconds / 3600).toFixed(1)} h ago`;
}

// The server judges report age by its own clock; the browser only advances
// it locally, so a skewed phone clock cannot make an old report look fresh.
function deviceReportAgeS() {
  const age = Number(latestDeviceReport?.report_age_s);
  if (!latestDeviceReport || latestDeviceReport.report_age_s == null || !Number.isFinite(age)) return null;
  return age + (Date.now() - latestDeviceReportMs) / 1000;
}

function deviceReportIsFresh() {
  const age = deviceReportAgeS();
  return age !== null && age <= DEVICE_REPORT_MAX_AGE_S;
}

function updateDeviceReport(status) {
  // A response without a report keeps the last one; it then ages to stale
  // instead of disappearing.
  if (status) {
    const age = deviceReportAgeS();
    latestDeviceReport = {...latestDeviceReport, ...status};
    if (status.shelly_connected === false) {
      ['is_heater_on', 'instant_power_w', 'voltage_v', 'current_a', 'energy_total_wh', 'energy_last_min_wh', 'device_temp_c'].forEach(key => { latestDeviceReport[key] = null; });
    } else if (typeof status.is_heater_on === 'boolean') latestDeviceReport.shelly_connected = true;
    if (!Object.prototype.hasOwnProperty.call(status, 'report_age_s')) latestDeviceReport.report_age_s = age;
    latestDeviceReportMs = Date.now();
  }
  const power = document.getElementById('conditionPower');
  // Without Shelly data, or once the report is stale, the relay and its power
  // are unknown, not zero.
  if (power) {
    const shellyKnown = !!latestDeviceReport && typeof latestDeviceReport.is_heater_on === 'boolean';
    power.textContent = shellyKnown && deviceReportIsFresh() ? fmtNum(latestDeviceReport.instant_power_w, ' W', 0) : '—';
  }
  renderDeviceFreshness();
  window.dispatchEvent(new CustomEvent('car_heater_telemetry', {
    detail: { status: latestDeviceReport, isFresh: deviceReportIsFresh },
  }));
}

function renderDeviceFreshness() {
  const el = document.getElementById('deviceFreshness');
  if (!el) return;
  const age = deviceReportAgeS();
  const reported = latestDeviceReport?.timestamp;
  if (age === null) {
    el.textContent = 'No device report yet.';
    const health = document.getElementById('diagnosticsDevice');
    if (health) health.textContent = el.textContent;
    el.classList.remove('is-stale');
    return;
  }
  const shelly = typeof latestDeviceReport.is_heater_on === 'boolean' ? '' : ' Relay not connected.';
  const stale = age > DEVICE_REPORT_MAX_AGE_S;
  el.textContent = stale
    ? `Device not reporting · last report ${fmtHelsinkiTime(reported, true)} (${fmtHeaterReportAge(age)}).${shelly}`
    : `Device reported ${fmtHelsinkiTime(reported, true)} (${fmtHeaterReportAge(age)}).${shelly}`;
  el.classList.toggle('is-stale', stale || !!shelly);
  const health = document.getElementById('diagnosticsDevice');
  const relay = document.getElementById('diagnosticsRelay');
  if (health) health.textContent = el.textContent;
  if (relay) relay.textContent = stale ? 'Unknown · device report stale' : typeof latestDeviceReport.is_heater_on === 'boolean' ? 'Connected · reported by device' : 'Not connected · relay state unknown';
}

function updateWeatherDisplay(weather) {
  if (weather) latestWeather = weather;
  const w = latestWeather;
  const heroOutsideTemp = document.getElementById('heroOutsideTemp');
  const heroWind = document.getElementById('heroWind');
  const freshness = document.getElementById('weatherFreshness');
  if (heroOutsideTemp) heroOutsideTemp.textContent = fmtNum(w?.outside_temp_c, ' °C', 1);
  if (heroWind) heroWind.textContent = fmtNum(w?.wind_speed_mps, ' m/s', 1);
  if (!freshness) return;
  const observed = Date.parse(w?.observed_at);
  if (!Number.isFinite(observed)) {
    freshness.textContent = 'no weather observation yet';
    return;
  }
  const age = (Date.now() - observed) / 1000;
  const station = w.station_name ? `FMI ${w.station_name}` : 'FMI';
  freshness.textContent = `${station} observed ${fmtHelsinkiTime(observed)}${age > WEATHER_MAX_AGE_S ? ` · stale (${fmtHeaterReportAge(age)})` : ''}`;
}

// ============================================
// Diagnostics: latest device telemetry
// ============================================

function updateDetailsSection(status) {
  const detailPower = document.getElementById('detailPower');
  const detailAmbient = document.getElementById('detailAmbient');
  const detailDeviceTemp = document.getElementById('detailDeviceTemp');
  const detailVoltage = document.getElementById('detailVoltage');
  const detailCurrent = document.getElementById('detailCurrent');
  const detailEnergyToday = document.getElementById('detailEnergyToday');
  const detailEnergyTotal = document.getElementById('detailEnergyTotal');
  const detailSource = document.getElementById('detailSource');
  const lastUpdateTime = document.getElementById('lastUpdateTime');

  if (!status) return;

  if (detailPower) detailPower.textContent = fmtNum(status.instant_power_w, ' W', 1);
  if (detailAmbient) detailAmbient.textContent = fmtNum(status.ambient_temp, ' °C', 2);
  if (detailDeviceTemp) {
    // A hot relay is stated in text; color only reinforces it.
    const temp = Number(status.device_temp_c);
    const hot = status.device_temp_c == null || !Number.isFinite(temp) ? '' : temp >= 50 ? ' · very hot' : temp >= 40 ? ' · hot' : '';
    detailDeviceTemp.textContent = fmtNum(status.device_temp_c, ' °C', 1) + hot;
    detailDeviceTemp.classList.toggle('value-warning', hot !== '');
  }
  if (detailVoltage) detailVoltage.textContent = fmtNum(status.voltage_v, ' V', 1);
  if (detailCurrent) detailCurrent.textContent = fmtNum(status.current_a, ' A', 2);
  if (detailEnergyToday) detailEnergyToday.textContent = fmtNum(status.energy_last_min_wh, ' Wh', 0);
  if (detailEnergyTotal) detailEnergyTotal.textContent = fmtNum(status.energy_total_wh, ' Wh', 0);
  if (detailSource) detailSource.textContent = status.source || '—';
  if (lastUpdateTime) lastUpdateTime.textContent = fmtTs(status.timestamp);
}

// ============================================
// Glass & Cabin Sensor Readings
// ============================================

const CAR_SENSOR_ROWS = [
  { sensor: 'glass_reference', elementId: 'sensorGlassReference', field: 'temperature_c', unit: ' °C', digits: 2 },
  { sensor: 'glass_comparison', elementId: 'sensorGlassComparison', field: 'temperature_c', unit: ' °C', digits: 2 },
  { sensor: 'cabin', elementId: 'sensorCabinTemp', field: 'temperature_c', unit: ' °C', digits: 1 },
  { sensor: 'cabin', elementId: 'sensorCabinHumidity', field: 'humidity_pct', unit: ' %', digits: 0 },
];

// Latest reading per sensor. Frames that omit a sensor keep its last reading.
const carSensorReadings = {};

function mergeCarSensorReadings(sensors) {
  if (!sensors || typeof sensors !== 'object') return;
  const receivedMs = Date.now();
  Object.entries(sensors).forEach(([sensor, reading]) => {
    if (reading && typeof reading === 'object') {
      carSensorReadings[sensor] = { ...reading, receivedMs };
    }
  });
}

function fmtSensorAge(seconds) {
  if (seconds < 90) return `${Math.round(seconds)} s old`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min old`;
  return `${(seconds / 3600).toFixed(1)} h old`;
}

// Age is advanced locally from the server-reported age, so the browser
// clock does not need to match the server.
function describeCarSensor(reading) {
  if (!reading) return { kind: 'missing', text: 'No data' };
  if (reading.state === 'missing') return { kind: 'missing', text: 'Missing' };
  if (reading.state === 'invalid') return { kind: 'missing', text: 'Invalid reading' };
  const baseAge = Number(reading.age_s);
  const maxAge = Number(reading.max_age_s);
  if (!Number.isFinite(baseAge) || !Number.isFinite(maxAge)) return { kind: 'missing', text: 'No data' };
  const age = baseAge + (Date.now() - reading.receivedMs) / 1000;
  if (age > maxAge) return { kind: 'stale', text: `Stale · ${fmtSensorAge(age)}` };
  if (reading.cached) return { kind: 'cached', text: 'Cached · last read failed' };
  return { kind: 'fresh', text: 'Live' };
}

function renderCarSensorReadings() {
  CAR_SENSOR_ROWS.forEach(row => {
    const el = document.getElementById(row.elementId);
    if (!el) return;
    const reading = carSensorReadings[row.sensor];
    const state = describeCarSensor(reading);
    const valueEl = el.querySelector('.sensor-value');
    const stateEl = el.querySelector('.sensor-state');
    const value = state.kind === 'missing' ? null : reading?.[row.field];
    if (valueEl) valueEl.textContent = fmtNum(value, row.unit, row.digits);
    if (stateEl) stateEl.textContent = state.text;
    el.classList.remove('sensor-fresh', 'sensor-stale', 'sensor-cached', 'sensor-missing');
    el.classList.add(`sensor-${state.kind}`);
  });
}

// ============================================
// Combined Status Update
// ============================================

function updateStatusUI(status) {
  updateDeviceReport(status);
  updateDetailsSection(latestDeviceReport);
}

function applyCarHeaterPayload(data, source = 'unknown') {

  if (!data) return;

  // A response without a status (no device report yet) is not a status.
  const status = Object.prototype.hasOwnProperty.call(data, 'status') ? data.status : data;
  updateStatusUI(status);
  mergeCarSensorReadings(status?.sensors);
  renderCarSensorReadings();

  if (data.logs_snapshot) window.dispatchEvent(new CustomEvent('car_heater_logs_snapshot', {detail: data.logs_snapshot}));
  if (data.command_status || data.commandStatus) {
    const cmdStatus = data.command_status || data.commandStatus;
    updateCommandStatusUI(cmdStatus);
    if (lastQueuedAction && cmdStatus && Object.prototype.hasOwnProperty.call(cmdStatus, lastQueuedAction)) {
      const status = cmdStatus[lastQueuedAction];
      if (status === 'sent') {
        setQueueStatus('sent', 'Sent · awaiting device acknowledgement');
      } else if (status === 'success') {
        setQueueStatus('sent', 'Device acknowledged success');
        lastQueuedAction = null;
      } else if (status === 'failed') {
        setQueueStatus('error', 'Device reported failure');
        lastQueuedAction = null;
      }
    }
  }

  if (data.weather) {
    updateWeatherDisplay(data.weather);
  }
}

async function fetchCurrentStatus() {
  try {
    const resp = await fetch('/api/car_heater/status', {
      cache: 'no-store',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const data = await resp.json();
    applyCarHeaterPayload(data, 'http');
    setDiagnosticsConnection(window.socket?.connected ? 'Live connection · status received' : 'HTTP fallback · status received');
  } catch (err) {
    console.warn('Failed to fetch current car heater status:', err);
    setDiagnosticsConnection('Status unavailable · reconnecting');
  }
}

function startStatusPolling() {
  if (statusPollTimer) return;
  fetchCurrentStatus();
  statusPollTimer = window.setInterval(fetchCurrentStatus, 5000);
}

function stopStatusPolling() {
  if (!statusPollTimer) return;
  window.clearInterval(statusPollTimer);
  statusPollTimer = null;
}

// ============================================
// Command Status
// ============================================

const COMMAND_ACTIONS = {
  turn_on: 'Turn ON',
  turn_off: 'Turn OFF',
  get_logs: 'Get Logs',
  esp_restart: 'Restart ESP',
  shelly_restart: 'Restart Shelly',
};

// A timeout is missing evidence, not a device failure or a successful restart.
const commandWaits = new Map();
const knownCommandStages = {};
const COMMAND_WAIT_MS = 30000;
const COMMAND_STAGE_LABELS = {
  requesting: 'Requesting · not yet queued',
  queued: 'Queued · awaiting delivery',
  sent: 'Sent · awaiting device acknowledgement',
  success: 'Device acknowledged success',
  failed: 'Device reported failure',
  rejected: 'Request rejected · not queued',
  unknown: 'Unknown · no acknowledgement',
};

function setDiagnosticsConnection(text) {
  const el = document.getElementById('diagnosticsConnection');
  if (el) el.textContent = text;
}

function updateSingleCommandStatus(action, status) {
  const el = document.getElementById(`cmdStatus_${action}`);
  if (!el) return;
  const previous = knownCommandStages[action];
  if (previous === status && commandWaits.has(action) && commandWaits.get(action) === null) return;
  knownCommandStages[action] = status;
  el.textContent = COMMAND_STAGE_LABELS[status] || 'No command result';
  if (action === 'get_logs' && status === 'failed' && previous !== status) {
    window.dispatchEvent(new CustomEvent('car_heater_command_result', {detail: {action, stage: 'executed', ok: false}}));
  }
  el.classList.toggle('status-error', ['failed', 'rejected', 'unknown'].includes(status));
  if (['requesting', 'queued', 'sent'].includes(status)) {
    if (previous !== status) {
      clearTimeout(commandWaits.get(action));
      commandWaits.set(action, setTimeout(() => {
        // Keep the source stage so repeated polling cannot reset this timeout.
        commandWaits.set(action, null);
        el.textContent = COMMAND_STAGE_LABELS.unknown;
        el.classList.add('status-error');
        if (lastQueuedAction === action) setQueueStatus('error', 'Command outcome unknown · no acknowledgement');
      }, COMMAND_WAIT_MS));
    } else if (!commandWaits.has(action)) return;
  } else {
    clearTimeout(commandWaits.get(action));
    commandWaits.delete(action);
  }
}

function updateCommandStatusUI(commandStatus) {
  // Only fields present in a partial update replace known stages.
  if (!commandStatus) return;
  Object.keys(COMMAND_ACTIONS).forEach(action => {
    if (Object.prototype.hasOwnProperty.call(commandStatus, action)) {
      updateSingleCommandStatus(action, commandStatus[action]);
    }
  });
}

function handleCarHeaterActionResult(data) {
  if (!data?.action) return;
  const ok = typeof data.ok === 'boolean' ? data.ok : data.success;
  if (data.stage === 'queued') {
    updateSingleCommandStatus(data.action, ok === true ? 'queued' : ok === false ? 'rejected' : 'unknown');
    setQueueStatus(ok === true ? 'pending' : 'error', ok === true ? 'Action queued · awaiting device' : 'Queue request not confirmed');
  } else if (data.stage === 'executed') {
    if (data.command_status) updateCommandStatusUI(data.command_status);
    updateSingleCommandStatus(data.action, ok === true ? 'success' : ok === false ? 'failed' : 'unknown');
    setQueueStatus(ok === true ? 'sent' : 'error', ok === true ? 'Device acknowledged success' : ok === false ? 'Device reported failure' : 'Command outcome unknown');
    if (lastQueuedAction === data.action) lastQueuedAction = null;
  } else {
    updateSingleCommandStatus(data.action, 'unknown');
  }
  window.dispatchEvent(new CustomEvent('car_heater_command_result', {detail: data}));
}

// ============================================
// Maintenance feedback (Diagnostics)
// ============================================

let lastQueuedAction = null;

// Settings group anchors scroll below the app navbar.
function updateNavHeightVar() {
  const nav = document.querySelector('nav.navbar');
  if (nav) document.documentElement.style.setProperty('--nav-height', `${nav.getBoundingClientRect().height}px`);
}

// Feedback stays beside the maintenance buttons rather than in a floating
// banner; the command stages above hold the lasting per-command state.
function setQueueStatus(mode, text) {
  const el = document.getElementById('maintenanceStatus');
  if (!el) return;
  el.textContent = mode ? text : '';
  el.classList.toggle('status-error', mode === 'error');
}

// ============================================
// Command Queue
// ============================================

function queueCommand(action) {
  if (action === 'turn_on' || action === 'turn_off') {
    window.dispatchEvent(new CustomEvent('car_heater_bounded_control', {detail: {action}}));
    return;
  }
  const payload = { action: action, csrf_token: document.querySelector('meta[name="csrf-token"]')?.content || '' };
  lastQueuedAction = action;
  knownCommandStages[action] = null;
  setQueueStatus('pending', 'Requesting action…');
  updateSingleCommandStatus(action, 'requesting');

  if (action === 'get_logs') {
    window.dispatchEvent(new CustomEvent('car_heater_get_logs_requested'));
  }

  // Prefer Socket.IO for low latency
  try {
    if (window.socket?.connected) {
      window.socket.emit('car_heater_control', payload);
      return;
    }
  } catch (err) {
    console.warn('Socket emit failed, falling back to HTTP:', err);
  }

  // HTTP fallback
  fetch('/api/car_heater/queue', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
      'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '',
    },
    body: JSON.stringify(payload),
  }).then(resp => {
    if (!resp.ok) {
      handleCarHeaterActionResult({action, stage: 'queued', ok: false});
      throw new Error('HTTP ' + resp.status);
    }
    return resp.json();
  }).then(data => {
    handleCarHeaterActionResult({action, stage: 'queued', ok: data.ok});
  }).catch(err => {
    console.error('Queue HTTP error:', err);
    if (knownCommandStages[action] !== 'rejected') {
      updateSingleCommandStatus(action, 'unknown');
      setQueueStatus('error', 'Queue outcome unknown · check command status before retrying');
    }
  });
}

// ============================================
// Event Listeners
// ============================================

document.addEventListener('DOMContentLoaded', () => {
  updateNavHeightVar();
  window.addEventListener('resize', updateNavHeightVar);
  // Initial status from server-rendered context
  if (window.CAR_HEATER_LAST_STATUS) {
    updateStatusUI(window.CAR_HEATER_LAST_STATUS);
  }
  // Re-evaluate freshness between updates so old readings turn stale.
  window.setInterval(renderCarSensorReadings, 15000);
  window.setInterval(() => { updateDeviceReport(null); updateWeatherDisplay(null); }, 5000);

  if (window.CAR_HEATER_CMD_STATUS) {
    updateCommandStatusUI(window.CAR_HEATER_CMD_STATUS);
  }

  // Weather display
  if (typeof window.CAR_HEATER_WEATHER !== 'undefined') {
    updateWeatherDisplay(window.CAR_HEATER_WEATHER);
  }

  fetchCurrentStatus();

  // Quick action buttons
  const btnOn = document.getElementById('btnTurnOn');
  const btnOff = document.getElementById('btnTurnOff');
  const btnLogs = document.getElementById('btnGetLogs');
  const btnEspRestart = document.getElementById('btnEspRestart');
  const btnShellyRestart = document.getElementById('btnShellyRestart');

  if (btnOn) btnOn.addEventListener('click', () => queueCommand('turn_on'));
  if (btnOff) btnOff.addEventListener('click', () => queueCommand('turn_off'));
  if (btnLogs) btnLogs.addEventListener('click', () => queueCommand('get_logs'));

  const restartDialog = document.getElementById('heaterRestartDialog');
  const restartTitle = document.getElementById('heaterRestartTitle');
  const restartConfirm = document.getElementById('heaterRestartConfirm');
  let restartAction = null;
  let restartTrigger = null;
  [[btnEspRestart, 'esp_restart', 'Restart ESP?'], [btnShellyRestart, 'shelly_restart', 'Restart Shelly?']].forEach(([button, action, title]) => {
    button?.addEventListener('click', () => {
      if (!restartDialog) return;
      restartAction = action;
      restartTrigger = button;
      restartTitle.textContent = title;
      restartDialog.showModal();
      document.getElementById('heaterRestartCancel').focus();
    });
  });
  document.getElementById('heaterRestartCancel')?.addEventListener('click', () => restartDialog.close());
  restartConfirm?.addEventListener('click', () => {
    const action = restartAction;
    restartDialog.close();
    if (action) queueCommand(action);
  });
  restartDialog?.addEventListener('close', () => {
    restartAction = null;
    restartTrigger?.focus();
  });

  // Socket.IO event handlers
  if (window.socket) {
    window.socket.on('connect', () => {
      stopStatusPolling();
      fetchCurrentStatus();
    });

    window.socket.on('disconnect', () => {
      setDiagnosticsConnection('Live connection lost · using HTTP fallback');
      startStatusPolling();
    });

    // A socket that never connects still needs the HTTP status fallback.
    window.socket.on('connect_error', () => {
      setDiagnosticsConnection('Live connection lost · using HTTP fallback');
      startStatusPolling();
    });

    window.socket.on('car_heater_status', data => {
      applyCarHeaterPayload(data, 'socket');
    });

    window.socket.on('car_heater_action_result', handleCarHeaterActionResult);
  } else {
    startStatusPolling();
  }
});
