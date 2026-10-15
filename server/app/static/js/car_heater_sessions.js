/* Session evidence never calls the actuator or scheduling APIs. */
(() => {
  'use strict';
  const workspace = document.querySelector('main.heater-evidence');
  if (!workspace) return;
  const sessionId = workspace.dataset.sessionId;
  const base = '/api/car_heater/sessions';
  const feedback = document.getElementById('sessionFeedback');
  const retry = document.getElementById('sessionRetry');
  const outcomeLabels = {clear: 'Clear', wipers_enough: 'Wipers enough', partial_scrape: 'Partial scrape', full_scrape: 'Full scrape'};
  let loadGeneration = 0;
  let finishedShown = false;
  const timeZone = 'Europe/Helsinki';
  const formatTime = value => value ? new Date(value).toLocaleString('en-GB', {hour12: false, timeZone}) : '—';
  const shortTime = value => value ? new Date(value).toLocaleTimeString('en-GB', {hour12: false, timeZone, hour: '2-digit', minute: '2-digit'}) : '—';
  const localInput = value => {
    if (!value) return '';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(new Date(value)).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  };
  const isoInput = value => {
    if (!value) return null;
    const wallTime = Date.parse(value + 'Z');
    let candidate = wallTime;
    // Resolve the Helsinki offset at the instant, including DST transitions.
    for (let i = 0; i < 3; i++) {
      const offset = new Intl.DateTimeFormat('en-GB', {timeZone, timeZoneName: 'longOffset'})
        .formatToParts(new Date(candidate)).find(part => part.type === 'timeZoneName').value;
      const match = offset.match(/GMT([+-])(\d{2}):(\d{2})/);
      const minutes = match ? (Number(match[2]) * 60 + Number(match[3])) * (match[1] === '+' ? 1 : -1) : 0;
      candidate = wallTime - minutes * 60000;
    }
    const iso = new Date(candidate).toISOString();
    if (localInput(iso) !== value) throw new Error('This time does not exist in Europe/Helsinki. Choose another time.');
    return iso;
  };
  const element = (tag, value) => {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    return node;
  };
  async function api(url, method = 'GET', body) {
    const response = await fetch(url, {method, credentials: 'same-origin', headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
    }, ...(body ? {body: JSON.stringify(body)} : {})});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('Session request failed. Reload to check your sign-in.'); }
    if (!response.ok) throw new Error(data.error || 'Session request failed. Reload to check your sign-in.');
    return data;
  }
  function renderList(rows) {
    const list = document.getElementById('sessionList');
    list.replaceChildren();
    if (!rows.length) list.append(element('p', 'No sessions for this date. Record an attempt or choose Show all.'));
    rows.forEach(row => {
      const article = element('article');
      article.className = 'evidence-attempt';
      const link = element('a', formatTime(row.requested_departure_at || row.observed_at));
      link.href = '/car_heater/sessions/' + encodeURIComponent(row.id);
      article.append(link, element('p', outcomeLabels[row.outcome?.result] || 'Outcome not recorded'),
        element('p', `${row.trigger.replaceAll('_', ' ')} · Recorded heating duration: ${row.heating_duration_minutes == null ? '—' : row.heating_duration_minutes + ' min'}`),
        element('p', photoSummary(row.photos)));
      list.append(article);
    });
  }
  function photoSummary(photos) {
    if (!photos?.saved) return 'No photos saved';
    const range = photos.saved > 1 ? ` · ${shortTime(photos.first_captured_at)}–${shortTime(photos.last_captured_at)}` : ` · ${shortTime(photos.first_captured_at)}`;
    return `${photos.saved} photo${photos.saved === 1 ? '' : 's'} saved${range}`;
  }
  function renderPhotos(photos, observations) {
    const section = document.getElementById('sessionPhotos');
    if (!section) return;
    const grid = document.getElementById('sessionPhotoGrid');
    const windows = document.getElementById('sessionObservations');
    grid.replaceChildren();
    windows.replaceChildren();
    observations.forEach(run => {
      const saved = new Set(photos.filter(p => p.observation_started_at === run.started_at && p.sequence != null).map(p => p.sequence));
      const missed = new Map(run.missed.map(m => [m.sequence, m]));
      // A finished window also accounts for slots after the last saved photo.
      const lastDue = run.finished_at ? Math.floor((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 600000) : -1;
      const highest = Math.max(lastDue, ...saved, ...missed.keys());
      const item = element('li', run.finished_at
        ? `Observation ${shortTime(run.started_at)}–${shortTime(run.finished_at)} · ${Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 60000)} min · ${saved.size} scheduled photo${saved.size === 1 ? '' : 's'} saved`
        : `Observation started ${shortTime(run.started_at)} · finish not recorded · ${saved.size} scheduled photo${saved.size === 1 ? '' : 's'} saved`);
      const gaps = element('ul');
      for (let n = 0; n <= highest; n++) {
        if (saved.has(n)) continue;
        const due = shortTime(missed.get(n)?.due_at || new Date(Date.parse(run.started_at) + n * 600000).toISOString());
        gaps.append(element('li', missed.has(n)
          ? `${due} scheduled photo missed (${missed.get(n).reason.replaceAll('_', ' ')})`
          : `${due} scheduled photo not saved`));
      }
      if (gaps.children.length) item.append(gaps);
      windows.append(item);
    });
    if (!photos.length) {
      grid.append(element('p', 'No photos saved for this session. Unsaved or missed captures are not evidence.'));
      return;
    }
    photos.forEach(photo => {
      const figure = element('figure');
      figure.className = 'evidence-photo';
      const image = element('img');
      image.loading = 'lazy';
      image.decoding = 'async';
      image.src = `${base}/${encodeURIComponent(sessionId)}/photos/${encodeURIComponent(photo.id)}`;
      image.alt = `Windshield photo captured ${formatTime(photo.captured_at)}`;
      const link = element('a');
      link.href = image.src;
      link.append(image);
      const late = photo.due_at ? Math.round((Date.parse(photo.captured_at) - Date.parse(photo.due_at)) / 1000) : null;
      const caption = photo.capture_kind === 'manual'
        ? 'Manual capture'
        : `Scheduled ${shortTime(photo.due_at)}${late > 0 ? ` · ${late} s late` : ''}`;
      figure.append(link, element('figcaption', `Captured ${formatTime(photo.captured_at)} · ${caption}${photo.camera_label ? ' · ' + photo.camera_label : ''}`));
      grid.append(figure);
    });
  }
  function renderDetail(row, populate = true) {
    const detail = document.getElementById('sessionDetail');
    const heading = element('h2', outcomeLabels[row.outcome?.result] || 'Outcome not recorded');
    heading.id = 'sessionDetailHeading';
    detail.replaceChildren(heading);
    detail.append(element('p', row.outcome?.already_clear_at_observation ? 'Already clear when observation began; exact clearing time unknown.' : 'Exact clearing time not recorded.'));
    const values = {
      'Session identity': row.id, 'Trigger': row.trigger.replaceAll('_', ' '),
      'Observation began': formatTime(row.observed_at), 'Record created': formatTime(row.created_at),
      'Requested departure': formatTime(row.requested_departure_at),
      'Confirmed departure': formatTime(row.outcome?.actual_departure_at),
      'Recorded heating duration': row.heating_duration_minutes == null ? '—' : row.heating_duration_minutes + ' min',
      'Outcome observation': formatTime(row.outcome?.observed_at),
      'Frost or ice type': row.outcome?.frost_type, 'Other windows': row.outcome?.other_windows,
      'Interior fog or frost': row.outcome?.interior_condition,
      'Installation': row.snapshot.installation, 'Firmware': row.snapshot.firmware_version,
      'Firmware source': row.snapshot.firmware_source?.replaceAll('_', ' '),
      'Firmware report time': formatTime(row.snapshot.firmware_reported_at),
      'App version': row.snapshot.software_version, 'App revision': row.snapshot.software_revision,
    };
    const dl = element('dl');
    Object.entries(values).forEach(([key, value]) => dl.append(element('dt', key), element('dd', value ?? '—')));
    detail.append(dl, element('p', 'Recorded times and duration are driver evidence.'));
    renderPhotos(row.photos || [], row.observations || []);
    if (row.departure_plan) {
      const plan = row.departure_plan;
      detail.append(element('h3', 'Departure plan'), element('p', `${plan.status.replaceAll('_', ' ')} · ${plan.preheat_minutes} min preheat · ${plan.grace_minutes} min grace`));
      const times = element('dl');
      [['Planned start', plan.planned_start_at], ['Departure', plan.departure_at], ['Latest finish', plan.finish_at], ['Actual start requested', plan.actual_start_requested_at]].forEach(([label, value]) => {
        times.append(element('dt', label), element('dd', formatTime(value)));
      });
      detail.append(times);
      if (plan.late_start_seconds > 0) detail.append(element('p', `Late start: ${Math.ceil(plan.late_start_seconds / 60)} minutes. Original finish unchanged.`));
      const revisions = element('ol');
      plan.events.forEach(event => revisions.append(element('li', `${formatTime(event.at)} · ${event.kind.replaceAll('_', ' ')} · ${event.reason}`)));
      detail.append(revisions);
      Object.entries(plan.weather_snapshots).forEach(([stage, weather]) => {
        detail.append(element('h4', `${stage[0].toUpperCase() + stage.slice(1)} weather`),
          element('p', `${weather.source} · ${weather.station_name || 'Station unknown'} · ${weather.fmisid || '—'} · ${weather.temperature_c == null ? '—' : weather.temperature_c + ' °C'} · observed ${formatTime(weather.observed_at)} · captured ${formatTime(weather.captured_at)} · ${weather.fallback_reason || 'Fresh observation'}`));
      });
      if (plan.supersedes) {
        const previous = element('a', 'Review previous departure revision');
        previous.href = '/car_heater/sessions/' + encodeURIComponent(plan.supersedes); detail.append(previous);
      }
    }
    if (row.execution) {
      detail.append(element('h3', 'Heating execution'), element('p', `Latest finish: ${formatTime(new Date(row.execution.finish_at * 1000).toISOString())}`));
      const milestones = element('ol');
      row.execution.milestones.forEach(item => milestones.append(element('li',
        `${formatTime(new Date(item.at * 1000).toISOString())} · ${item.kind.replaceAll('_', ' ')} · ${item.reason || '—'}${item.power_w == null ? '' : ' · ' + item.power_w + ' W'}`)));
      detail.append(milestones);
    }
    const form = document.getElementById('sessionOutcome');
    if (populate) {
      form.reset();
      const outcome = row.outcome || {};
      Object.entries(outcome).forEach(([key, value]) => {
        const input = form.elements.namedItem(key);
        if (!input) return;
        if (key === 'result') input.value = value;
        else if (input.type === 'checkbox') input.checked = value;
        else input.value = input.type === 'datetime-local' ? localInput(value) : value || '';
      });
    }
    form.hidden = false;
  }
  async function load() {
    const generation = ++loadGeneration;
    const target = document.getElementById(sessionId ? 'sessionDetail' : 'sessionList');
    target.setAttribute('aria-busy', 'true');
    feedback.textContent = 'Loading sessions…';
    retry.hidden = true;
    try {
      const date = document.getElementById('sessionDate')?.value;
      const data = await api(sessionId ? `${base}/${encodeURIComponent(sessionId)}` : base + (date ? '?date=' + encodeURIComponent(date) : ''));
      if (generation !== loadGeneration) return;
      if (sessionId) renderDetail(data); else renderList(data);
      feedback.textContent = '';
      if (sessionId && new URLSearchParams(location.search).get('observation') === 'finished' && !finishedShown) {
        finishedShown = true;
        feedback.textContent = 'Observation finished. Record the departure outcome; heating is unchanged.';
        document.getElementById('outcomeHeading').focus();
      }
    } catch (error) {
      if (generation !== loadGeneration) return;
      feedback.textContent = error.message;
      target.replaceChildren(element('p', 'Session data could not be loaded.'));
      retry.hidden = false;
    } finally {
      if (generation === loadGeneration) target.setAttribute('aria-busy', 'false');
    }
  }
  retry.addEventListener('click', load);
  const filter = document.getElementById('sessionFilter');
  if (filter) {
    filter.elements.date.value = new URLSearchParams(location.search).get('date') || '';
    const apply = () => {
      const date = filter.elements.date.value;
      history.replaceState(null, '', location.pathname + (date ? '?date=' + encodeURIComponent(date) : ''));
      load();
    };
    filter.addEventListener('submit', event => { event.preventDefault(); apply(); });
    document.getElementById('sessionResetFilter').addEventListener('click', () => { filter.reset(); apply(); });
  }
  const create = document.getElementById('sessionCreate');
  if (create) create.elements.observed_at.value = localInput(new Date().toISOString());
  [create, document.getElementById('sessionOutcome')].filter(Boolean).forEach(form => {
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      const status = form.querySelector('.evidence-form-status');
      button.disabled = true;
      status.textContent = 'Saving…';
      try {
        const data = Object.fromEntries(new FormData(form));
        ['observed_at', 'requested_departure_at', 'actual_departure_at'].forEach(key => {
          if (key in data) data[key] = isoInput(data[key]);
        });
        if (form === create) data.heating_duration_minutes = data.heating_duration_minutes === '' ? null : Number(data.heating_duration_minutes);
        else data.already_clear_at_observation = form.elements.already_clear_at_observation.checked;
        const row = await api(form === create ? base : `${base}/${encodeURIComponent(sessionId)}/outcome`, form === create ? 'POST' : 'PUT', data);
        if (form === create) location.assign('/car_heater/sessions/' + encodeURIComponent(row.id));
        else { renderDetail(row, false); status.textContent = 'Outcome saved. Heating unchanged.'; }
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    });
  });
  load();
})();
