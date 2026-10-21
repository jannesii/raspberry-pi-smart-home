// Calibration Settings (Settings view). Nothing is saved until Save or one of
// the explicit actions, only changed fields are sent, and every shown value
// comes from the server's persisted reply. Calibration is experimental and
// never controls departure heating.
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const setText = (id, text) => { const el = byId(id); if (el && el.textContent !== text) el.textContent = text; };
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  // Saved calibration state from the server; null until loaded.
  let calibration = null;
  let pending = false;
  let modeLock = {locked: false, reason: ''};
  // Bumped by each change, so a read that started earlier is discarded.
  let generation = 0;

  const STATES = {
    IDLE: 'Idle, waiting for heating to record',
    PASSIVE_RECORDING: 'Recording heating started by another mode',
    AUTONOMOUS_ARMED: 'Autonomous calibration armed',
    AUTONOMOUS_RECORDING: 'Autonomous calibration heating and recording',
    COOLDOWN: 'Cooling down before the next recording'
  };
  const WAITING = {
    ready_by_running: 'Ready-by heating is running',
    ready_by_scheduled_soon: 'a Ready-by schedule starts within two hours',
    keep_at_temp_enabled: 'comfort heating is on',
    charge_mode_active: 'charge mode is active'
  };

  async function api(path, method = 'GET', body) {
    const response = await fetch(path, {
      method, credentials: 'same-origin', headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrf()
      }, ...(body ? {body: JSON.stringify(body)} : {})
    });
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error('Request failed. Reload to check your sign-in and try again.'); }
    if (!response.ok) {
      const error = new Error(data.error || 'Request failed');
      error.data = data;
      throw error;
    }
    return data;
  }

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const fixed = (value, digits) => finite(value) ? value.toFixed(digits) : '—';

  function helsinki(iso) {
    // Server stamps may use a space separator, which Safari cannot parse.
    const date = iso ? new Date(String(iso).replace(' ', 'T')) : null;
    if (!date || Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('en-GB', {
      timeZone: 'Europe/Helsinki', day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    });
  }

  // Form inputs carry name="config.<key>" or name="ready_by.<key>".
  const fields = () => Array.from(byId('calibrationForm')?.querySelectorAll('[name]') || []);

  function savedValue(input) {
    const [section, key] = input.name.split('.');
    const source = section === 'ready_by' ? calibration.ready_by : calibration.config;
    return source ? source[key] : undefined;
  }

  function lockReason() {
    if (!calibration || calibration.autonomous_enabled) return '';
    if (modeLock.locked) return modeLock.reason;
    if (!calibration.autonomous_start_available) {
      return calibration.unavailable_reason || 'Autonomous calibration cannot start now.';
    }
    return '';
  }

  function renderStatus() {
    const known = calibration !== null;
    const params = known ? calibration.active_params || {} : {};
    setText('calState', known ? STATES[calibration.state] || calibration.state || '—' : '—');
    setText('calParams', known && (finite(params.k_loss_W_per_K) || finite(params.eta))
      ? `Heat loss ${fixed(params.k_loss_W_per_K, 1)} W/K · efficiency η ${fixed(params.eta, 2)}`
      : '—');
    const last = known ? calibration.last_session : null;
    setText('calLastSession', !last ? 'No calibration session recorded yet'
      : `${last.accepted ? 'Accepted' : 'Rejected'} · quality ${fixed(last.quality_score, 2)}`
        + (last.started_ts ? ` · ${helsinki(last.started_ts)}` : '')
        + (!last.accepted && last.reason ? ` · ${last.reason}` : ''));
    const config = known ? calibration.config || {} : {};
    setText('calWindow', config.auto_calib_start_hhmm && config.auto_calib_stop_hhmm
      ? `${config.auto_calib_start_hhmm}–${config.auto_calib_stop_hhmm} (Helsinki)` : '—');
    const cooldowns = known ? [
      calibration.autonomous_cooldown_until && `Autonomous ${helsinki(calibration.autonomous_cooldown_until)}`,
      calibration.passive_cooldown_until && `Passive ${helsinki(calibration.passive_cooldown_until)}`
    ].filter(Boolean) : [];
    setText('calCooldown', cooldowns.length ? cooldowns.join(' · ') : known ? 'No cooldown' : '—');
    setText('calVersion', !known ? '—' : `Model ${calibration.model_version || 'version not reported'} · `
      + (calibration.config_saved_at ? `settings saved ${helsinki(calibration.config_saved_at)}`
        : 'defaults, never saved'));

    const on = known && calibration.autonomous_enabled;
    const waiting = on && calibration.autonomous_waiting_for
      ? ` · waiting because ${WAITING[calibration.autonomous_waiting_for] || calibration.autonomous_waiting_for}` : '';
    setText('calAutonomousState', !known ? '—' : on ? `Autonomous calibration on${waiting}` : 'Autonomous calibration off');
    const reason = lockReason();
    setText('calAutonomousLock', reason);
    const toggle = byId('calAutonomousToggle');
    if (toggle) {
      toggle.textContent = on ? 'Turn off autonomous calibration' : 'Turn on autonomous calibration';
      toggle.disabled = !known || pending || Boolean(reason);
    }
    ['calResetCooldown', 'calResetDefaults', 'calibrationSave'].forEach(id => {
      const button = byId(id);
      if (button) button.disabled = !known || pending;
    });
  }

  function accept(data, {keepEdits = true} = {}) {
    calibration = data;
    fields().forEach(input => {
      if (keepEdits && input.dataset.dirty === 'true') return;
      const value = savedValue(input);
      if (input.type === 'checkbox') input.checked = value === true;
      else input.value = value ?? '';
      input.dataset.dirty = 'false';
    });
    renderStatus();
  }

  async function load() {
    if (!byId('calibrationForm')) return;
    const started = generation;
    try {
      const data = await api('/api/car_heater/calibration');
      if (started === generation && !pending) accept(data);
      setText('calLoadStatus', '');
    } catch (error) {
      console.warn('Calibration settings unavailable:', error);
      setText('calLoadStatus', calibration === null
        ? 'Calibration settings could not be loaded. Reload to try again.'
        : 'Calibration status could not be refreshed. Earlier values stay shown.');
    }
  }

  async function send(path, method, body, feedbackId, pendingText, doneText) {
    pending = true;
    generation += 1;
    setText(feedbackId, pendingText);
    renderStatus();
    try {
      accept(await api(path, method, body));
      setText(feedbackId, doneText);
      return true;
    } catch (error) {
      // A refusal or failure carries the saved state; unsaved edits stay.
      if (error.data && 'config' in error.data) accept(error.data);
      setText(feedbackId, `Not saved: ${error.message}`);
      return false;
    } finally {
      pending = false;
      renderStatus();
    }
  }

  function changedFields() {
    const changes = {};
    for (const input of fields()) {
      if (input.dataset.dirty !== 'true') continue;
      if (!input.checkValidity()) {
        // Open the collapsed group so the browser can point at the field.
        input.closest('details')?.setAttribute('open', '');
        input.closest('details')?.parentElement?.closest('details')?.setAttribute('open', '');
        input.reportValidity();
        return null;
      }
      if (input.type !== 'checkbox' && input.value.trim() === '') {
        setText('calibrationFeedback', 'Enter a value or reload to restore the saved one.');
        input.focus();
        return null;
      }
      const [section, key] = input.name.split('.');
      const value = input.type === 'checkbox' ? input.checked
        : input.type === 'number' ? Number(input.value) : input.value.trim();
      (changes[section] = changes[section] || {})[key] = value;
    }
    return changes;
  }

  async function save(event) {
    event.preventDefault();
    const changes = changedFields();
    if (changes === null) return;
    if (Object.keys(changes).length === 0) {
      setText('calibrationFeedback', 'No changes to save.');
      return;
    }
    const count = Object.values(changes).reduce((n, section) => n + Object.keys(section).length, 0);
    await send('/api/car_heater/calibration', 'PATCH', changes, 'calibrationFeedback', 'Saving…',
      `Saved ${count} ${count === 1 ? 'setting' : 'settings'}. Future fits and estimates use them.`);
  }

  function toggleAutonomous() {
    if (!calibration) return;
    const enable = !calibration.autonomous_enabled;
    if (enable && !window.confirm('Autonomous calibration switches the heater on by itself inside the calibration window. Turn it on?')) return;
    send('/api/car_heater/calibration', 'PATCH', {autonomous_enabled: enable}, 'calActionFeedback',
      enable ? 'Turning on…' : 'Turning off…',
      enable ? 'Autonomous calibration is on.' : 'Autonomous calibration is off. A running calibration session was ended.');
  }

  function action(name, confirmText, pendingText, doneText) {
    if (confirmText && !window.confirm(confirmText)) return;
    send(`/api/car_heater/calibration/actions/${name}`, 'POST', null, 'calActionFeedback', pendingText, doneText)
      .then(ok => {
        if (!ok || name !== 'reset_defaults') return;
        // Show the restored defaults; unsaved Ready-by edits are untouched.
        fields().forEach(input => { if (input.name.startsWith('config.')) input.dataset.dirty = 'false'; });
        accept(calibration);
        setText('calibrationFeedback', '');
      });
  }

  // -------------------------------------------------------- recorded data
  function factList(id, rows) {
    const list = byId(id);
    if (!list) return;
    list.replaceChildren(...rows.flatMap(([term, detail]) => {
      const dt = document.createElement('dt');
      dt.textContent = term;
      const dd = document.createElement('dd');
      dd.textContent = detail;
      return [dt, dd];
    }));
  }

  function renderData(data) {
    const live = data.live_session;
    const liveBox = byId('calLiveSession');
    if (liveBox) liveBox.hidden = !(live && live.active);
    if (live && live.active) {
      const minutes = finite(live.duration_s) ? Math.floor(live.duration_s / 60) : null;
      factList('calLiveFacts', [
        ['Mode', live.mode === 'autonomous' ? 'Autonomous' : 'Passive'],
        ['Duration', minutes === null ? '—' : `${minutes} min`],
        ['Reports', String(live.sample_count ?? '—')],
        ['Cabin', `${fixed(live.cabin_temp_start, 1)} → ${fixed(live.cabin_temp_current, 1)} °C`
          + (finite(live.cabin_temp_rise) ? ` (rise ${fixed(live.cabin_temp_rise, 1)} °C)` : '')],
        ['Outside', finite(live.outside_temp_current) ? `${fixed(live.outside_temp_current, 1)} °C` : '—'],
        ['Wind', finite(live.wind_current) ? `${fixed(live.wind_current, 1)} m/s` : '—'],
        ['Heater power', finite(live.power_current) ? `${Math.round(live.power_current)} W` : '—'],
        ['Rise rate', finite(live.rise_rate_c_per_min) ? `${fixed(live.rise_rate_c_per_min, 2)} °C/min` : '—'],
        ...(live.mode === 'autonomous' && live.slow_rise_counter != null
          ? [['Slow-rise checks', `${live.slow_rise_counter} of ${live.slow_rise_threshold ?? '—'}`]] : [])
      ]);
    }
    const stats = data.statistics || {};
    factList('calStats', [
      ['Sessions recorded', String(stats.total_sessions ?? '—')],
      ['Accepted', String(stats.accepted_sessions ?? '—')],
      ['Last 7 days', String(stats.sessions_last_7d ?? '—')],
      // No sessions means no average, not a zero score.
      ['Average quality', stats.total_sessions ? fixed(stats.avg_quality, 2) : '—'],
      ['Bucket coverage', stats.buckets_total ? `${stats.buckets_covered ?? 0} of ${stats.buckets_total} (${stats.coverage_pct ?? 0} %)` : '—'],
      ['Since last session', stats.days_since_last_session == null ? '—'
        : stats.days_since_last_session === 0 ? 'Today' : `${stats.days_since_last_session} days`]
    ]);
    setText('calSessionCount', String((data.recent_sessions || []).length));
    const body = byId('calSessions')?.querySelector('tbody');
    if (body) {
      const sessions = data.recent_sessions || [];
      if (!sessions.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 6;
        cell.textContent = 'No calibration sessions recorded yet.';
        row.append(cell);
        body.replaceChildren(row);
      } else {
        body.replaceChildren(...sessions.map(s => {
          const row = document.createElement('tr');
          const fit = s.fit || {};
          [
            helsinki(s.start_ts),
            finite(s.outside_t_mean) ? `${fixed(s.outside_t_mean, 1)} °C` : '—',
            fixed(s.quality_score, 2),
            fixed(fit.k_loss, 1),
            fixed(fit.eta, 2),
            s.accepted === true ? 'Accepted' : s.accepted === false ? `Rejected${s.reason ? ` · ${s.reason}` : ''}` : 'Not evaluated'
          ].forEach(text => {
            const cell = document.createElement('td');
            cell.textContent = text;
            row.append(cell);
          });
          return row;
        }));
      }
    }
    const buckets = byId('calBuckets');
    if (buckets) {
      const items = (data.bucket_coverage || []).map(b => {
        const item = document.createElement('li');
        const fitted = finite(b.k_loss) && finite(b.eta);
        const age = b.age_days == null ? 'age unknown' : `${b.age_days} days old`;
        const stale = b.age_days == null || b.age_days > 30;
        item.textContent = `${b.t_bucket ?? '?'} °C, wind ${b.wind_bucket ?? '?'}: `
          + (fitted ? `k_loss ${fixed(b.k_loss, 1)} W/K, η ${fixed(b.eta, 2)} · ${age}${stale ? ' · stale' : ''}` : 'no fit');
        return item;
      });
      if (!items.length) {
        const empty = document.createElement('li');
        empty.textContent = 'No bucket has been calibrated yet.';
        items.push(empty);
      }
      buckets.replaceChildren(...items);
    }
  }

  // The last recorded data shown, so live status can refresh just its session.
  let lastData = {};

  async function loadData() {
    const button = byId('calDataRefresh');
    if (button) button.disabled = true;
    setText('calDataStatus', 'Loading recorded data…');
    try {
      lastData = await api('/api/car_heater/calibration/data');
      renderData(lastData);
      setText('calDataStatus', '');
    } catch (error) {
      setText('calDataStatus', `Recorded data could not be loaded: ${error.message}`);
    } finally {
      if (button) button.disabled = false;
    }
  }

  window.addEventListener('car_heater_mode_lock', event => {
    const next = event.detail || {locked: false, reason: ''};
    if (next.locked === modeLock.locked && next.reason === modeLock.reason) return;
    modeLock = next;
    renderStatus();
    // A bounded start suspends autonomous calibration on the server.
    load();
  });

  document.addEventListener('DOMContentLoaded', () => {
    const form = byId('calibrationForm');
    if (!form) return;
    form.addEventListener('input', event => { if (event.target.name) event.target.dataset.dirty = 'true'; });
    form.addEventListener('change', event => { if (event.target.name) event.target.dataset.dirty = 'true'; });
    form.addEventListener('submit', save);
    byId('calAutonomousToggle')?.addEventListener('click', toggleAutonomous);
    byId('calResetCooldown')?.addEventListener('click', () => action('reset_cooldown', null,
      'Resetting cooldown…', 'Cooldown cleared. Heating was not started.'));
    byId('calResetDefaults')?.addEventListener('click', () => action('reset_defaults',
      'Reset every calibration model setting to its default and turn autonomous calibration off? Ready-by settings, recorded sessions and fitted parameters are kept.',
      'Resetting…', 'Model settings reset to defaults. Autonomous calibration is off.'));
    const data = byId('calData');
    data?.addEventListener('toggle', () => { if (data.open) loadData(); });
    byId('calDataRefresh')?.addEventListener('click', loadData);
    if (window.socket) {
      window.socket.on('calibration_settings', payload => {
        if (payload && 'config' in payload && !pending) accept(payload);
      });
      // Telemetry ticks push model status; merge it so absent fields keep their values.
      window.socket.on('kfactor_status', status => {
        if (!status || !calibration || pending) return;
        const merged = {...calibration};
        ['state', 'autonomous_enabled', 'active_params', 'last_session', 'live_session',
          'autonomous_cooldown_until', 'passive_cooldown_until'].forEach(key => {
          if (key in status) merged[key] = status[key];
        });
        calibration = merged;
        renderStatus();
        if (byId('calData')?.open && 'live_session' in status) renderData({...lastData, live_session: status.live_session});
      });
      window.socket.on('connect', load);
    }
    load();
    const settings = form.closest('.heater-settings');
    // Status changes with heating; refresh only while Settings is visible.
    window.setInterval(() => { if (settings && !settings.hidden && !pending) load(); }, 30000);
  });
})();
