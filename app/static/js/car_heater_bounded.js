(() => {
  'use strict';
  const LOCK_REASON = 'Stop heating and wait for confirmed OFF before starting another mode.';
  let pendingStart = false;
  let pendingStops = 0;
  let actionGeneration = 0;
  let latestAction = null;
  // Last successfully loaded session; kept when a refresh fails so absent
  // facts are not reset. `undefined` means never loaded.
  let lastRun;
  let runReceivedMs = 0;
  let runAvailable = false;
  let telemetry = null;
  const byId = id => document.getElementById(id);
  const feedback = text => { const el = byId('boundedHeatingStatus'); if (el) el.textContent = text; };
  async function request(method = 'GET', body) {
    const response = await fetch('/api/car_heater/bounded', {
      method, credentials: 'same-origin', headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
      }, ...(body ? {body: JSON.stringify(body)} : {})
    });
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error('Heating request failed. Reload to check your sign-in and try again.'); }
    if (!response.ok) throw new Error(data.error || 'Heating request failed');
    return data;
  }
  function helsinki(seconds) {
    return new Date(seconds * 1000).toLocaleString('en-GB', {
      timeZone: 'Europe/Helsinki', weekday: 'short', day: 'numeric', month: 'short',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    });
  }
  function remainingText(run) {
    if (!run || run.phase === 'stopped' || typeof run.remaining_s !== 'number') return '—';
    const left = Math.max(0, run.remaining_s - (Date.now() - runReceivedMs) / 1000);
    if (left <= 0) return '0 min · finish reached';
    const minutes = Math.ceil(left / 60);
    return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min` : `${minutes} min`;
  }
  // Relay state from the latest device report, only while it is fresh.
  function telemetryRelay() {
    const status = telemetry?.status;
    if (!status || typeof status.is_heater_on !== 'boolean' || !telemetry.isFresh()) return null;
    return status.is_heater_on;
  }
  function describe(run) {
    if (!runAvailable) return ['unknown', 'Heater state unknown', 'Heating status unavailable. Stop heating remains available.'];
    const active = run && run.phase !== 'stopped';
    if (active) {
      const mode = run.mode === 'departure' ? 'Departure heating' : 'Heating';
      if (run.phase === 'starting') return ['pending', 'Start requested', 'Waiting for the relay to confirm ON. Not yet heating.'];
      if (run.phase === 'heating') {
        if (run.relay_on === true) return ['on', `${mode} · confirmed ON`, 'Confirmed by the relay. It switches itself off by the original finish.'];
        if (run.relay_on === false) return ['unknown', `${mode} · relay reports OFF`, 'The relay reports OFF before the original finish, but the session has not confirmed shutdown.'];
        return ['unknown', `${mode} · relay state unknown`, 'Device evidence is missing or stale. OFF is not confirmed.'];
      }
      if (run.off_state === 'failed_or_unknown') {
        return ['bad', 'Stop not confirmed', 'OFF failed or its result is unknown. The server keeps retrying; you can request OFF again.'];
      }
      return ['pending', run.relay_on === null ? 'Stopping · relay state unknown' : 'Stopping…',
        'OFF requested. Waiting for the relay to confirm shutdown; heating may still be running.'];
    }
    const relay = telemetryRelay();
    const confirmed = run && run.off_state === 'confirmed'
      ? (run.milestones || []).filter(m => m.kind === 'off_confirmed').pop() : null;
    const last = confirmed ? ` Last timed session: OFF confirmed ${helsinki(confirmed.at)}.` : '';
    if (relay === true) return ['on', 'Heater ON · not a timed session', 'The relay reports ON outside a timed session. Stop heating requests OFF.' + last];
    if (confirmed && relay !== false) return ['off', 'OFF confirmed', 'Confirmed by the relay at ' + helsinki(confirmed.at) + '.'];
    if (relay === false) return ['off', 'Heater OFF', 'Reported by the relay.' + last];
    return ['unknown', 'Heater state unknown', 'No fresh device report. OFF is not assumed.' + last];
  }
  function noticeText(run, active, tone) {
    if (!active) {
      return tone === 'unknown' ? 'OFF has not been confirmed. Request OFF if the heater may be running.' : '';
    }
    if (run.phase === 'stopping') {
      return run.relay_on === null
        ? 'OFF requested, but there is no current relay evidence. OFF has not been confirmed; heating may still be running.'
        : 'OFF requested. Heating may still be running until the relay confirms shutdown.';
    }
    if (tone === 'unknown') return 'Connection lost or relay evidence is stale. OFF has not been confirmed; heating may still be running.';
    return `${remainingText(run)} remaining · original finish ${helsinki(run.finish_at)}. No restart or new selection extends this limit.`;
  }
  function render() {
    const run = lastRun || null;
    const active = Boolean(run && run.phase !== 'stopped');
    const pending = pendingStops > 0 || (pendingStart && latestAction === 'turn_on');
    const [tone, title, detail] = describe(run);
    const state = byId('heaterState');
    if (state) state.className = `heater-state state-${tone}`;
    // The live region only changes when its text does, so it is not re-announced
    // by the once-a-second countdown render.
    [['heaterStateTitle', title], ['heaterStateDetail', detail]].forEach(([id, text]) => {
      const el = byId(id);
      if (el && el.textContent !== text) el.textContent = text;
    });
    const relayOn = !active && telemetryRelay() === true;
    const blocked = pending || active || !runAvailable || relayOn;
    const on = byId('btnTurnOn');
    if (on) on.disabled = blocked;
    const duration = byId('manualDuration');
    if (duration) duration.disabled = blocked;
    document.querySelectorAll('[data-duration]').forEach(preset => {
      preset.disabled = blocked;
      preset.setAttribute('aria-pressed', String(duration && Number(duration.value) === Number(preset.dataset.duration)));
    });
    const off = byId('btnTurnOff');
    if (off) {
      off.textContent = pendingStops > 0 ? 'Stopping…'
        : active && run.phase === 'stopping' ? 'Request OFF again'
          : !active && tone === 'unknown' ? 'Request OFF' : 'Stop heating';
    }
    // Stop stays in the principal column unless the relay reports OFF.
    const session = byId('activeSession');
    if (session) session.hidden = !(active || pending || tone !== 'off');
    const notice = byId('activeNotice');
    if (notice) {
      const text = noticeText(run, active, tone);
      notice.hidden = !text;
      notice.textContent = text;
      notice.classList.toggle('is-bad', tone === 'unknown' || tone === 'bad');
    }
    const observe = byId('observeLink');
    if (observe) {
      observe.hidden = !active;
      if (active) observe.href = '/car_heater/sessions/' + encodeURIComponent(run.session_id) + '/capture';
    }
    const reason = active || pending ? LOCK_REASON
      : !runAvailable ? 'Unavailable until heating status loads.'
        : relayOn ? 'The relay reports ON. Stop heating and wait for OFF before starting timed heating.' : '';
    const lock = byId('modeLockReason');
    if (lock) lock.textContent = reason;
    const comfortLock = byId('comfortLockReason');
    if (comfortLock) comfortLock.textContent = active || pending ? LOCK_REASON : '';
    ['keepAtTempToggle', 'btnReadyBySchedule', 'kfactorEnabled'].forEach(id => {
      const control = byId(id);
      if (control) { control.disabled = active || pending; control.title = active || pending ? LOCK_REASON : ''; }
    });
    const facts = {boundedMode: '—', boundedFinish: '—', boundedRemaining: remainingText(run)};
    if (run) {
      facts.boundedMode = run.mode === 'departure' ? 'Departure' : run.mode === 'manual' ? 'Manual' : '—';
      facts.boundedFinish = helsinki(run.finish_at) + (active ? '' : ' · ended');
    }
    Object.entries(facts).forEach(([id, text]) => { const el = byId(id); if (el) el.textContent = text; });
    const link = byId('boundedSessionLink');
    if (link) {
      link.hidden = !run;
      if (run) link.href = '/car_heater/sessions/' + encodeURIComponent(run.session_id);
    }
  }
  function accept(run) {
    lastRun = run; runReceivedMs = Date.now(); runAvailable = true;
  }
  async function refresh() {
    if (!byId('heaterState') || pendingStops > 0 || (pendingStart && latestAction === 'turn_on')) return;
    const generation = actionGeneration;
    try {
      const run = await request();
      if (generation === actionGeneration) accept(run);
    } catch (error) {
      console.warn('Bounded heating status unavailable:', error);
      runAvailable = false;
    }
    render();
  }
  window.addEventListener('car_heater_telemetry', event => { telemetry = event.detail; render(); });
  window.addEventListener('car_heater_bounded_control', async event => {
    const start = event.detail.action === 'turn_on';
    if (!byId('heaterState') || (start && (pendingStart || pendingStops > 0))) return;
    const input = byId('manualDuration');
    if (start && input && !input.reportValidity()) return;
    const generation = ++actionGeneration;
    latestAction = event.detail.action;
    if (start) pendingStart = true; else pendingStops += 1;
    feedback(start ? 'Requesting timed heating…' : 'Requesting OFF…');
    render();
    let run;
    try {
      run = await request(start ? 'POST' : 'DELETE', start ? {duration_minutes: Number(input?.value || 60)} : undefined);
      if (generation === actionGeneration) feedback(start ? 'Start submitted. Only relay evidence confirms heating.' : 'OFF submitted. Only relay evidence confirms shutdown.');
    } catch (error) {
      if (generation === actionGeneration) feedback(error.message);
    } finally {
      if (start) pendingStart = false; else pendingStops -= 1;
      if (generation === actionGeneration && run !== undefined) accept(run);
      render();
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    if (!byId('heaterState')) return;
    document.querySelectorAll('[data-duration]').forEach(preset => preset.addEventListener('click', () => {
      const input = byId('manualDuration');
      if (input && !input.disabled) { input.value = preset.dataset.duration; render(); }
    }));
    byId('manualDuration')?.addEventListener('input', render);
    if (window.socket) window.socket.on('connect', refresh);
    refresh();
    window.setInterval(refresh, 5000);
    // Countdown and telemetry freshness advance between server updates.
    window.setInterval(render, 1000);
  });
})();
