/**
 * Car Heater Settings & Ready-by JavaScript
 * Handles settings modal and Ready-by scheduling
 */

console.log('⚙️ car_heater_settings.js loaded');

// ============================================
// Ready-by State
// ============================================

let kfactorStatus = null;
let readyBySchedule = null;

// ============================================
// Utility Functions
// ============================================

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  } catch {
    return '—';
  }
}

function fmtTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString(undefined, {
      hour: '2-digit', minute: '2-digit'
    });
  } catch {
    return '—';
  }
}

function fmtNum(v, decimals = 1) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(decimals);
}

function getDefaultReadyByTime() {
  // Default to tomorrow at 07:00
  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  tomorrow.setHours(7, 0, 0, 0);
  
  // Format for datetime-local input (YYYY-MM-DDTHH:MM)
  const year = tomorrow.getFullYear();
  const month = String(tomorrow.getMonth() + 1).padStart(2, '0');
  const day = String(tomorrow.getDate()).padStart(2, '0');
  const hours = String(tomorrow.getHours()).padStart(2, '0');
  const minutes = String(tomorrow.getMinutes()).padStart(2, '0');
  
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

// ============================================
// Ready-by UI Updates
// ============================================
function updateReadyByUI(data) {
  if (!data) return;
  updateReadyByConfig(data.config || null);
  updateReadyBySchedule(data.schedule || null);
}

function updateReadyByConfig(config) {
  if (!config) return;

  const readyByEnabledToggle = document.getElementById('readyByEnabled');
  const readyByToleranceInput = document.getElementById('readyByTolerance');
  const readyByCooldownInput = document.getElementById('readyByCooldown');
  
  if (readyByEnabledToggle) readyByEnabledToggle.checked = config.enabled;
  if (readyByToleranceInput) readyByToleranceInput.value = config.reach_tolerance_minutes;
  if (readyByCooldownInput) readyByCooldownInput.value = config.command_cooldown_s;
}

function updateReadyBySchedule(schedule) {
  readyBySchedule = schedule || null;

  const statusText = document.getElementById('readyByStatusText');
  const infoPanel = document.getElementById('readyByInfoPanel');
  const etaText = document.getElementById('readyByEtaText');
  const plannedStart = document.getElementById('readyByPlannedStart');
  const cabinTemp = document.getElementById('readyByCabinTemp');
  const outsideTemp = document.getElementById('readyByOutsideTemp');
  const modelParams = document.getElementById('readyByModelParams');
  const scheduleBtn = document.getElementById('btnReadyBySchedule');
  const cancelBtn = document.getElementById('btnReadyByCancel');
  const timeInput = document.getElementById('readyByTimeInput');
  const targetInput = document.getElementById('readyByTargetInput');
  const progressEl = document.getElementById('readyByProgress');
  const progressFill = document.getElementById('readyByProgressFill');
  const progressStart = document.getElementById('readyByProgressStart');
  const progressEnd = document.getElementById('readyByProgressEnd');
  if (!statusText) return;
  
  // Remove all status classes
  statusText.classList.remove('status-scheduled', 'status-running', 'status-completed', 'status-canceled', 'status-expired');
  
  if (!schedule || !schedule.status || schedule.status === 'canceled' || schedule.status === 'expired' || schedule.status === 'completed') {
    // No active schedule
    if (schedule && schedule.status === 'completed') {
      statusText.textContent = '✓ Completed';
      statusText.classList.add('status-completed');
    } else if (schedule && schedule.status === 'canceled') {
      statusText.textContent = 'Canceled';
      statusText.classList.add('status-canceled');
    } else if (schedule && schedule.status === 'expired') {
      statusText.textContent = 'Expired';
      statusText.classList.add('status-expired');
    } else {
      statusText.textContent = 'No schedule';
    }
    if (infoPanel) infoPanel.style.display = 'none';
    if (progressEl) progressEl.style.display = 'none';
    if (scheduleBtn) scheduleBtn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = true;
    if (timeInput) timeInput.disabled = false;
    if (targetInput) targetInput.disabled = false;
    // Set default time if empty
    if (timeInput && !timeInput.value) {
      timeInput.value = getDefaultReadyByTime();
    }
    return;
  }
  
  // Active schedule
  const status = schedule.status;
  statusText.classList.add(`status-${status}`);
  
  if (status === 'scheduled') {
    statusText.textContent = `⏰ Scheduled for ${fmtTime(schedule.ready_by_ts)}`;
  } else if (status === 'running') {
    statusText.textContent = '🔥 Heating in progress';
  } else {
    statusText.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  }
  
  // Show info panel
  if (infoPanel) {
    infoPanel.style.display = 'block';
  }
  
  // Update progress bar
  if (progressEl && progressFill) {
    if (status === 'scheduled' || status === 'running') {
      progressEl.style.display = 'block';
      
      // Calculate progress
      const now = new Date();
      const readyByTime = new Date(schedule.ready_by_ts);
      const plannedStartTime = schedule.planned_start_ts ? new Date(schedule.planned_start_ts) : now;
      
      const totalDuration = readyByTime - plannedStartTime;
      const elapsed = now - plannedStartTime;
      let progress = 0;
      
      if (totalDuration > 0) {
        progress = Math.min(100, Math.max(0, (elapsed / totalDuration) * 100));
      }
      
      progressFill.style.width = `${progress}%`;
      
      if (status === 'running') {
        progressFill.classList.add('heating');
      } else {
        progressFill.classList.remove('heating');
      }
      
      if (progressStart) {
        progressStart.textContent = status === 'running' ? 'Started' : `Starts ${fmtTime(schedule.planned_start_ts)}`;
      }
      if (progressEnd) {
        progressEnd.textContent = `Ready ${fmtTime(schedule.ready_by_ts)}`;
      }
    } else {
      progressEl.style.display = 'none';
    }
  }
  
  // Update info values
  if (etaText) {
    if (schedule.unreachable) {
      etaText.textContent = '⚠️ May not reach target';
      etaText.classList.add('unreachable');
    } else if (schedule.predicted_eta_minutes != null) {
      const mins = Math.round(schedule.predicted_eta_minutes);
      if (mins < 60) {
        etaText.textContent = `~${mins} min`;
      } else {
        const hrs = Math.floor(mins / 60);
        const remainMins = mins % 60;
        etaText.textContent = `~${hrs}h ${remainMins}m`;
      }
      etaText.classList.remove('unreachable');
    } else {
      etaText.textContent = '—';
      etaText.classList.remove('unreachable');
    }
  }
  
  if (plannedStart && schedule.planned_start_ts) {
    plannedStart.textContent = fmtTime(schedule.planned_start_ts);
  } else if (plannedStart) {
    plannedStart.textContent = '—';
  }
  
  if (cabinTemp) {
    if (schedule.cabin_temp_c != null) {
      cabinTemp.textContent = `${fmtNum(schedule.cabin_temp_c)}°C → ${fmtNum(schedule.target_temp_c)}°C`;
    } else {
      cabinTemp.textContent = `— → ${fmtNum(schedule.target_temp_c)}°C`;
    }
  }
  
  if (outsideTemp) {
    if (schedule.outside_temp_c != null) {
      outsideTemp.textContent = `${fmtNum(schedule.outside_temp_c)}°C`;
    } else {
      outsideTemp.textContent = '—';
    }
  }
  
  if (modelParams) {
    if (schedule.used_k_loss_W_per_K != null && schedule.used_eta != null) {
      modelParams.textContent = `k=${fmtNum(schedule.used_k_loss_W_per_K, 1)} W/K, η=${fmtNum(schedule.used_eta, 2)}`;
    } else {
      modelParams.textContent = '—';
    }
  }
  
  // Update buttons
  if (scheduleBtn) scheduleBtn.disabled = true;
  if (cancelBtn) cancelBtn.disabled = false;
  if (timeInput) timeInput.disabled = true;
  if (targetInput) targetInput.disabled = true;
  
  // Populate inputs with current schedule values
  if (timeInput && schedule.ready_by_ts) {
    try {
      const dt = new Date(schedule.ready_by_ts);
      const local = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000);
      timeInput.value = local.toISOString().slice(0, 16);
    } catch {}
  }
  if (targetInput && schedule.target_temp_c != null) {
    targetInput.value = String(schedule.target_temp_c);
  }
}

