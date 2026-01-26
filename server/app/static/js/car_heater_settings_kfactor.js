/**
 * Car Heater Settings - KFactor Calibration Module
 * Handles KFactor calibration UI, config, and data panel
 */

(() => {
  console.log('⚙️ car_heater_settings_kfactor.js loaded');

// ============================================
// KFactor State
// ============================================

let kfactorStatus = null;
let kfExtendedDataLoading = false;

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
// KFactor Config Field Mappings
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

// ============================================
// KFactor UI Updates
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
    stateIndicator.classList.remove('state-idle', 'state-armed', 'state-recording', 'state-cooldown',
                                   'state-passive_recording', 'state-autonomous_armed', 'state-autonomous_recording');
    stateIndicator.classList.add(`state-${state.toLowerCase()}`);
  }
  if (stateText) {
    // Make state text more readable
    const stateLabels = {
      'IDLE': 'Idle',
      'PASSIVE_RECORDING': 'Recording (passive)',
      'AUTONOMOUS_ARMED': 'Armed (autonomous)',
      'AUTONOMOUS_RECORDING': 'Recording (autonomous)',
      'COOLDOWN': 'Cooldown'
    };
    stateText.textContent = stateLabels[state] || state;
  }

  // Update enabled toggle (now controls autonomous mode)
  if (enabledToggle) {
    enabledToggle.checked = status.autonomous_enabled !== false;
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

  // Update cooldown - show whichever is later
  if (cooldownEl) {
    const autoCd = status.autonomous_cooldown_until;
    const passiveCd = status.passive_cooldown_until;
    if (autoCd || passiveCd) {
      const parts = [];
      if (autoCd) parts.push(`Auto: ${fmtTs(autoCd)}`);
      if (passiveCd) parts.push(`Passive: ${fmtTs(passiveCd)}`);
      cooldownEl.textContent = parts.join(' | ');
    } else {
      cooldownEl.textContent = '—';
    }
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
      const isAuto = session.is_autonomous;

      let html = `<span class="${accepted ? 'session-accepted' : 'session-rejected'}">`;
      html += accepted ? '✓ Accepted' : '✗ Rejected';
      html += `</span> (quality: ${quality})`;

      if (isAuto) {
        html += ' <span class="session-mode-auto">auto</span>';
      }

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
// KFactor Config Management
// ============================================

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
  console.log('⚙️ resetKFactorConfigToDefaults clicked');
  if (!confirm('Reset all kFactor calibration settings to defaults?')) {
    return;
  }

  showConfigSaveIndicator(true, 'Resetting...');

  if (window.socket) {
    window.socket.emit('kfactor_control', { action: 'reset_defaults' });
  }
}

function resetKfactorCooldown() {
  console.log('⏱️ resetKfactorCooldown clicked');
  if (!confirm('Reset kFactor cooldown timers?')) {
    return;
  }

  if (window.socket) {
    window.socket.emit('kfactor_control', { action: 'reset_cooldown' });
    showToast('Resetting kFactor cooldown...');
  } else {
    showToast('Socket unavailable');
  }
}

function setupKFactorConfigListeners() {
  console.log('⚙️ setupKFactorConfigListeners init');
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

  const resetCooldownBtn = document.getElementById('btnKfactorResetCooldown');
  if (resetCooldownBtn) {
    resetCooldownBtn.addEventListener('click', resetKfactorCooldown);
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

// ============================================
// KFactor Extended Data Panel
// ============================================

function requestKFactorExtendedData() {
  if (!window.socket || kfExtendedDataLoading) return;

  kfExtendedDataLoading = true;
  const btn = document.getElementById('kfRefreshDataBtn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner">↻</span> Loading...';
  }

  window.socket.emit('kfactor_control', { action: 'extended_data' });
}

function renderKFactorExtendedData(data) {
  kfExtendedDataLoading = false;

  const btn = document.getElementById('kfDataRefresh');
  if (btn) {
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">🔄</span> Refresh Data';
  }

  if (!data) return;

  console.log('📊 Rendering extended data:', data);

  // Update recent sessions count badge
  const recentCount = document.getElementById('kfRecentCount');
  if (recentCount) {
    recentCount.textContent = (data.recent_sessions || []).length;
  }

  // Render each section
  renderKfLiveSession(data.live_session);
  renderKfRecentSessions(data.recent_sessions || []);
  renderKfBucketCoverage(data.bucket_coverage || []);
  renderKfStatistics(data.statistics || {});
}

function renderKfLiveSession(session) {
  const container = document.getElementById('kfDataLiveSession');
  if (!container) return;

  // Update individual elements
  const els = {
    duration: document.getElementById('kfLiveDuration'),
    samples: document.getElementById('kfLiveSamples'),
    cabinTemp: document.getElementById('kfLiveCabinTemp'),
    riseRate: document.getElementById('kfLiveRiseRate'),
    outsideTemp: document.getElementById('kfLiveOutsideTemp'),
    wind: document.getElementById('kfLiveWind'),
    power: document.getElementById('kfLivePower'),
    slowRise: document.getElementById('kfLiveSlowRise'),
    slowRiseRow: document.getElementById('kfLiveSlowRiseRow'),
    sessionMode: document.getElementById('kfLiveSessionMode')
  };

  if (!session || !session.active) {
    // Not recording - hide live session container
    container.style.display = 'none';
    return;
  }

  // Show live session container
  container.style.display = 'block';

  // Recording active - update values
  if (els.sessionMode) {
    els.sessionMode.textContent = session.mode || 'passive';
    els.sessionMode.className = `kf-mode-badge ${session.mode || 'passive'}`;
  }

  if (els.duration) {
    const mins = Math.floor((session.duration_s || 0) / 60);
    const secs = Math.floor((session.duration_s || 0) % 60);
    els.duration.textContent = `${mins}:${String(secs).padStart(2, '0')}`;
  }

  if (els.samples) {
    els.samples.textContent = session.sample_count || 0;
  }

  if (els.cabinTemp) {
    const start = session.cabin_temp_start != null ? fmtNum(session.cabin_temp_start) : '?';
    const current = session.cabin_temp_current != null ? fmtNum(session.cabin_temp_current) : '?';
    const rise = session.cabin_temp_rise != null ? fmtNum(session.cabin_temp_rise) : '?';
    els.cabinTemp.textContent = `${start}° → ${current}° (+${rise}°)`;
  }

  if (els.outsideTemp) {
    els.outsideTemp.textContent = session.outside_temp_current != null ? `${fmtNum(session.outside_temp_current)}°` : '—';
  }

  if (els.wind) {
    els.wind.textContent = session.wind_current != null ? `${fmtNum(session.wind_current)} m/s` : '—';
  }

  if (els.power) {
    els.power.textContent = session.power_current != null ? `${Math.round(session.power_current)} W` : '—';
  }

  if (els.riseRate) {
    const rate = session.rise_rate_c_per_min;
    els.riseRate.textContent = rate != null ? `${fmtNum(rate, 2)}°/min` : '—';
  }

  // Show slow rise counter for autonomous mode
  if (els.slowRiseRow && els.slowRise) {
    if (session.mode === 'autonomous' && session.slow_rise_counter != null) {
      els.slowRiseRow.style.display = 'block';
      els.slowRise.textContent = `${session.slow_rise_counter}/${session.slow_rise_threshold || '?'}`;
    } else {
      els.slowRiseRow.style.display = 'none';
    }
  }
}

function renderKfRecentSessions(sessions) {
  const container = document.getElementById('kfRecentSessionsTable');
  if (!container) return;

  if (!sessions || sessions.length === 0) {
    container.innerHTML = '<div class="kf-sessions-empty">No recent sessions</div>';
    return;
  }

  let html = `
    <table class="kf-sessions-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Bucket</th>
          <th>Quality</th>
          <th>k</th>
          <th>η</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
  `;

  for (const s of sessions) {
    const time = s.start_ts ? fmtTs(s.start_ts) : '—';
    // Bucket info from outside_t_mean and wind_mean (rounded to bucket)
    const tBucket = s.outside_t_mean != null ? Math.round(s.outside_t_mean / 5) * 5 : null;
    const wBucket = s.wind_mean != null ? (s.wind_mean < 2 ? '0-2' : s.wind_mean < 5 ? '2-5' : s.wind_mean < 10 ? '5-10' : '10+') : null;
    const bucket = tBucket != null ? `${tBucket}°/${wBucket || '?'}` : '—';
    const quality = s.quality_score != null ? fmtNum(s.quality_score, 2) : '—';
    // k_loss and eta are nested under fit object
    const kLoss = s.fit && s.fit.k_loss != null ? fmtNum(s.fit.k_loss, 1) : '—';
    const eta = s.fit && s.fit.eta != null ? fmtNum(s.fit.eta, 2) : '—';

    let statusClass = '';
    let statusText = '';
    if (s.accepted === true) {
      statusClass = 'fit-ok';
      statusText = '✓';
    } else if (s.accepted === false) {
      statusClass = 'fit-bad';
      statusText = '✗';
    } else {
      statusClass = 'fit-warn';
      statusText = '?';
    }

    html += `
      <tr>
        <td>${time}</td>
        <td class="mono">${bucket}</td>
        <td class="mono">${quality}</td>
        <td class="mono">${kLoss}</td>
        <td class="mono">${eta}</td>
        <td class="${statusClass}">${statusText}</td>
      </tr>
    `;
  }

  html += '</tbody></table>';
  container.innerHTML = html;
}

function renderKfBucketCoverage(buckets) {
  const container = document.getElementById('kfBucketGrid');
  if (!container) return;

  if (!buckets || buckets.length === 0) {
    container.innerHTML = '<div class="kf-sessions-empty">No bucket data</div>';
    return;
  }

  // Group buckets by temp_bucket for better organization
  let html = '';

  for (const b of buckets) {
    const label = `${b.t_bucket || '?'}° / ${b.wind_bucket ?? '?'}`;
    const kLoss = b.k_loss;
    const eta = b.eta;
    const calibTs = b.updated_ts;
    const ageDays = b.age_days;

    // Determine status: calibrated (recent), stale (old), or empty
    let cellClass = 'empty';
    let valueText = '—';

    if (kLoss != null && eta != null) {
      // Check if recent (within 30 days)
      cellClass = (ageDays != null && ageDays <= 30) ? 'calibrated' : 'stale';
      valueText = `k=${fmtNum(kLoss, 1)}`;
    }

    const ageLabel = ageDays != null ? `${ageDays}d ago` : '';

    html += `
      <div class="kf-bucket-cell ${cellClass}" title="η=${eta != null ? fmtNum(eta, 2) : '—'}${ageLabel ? ', ' + ageLabel : ''}">
        <span class="kf-bucket-label">${label}</span>
        <span class="kf-bucket-value">${valueText}</span>
        <span class="kf-bucket-samples">${ageLabel || '—'}</span>
      </div>
    `;
  }

  container.innerHTML = html;
}

function renderKfStatistics(stats) {
  const els = {
    totalSessions: document.getElementById('kfStatTotalSessions'),
    accepted: document.getElementById('kfStatAccepted'),
    recent: document.getElementById('kfStatRecent'),
    avgQuality: document.getElementById('kfStatAvgQuality'),
    coverage: document.getElementById('kfStatCoverage'),
    lastSession: document.getElementById('kfStatLastSession')
  };

  if (els.totalSessions) {
    els.totalSessions.textContent = stats.total_sessions ?? 0;
  }

  if (els.accepted) {
    els.accepted.textContent = stats.accepted_sessions ?? 0;
  }

  if (els.recent) {
    els.recent.textContent = stats.sessions_last_7d ?? 0;
  }

  if (els.avgQuality) {
    const avg = stats.avg_quality;
    els.avgQuality.textContent = avg != null ? fmtNum(avg, 2) : '—';
  }

  if (els.coverage) {
    const covered = stats.buckets_covered ?? 0;
    const total = stats.buckets_total ?? 45;
    const pct = stats.coverage_pct ?? 0;
    els.coverage.textContent = `${covered}/${total} (${pct}%)`;
  }

  if (els.lastSession) {
    const days = stats.days_since_last_session;
    els.lastSession.textContent = days != null ? (days === 0 ? 'Today' : `${days}d ago`) : '—';
  }
}

// ============================================
// Socket.IO Event Handlers
// ============================================

if (window.socket) {
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

  window.socket.on('kfactor_extended_data', (data) => {
    console.log('📡 kfactor_extended_data:', data);
    renderKFactorExtendedData(data);
  });
}

// ============================================
// Initialization
// ============================================

document.addEventListener('DOMContentLoaded', () => {
  // Initialize from server-rendered data
  if (typeof window.CAR_HEATER_KFACTOR !== 'undefined') {
    updateKFactorUI(window.CAR_HEATER_KFACTOR);
    // Also populate config if available
    if (window.CAR_HEATER_KFACTOR.config) {
      populateKFactorConfig(window.CAR_HEATER_KFACTOR.config);
    }
  }

  // Setup kFactor config listeners
  setupKFactorConfigListeners();

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

  // Calibration data panel accordion toggle (uses <details> element)
  const kfDataPanel = document.getElementById('kfactorDataPanel');
  if (kfDataPanel) {
    kfDataPanel.addEventListener('toggle', () => {
      // Auto-fetch data when opening
      if (kfDataPanel.open) {
        requestKFactorExtendedData();
      }
    });
  }

  // Refresh button for calibration data
  const kfRefreshBtn = document.getElementById('kfDataRefresh');
  if (kfRefreshBtn) {
    kfRefreshBtn.addEventListener('click', requestKFactorExtendedData);
  }
});

// Export for other modules
window.CarHeaterSettings = window.CarHeaterSettings || {};
Object.assign(window.CarHeaterSettings, {
  updateKFactorUI,
  populateKFactorConfig,
  requestKFactorExtendedData,
});

})();
