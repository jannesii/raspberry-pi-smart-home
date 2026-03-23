/**
 * Car Heater Page - iOS-Optimized JavaScript
 * Handles real-time updates, controls, and UI state
 */

console.log('🚗 car_heater.js loaded');

let statusPollTimer = null;

// ============================================
// Utility Functions
// ============================================

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true
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

function fmtCompact(v, unit = '', digits = 0) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  if (n >= 1000) {
    return `${(n / 1000).toFixed(1)}k${unit}`;
  }
  return `${n.toFixed(digits)}${unit}`;
}

// ============================================
// Hero Status Updates
// ============================================

function updateHeroStatus(status) {
  const stateIndicator = document.getElementById('stateIndicator');
  const stateIcon = document.getElementById('stateIcon');
  const stateTitle = document.getElementById('stateTitle');
  const heroPowerValue = document.getElementById('heroPowerValue');
  const heroAmbient = document.getElementById('heroAmbient');
  const heroEnergy = document.getElementById('heroEnergy');

  if (!status) {
    if (stateIndicator) {
      stateIndicator.classList.remove('state-on', 'state-off');
      stateIndicator.classList.add('state-unknown');
    }
    if (stateIcon) stateIcon.textContent = '❓';
    if (stateTitle) stateTitle.textContent = 'Unknown';
    if (heroPowerValue) heroPowerValue.textContent = '—';
    return;
  }

  const isOn = !!status.is_heater_on;
  const instantW = status.instant_power_w;
  const ambient = status.ambient_temp;
  const energyToday = status.energy_last_min_wh;

  // State indicator
  if (stateIndicator) {
    stateIndicator.classList.remove('state-on', 'state-off', 'state-unknown');
    stateIndicator.classList.add(isOn ? 'state-on' : 'state-off');
  }
  if (stateIcon) stateIcon.textContent = isOn ? '🔥' : '❄️';
  if (stateTitle) stateTitle.textContent = isOn ? 'Heating' : 'Off';

  // Power display
  if (heroPowerValue) {
    const power = Number(instantW);
    heroPowerValue.textContent = Number.isFinite(power) ? power.toFixed(0) : '—';
  }

  // Stats row
  if (heroAmbient) heroAmbient.textContent = fmtNum(ambient, '°', 1);
  if (heroEnergy) heroEnergy.textContent = fmtCompact(energyToday, 'Wh');
}

function updateWeatherDisplay(weather) {
  const heroOutsideTemp = document.getElementById('heroOutsideTemp');
  const heroWind = document.getElementById('heroWind');

  if (!weather) return;

  if (heroOutsideTemp && weather.outside_temp_c != null) {
    heroOutsideTemp.textContent = fmtNum(weather.outside_temp_c, '°', 1);
  }
  if (heroWind && weather.wind_speed_mps != null) {
    heroWind.textContent = fmtNum(weather.wind_speed_mps, ' m/s', 1);
  }
}

// ============================================
// Details Section Updates
// ============================================

