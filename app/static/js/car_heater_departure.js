(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const format = value => value ? new Date(value).toLocaleString('en-GB', {
    timeZone: 'Europe/Helsinki', hour12: false, timeZoneName: 'short',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  }) : '—';
  function localParts(value) {
    const parts = new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Helsinki',
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(value);
    const p = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return {date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}`};
  }
  function tomorrowDate() {
    const today = localParts(new Date()).date;
    const tomorrow = new Date(today + 'T12:00:00Z');
    tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
    return tomorrow.toISOString().slice(0, 10);
  }
  function node(tag, value) { const el = document.createElement(tag); el.textContent = value; return el; }
  async function request(method = 'GET', body, suffix = '') {
    const response = await fetch('/api/car_heater/departure' + suffix, {
      method, credentials: 'same-origin', headers: {'Content-Type': 'application/json',
        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''},
      ...(body ? {body: JSON.stringify(body)} : {})
    });
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error('Departure request failed. Check your sign-in and reload.'); }
    if (!response.ok) throw new Error(data.error || 'Departure request failed');
    return data;
  }
  function timeline(target, plan, heading) {
    target.replaceChildren(node('h3', heading));
    const list = node('ol', ''); list.className = 'departure-timeline';
    [['Heating start', plan.planned_start_at], ['Departure', plan.departure_at], ['Latest finish', plan.finish_at]].forEach(([label, value]) => {
      const item = node('li', ''); const time = node('time', value ? localParts(new Date(value)).time : '—'); if (value) time.dateTime = value;
      const day = node('small', format(value)); day.className = 'departure-timeline-date';
      item.append(node('strong', label), time, day); list.append(item);
    });
    target.append(list, node('p', `${plan.preheat_minutes} min preheat · ${plan.grace_minutes} min grace · original finish stays fixed.`));
    const weather = plan.weather;
    const info = node('p', `${weather.source} · ${weather.station_name || 'Station unknown'}${weather.fmisid ? ' (' + weather.fmisid + ')' : ''} · ${weather.temperature_c == null ? '—' : weather.temperature_c + ' °C'} · observed ${format(weather.observed_at)}. Freshness limit: ${weather.max_age_s / 60} min.`);
    info.className = 'departure-weather'; target.append(info);
    if (plan.fallback_reason) {
      const fallback = node('p', `Fallback: ${plan.fallback_reason}. 180 minutes, no extra grace.`);
      fallback.className = 'departure-fallback'; target.append(fallback);
    }
    target.hidden = false;
  }
  document.addEventListener('DOMContentLoaded', () => {
    if (!byId('departureForm')) return;
    let saved = null, preview = null, busy = false, generation = 0, activeRun = false, loaded = false;
    const fields = ['departureDate', 'departureTime', 'departureFold', 'departureGrace'];
    const values = () => ({departure_at: `${byId('departureDate').value}T${byId('departureTime').value}`,
      fold: byId('departureFold').value === '' ? null : Number(byId('departureFold').value),
      grace_minutes: Number(byId('departureGrace').value)});
    const fingerprint = () => JSON.stringify(values());
    let previewFields = '';
    let renderedPlan = '';
    function controls() {
      byId('departureSave').disabled = busy || !loaded || activeRun || !preview || previewFields !== fingerprint();
      byId('departurePreviewButton').disabled = busy || activeRun;
      byId('departureCancel').disabled = busy || !loaded || !saved || !['scheduled', 'waiting_for_device'].includes(saved.status);
      fields.forEach(id => { byId(id).disabled = busy || activeRun; });
      byId('departureTomorrow').disabled = busy || activeRun;
      byId('departureForm').title = activeRun ? 'Confirm Stop heating before changing the departure plan' : '';
      const lock = byId('departureLockReason');
      if (lock) lock.textContent = activeRun ? 'Stop heating and wait for confirmed OFF before changing the departure plan.' : '';
    }
    const upcoming = ['scheduled', 'waiting_for_device', 'running', 'stopping'];
    function renderDepartureHero(plan) {
      const time = byId('departureBigTime'), date = byId('departureBigDate');
      if (!time || !date) return;
      if (!plan || !upcoming.includes(plan.status)) {
        time.textContent = '—'; date.textContent = 'No upcoming departure'; return;
      }
      const departure = new Date(plan.departure_at);
      time.textContent = localParts(departure).time;
      date.textContent = departure.toLocaleDateString('en-GB', {timeZone: 'Europe/Helsinki',
        weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'});
    }
    function render(plan, populate = false) {
      saved = plan;
      const serialized = JSON.stringify(plan);
      if (!populate && serialized === renderedPlan) { controls(); return; }
      renderedPlan = serialized;
      renderDepartureHero(plan);
      if (!plan) { byId('departureSaved').textContent = 'No saved departure.'; }
      else {
        const labels = {scheduled: 'Scheduled', waiting_for_device: 'Waiting for device; original finish unchanged',
          running: 'Start requested', stopping: 'Stop pending', completed: 'Finished', cancelled: 'Cancelled',
          expired: 'Expired without a start', interrupted: 'Interrupted after server restart; ON will not resume'};
        timeline(byId('departureSaved'), plan, labels[plan.status] || plan.status);
        if (plan.late_start_seconds > 0) byId('departureSaved').append(node('p', `Recorded late start: ${Math.ceil(plan.late_start_seconds / 60)} min. Preheat was incomplete; finish unchanged.`));
        if (populate) {
          const local = localParts(new Date(plan.departure_at));
          byId('departureDate').value = local.date; byId('departureTime').value = local.time;
          byId('departureGrace').value = plan.grace_minutes;
          byId('departureFold').value = plan.fold == null ? '' : String(plan.fold);
        }
      }
      const link = byId('departureSessionLink'); link.hidden = !plan;
      if (plan) link.href = '/car_heater/sessions/' + encodeURIComponent(plan.session_id);
      controls();
    }
    function invalidate() {
      generation += 1; preview = null; byId('departurePreview').hidden = true;
      byId('departureFeedback').textContent = 'Unsaved changes. Preview before saving.'; controls();
    }
    fields.forEach(id => byId(id).addEventListener('input', invalidate));
    byId('departureTomorrow').addEventListener('click', () => {
      byId('departureDate').value = tomorrowDate(); invalidate();
    });
    byId('departureForm').addEventListener('submit', async event => {
      event.preventDefault(); if (busy || activeRun) return;
      const current = ++generation; busy = true; preview = null; controls();
      byId('departureFeedback').textContent = 'Loading preview…';
      try {
        const result = await request('POST', values(), '/preview');
        if (current !== generation) return;
        preview = result; previewFields = fingerprint(); timeline(byId('departurePreview'), result, 'Unsaved preview');
        byId('departureFeedback').textContent = 'Review this timeline, then Save departure.';
      } catch (error) { byId('departureFeedback').textContent = error.message; }
      finally { busy = false; controls(); }
    });
    async function mutate(method) {
      if (busy) return;
      busy = true; generation += 1; controls();
      byId('departureFeedback').textContent = method === 'POST' ? 'Saving departure…' : 'Cancelling upcoming departure…';
      try {
        const result = await request(method, method === 'POST' ? {...values(), expected_preview: preview} : undefined);
        preview = null; byId('departurePreview').hidden = true; render(result, true);
        byId('departureFeedback').textContent = method === 'POST' ? 'Departure saved. This is the persisted timeline.' : result?.status === 'stopping' ? 'Heating began before cancellation; Stop is pending. Waiting for relay confirmation.' : 'Upcoming departure cancelled. No heating command was sent.';
      } catch (error) {
        preview = null; loaded = false; byId('departurePreview').hidden = true;
        byId('departureFeedback').textContent = error.message + ' Reload to check the saved plan before retrying.';
      }
      finally { busy = false; controls(); }
    }
    byId('departureSave').addEventListener('click', () => {
      if (preview && previewFields === fingerprint() && !activeRun) mutate('POST');
    });
    byId('departureCancel').addEventListener('click', () => mutate('DELETE'));
    async function refresh(populate = false) {
      if (busy) return;
      const current = generation;
      try {
        const [plan, response] = await Promise.all([request(), fetch('/api/car_heater/bounded', {credentials: 'same-origin'})]);
        if (!response.ok) throw new Error('Heating status unavailable; relay state unknown.');
        const run = await response.json();
        if (current !== generation) return;
        activeRun = Boolean(run && run.phase !== 'stopped'); loaded = true; render(plan, populate);
      } catch (error) {
        loaded = false; byId('departureFeedback').textContent = error.message + ' Reload to check departure status.'; controls();
        if (!saved && byId('departureBigDate')) byId('departureBigDate').textContent = 'Departure plan unavailable';
      }
    }
    byId('departureDate').value = tomorrowDate();
    refresh(true); window.setInterval(() => refresh(), 5000);
  });
})();