// ============================================
// kFactor UI Updates
// ============================================

function updateKFactorUI(status) {
  kfactorStatus = status || null;
  
  const stateIndicator = document.getElementById('kfactorStateIndicator');
  const stateText = document.getElementById('kfactorStateText');
  const enabledToggle = document.getElementById('kfactorEnabled');
  const kLossEl = document.getElementById('kfactorKLoss');
  const etaEl = document.getElementById('kfactorEta');
  const windowEl = document.getElementById('kfactorWindow');
  const cooldownEl = document.getElementById('kfactorCooldownUntil');
  const lastSessionEl = document.getElementById('kfactorLastSessionInfo');
  
  if (!status) {
    if (stateText) stateText.textContent = 'Not available';
    return;
  }
  
  // Update state indicator
  const state = (status.state || 'IDLE').toUpperCase();
  if (stateIndicator) {
    stateIndicator.classList.remove('state-idle', 'state-armed', 'state-recording', 'state-cooldown');
    stateIndicator.classList.add(`state-${state.toLowerCase()}`);
  }
  if (stateText) {
    stateText.textContent = state;
  }
  
  // Update enabled toggle
  if (enabledToggle) {
    enabledToggle.checked = status.enabled !== false;
  }
  
  // Update active params
  const params = status.active_params || {};
  if (kLossEl) {
    kLossEl.textContent = params.k_loss_W_per_K != null ? fmtNum(params.k_loss_W_per_K, 1) : '—';
  }
  if (etaEl) {
    etaEl.textContent = params.eta != null ? fmtNum(params.eta, 2) : '—';
  }
  
  // Update calibration window
  if (windowEl && status.config) {
    const start = status.config.auto_calib_start_hhmm || '00:00';
    const stop = status.config.auto_calib_stop_hhmm || '23:59';
    windowEl.textContent = `${start} – ${stop}`;
  }
  
  // Update cooldown
  if (cooldownEl) {
    cooldownEl.textContent = status.cooldown_until ? fmtTs(status.cooldown_until) : '—';
  }
  
  // Update last session
  if (lastSessionEl) {
    const session = status.last_session;
    if (!session) {
      lastSessionEl.innerHTML = '<span class="session-empty">No session data</span>';
    } else {
      const accepted = session.accepted;
      const quality = session.quality_score != null ? fmtNum(session.quality_score, 2) : '?';
      const reason = session.reason || '';
      const startTs = session.started_ts;
      const endTs = session.ended_ts;
      
      let html = `<span class="${accepted ? 'session-accepted' : 'session-rejected'}">`;
      html += accepted ? '✓ Accepted' : '✗ Rejected';
      html += `</span> (quality: ${quality})`;
      
      if (reason && !accepted) {
        html += ` — ${reason}`;
      }
      
      if (startTs && endTs) {
        html += `<span class="session-time">${fmtTs(startTs)} – ${fmtTime(endTs)}</span>`;
      }
      
      lastSessionEl.innerHTML = html;
    }
  }
}

