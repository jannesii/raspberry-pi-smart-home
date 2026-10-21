// Comfort heating (Heating view), Heating preferences and Installation &
// devices (Settings view). Nothing is saved until the matching Save/Start/Stop
// action, and every shown "saved" value comes from the server's persisted reply.
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const setText = (id, text) => { const el = byId(id); if (el && el.textContent !== text) el.textContent = text; };
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  // Saved comfort state from the server; null until loaded.
  let comfort = null;
  let comfortPending = false;
  let modeLock = {locked: false, reason: '', phase: 'none'};
  let installation = null;
  let installationPending = false;
  let preferencesPending = false;
  // Bumped by each save, so a read that started earlier is discarded.
  let comfortGeneration = 0;
  let installationGeneration = 0;

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

  function celsius(value, digits = 1) {
    return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)} °C` : '—';
  }

  function helsinki(iso) {
    const date = iso ? new Date(iso) : null;
    if (!date || Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('en-GB', {
      timeZone: 'Europe/Helsinki', day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    });
  }

  // ---------------------------------------------------------------- comfort
  function comfortLockReason() {
    if (!comfort) return '';
    if (comfort.enabled) return '';
    if (modeLock.locked) return modeLock.reason;
    if (!comfort.start_available) return comfort.unavailable_reason || 'Comfort heating cannot start now.';
    if (comfort.hysteresis_c == null) return 'Save a hysteresis in Settings → Heating preferences before starting.';
    return '';
  }

  function renderComfort() {
    const target = byId('comfortTarget');
    if (!target) return;
    const known = comfort !== null;
    const on = known && comfort.enabled;
    setText('comfortSummaryState', !known ? '—' : on ? 'On' : 'Off');
    setText('comfortState', !known ? 'Comfort heating status unavailable.'
      : on ? `Comfort heating on · keeping cabin near ${celsius(comfort.target_temperature_c)}`
        : 'Comfort heating off');
    setText('comfortSavedTarget', known ? celsius(comfort.target_temperature_c) : '—');
    setText('comfortHysteresis', known ? celsius(comfort.hysteresis_c) : '—');
    const reason = comfortLockReason();
    setText('comfortLockReason', reason);
    const start = byId('comfortStart');
    if (start) start.disabled = !known || on || comfortPending || Boolean(reason);
    const stop = byId('comfortStop');
    if (stop) stop.disabled = !known || !on || comfortPending;
    const save = byId('comfortSave');
    if (save) save.disabled = !known || comfortPending || target.dataset.dirty !== 'true';
    target.disabled = !known || comfortPending;
    renderPreferenceFacts();
  }

  function acceptComfort(data, {keepEdits = true} = {}) {
    comfort = data;
    const target = byId('comfortTarget');
    if (target && (!keepEdits || target.dataset.dirty !== 'true')) {
      target.value = data.target_temperature_c ?? '';
      target.dataset.dirty = 'false';
    }
    const hysteresis = byId('prefHysteresis');
    if (hysteresis && (!keepEdits || hysteresis.dataset.dirty !== 'true')) {
      hysteresis.value = data.hysteresis_c ?? '';
      hysteresis.dataset.dirty = 'false';
    }
    renderComfort();
  }

  async function loadComfort() {
    if (!byId('comfortTarget') && !byId('prefHysteresis')) return;
    const generation = comfortGeneration;
    try {
      const data = await api('/api/car_heater/comfort');
      if (generation === comfortGeneration && !comfortPending && !preferencesPending) acceptComfort(data);
    } catch (error) {
      console.warn('Comfort settings unavailable:', error);
      if (comfort === null) setText('comfortFeedback', 'Comfort settings could not be loaded. Reload to try again.');
    }
  }

  async function sendComfort(changes, pendingText, doneText) {
    comfortPending = true;
    comfortGeneration += 1;
    setText('comfortFeedback', pendingText);
    renderComfort();
    try {
      acceptComfort(await api('/api/car_heater/comfort', 'PATCH', changes), {keepEdits: false});
      setText('comfortFeedback', doneText);
    } catch (error) {
      // A refusal carries the saved state; unsaved edits stay in the field.
      if (error.data && 'enabled' in error.data) acceptComfort(error.data);
      setText('comfortFeedback', `Not saved: ${error.message}`);
    } finally {
      comfortPending = false;
      renderComfort();
    }
  }

  function comfortTargetChanges() {
    const target = byId('comfortTarget');
    if (!target.reportValidity()) return null;
    if (target.value === '') {
      setText('comfortFeedback', 'Enter a target temperature first.');
      target.focus();
      return null;
    }
    return {target_temperature_c: Number(target.value)};
  }

  // ------------------------------------------------------------ preferences
  function renderPreferenceFacts() {
    if (!byId('prefHysteresis')) return;
    setText('prefComfortTarget', comfort ? celsius(comfort.target_temperature_c) : '—');
    setText('prefComfortState', !comfort ? '—' : comfort.enabled ? 'On'
      : comfortLockReason() ? `Off · ${comfortLockReason()}` : 'Off');
    const save = byId('preferencesSave');
    if (save) save.disabled = comfort === null || preferencesPending;
  }

  async function savePreferences(event) {
    event.preventDefault();
    const input = byId('prefHysteresis');
    if (!input.reportValidity()) return;
    if (input.value === '') {
      setText('preferencesFeedback', 'Enter a hysteresis between 0.1 and 10 °C.');
      input.focus();
      return;
    }
    preferencesPending = true;
    comfortGeneration += 1;
    setText('preferencesFeedback', 'Saving…');
    renderPreferenceFacts();
    try {
      const saved = await api('/api/car_heater/comfort', 'PATCH', {hysteresis_c: Number(input.value)});
      acceptComfort(saved, {keepEdits: false});
      setText('preferencesFeedback', `Saved. Hysteresis is ${celsius(saved.hysteresis_c)}.`);
    } catch (error) {
      setText('preferencesFeedback', `Not saved: ${error.message}`);
    } finally {
      preferencesPending = false;
      renderPreferenceFacts();
    }
  }

  // ----------------------------------------------------------- installation
  const INSTALLATION_INPUTS = {
    heater: 'instHeater', power_topology: 'instPower', glass_reference_position: 'instGlassReference',
    glass_comparison_position: 'instGlassComparison', cabin_position: 'instCabin'
  };

  function sensorText(sensor) {
    const identity = `${sensor.model}, firmware default address ${sensor.default_address}`;
    if (sensor.state === 'fresh') return `${celsius(sensor.temperature_c)} · current · ${identity}`;
    if (sensor.state === 'stale') return `${celsius(sensor.temperature_c)} · stale since ${helsinki(sensor.sampled_at)} · ${identity}`;
    if (sensor.state === 'invalid') return `Invalid reading · ${identity}`;
    return `No reading reported · ${identity}`;
  }

  function renderDevices(devices) {
    const firmware = devices.firmware;
    setText('factFirmware', firmware.reported == null
      ? `Not reported · this server was tested with ${firmware.tested}`
      : firmware.matches ? `${firmware.reported} · matches the tested version`
        : `${firmware.reported} · differs from the tested ${firmware.tested}`);
    const relay = devices.relay;
    setText('factRelay', !relay.reported ? 'No relay capability report yet. Timed heating is unavailable until one arrives.'
      : `${relay.ready ? 'Ready' : 'Not ready'} for timed heating · protocol ${relay.protocol ?? '—'}`
        + ` · reported ${relay.age_s ?? '—'} s ago` + (relay.note ? ` · ${relay.note}` : ''));
    const list = byId('deviceSensors');
    if (!list) return;
    list.replaceChildren(...devices.sensors.map(sensor => {
      const item = document.createElement('li');
      const label = document.createElement('strong');
      label.textContent = sensor.label;
      const value = document.createElement('span');
      value.textContent = sensorText(sensor);
      item.append(label, value);
      return item;
    }));
  }

  function renderInstallation({keepEdits = true} = {}) {
    if (!installation) return;
    const record = installation.record || {};
    Object.entries(INSTALLATION_INPUTS).forEach(([key, id]) => {
      const input = byId(id);
      if (input && (!keepEdits || input.dataset.dirty !== 'true')) {
        input.value = record[key] ?? '';
        input.dataset.dirty = 'false';
      }
    });
    setText('installationSaved', installation.record_available === false
      ? 'The installation record could not be read. Device facts below are still current.'
      : installation.record
      ? `Current record saved ${helsinki(installation.record.saved_at)} by ${installation.record.saved_by || 'unknown'}.`
      : 'No installation recorded yet. Empty fields are shown as not recorded in sessions.');
    const earlier = installation.history.slice(1);
    const history = byId('installationHistory');
    if (history) history.hidden = earlier.length === 0;
    setText('installationHistoryCount', String(earlier.length));
    const list = byId('installationHistoryList');
    if (list) {
      list.replaceChildren(...earlier.map(row => {
        const item = document.createElement('li');
        const parts = Object.keys(INSTALLATION_INPUTS)
          .map(key => row[key]).filter(Boolean);
        item.textContent = `${helsinki(row.saved_at)}: ${parts.length ? parts.join(' · ') : 'nothing recorded'}`;
        return item;
      }));
    }
    renderDevices(installation.devices);
  }

  async function loadInstallation() {
    if (!byId('installationForm')) return;
    const generation = installationGeneration;
    try {
      const data = await api('/api/car_heater/installation');
      if (generation !== installationGeneration || installationPending) return;
      installation = data;
      renderInstallation();
      setText('deviceFactsStatus', '');
    } catch (error) {
      console.warn('Installation details unavailable:', error);
      setText('deviceFactsStatus', 'Device facts could not be loaded. Earlier values stay shown.');
      if (!installation) setText('installationSaved', 'Installation record could not be loaded.');
    }
  }

  async function saveInstallation(event) {
    event.preventDefault();
    const changes = {};
    Object.entries(INSTALLATION_INPUTS).forEach(([key, id]) => {
      const input = byId(id);
      if (input && input.dataset.dirty === 'true') changes[key] = input.value;
    });
    if (Object.keys(changes).length === 0) {
      setText('installationFeedback', 'No changes to save.');
      return;
    }
    installationPending = true;
    installationGeneration += 1;
    const button = byId('installationSave');
    if (button) button.disabled = true;
    setText('installationFeedback', 'Saving…');
    try {
      installation = await api('/api/car_heater/installation', 'PATCH', changes);
      renderInstallation({keepEdits: false});
      setText('installationFeedback', 'Saved. New heating sessions will record this installation.');
    } catch (error) {
      setText('installationFeedback', `Not saved: ${error.message}`);
    } finally {
      installationPending = false;
      if (button) button.disabled = false;
    }
  }

  function markDirty(event) { event.target.dataset.dirty = 'true'; }

  window.addEventListener('car_heater_mode_lock', event => {
    const next = event.detail || {locked: false, reason: '', phase: 'none'};
    if (next.locked === modeLock.locked && next.reason === modeLock.reason
      && next.phase === modeLock.phase) return;
    modeLock = next;
    renderComfort();
    // A bounded start suspends comfort on the server; show the saved result
    // once the session phase moves on.
    loadComfort();
  });

  document.addEventListener('DOMContentLoaded', () => {
    const target = byId('comfortTarget');
    if (target) {
      target.addEventListener('input', event => { markDirty(event); renderComfort(); });
      byId('comfortForm')?.addEventListener('submit', event => {
        event.preventDefault();
        const changes = comfortTargetChanges();
        if (changes) sendComfort({...changes, enabled: true}, 'Starting comfort heating…', 'Comfort heating started. It switches the relay from cabin readings.');
      });
      byId('comfortSave')?.addEventListener('click', () => {
        const changes = comfortTargetChanges();
        if (changes) sendComfort(changes, 'Saving target…', 'Target saved.');
      });
      byId('comfortStop')?.addEventListener('click', () => sendComfort({enabled: false}, 'Stopping comfort heating…', 'Comfort heating stopped. The relay was not switched.'));
    }
    const hysteresis = byId('prefHysteresis');
    if (hysteresis) {
      hysteresis.addEventListener('input', markDirty);
      byId('preferencesForm')?.addEventListener('submit', savePreferences);
    }
    const form = byId('installationForm');
    if (form) {
      form.addEventListener('input', markDirty);
      form.addEventListener('submit', saveInstallation);
      byId('btnRefreshDevices')?.addEventListener('click', loadInstallation);
      const settings = form.closest('.heater-settings');
      // Device facts change with every report; refresh only while visible.
      window.setInterval(() => {
        if (settings && !settings.hidden && !installationPending) loadInstallation();
      }, 15000);
    }
    if (window.socket) {
      window.socket.on('keep_at_temp_settings', data => { if (data && 'start_available' in data) acceptComfort(data); });
      window.socket.on('connect', loadComfort);
    }
    loadComfort();
    loadInstallation();
    // Bounded-session ownership changes on the server; re-check availability.
    window.setInterval(loadComfort, 30000);
  });
})();
