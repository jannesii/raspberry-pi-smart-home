/**
 * Car Heater Settings - Ready-by Schedule Module
 * Handles Ready-by scheduling UI and actions
 */

console.log('⚙️ car_heater_settings_schedule.js loaded');

// ============================================
// Ready-by State
// ============================================

let readyBySchedule = null;
let progressUpdateInterval = null;

// ============================================
// Utility References
// ============================================

const { fmtTs, fmtTime, fmtNum, showToast } = window.CarHeaterSettings || {
  fmtTs: (ts) => ts || '—',
  fmtTime: (ts) => ts || '—',
  fmtNum: (v, d = 1) => v?.toFixed?.(d) ?? '—',
  showToast: (msg) => console.log(msg),
};

// ============================================
// Default Time Helper
// ============================================

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
// Progress Bar Timer
// ============================================

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

// ============================================
// Socket.IO Event Handlers
// ============================================

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
}

// ============================================
// Initialization
// ============================================

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
});

// Export for other modules
window.CarHeaterSettings = window.CarHeaterSettings || {};
Object.assign(window.CarHeaterSettings, {
  updateReadyByUI,
  updateReadyBySchedule,
  scheduleReadyBy,
  cancelReadyBy,
  getDefaultReadyByTime,
});