// ============================================
// Settings Modal
// ============================================

function openSettingsModal() {
  const modal = document.getElementById('carHeaterSettingsModal');
  if (modal) {
    modal.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    
    // Request latest status via socket
    if (window.socket) {
      window.socket.emit('kfactor_control', { action: 'status' });
    }
  }
}

function closeSettingsModal() {
  const modal = document.getElementById('carHeaterSettingsModal');
  if (modal) {
    modal.classList.remove('is-open');
    document.body.style.overflow = '';
  }
}

// ============================================
// Ready-by Actions
// ============================================

function scheduleReadyBy() {
  const timeInput = document.getElementById('readyByTimeInput');
  const targetInput = document.getElementById('readyByTargetInput');
  
  if (!timeInput || !timeInput.value) {
    alert('Please select a time');
    return;
  }
  
  const readyByTs = new Date(timeInput.value).toISOString();
  const targetTempC = targetInput ? parseFloat(targetInput.value) : -5;
  
  if (isNaN(targetTempC)) {
    alert('Please enter a valid target temperature');
    return;
  }
  
  // Check if time is in the future
  if (new Date(readyByTs) <= new Date()) {
    alert('Please select a future time');
    return;
  }
  
  const payload = {
    action: 'schedule',
    ready_by_ts: readyByTs,
    target_temp_c: targetTempC,
  };
  
  console.log('Scheduling ready-by:', payload);
  
  if (window.socket) {
    window.socket.emit('ready_by_schedule', payload);
  } else {
    // HTTP fallback
    fetch('/api/car_heater/ready_by/schedule', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify({
        ready_by_ts: readyByTs,
        target_temp_c: targetTempC,
      }),
    })
    .then(r => r.json())
    .then(data => {
      if (data) {
        updateReadyByUI(data);
      }
    })
    .catch(err => {
      console.error('Failed to schedule ready-by:', err);
      alert('Failed to schedule. Please try again.');
    });
  }
}

