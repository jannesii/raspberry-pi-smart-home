(() => {
  'use strict';
  let pendingStart = false;
  let pendingStops = 0;
  let actionGeneration = 0;
  let latestAction = null;
  const status = () => document.getElementById('boundedHeatingStatus');
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
  function render(run) {
    const active = run && run.phase !== 'stopped';
    const pending = pendingStops > 0 || (pendingStart && latestAction === 'turn_on');
    const on = document.getElementById('btnTurnOn');
    if (on) on.disabled = pending || Boolean(active);
    const duration = document.getElementById('manualDuration');
    if (duration) duration.disabled = pending || Boolean(active);
    const link = document.getElementById('boundedSessionLink');
    if (link) {
      link.hidden = !run;
      if (run) link.href = '/car_heater/sessions/' + encodeURIComponent(run.session_id);
    }
    ['keepAtTempToggle', 'btnReadyBySchedule', 'kfactorEnabled'].forEach(id => {
      const control = document.getElementById(id);
      if (control) { control.disabled = Boolean(active); control.title = active ? 'Confirm Stop heating before starting another mode' : ''; }
    });
    if (!status()) return;
    if (!run) { status().textContent = 'Manual heating: choose 1–180 minutes. Relay-local shutdown capability is required.'; return; }
    const finish = new Date(run.finish_at * 1000).toLocaleString('en-GB', {timeZone: 'Europe/Helsinki', hour12: false});
    const remaining = active ? Math.max(0, Math.ceil((run.finish_at - Date.now() / 1000) / 60)) : 0;
    const relay = run.relay_on === true ? 'confirmed ON' : run.relay_on === false ? 'confirmed OFF' : 'relay unknown';
    const off = run.off_state === 'confirmed' ? 'Stop confirmed' : run.off_state === 'failed_or_unknown' ? 'Stop failed or unknown; retrying' : run.off_state === 'pending' ? 'Stop pending; waiting for relay confirmation' : run.phase === 'starting' ? 'Start queued; waiting for relay confirmation' : 'Timed heating';
    status().textContent = `${off} · ${relay} · ${remaining} min allowance remaining · latest finish ${finish}. ${active ? 'Other mode starts require confirmed shutdown.' : ''}`;
  }
  async function refresh() {
    if (!status() || pendingStops > 0 || (pendingStart && latestAction === 'turn_on')) return;
    const generation = actionGeneration;
    try { const run = await request(); if (generation === actionGeneration) render(run); }
    catch (_) { status().textContent = 'Heating status unavailable; relay state unknown. Stop heating remains available.'; }
  }
  window.addEventListener('car_heater_bounded_control', async event => {
    const start = event.detail.action === 'turn_on';
    if (!status() || (start && (pendingStart || pendingStops > 0))) return;
    const input = document.getElementById('manualDuration');
    if (start && input && !input.reportValidity()) return;
    const generation = ++actionGeneration;
    latestAction = event.detail.action;
    if (start) pendingStart = true; else pendingStops += 1;
    status().textContent = start ? 'Requesting timed heating…' : 'Requesting Stop; relay state unknown…';
    let run;
    try {
      run = await request(start ? 'POST' : 'DELETE', start ? {duration_minutes: Number(input?.value || 60)} : undefined);
    } catch (error) {
      if (generation === actionGeneration) status().textContent = error.message;
    } finally {
      if (start) pendingStart = false; else pendingStops -= 1;
      if (generation === actionGeneration && run !== undefined) render(run);
    }
  });
  document.addEventListener('DOMContentLoaded', () => { refresh(); window.setInterval(refresh, 5000); });
})();