function updateDetailsSection(status) {
  // Pills
  const pillVoltage = document.getElementById('pillVoltage');
  const pillCurrent = document.getElementById('pillCurrent');
  const pillEnergyTotal = document.getElementById('pillEnergyTotal');

  // Detail list items
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

  // Update pills
  if (pillVoltage) {
    pillVoltage.querySelector('span:last-child').textContent = fmtNum(status.voltage_v, 'V', 0);
  }
  if (pillCurrent) {
    pillCurrent.querySelector('span:last-child').textContent = fmtNum(status.current_a, 'A', 2);
  }
  if (pillEnergyTotal) {
    pillEnergyTotal.querySelector('span:last-child').textContent = fmtCompact(status.energy_total_wh, 'Wh');
  }

  // Update detail list
  if (detailPower) detailPower.textContent = fmtNum(status.instant_power_w, ' W', 1);
  if (detailAmbient) detailAmbient.textContent = fmtNum(status.ambient_temp, ' °C', 1);
  if (detailDeviceTemp) {
    detailDeviceTemp.textContent = fmtNum(status.device_temp_c, ' °C', 1);
    // Highlight if hot
    const temp = Number(status.device_temp_c);
    detailDeviceTemp.classList.remove('value-warning', 'value-danger');
    if (temp >= 50) detailDeviceTemp.classList.add('value-danger');
    else if (temp >= 40) detailDeviceTemp.classList.add('value-warning');
  }
  if (detailVoltage) detailVoltage.textContent = fmtNum(status.voltage_v, ' V', 1);
  if (detailCurrent) detailCurrent.textContent = fmtNum(status.current_a, ' A', 2);
  if (detailEnergyToday) detailEnergyToday.textContent = fmtNum(status.energy_last_min_wh, ' Wh', 0);
  if (detailEnergyTotal) detailEnergyTotal.textContent = fmtNum(status.energy_total_wh, ' Wh', 0);
  if (detailSource) detailSource.textContent = status.source || '—';
  if (lastUpdateTime) lastUpdateTime.textContent = fmtTs(status.timestamp);
}

// ============================================
// Combined Status Update
// ============================================

function updateStatusUI(status) {
  updateHeroStatus(status);
  updateDetailsSection(status);
}

