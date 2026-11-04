/**
 * Heating → Ready by (cabin target): schedule, replace and cancel the earlier
 * cabin-temperature schedule. Times are Europe/Helsinki wall time whatever the
 * browser's timezone; the shown state always comes from the server's reply.
 */
(() => {
  const TZ = 'Europe/Helsinki';
  const TIME = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
  const byId = id => document.getElementById(id);
  let payload = null;
  let modeLock = {locked: false, reason: ''};
  let pending = false;

  const setText = (id, text) => { const el = byId(id); if (el) el.textContent = text; };
  const finite = v => v !== null && v !== undefined && Number.isFinite(Number(v));
  const fixed = (v, digits, unit = '') => finite(v) ? `${Number(v).toFixed(digits)}${unit}` : '—';
  const parse = ts => { const d = ts ? new Date(String(ts).replace(' ', 'T')) : null; return d && !Number.isNaN(d.getTime()) ? d : null; };
  const helsinki = (ts, opts) => {
    const d = parse(ts);
    return d ? d.toLocaleString('en-GB', {timeZone: TZ, hourCycle: 'h23', ...opts}) : '—';
  };
  const when = ts => helsinki(ts, {weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});
  const clock = ts => helsinki(ts, {hour: '2-digit', minute: '2-digit'});

  // YYYY-MM-DD and HH:MM of a moment in Helsinki.
  function helsinkiParts(date) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
      timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(date).map(p => [p.type, p.value]));
    return {date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}`};
  }

  function tomorrow() {
    const [y, m, d] = helsinkiParts(new Date()).date.split('-').map(Number);
    return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
  }

  const schedule = () => payload?.schedule || null;
  const active = () => ['scheduled', 'running'].includes(schedule()?.status);

  function describe(s) {
    if (!s) return 'No Ready-by schedule.';
    switch (s.status) {
      case 'scheduled': return `Scheduled · cabin ${fixed(s.target_temp_c, 1, ' °C')} by ${when(s.ready_by_ts)}.`;
      case 'running': return `Running · heating cabin toward ${fixed(s.target_temp_c, 1, ' °C')} by ${when(s.ready_by_ts)}.`;
      case 'completed': return `Target reached ${when(s.reached_ts)}; comfort heating keeps ${fixed(s.target_temp_c, 1, ' °C')}.`;
      case 'canceled': return 'No Ready-by schedule. The last one was cancelled.';
      case 'expired': return `No Ready-by schedule. The last one expired after ${when(s.ready_by_ts)}.`;
      default: return `Ready-by schedule error${s.cancel_reason ? `: ${s.cancel_reason}` : ''}.`;
    }
  }

  function render() {
    const s = schedule();
    const disabled = payload?.config && payload.config.enabled === false;
    setText('readyByState', describe(s) + (disabled
      ? ' Ready-by is switched off in Settings → Calibration, so saved schedules do not run.' : ''));
    setText('readyBySummaryState', !s || !active() ? 'Off'
      : s.status === 'running' ? 'Running' : `Scheduled ${clock(s.ready_by_ts)}`);
    const facts = byId('readyByFacts');
    if (facts) facts.hidden = !s || !['scheduled', 'running', 'completed'].includes(s.status);
    if (s) {
      setText('readyByWhen', when(s.ready_by_ts));
      setText('readyByPlannedStart', s.planned_start_ts ? when(s.planned_start_ts) : 'After the next status report');
      setText('readyByEta', s.unreachable ? 'Target not reachable · starts now'
        : finite(s.predicted_eta_minutes) ? `${Math.round(Number(s.predicted_eta_minutes))} min` : '—');
      setText('readyByCabin', fixed(s.cabin_temp_c, 1, ' °C'));
      setText('readyByOutside', fixed(s.outside_temp_c, 1, ' °C'));
      setText('readyByModel', finite(s.used_k_loss_W_per_K) || finite(s.used_eta)
        ? `Heat loss ${fixed(s.used_k_loss_W_per_K, 1)} W/K · η ${fixed(s.used_eta, 2)}` : '—');
    }
    // An active schedule fills the form for editing unless the driver is typing.
    const form = byId('readyByForm');
    if (form && form.dataset.dirty !== 'true') {
      const date = byId('readyByDate');
      const time = byId('readyByTime');
      const target = byId('readyByTarget');
      if (!date || !time || !target) {
        // Nothing to fill.
      } else if (active() && parse(s.ready_by_ts)) {
        const saved = helsinkiParts(parse(s.ready_by_ts));
        date.value = saved.date;
        time.value = saved.time;
        target.value = String(s.target_temp_c);
      } else if (!date.value) {
        date.value = tomorrow();
      }
    }
    renderControls();
  }

  function renderControls() {
    const save = byId('readyBySchedule');
    const cancel = byId('readyByCancel');
    // A timed session owns the relay; the server refuses a start too.
    if (save) save.disabled = pending || modeLock.locked;
    if (cancel) cancel.disabled = pending || !active();
    setText('readyByLockReason', modeLock.locked ? modeLock.reason : '');
  }

  async function request(method, body, pendingText, doneText) {
    pending = true;
    renderControls();
    setText('readyByFeedback', pendingText);
    try {
      const resp = await fetch('/api/car_heater/ready_by/schedule', {
        method,
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '',
        },
        body: JSON.stringify(body),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      byId('readyByForm').dataset.dirty = 'false';
      payload = {...(payload || {}), schedule: data.schedule};
      setText('readyByFeedback', doneText);
    } catch (err) {
      console.warn('Ready-by request failed:', err);
      setText('readyByFeedback', `Not saved: ${err.message}. The shown schedule is unchanged.`);
    } finally {
      pending = false;
      render();
    }
  }

  function submit(event) {
    event.preventDefault();
    const fields = ['readyByDate', 'readyByTime', 'readyByTarget'].map(byId);
    if (fields.some(field => !field)) return;
    const date = fields[0].value;
    const time = fields[1].value.trim();
    const target = Number(fields[2].value);
    if (!date || !TIME.test(time)) {
      setText('readyByFeedback', 'Enter a date and a 24-hour time such as 07:00.');
      return;
    }
    if (fields[2].value === '' || !Number.isFinite(target) || target < -40 || target > 40) {
      setText('readyByFeedback', 'Enter a cabin target between -40 and 40 °C.');
      return;
    }
    const now = helsinkiParts(new Date());
    if (`${date}T${time}` <= `${now.date}T${now.time}`) {
      setText('readyByFeedback', 'Choose a future Helsinki time.');
      return;
    }
    request('POST', {ready_by_ts: `${date}T${time}`, target_temp_c: target}, 'Saving…',
      `Saved. Cabin ${fixed(target, 1, ' °C')} by ${date} ${time} (Helsinki).`);
  }

  async function refresh() {
    try {
      const resp = await fetch('/api/car_heater/ready_by/schedule', {cache: 'no-store'});
      if (resp.ok) { payload = await resp.json(); render(); }
    } catch (err) {
      console.debug('Ready-by refresh failed:', err);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const form = byId('readyByForm');
    if (!form) return;
    payload = window.CAR_HEATER_READY_BY || null;
    render();
    if (!payload) setText('readyByState', 'Ready-by schedule unavailable.');
    form.addEventListener('input', () => { form.dataset.dirty = 'true'; });
    form.addEventListener('submit', submit);
    byId('readyByCancel')?.addEventListener('click', () => {
      if (!window.confirm('Cancel the Ready-by schedule? Current heating is unchanged.')) return;
      request('DELETE', {reason: 'user', turn_off: false},
        'Cancelling…', 'Ready-by schedule cancelled. Current heating is unchanged.');
    });
    window.addEventListener('car_heater_mode_lock', event => {
      modeLock = {locked: !!event.detail?.locked, reason: event.detail?.reason || ''};
      renderControls();
    });
    window.socket?.on('ready_by_status', data => { if (data) { payload = data; render(); } });
    window.setInterval(refresh, 30000);
  });
})();