function cancelReadyBy() {
  if (!confirm('Cancel the scheduled heating?')) {
    return;
  }
  
  const payload = {
    action: 'cancel',
    reason: 'user',
    turn_off: false,
  };
  
  console.log('Canceling ready-by:', payload);
  
  if (window.socket) {
    window.socket.emit('ready_by_schedule', payload);
  } else {
    // HTTP fallback
    fetch('/api/car_heater/ready_by/schedule', {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify({ reason: 'user', turn_off: false }),
    })
    .then(r => r.json())
    .then(data => {
      updateReadyByUI(data);
    })
    .catch(err => {
      console.error('Failed to cancel ready-by:', err);
    });
  }
}

// ============================================
// Event Listeners
// ============================================

let progressUpdateInterval = null;

function startProgressUpdateTimer() {
  if (progressUpdateInterval) return;
  progressUpdateInterval = setInterval(() => {
    if (readyBySchedule && (readyBySchedule.status === 'scheduled' || readyBySchedule.status === 'running')) {
      updateProgressBar(readyBySchedule);
    } else {
      stopProgressUpdateTimer();
    }
  }, 5000); // Update every 5 seconds
}

function stopProgressUpdateTimer() {
  if (progressUpdateInterval) {
    clearInterval(progressUpdateInterval);
    progressUpdateInterval = null;
  }
}

function updateProgressBar(schedule) {
  const progressFill = document.getElementById('readyByProgressFill');
  if (!progressFill || !schedule) return;
  
  const now = new Date();
  const readyByTime = new Date(schedule.ready_by_ts);
  const plannedStartTime = schedule.planned_start_ts ? new Date(schedule.planned_start_ts) : now;
  
  const totalDuration = readyByTime - plannedStartTime;
  const elapsed = now - plannedStartTime;
  let progress = 0;
  
  if (totalDuration > 0) {
    progress = Math.min(100, Math.max(0, (elapsed / totalDuration) * 100));
  }
  
  progressFill.style.width = `${progress}%`;
}