function applyCarHeaterPayload(data, source = 'unknown') {
  console.log(`📡 applyCarHeaterPayload (${source}):`, data);
  if (!data) return;

  if (data.status) {
    updateStatusUI(data.status);
  } else {
    updateStatusUI(data);
  }

  if (data.command_status || data.commandStatus) {
    const cmdStatus = data.command_status || data.commandStatus;
    updateCommandStatusUI(cmdStatus);
    if (lastQueuedAction && cmdStatus && Object.prototype.hasOwnProperty.call(cmdStatus, lastQueuedAction)) {
      const status = cmdStatus[lastQueuedAction];
      if (status === 'sent') {
        setQueueStatus('sent', 'Sent to heater');
      } else if (status === 'success') {
        setQueueStatus('sent', 'Action successful');
        lastQueuedAction = null;
      } else if (status === 'failed') {
        setQueueStatus('error', 'Action failed');
        lastQueuedAction = null;
      }
    }
  }

  if (data.charge_mode || data.chargeMode) {
    updateChargeModeUI(data.charge_mode || data.chargeMode);
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
  } catch (err) {
    console.warn('Failed to fetch current car heater status:', err);
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

function updateSingleCommandStatus(action, status) {
  const el = document.getElementById(`cmdStatus_${action}`);
  if (!el) return;

  el.classList.remove('status-queued', 'status-sent', 'status-error');
  el.style.display = 'none';

  if (!status) {
    el.textContent = '';
    return;
  }

  let text = '';
  let cls = '';
  switch (status) {
    case 'queued':
      text = 'Queued';
      cls = 'status-queued';
      break;
    case 'sent':
      text = 'Sent';
      cls = 'status-sent';
      break;
    case 'success':
      text = 'Success';
      cls = 'status-sent';
      break;
    case 'failed':
      text = 'Failed';
      cls = 'status-error';
      break;
    default:
      text = String(status);
      break;
  }

  el.textContent = text;
  if (cls) el.classList.add(cls);
  el.style.display = '';
}

function updateCommandStatusUI(commandStatus) {
  const actions = Object.keys(COMMAND_ACTIONS);
  if (!commandStatus) {
    actions.forEach(action => updateSingleCommandStatus(action, null));
    return;
  }
  actions.forEach(action => {
    updateSingleCommandStatus(action, commandStatus[action]);
  });
}

// ============================================
// Charge Mode
// ============================================

let chargeModeState = null;

function updateChargeModeUI(state) {
  chargeModeState = state || null;

  const toggle = document.getElementById('chargeModeToggle');
  const statusText = document.getElementById('chargeModeStatusText');
  if (!toggle || !statusText) return;

  const enabled = !!(state && state.enabled);
  const powerCut = !!(state && state.power_cut);
  const threshold = state && state.threshold_w != null ? Number(state.threshold_w) : 40;

  toggle.checked = enabled;
  statusText.classList.remove('active', 'cut');

  if (powerCut) {
    const ts = state.power_cut_at;
    statusText.textContent = ts ? `Power cut at ${fmtTs(ts)}` : 'Power cut';
    statusText.classList.add('cut');
  } else if (enabled) {
    statusText.textContent = `Active (cut at ${threshold}W)`;
    statusText.classList.add('active');
  } else {
    statusText.textContent = 'Idle';
  }
}

// ============================================
// Keep at Temperature
// ============================================

function updateKeepAtTempUI(settings) {
  const toggle = document.getElementById('keepAtTempToggle');
  const targetInput = document.getElementById('targetTempInput');
  const hysteresisInput = document.getElementById('hysteresisInput');
  if (!toggle || !targetInput || !hysteresisInput) return;

  toggle.checked = !!(settings && settings.enabled);
  targetInput.value = settings && settings.target_temperature_c != null
    ? String(settings.target_temperature_c)
    : '';
  hysteresisInput.value = settings && settings.hysteresis_c != null
    ? String(settings.hysteresis_c)
    : '';
}

// ============================================
// Queue Status Banner
// ============================================

let queueBannerTimeout = null;
let lastQueuedAction = null;

function updateNavHeightVar() {
  const nav = document.querySelector('nav.navbar');
  if (!nav) return;
  const height = nav.getBoundingClientRect().height;
  document.documentElement.style.setProperty('--nav-height', `${height}px`);
}

function setQueueStatus(mode, text) {
  const banner = document.getElementById('queueBanner');
  const bannerText = document.getElementById('queueBannerText');
  if (!banner || !bannerText) return;

  updateNavHeightVar();

  // Clear any existing timeout
  if (queueBannerTimeout) {
    clearTimeout(queueBannerTimeout);
    queueBannerTimeout = null;
  }

  banner.classList.remove('visible', 'pending', 'sent', 'idle');

  if (mode) {
    banner.classList.add('visible', mode);
    bannerText.textContent = text;

    // Auto-hide after 5 seconds for 'sent' status
    if (mode === 'sent') {
      queueBannerTimeout = setTimeout(() => {
        banner.classList.remove('visible');
      }, 5000);
    }
  }
}

// ============================================
// Command Queue
// ============================================

function queueCommand(action) {
  const payload = { action: action };
  lastQueuedAction = action;
  setQueueStatus('pending', 'Action queued…');
  updateSingleCommandStatus(action, 'queued');

  if (action === 'get_logs') {
    window.dispatchEvent(new CustomEvent('car_heater_get_logs_requested'));
  }

  // Haptic feedback on iOS
  if (window.navigator && window.navigator.vibrate) {
    window.navigator.vibrate(10);
  }

  // Prefer Socket.IO for low latency
  try {
    if (window.socket) {
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
    },
    body: JSON.stringify(payload),
  }).then(resp => {
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.json();
  }).then(data => {
    console.log('Queue HTTP result:', data);
    setQueueStatus('pending', 'Action queued');
  }).catch(err => {
    console.error('Queue HTTP error:', err);
    setQueueStatus('error', 'Failed to queue action');
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

  if (window.CAR_HEATER_CMD_STATUS) {
    updateCommandStatusUI(window.CAR_HEATER_CMD_STATUS);
  }

  if (typeof window.CAR_HEATER_CHARGE_STATE !== 'undefined') {
    updateChargeModeUI(window.CAR_HEATER_CHARGE_STATE);
  } else {
    updateChargeModeUI(null);
  }

  if (typeof window.CAR_HEATER_KEEP_AT_TEMP !== 'undefined') {
    updateKeepAtTempUI(window.CAR_HEATER_KEEP_AT_TEMP);
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

  if (btnEspRestart) {
    btnEspRestart.addEventListener('click', () => {
      if (window.confirm('Restart ESP? This will reboot the microcontroller.')) {
        queueCommand('esp_restart');
      }
    });
  }

  if (btnShellyRestart) {
    btnShellyRestart.addEventListener('click', () => {
      if (window.confirm('Restart Shelly? This will reboot the relay device.')) {
        queueCommand('shelly_restart');
      }
    });
  }

  // Charge mode toggle
  const chargeModeToggle = document.getElementById('chargeModeToggle');
  if (chargeModeToggle) {
    chargeModeToggle.addEventListener('change', () => {
      const newEnabled = chargeModeToggle.checked;

      try {
        if (window.socket) {
          window.socket.emit('car_heater_charge_mode', { enabled: newEnabled });
        }
      } catch (err) {
        console.warn('Socket emit failed for charge mode toggle:', err);
      }

      // Optimistic UI update
      const nextState = Object.assign(
        { enabled: false, power_cut: false, power_cut_at: null, threshold_w: 40, last_instant_power_w: null },
        chargeModeState || {},
        { enabled: newEnabled, power_cut: false, power_cut_at: null }
      );
      updateChargeModeUI(nextState);

      if (newEnabled) {
        queueCommand('turn_on');
      }
    });
  }

  // Keep at temp controls
  const keepAtTempToggle = document.getElementById('keepAtTempToggle');
  const keepAtTempTarget = document.getElementById('targetTempInput');
  const keepAtTempHysteresis = document.getElementById('hysteresisInput');

  function sendKeepAtTempSettings() {
    try {
      const enabled = keepAtTempToggle ? keepAtTempToggle.checked : false;
      const targetTemp = keepAtTempTarget ? Number(keepAtTempTarget.value) : null;
      const hysteresis = keepAtTempHysteresis ? Number(keepAtTempHysteresis.value) : null;

      const settings = {
        enabled: enabled,
        target_temperature_c: targetTemp,
        hysteresis_c: hysteresis,
      };

      if (window.socket) {
        window.socket.emit('keep_at_temp_settings', settings);
      }
    } catch (err) {
      console.warn('Socket emit failed for keep-at-temp settings:', err);
    }
  }

  if (keepAtTempToggle) {
    keepAtTempToggle.addEventListener('change', sendKeepAtTempSettings);
  }
  if (keepAtTempTarget) {
    keepAtTempTarget.addEventListener('change', sendKeepAtTempSettings);
    // Also send on blur for better mobile UX
    keepAtTempTarget.addEventListener('blur', sendKeepAtTempSettings);
  }
  if (keepAtTempHysteresis) {
    keepAtTempHysteresis.addEventListener('change', sendKeepAtTempSettings);
    keepAtTempHysteresis.addEventListener('blur', sendKeepAtTempSettings);
  }

  // Socket.IO event handlers
  if (window.socket) {
    window.socket.on('connect', () => {
      stopStatusPolling();
      fetchCurrentStatus();
    });

    window.socket.on('disconnect', () => {
      startStatusPolling();
    });

    window.socket.on('car_heater_status', data => {
      console.log('📡 car_heater_status:', data);
      applyCarHeaterPayload(data, 'socket');
    });

    window.socket.on('car_heater_action_result', data => {
      console.log('📡 car_heater_action_result:', data);
      if (!data) return;
      let ok = true;
      if (typeof data.ok === 'boolean') ok = data.ok;
      else if (typeof data.success === 'boolean') ok = data.success;
      setQueueStatus(ok ? 'pending' : 'error', ok ? 'Action queued…' : 'Action failed');
      if (ok && data.action) {
        updateSingleCommandStatus(data.action, 'queued');
      } else if (!ok && data.action) {
        updateSingleCommandStatus(data.action, 'failed');
      }
    });

    window.socket.on('car_heater_charge_mode', data => {
      console.log('📡 car_heater_charge_mode:', data);
      if (data) {
        updateChargeModeUI(data);
      }
    });

    window.socket.on('keep_at_temp_settings', data => {
      console.log('📡 keep_at_temp_settings:', data);
      if (data) {
        updateKeepAtTempUI(data);
      }
    });
  } else {
    startStatusPolling();
  }
});
