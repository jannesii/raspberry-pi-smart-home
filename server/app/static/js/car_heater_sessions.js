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
  const minutes = value => value == null ? '—' : `${value} min`;
  const label = value => value ? value.replaceAll('_', ' ') : '—';
  let lastDetail = null;
  let measurementGeneration = 0;
  const clearingText = clearing => {
    if (!clearing) return '';
    if (clearing.interpretation === 'upper_bound') {
      const basis = {first_photo: 'first photo', observation_start: 'start of observation', outcome_observation: 'outcome observation'}[clearing.basis];
      return clearing.clear_no_later_than
        ? `Already clear at the ${basis} (${formatTime(clearing.clear_no_later_than)}): clearing happened at or before this time; the exact clearing time is unknown. This shortened observation is not a fully observed clearing trajectory.`
        : 'Already clear when observation began; no observation time was recorded, so clearing time is unknown.';
    }
    if (clearing.interpretation === 'outcome_not_recorded') return 'Outcome not recorded; no clearing time is inferred.';
    return 'Exact clearing time not recorded. Observation windows are not fully observed clearing trajectories.';
  };
  function renderHead(row, clearing) {
    const head = document.getElementById('sessionHead');
    const heading = element('h2', outcomeLabels[row.outcome?.result] || 'Outcome not recorded');
    heading.id = 'sessionDetailHeading';
    const when = row.requested_departure_at ? `Departure · ${formatTime(row.requested_departure_at)}` : `${label(row.trigger)} · ${formatTime(row.observed_at)}`;
    const eyebrow = element('p', when);
    eyebrow.className = 'evidence-eyebrow';
    const fallback = row.outcome?.already_clear_at_observation
      ? 'Already clear when observation began; exact clearing time unknown.'
      : row.outcome ? 'Exact clearing time not recorded.' : 'Outcome not recorded; no clearing time is inferred.';
    const interpretation = element('p', clearing ? clearingText(clearing) : fallback);
    interpretation.id = 'sessionClearing';
    head.replaceChildren(eyebrow, heading, interpretation);
  }
  function renderTimeline(row) {
    const body = document.getElementById('sessionTimelineBody');
    body.replaceChildren();
    const plan = row.departure_plan;
    if (plan) {
      body.append(element('p', `Planned: start ${formatTime(plan.planned_start_at)} → departure ${formatTime(plan.departure_at)} → latest finish ${formatTime(plan.finish_at)}`));
      body.append(element('h4', 'Departure plan'), element('p', `${label(plan.status)} · ${plan.preheat_minutes} min preheat · ${plan.grace_minutes} min grace`));
      const times = element('dl');
      [['Planned start', plan.planned_start_at], ['Departure', plan.departure_at], ['Latest finish', plan.finish_at], ['Actual start requested', plan.actual_start_requested_at]].forEach(([name, value]) => {
        times.append(element('dt', name), element('dd', formatTime(value)));
      });
      body.append(times);
      if (plan.late_start_seconds > 0) body.append(element('p', `Late start: ${Math.ceil(plan.late_start_seconds / 60)} minutes. Original finish unchanged.`));
      const revisions = element('ol');
      plan.events.forEach(event => revisions.append(element('li', `${formatTime(event.at)} · ${label(event.kind)} · ${event.reason}`)));
      body.append(revisions);
      Object.entries(plan.weather_snapshots).forEach(([stage, weather]) => {
        body.append(element('h5', `${stage[0].toUpperCase() + stage.slice(1)} weather`),
          element('p', `${weather.source} · ${weather.station_name || 'Station unknown'} · ${weather.fmisid || '—'} · ${weather.temperature_c == null ? '—' : weather.temperature_c + ' °C'} · observed ${formatTime(weather.observed_at)} · captured ${formatTime(weather.captured_at)} · ${weather.fallback_reason || 'Fresh observation'}`));
      });
      if (plan.supersedes) {
        const previous = element('a', 'Review previous departure revision');
        previous.href = '/car_heater/sessions/' + encodeURIComponent(plan.supersedes); body.append(previous);
      }
    }
    if (row.execution) {
      body.append(element('h4', 'Heating execution'), element('p', `Latest finish: ${formatTime(new Date(row.execution.finish_at * 1000).toISOString())}`));
      const milestones = element('ol');
      row.execution.milestones.forEach(item => milestones.append(element('li',
        `${formatTime(new Date(item.at * 1000).toISOString())} · ${label(item.kind)} · ${item.reason || '—'}${item.power_w == null ? '' : ' · ' + item.power_w + ' W'}`)));
      body.append(milestones);
    }
    if (!plan && !row.execution) {
      body.append(element('p', `${label(row.trigger)} · began ${formatTime(row.observed_at)} · recorded heating duration ${minutes(row.heating_duration_minutes)}`),
        element('p', 'No device execution was recorded for this session; these times are driver evidence, not confirmed heating.'));
    }
  }
  function renderFacts(row, configuration) {
    const values = {
      'Session identity': row.id, 'Trigger': label(row.trigger),
      'Observation began': formatTime(row.observed_at), 'Record created': formatTime(row.created_at),
      'Requested departure': formatTime(row.requested_departure_at),
      'Confirmed departure': formatTime(row.outcome?.actual_departure_at),
      'Recorded heating duration': minutes(row.heating_duration_minutes),
      'Outcome observation': formatTime(row.outcome?.observed_at),
      'Frost or ice type': row.outcome?.frost_type, 'Other windows': row.outcome?.other_windows,
      'Interior fog or frost': row.outcome?.interior_condition,
      'Installation': row.snapshot.installation, 'Firmware': row.snapshot.firmware_version,
      'Firmware source': row.snapshot.firmware_source?.replaceAll('_', ' '),
      'Firmware report time': formatTime(row.snapshot.firmware_reported_at),
      'App version': row.snapshot.software_version, 'App revision': row.snapshot.software_revision,
    };
    if (configuration) {
      values['Firmware reported during review'] = configuration.reported_firmware_versions.join(', ') || null;
      values['Execution firmware'] = configuration.execution?.firmware_version;
      values['Execution installation'] = configuration.execution?.installation;
    }
    const dl = element('dl');
    Object.entries(values).forEach(([key, value]) => dl.append(element('dt', key), element('dd', value ?? '—')));
    document.getElementById('sessionFactsBody').replaceChildren(dl, element('p', 'Recorded times and duration are driver evidence.'));
  }
  function renderDetail(row, populate = true) {
    lastDetail = row;
    renderHead(row);
    renderTimeline(row);
    renderFacts(row);
    renderPhotos(row.photos || [], row.observations || []);
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
  const indicatorStatus = {
    reached: result => `Reference glass held at or above +${result.threshold_c} °C for ${result.dwell_s / 60} minutes of valid readings at ${formatTime(result.first_reached_at)} (candidate time).`,
    not_reached: result => `Reference glass did not hold at or above +${result.threshold_c} °C for ${result.dwell_s / 60} minutes of valid readings.`,
    no_valid_evidence: () => 'No valid reference-glass readings in this window, so the indicator cannot be evaluated.',
  };
  const endReasons = {
    fell_below: 'fell below the threshold', gap: 'readings stopped for longer than the freshness limit',
    missing: 'sensor reported missing', invalid: 'invalid reading', cached: 'sensor read failed (repeated value)',
    stale: 'late reading', window_end: 'still at or above the threshold at the end of the window',
  };
  function renderIndicator(result) {
    const body = document.getElementById('glassIndicatorBody');
    const status = element('p', indicatorStatus[result.status](result));
    status.className = 'review-indicator-status';
    body.replaceChildren(status, element('p', `Missing, late, invalid or repeated readings and gaps over ${result.max_gap_s} s restart the ${result.dwell_s / 60}-minute dwell (definition ${result.definition_version}).`));
    if (result.runs.length) {
      const runs = element('ul');
      result.runs.forEach(run => runs.append(element('li',
        `${shortTime(run.started_at)}–${shortTime(run.ended_at || run.last_valid_at)} at or above +${result.threshold_c} °C${run.reached_at ? ` · ten minutes held at ${shortTime(run.reached_at)}` : ''} · ended: ${endReasons[run.end_reason] || label(run.end_reason)}`)));
      body.append(runs);
    }
  }
  function renderMeasurements(data) {
    const ms = value => Date.parse(value);
    const start = ms(data.window.start);
    const end = ms(data.window.end);
    const gapMs = data.gap_after_s * 1000;
    const anchorLabel = {execution: 'heating start request', departure_plan: 'planned heating start', session: 'recorded session start'}[data.window.anchor_source] || 'session start';
    document.getElementById('measurementWindow').textContent =
      `Showing ${formatTime(data.window.start)}–${shortTime(data.window.end)}, around the ${anchorLabel} and ${data.window.end_recorded ? 'the session\'s last recorded time' : 'the heating limit (no finish recorded)'}. Readings outside this window remain in history. Missing values are shown as gaps, never as zero; hollow markers are late readings.`;
    const series = data.series || {};
    const points = (rows, key) => (rows || []).map(p => ({x: ms(p.t), y: p[key], late: p.state === 'stale'}));
    const markers = [{x: ms(data.window.heating_anchor), label: data.window.anchor_source === 'departure_plan' ? 'Planned start' : 'Start'}];
    const row = lastDetail || {};
    if (row.requested_departure_at) markers.push({x: ms(row.requested_departure_at), label: 'Departure'});
    const finish = row.execution ? row.execution.finish_at * 1000 : row.departure_plan ? ms(row.departure_plan.finish_at) : null;
    if (finish) markers.push({x: finish, label: 'Latest finish'});
    const timeMarkers = markers.slice();
    const indicator = data.glass_indicator;
    if (indicator?.first_reached_at) markers.push({x: ms(indicator.first_reached_at), label: 'Indicator (experimental)'});
    if (data.clearing?.clear_no_later_than) markers.push({x: ms(data.clearing.clear_no_later_than), label: 'Clear by'});
    const charts = window.CarHeaterSessionCharts;
    if (!charts) return;
    charts.render(document.getElementById('temperatureChart'), {start, end, gapMs, markers, series: [
      {label: 'Glass reference', color: '#64d2ff', points: points(series.glass_reference, 'temperature_c')},
      {label: 'Glass comparison', color: '#d0a2ff', dash: '7 4', points: points(series.glass_comparison, 'temperature_c')},
      {label: 'Cabin temperature', color: '#6ee7a0', dash: '2 4', points: points(series.cabin, 'temperature_c')},
    ], thresholds: indicator ? [{y: indicator.threshold_c, label: `Experimental +${indicator.threshold_c} °C candidate threshold`}] : [],
    emptyText: 'No glass or cabin temperature measurements available for this session. Missing data is not shown as zero.'});
    charts.render(document.getElementById('humidityChart'), {start, end, gapMs, markers: timeMarkers, series: [
      {label: 'Cabin humidity', color: '#f0c674', points: points(series.cabin, 'humidity_pct')},
    ]});
    charts.render(document.getElementById('powerChart'), {start, end, gapMs, markers: timeMarkers, zero: true, series: [
      {label: 'Heater power', color: '#ff9f5a', points: points(series.power, 'power_w')},
    ], emptyText: 'No heater power measurements available for this session (Shelly data missing). Unknown power is not shown as 0 W.'});
    const energy = data.energy?.used_wh == null ? '—' : `${data.energy.used_wh} Wh`;
    const relay = series.power || [];
    const on = relay.filter(p => p.relay_on === true);
    const relayText = relay.length
      ? `Shelly relay reported ON in ${on.length} and OFF in ${relay.length - on.length} of ${relay.length} reports${on.length ? `, first ON ${shortTime(on[0].t)}, last ON ${shortTime(on[on.length - 1].t)}` : ''}. Between reports the relay state is unknown.`
      : 'Relay state unknown: no Shelly reports in this window.';
    document.getElementById('powerChart').append(element('p', relayText), element('p', `Energy from the Shelly counter in this window: ${energy}.`));
    if (indicator) renderIndicator(indicator);
    renderHead(row, data.clearing);
    renderFacts(row, data.configuration);
  }
  async function loadMeasurements() {
    const generation = ++measurementGeneration;
    const section = document.getElementById('sessionMeasurements');
    const retryButton = document.getElementById('measurementRetry');
    section.setAttribute('aria-busy', 'true');
    retryButton.hidden = true;
    try {
      const data = await api(`${base}/${encodeURIComponent(sessionId)}/measurements`);
      if (generation !== measurementGeneration) return;
      renderMeasurements(data);
    } catch (error) {
      if (generation !== measurementGeneration) return;
      document.getElementById('measurementWindow').textContent = `Measurements could not be loaded: ${error.message}`;
      retryButton.hidden = false;
    } finally {
      if (generation === measurementGeneration) section.setAttribute('aria-busy', 'false');
    }
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
      if (sessionId) { renderDetail(data); loadMeasurements(); } else renderList(data);
      feedback.textContent = '';
      if (sessionId && new URLSearchParams(location.search).get('observation') === 'finished' && !finishedShown) {
        finishedShown = true;
        feedback.textContent = 'Observation finished. Record the departure outcome; heating is unchanged.';
        document.getElementById('outcomeHeading').focus();
      }
    } catch (error) {
      if (generation !== loadGeneration) return;
      feedback.textContent = error.message;
      (sessionId ? document.getElementById('sessionHead') : target).replaceChildren(element('p', 'Session data could not be loaded.'));
      retry.hidden = false;
    } finally {
      if (generation === loadGeneration) target.setAttribute('aria-busy', 'false');
    }
  }
  retry.addEventListener('click', load);
  document.getElementById('measurementRetry')?.addEventListener('click', loadMeasurements);
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
        else {
          renderDetail({...lastDetail, ...row}, false);
          loadMeasurements();
          status.textContent = 'Outcome saved. Heating unchanged.';
        }
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    });
  });
  load();
})();