function showToast(message, type = 'success') {
  // Use existing flash system if available
  if (window.showFlash && typeof window.showFlash === 'function') {
    window.showFlash(message, type);
    return;
  }
  
  // Simple fallback toast
  const toast = document.createElement('div');
  toast.className = `ready-by-toast toast-${type}`;
  toast.textContent = message;
  toast.style.cssText = `
    position: fixed;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%);
    background: ${type === 'success' ? '#30d158' : type === 'error' ? '#ff453a' : '#0a84ff'};
    color: white;
    padding: 12px 24px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 500;
    z-index: 10001;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    animation: toastIn 0.3s ease-out;
  `;
  document.body.appendChild(toast);
  
  setTimeout(() => {
    toast.style.animation = 'toastOut 0.3s ease-in forwards';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// Add toast animations to head
const toastStyles = document.createElement('style');
toastStyles.textContent = `
  @keyframes toastIn { from { opacity: 0; transform: translateX(-50%) translateY(20px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
  @keyframes toastOut { from { opacity: 1; transform: translateX(-50%) translateY(0); } to { opacity: 0; transform: translateX(-50%) translateY(20px); } }
`;
document.head.appendChild(toastStyles);

// ============================================
// KFactor Config Management
// ============================================

// Config field mappings: input ID -> config key
const KFACTOR_CONFIG_FIELDS = {
  // Calibration Window
  'kfCfgCalibStart': 'auto_calib_start_hhmm',
  'kfCfgCalibStop': 'auto_calib_stop_hhmm',
  // Session Rules
  'kfCfgGraceSamples': 'grace_samples',
  'kfCfgMinSessionMin': 'min_session_minutes',
  'kfCfgMaxSessionMin': 'max_session_minutes',
  'kfCfgMinTempRise': 'min_temp_rise_C',
  'kfCfgCooldownMin': 'cooldown_minutes',
  'kfCfgBucketLookback': 'bucket_lookback_days',
  // Quality & Acceptance
  'kfCfgQualityThreshold': 'quality_threshold',
  'kfCfgEarlyWindowMin': 'early_window_minutes',
  'kfCfgDropThreshold': 'drop_threshold_C',
  'kfCfgSpikeThreshold': 'spike_threshold_C',
  // Physical Model Defaults
  'kfCfgDefaultEta': 'default_eta',
  'kfCfgDefaultKLoss': 'default_k_loss_W_per_K',
  'kfCfgMassFactor': 'mass_factor',
  // Parameter Bounds
  'kfCfgEtaMin': 'eta_min',
  'kfCfgEtaMax': 'eta_max',
  'kfCfgKLossMin': 'k_loss_min',
  'kfCfgKLossMax': 'k_loss_max',
  'kfCfgMassFactorMin': 'mass_factor_min',
  'kfCfgMassFactorMax': 'mass_factor_max',
  // Fitting Algorithm
  'kfCfgToutSmoothWindow': 'tout_smooth_window',
  'kfCfgInformativeMinMin': 'informative_min_minutes',
  'kfCfgCurvatureRatioMax': 'curvature_ratio_max',
  'kfCfgCurvatureMaxSampleDist': 'curvature_max_sample_distance_s',
  // Prior & Smoothing
  'kfCfgPriorLambdaK': 'prior_lambda_k',
  'kfCfgPriorLambdaEta': 'prior_lambda_eta',
  'kfCfgBaseAlpha': 'base_alpha',
  'kfCfgAlphaMin': 'alpha_min',
  'kfCfgAlphaMax': 'alpha_max',
};

// Debounce timer for config saves
let configSaveTimeout = null;
const CONFIG_SAVE_DEBOUNCE_MS = 500;

function populateKFactorConfig(config) {
  if (!config) return;
  
  for (const [inputId, configKey] of Object.entries(KFACTOR_CONFIG_FIELDS)) {
    const input = document.getElementById(inputId);
    if (!input) continue;
    
    const value = config[configKey];
    if (value === undefined || value === null) continue;
    
    if (input.type === 'time') {
      // Time inputs expect HH:MM format
      input.value = String(value).padStart(5, '0');
    } else if (input.type === 'checkbox') {
      input.checked = Boolean(value);
    } else {
      input.value = value;
    }
  }
}

function getKFactorConfigChanges() {
  const changes = {};
  
  for (const [inputId, configKey] of Object.entries(KFACTOR_CONFIG_FIELDS)) {
    const input = document.getElementById(inputId);
    if (!input) continue;
    
    let value;
    if (input.type === 'time') {
      value = input.value;
    } else if (input.type === 'checkbox') {
      value = input.checked;
    } else if (input.type === 'number') {
      value = input.valueAsNumber;
      if (isNaN(value)) continue;
    } else {
      value = input.value;
    }
    
    changes[configKey] = value;
  }
  
  return changes;
}

function showConfigSaveIndicator(show, text = 'Saving...') {
  const indicator = document.getElementById('kfactorConfigSaveIndicator');
  if (!indicator) return;
  
  const textEl = indicator.querySelector('.save-text');
  if (textEl) textEl.textContent = text;
  
  indicator.style.display = show ? 'flex' : 'none';
}

function saveKFactorConfig() {
  // Clear existing timeout
  if (configSaveTimeout) {
    clearTimeout(configSaveTimeout);
  }
  
  // Show saving indicator
  showConfigSaveIndicator(true, 'Saving...');
  
  // Debounce the save
  configSaveTimeout = setTimeout(() => {
    const config = getKFactorConfigChanges();
    
    if (window.socket) {
      window.socket.emit('kfactor_control', {
        action: 'update_config',
        config: config,
      });
    }
  }, CONFIG_SAVE_DEBOUNCE_MS);
}

function resetKFactorConfigToDefaults() {
  if (!confirm('Reset all kFactor calibration settings to defaults?')) {
    return;
  }
  
  showConfigSaveIndicator(true, 'Resetting...');
  
  if (window.socket) {
    window.socket.emit('kfactor_control', { action: 'reset_defaults' });
  }
}

function setupKFactorConfigListeners() {
  // Add change listeners to all config inputs
  for (const inputId of Object.keys(KFACTOR_CONFIG_FIELDS)) {
    const input = document.getElementById(inputId);
    if (!input) continue;
    
    // Use 'input' event for immediate feedback, 'change' for final value
    input.addEventListener('input', saveKFactorConfig);
    input.addEventListener('change', saveKFactorConfig);
  }
  
  // Reset button
  const resetBtn = document.getElementById('kfCfgResetDefaults');
  if (resetBtn) {
    resetBtn.addEventListener('click', resetKFactorConfigToDefaults);
  }
  
  // Request config when accordion opens
  const accordion = document.getElementById('kfactorAdvancedConfig');
  if (accordion) {
    accordion.addEventListener('toggle', () => {
      if (accordion.open && window.socket) {
        window.socket.emit('kfactor_control', { action: 'get_config' });
      }
    });
  }
}

function emitReadyByConfigChange() {
  const readyByEnabledToggle = document.getElementById('readyByEnabled');
  const readyByCooldownInput = document.getElementById('readyByCooldown');
  const readyByToleranceInput = document.getElementById('readyByTolerance');
  
  const enabled = readyByEnabledToggle ? readyByEnabledToggle.checked : true;
  const commandCooldownS = readyByCooldownInput ? parseInt(readyByCooldownInput.value) : null;
  const reachToleranceMinutes = readyByToleranceInput ? parseInt(readyByToleranceInput.value) : null;
  
  const config = {
    enabled: enabled,
    command_cooldown_s: commandCooldownS,
    reach_tolerance_minutes: reachToleranceMinutes,
  };
  if (window.socket) {
    window.socket.emit('ready_by_control', { action: 'update_config', config });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  // Initialize from server-rendered data
  if (typeof window.CAR_HEATER_READY_BY !== 'undefined') {
    updateReadyByUI(window.CAR_HEATER_READY_BY);
    if (window.CAR_HEATER_READY_BY && 
        (window.CAR_HEATER_READY_BY.status === 'scheduled' || window.CAR_HEATER_READY_BY.status === 'running')) {
      startProgressUpdateTimer();
    }
  } else {
    // Set default time
    const timeInput = document.getElementById('readyByTimeInput');
    if (timeInput && !timeInput.value) {
      timeInput.value = getDefaultReadyByTime();
    }
  }
  
  if (typeof window.CAR_HEATER_KFACTOR !== 'undefined') {
    updateKFactorUI(window.CAR_HEATER_KFACTOR);
    // Also populate config if available
    if (window.CAR_HEATER_KFACTOR.config) {
      populateKFactorConfig(window.CAR_HEATER_KFACTOR.config);
    }
  }
  
  // Setup kFactor config listeners
  setupKFactorConfigListeners();
  
  // Settings modal buttons
  const openSettingsBtn = document.getElementById('btnOpenReadyBySettings');
  const closeSettingsBtn = document.getElementById('carHeaterSettingsClose');
  const settingsModal = document.getElementById('carHeaterSettingsModal');
  
  if (openSettingsBtn) {
    openSettingsBtn.addEventListener('click', openSettingsModal);
  }
  
  if (closeSettingsBtn) {
    closeSettingsBtn.addEventListener('click', closeSettingsModal);
  }
  
  // Close modal on backdrop click
  if (settingsModal) {
    settingsModal.addEventListener('click', (e) => {
      if (e.target === settingsModal) {
        closeSettingsModal();
      }
    });
  }
  
  // Close modal on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeSettingsModal();
    }
  });

  
  // Ready-by action buttons
  const scheduleBtn = document.getElementById('btnReadyBySchedule');
  const cancelBtn = document.getElementById('btnReadyByCancel');
  const readyByEnabledToggle = document.getElementById('readyByEnabled');
  const readyByToleranceInput = document.getElementById('readyByTolerance');
  const readyByCooldownInput = document.getElementById('readyByCooldown');
  
  
  if (scheduleBtn) {
    scheduleBtn.addEventListener('click', scheduleReadyBy);
  }
  
  if (cancelBtn) {
    cancelBtn.addEventListener('click', cancelReadyBy);
  }

  if (readyByEnabledToggle) {
    readyByEnabledToggle.addEventListener('change', () => {
      if (window.socket) {
        emitReadyByConfigChange();
      }
    });
  }
  if (readyByToleranceInput) {
    readyByToleranceInput.addEventListener('change', () => {
      if (window.socket) {
        emitReadyByConfigChange();
      }
    });
  }
  if (readyByCooldownInput) {
    readyByCooldownInput.addEventListener('change', () => {
      if (window.socket) {
        emitReadyByConfigChange();
      }
    });
  }
  
  // kFactor enabled toggle
  const kfactorEnabledToggle = document.getElementById('kfactorEnabled');
  if (kfactorEnabledToggle) {
    kfactorEnabledToggle.addEventListener('change', () => {
      const enabled = kfactorEnabledToggle.checked;
      if (window.socket) {
        window.socket.emit('kfactor_control', { action: 'set_enabled', enabled });
      }
    });
  }
  
  // Socket.IO event handlers
  if (window.socket) {
    window.socket.on('ready_by_status', (data) => {
      console.log('📡 ready_by_status:', data);
      updateReadyByUI(data);
      if (data && data.schedule !== undefined) {
        const prevStatus = readyBySchedule ? readyBySchedule.status : null;
        const newStatus = data.schedule ? data.schedule.status : null;
        
        // Show toast on status changes
        if (newStatus && newStatus !== prevStatus) {
          if (newStatus === 'scheduled') {
            showToast('✓ Heating scheduled', 'success');
            startProgressUpdateTimer();
          } else if (newStatus === 'running') {
            showToast('🔥 Heating started', 'info');
          } else if (newStatus === 'completed') {
            showToast('✓ Target temperature reached!', 'success');
            stopProgressUpdateTimer();
          } else if (newStatus === 'canceled') {
            showToast('Schedule canceled', 'info');
            stopProgressUpdateTimer();
          }
        }
      }
    });
    
    window.socket.on('kfactor_status', (data) => {
      console.log('📡 kfactor_status:', data);
      if (data) {
        updateKFactorUI(data);
        // Also update config if included
        if (data.config) {
          populateKFactorConfig(data.config);
        }
      }
    });
    
    window.socket.on('kfactor_config', (data) => {
      console.log('📡 kfactor_config:', data);
      if (data && data.config) {
        populateKFactorConfig(data.config);
        
        if (data.saved) {
          showConfigSaveIndicator(true, data.reset ? 'Reset!' : 'Saved!');
          setTimeout(() => showConfigSaveIndicator(false), 1500);
        }
      }
    });
  }
});
