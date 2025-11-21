(() => {
  const els = {
    state: document.getElementById('state'),
    currentK: document.getElementById('currentK'),
    ambientStart: document.getElementById('ambientStart'),
    target: document.getElementById('targetTemp'),
    elapsed: document.getElementById('elapsed'),
    timeSync: document.getElementById('timeSync'),
    records: document.getElementById('records'),
    recordsStatus: document.getElementById('records-status'),
    form: document.getElementById('start-form'),
    targetInput: document.getElementById('target-input'),
    modeNow: () => document.querySelector('input[name="mode"][value="now"]'),
    modeSchedule: () => document.querySelector('input[name="mode"][value="schedule"]'),
    scheduleFields: document.getElementById('schedule-fields'),
    dateInput: document.getElementById('date-input'),
    timeInput: document.getElementById('time-input'),
    formStatus: document.getElementById('form-status'),
    startBtn: document.getElementById('start-btn'),
    cancelBtn: document.getElementById('cancel-btn')
    ,
    autoForm: document.getElementById('auto-form'),
    autoEnabled: document.getElementById('auto-enabled'),
    autoStart: document.getElementById('auto-start'),
    autoEnd: document.getElementById('auto-end'),
    autoTarget: document.getElementById('auto-target'),
    autoStatus: document.getElementById('auto-status')
    ,
    navTemp: document.getElementById('navTemp')
  };

  function updateNavTemp(t) {
    if (!els.navTemp || typeof t !== 'number') return;
    els.navTemp.textContent = `${t.toFixed(1)}°`;
    els.navTemp.className = 'nav-temp';
    if (t < 0) els.navTemp.classList.add('cold');
    else if (t < 10) els.navTemp.classList.add('cool');
    else if (t < 25) els.navTemp.classList.add('warm');
    else els.navTemp.classList.add('hot');
  }

  function setFormStatus(msg, ok = true) {
    els.formStatus.textContent = msg || '';
    els.formStatus.style.color = ok ? '#aaa' : '#e53935';
  }

  function setRecordsStatus(msg, ok = true) {
    if (!els.recordsStatus) return;
    els.recordsStatus.textContent = msg || '';
    els.recordsStatus.style.color = ok ? '#aaa' : '#e53935';
  }

  async function syncTimeFromDevice() {
    try {
      const now = new Date();
      const epochSeconds = Math.floor(now.getTime() / 1000);
      const tzOffsetMin = -now.getTimezoneOffset(); // east-positive

      const params = new URLSearchParams();
      params.set('epoch', epochSeconds.toString());
      params.set('tz', tzOffsetMin.toString());

      await fetch('/sync-time', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: params.toString()
      });
      // refresh after sync attempt
      fetchStatus();
    } catch (e) {
      console.error('Failed to sync time:', e);
    }
  }

  function fmtSeconds(sec) {
    if (!Number.isFinite(sec) || sec <= 0) return '–';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    if (m === 0) return `${s}s`;
    return `${m}m ${s}s`;
  }

  async function confirmDelete(record) {
    if (!record || !record.epoch_utc) return;
    const k = Number.isFinite(record.k) ? record.k.toFixed(2) : '?';
    const ambient = Number.isFinite(record.ambient_c) ? record.ambient_c.toFixed(1) : '?';
    const target = Number.isFinite(record.target_c) ? record.target_c.toFixed(1) : '?';
    const when = record.epoch_utc ? new Date(record.epoch_utc * 1000).toLocaleString() : 'this entry';
    const ok = window.confirm(`Delete calibration k=${k} (${ambient}°C → ${target}°C) from ${when}?`);
    if (!ok) return;
    await deleteRecord(record.epoch_utc);
  }

  async function deleteRecord(epochUtc) {
    if (!epochUtc) {
      setRecordsStatus('Missing record identifier', false);
      return;
    }
    setRecordsStatus('Deleting...');
    try {
      const params = new URLSearchParams();
      params.set('epoch_utc', String(epochUtc));
      const res = await fetch('/api/calibration/delete', {
        method: 'POST',
        body: params
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Delete failed');
      setRecordsStatus('Entry deleted', true);
      fetchStatus();
    } catch (err) {
      console.error(err);
      setRecordsStatus(err.message || 'Delete failed', false);
    }
  }

  function renderRecords(list) {
    if (!list || list.length === 0) {
      els.records.textContent = 'No records yet.';
      setRecordsStatus('');
      return;
    }
    els.records.innerHTML = '';
    list.forEach((r) => {
      const div = document.createElement('div');
      div.className = 'card muted';
      const when = r.epoch_utc ? new Date(r.epoch_utc * 1000).toLocaleString() : 'n/a';
      const k = Number.isFinite(r.k) ? r.k.toFixed(2) : 'n/a';
      const ambient = Number.isFinite(r.ambient_c) ? r.ambient_c.toFixed(1) : '--';
      const target = Number.isFinite(r.target_c) ? r.target_c.toFixed(1) : '--';

      const line1 = document.createElement('div');
      line1.innerHTML = `<strong>k=${k}</strong> | Ambient ${ambient}°C → Target ${target}°C`;
      const line2 = document.createElement('div');
      line2.textContent = `Warmup ${fmtSeconds(r.warmup_seconds)} | ${when}`;

      const actions = document.createElement('div');
      actions.className = 'btn-row';
      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'btn-danger';
      delBtn.textContent = 'Delete';
      delBtn.addEventListener('click', () => confirmDelete(r));
      actions.appendChild(delBtn);

      div.appendChild(line1);
      div.appendChild(line2);
      div.appendChild(actions);
      els.records.appendChild(div);
    });
  }

  function applyStatus(msg) {
    if (!msg) return;
    els.state.textContent = msg.state || 'idle';
    els.currentK.textContent = Number.isFinite(msg.current_k) ? msg.current_k.toFixed(2) : '–';
    els.ambientStart.textContent = Number.isFinite(msg.ambient_start_c) ? msg.ambient_start_c.toFixed(1) : '–';
    els.target.textContent = Number.isFinite(msg.target_temp_c) ? msg.target_temp_c.toFixed(1) : '–';
    els.elapsed.textContent = fmtSeconds(msg.elapsed_seconds);
    els.timeSync.textContent = msg.time_synced ? 'Time OK' : 'Time not synced – cannot start calibration';
    if (!msg.time_synced) {
      syncTimeFromDevice();
    }
    renderRecords(msg.records || []);
    updateNavTemp(msg.current_temp);

    if (typeof msg.auto_enabled === 'boolean') {
      els.autoEnabled.checked = msg.auto_enabled;
    }
    if (typeof msg.auto_start_min === 'number') {
      const h = Math.floor(msg.auto_start_min / 60).toString().padStart(2, '0');
      const m = Math.floor(msg.auto_start_min % 60).toString().padStart(2, '0');
      els.autoStart.value = `${h}:${m}`;
    }
    if (typeof msg.auto_end_min === 'number') {
      const h = Math.floor(msg.auto_end_min / 60).toString().padStart(2, '0');
      const m = Math.floor(msg.auto_end_min % 60).toString().padStart(2, '0');
      els.autoEnd.value = `${h}:${m}`;
    }
    if (typeof msg.auto_target_cap_c === 'number') {
      els.autoTarget.value = msg.auto_target_cap_c.toFixed(1);
    }

    const busy = msg.state === 'running' || msg.state === 'scheduled';
    els.startBtn.disabled = busy || !msg.time_synced;
    els.cancelBtn.disabled = !busy;
  }

  async function fetchStatus() {
    try {
      const res = await fetch('/api/calibration/status');
      const data = await res.json();
      applyStatus(data);
    } catch (err) {
      setFormStatus('Failed to load status', false);
    }
  }

  function connectWs() {
    try {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'calibration_update') {
          applyStatus(msg);
          if (typeof msg.current_temp === 'number') updateNavTemp(msg.current_temp);
        } else if (msg.type === 'time_sync' && !msg.time_synced) {
          syncTimeFromDevice();
        } else if (msg.type === 'temp_update' && typeof msg.temp === 'number') {
          updateNavTemp(msg.temp);
        }
      };
      ws.onopen = fetchStatus;
    } catch (err) {
      console.error(err);
      fetchStatus();
    }
  }

  function scheduleToggle() {
    const show = els.modeSchedule().checked;
    els.scheduleFields.style.display = show ? 'block' : 'none';
  }

  async function startCalibration(ev) {
    ev.preventDefault();
    const target = parseFloat(els.targetInput.value);
    if (Number.isNaN(target)) {
      setFormStatus('Enter a target temperature', false);
      return;
    }

    let startEpoch = 0;
    if (els.modeSchedule().checked) {
      if (!els.dateInput.value || !els.timeInput.value) {
        setFormStatus('Set date and time for scheduled start', false);
        return;
      }
      const localIso = `${els.dateInput.value}T${els.timeInput.value}:00`;
      startEpoch = Math.floor(new Date(localIso).getTime() / 1000);
    }

    setFormStatus('Starting...');
    try {
      const res = await fetch('/api/calibration/start', {
        method: 'POST',
        body: new URLSearchParams({
          target,
          start_epoch_utc: startEpoch || ''
        })
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Start failed');
      setFormStatus('Calibration scheduled', true);
      fetchStatus();
    } catch (err) {
      console.error(err);
      setFormStatus(err.message, false);
    }
  }

  async function cancelCalibration() {
    try {
      const res = await fetch('/api/calibration/cancel', { method: 'POST' });
      const data = await res.json();
      if (!data.ok) throw new Error('Cancel failed');
      setFormStatus('Calibration cancelled', true);
      fetchStatus();
    } catch (err) {
      setFormStatus(err.message, false);
    }
  }

  async function saveAutomation(ev) {
    ev.preventDefault();
    try {
      const params = new URLSearchParams();
      params.set('auto_enabled', els.autoEnabled.checked ? '1' : '0');
      if (els.autoStart.value) {
        const [h, m] = els.autoStart.value.split(':').map(Number);
        params.set('auto_start_min', (h * 60 + m).toString());
      }
      if (els.autoEnd.value) {
        const [h, m] = els.autoEnd.value.split(':').map(Number);
        params.set('auto_end_min', (h * 60 + m).toString());
      }
      if (els.autoTarget.value) {
        params.set('auto_target_cap_c', els.autoTarget.value);
      }
      const res = await fetch('/api/calibration/settings', {
        method: 'POST',
        body: params
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Save failed');
      els.autoStatus.textContent = 'Saved';
    } catch (err) {
      els.autoStatus.textContent = err.message;
    }
  }

  els.form.addEventListener('submit', startCalibration);
  els.modeNow().addEventListener('change', scheduleToggle);
  els.modeSchedule().addEventListener('change', scheduleToggle);
  els.cancelBtn.addEventListener('click', cancelCalibration);
  if (els.autoForm) {
    els.autoForm.addEventListener('submit', saveAutomation);
  }

  connectWs();
  fetchStatus();
})();
