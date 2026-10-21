/* Read-only weather evidence for one session; never calls heater or scheduling APIs. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const workspace = document.querySelector('main.heater-evidence');
  const section = byId('sessionWeather');
  const parts = ['sessionWeatherWindow', 'sessionWeatherStatus', 'sessionWeatherRetry', 'sessionWeatherSources', 'sessionWeatherExports'];
  if (!workspace?.dataset.sessionId || !section || parts.some(id => !byId(id))) return;
  const sessionId = workspace.dataset.sessionId;
  const timeZone = 'Europe/Helsinki';
  const when = value => value ? new Date(value).toLocaleString('en-GB', {
    timeZone, hour12: false, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
  }) : '—';
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const ERRORS = {timeout: 'FMI timed out', malformed_reply: 'the reply could not be read'};
  const failure = health => health.last_error === 'http_error'
    ? `FMI returned HTTP ${health.last_http_status ?? '—'}`
    : ERRORS[health.last_error] || 'connection failed';
  // Forecast checks are not repaired: missed runs stay unchecked stretches.
  function stateText(health, days) {
    const repair = days ? ` Gaps up to ${days} days old are repaired automatically.` : '';
    if (!health || health.state === 'not_started') return 'No collection yet.';
    if (health.state === 'stale') return `Not collecting since ${when(health.last_attempt_at)}. Check that the server is running.${repair}`;
    if (health.state === 'failing') return `Last collection failed ${when(health.last_failure_at)}: ${failure(health)}. It is retried every cycle.${repair}`;
    return 'Collecting.';
  }
  function gapText(kind, days) {
    return {
      not_collected: 'not collected yet; repair pending',
      failed: 'collection failed; repair pending',
      source_empty: 'FMI answered without values (a source limitation, not a failure)',
      unrepaired: `older than the ${days}-day repair limit; will not be recovered`,
      before_history: 'before the archive history starts'
    }[kind] || kind.replaceAll('_', ' ');
  }
  function replyLink(replyId, text) {
    const link = node('a', text);
    link.href = `/api/car_heater/weather/replies/${encodeURIComponent(replyId)}`;
    link.setAttribute('download', '');
    return link;
  }
  // One source card: name, collection state and a details list filled by the caller.
  function sourceCard(name, health, state) {
    const item = node('li', null, 'weather-health-item');
    const healthy = health?.state === 'collecting';
    const details = node('dl');
    item.append(node('strong', name), node('span', state, healthy ? 'weather-health-state' : 'weather-health-state weather-health-problem'), details);
    const row = (label, value) => {
      const dd = node('dd');
      if (value instanceof Node) dd.append(value); else dd.textContent = value;
      details.append(node('dt', label), dd);
    };
    return {item, row};
  }
  function stationItem(station, days, sentences) {
    const name = `${station.station_name || 'Station'} (${station.source_id})`;
    const state = stateText(station.health, days);
    const {item, row} = sourceCard(name, station.health, state);
    const gaps = station.segments.filter(s => s.kind !== 'collected');
    const counts = station.readings;
    row('Readings in window', counts.current
      ? `${counts.current} · ${counts.revised} corrected by FMI · ${counts.backfilled} recovered later, with their real retrieval time`
      : 'None');
    if (gaps.length) {
      const list = node('ul', null, 'weather-gap-list');
      gaps.forEach(gap => list.append(node('li', `${when(gap.start)} – ${when(gap.end)}: ${gapText(gap.kind, days)}`)));
      row('Gaps', list);
    } else {
      row('Gaps', station.segments.length ? 'None: every source time was answered' : 'Nothing due yet');
    }
    if (station.replies.length) {
      const more = node('details');
      more.append(node('summary', `Original FMI replies (${station.replies.length})`));
      const list = node('ul', null, 'weather-reply-list');
      station.replies.forEach(reply => {
        const li = node('li');
        li.append(replyLink(reply.reply_id, `Retrieved ${when(reply.retrieved_at)} · ${reply.purpose}`));
        list.append(li);
      });
      more.append(list);
      row('Source evidence', more);
    }
    const gapSentence = gaps.length ? `${plural(gaps.length, 'gap')} in the window` : 'no gaps in the window';
    sentences.push(`${name}: ${state} ${counts.current ? plural(counts.current, 'reading') : 'No readings'}, ${gapSentence}.`);
    return item;
  }
  function versionText(version) {
    return `Model run ${when(version.analysis_time)} · revision ${version.revision}${version.current ? '' : ' (revised later)'} · first received ${when(version.first_retrieved_at)}`;
  }
  function forecastItem(forecast, sentences) {
    const at = forecast.requested_location;
    const name = typeof at?.lat === 'number' && typeof at?.lon === 'number'
      ? `Parking-point forecast (${at.lat.toFixed(4)} N, ${at.lon.toFixed(4)} E)`
      : `Parking-point forecast (${forecast.source_id})`;
    const state = stateText(forecast.health, 0);
    const {item, row} = sourceCard(name, forecast.health, state);
    const held = forecast.held_at_anchor;
    row('Held when heating began', held.length ? held.map(versionText).join('; ') : 'No forecast had been received');
    if (forecast.received_in_window.length) {
      const list = node('ul', null, 'weather-reply-list');
      forecast.received_in_window.forEach(version => {
        const li = node('li', versionText(version) + ' · ');
        const last = version.receipts[version.receipts.length - 1];
        if (last?.reply_id) li.append(replyLink(last.reply_id, 'Original reply'));
        list.append(li);
      });
      row('Received in window', list);
    } else {
      row('Received in window', 'None');
    }
    row('Unchecked stretches', forecast.unobserved.length
      ? forecast.unobserved.map(g => `${when(g.start)} – ${when(g.end)}${g.failed_attempts ? ` (${plural(g.failed_attempts, 'failed check')})` : ''}`).join('; ')
      : 'None');
    item.append(node('p', 'Forecasts are evidence only; they never control heating.'));
    sentences.push(`${name}: ${state}`);
    return item;
  }
  function exportLinks(exports) {
    const wrap = byId('sessionWeatherExports');
    const list = node('ul', null, 'weather-export-links');
    [['observations', 'Station observations'], ['forecasts', 'Forecast values'], ['coverage', 'Coverage and gaps']].forEach(([key, label]) => {
      ['csv', 'json'].forEach(fmt => {
        const li = node('li');
        const link = node('a', `${label} (${fmt.toUpperCase()})`);
        link.href = exports[key][fmt];
        link.setAttribute('download', '');
        li.append(link);
        list.append(li);
      });
    });
    wrap.replaceChildren(node('h4', 'Export this window'), list,
      node('p', 'Exports keep every revision, received missing value and backfill label; coverage lists the stretches without evidence. Other dates: Settings → Weather collection.', 'review-note'));
  }
  function render(data) {
    const days = Math.round((data.collector.repair_limit_s || 0) / 86400);
    const pending = Date.parse(data.checked_until) < Date.parse(data.window.end)
      ? ` Source times after ${when(data.checked_until)} are not due yet.` : '';
    byId('sessionWeatherWindow').textContent = `Weather archived ${when(data.window.start)} – ${when(data.window.end)} (Helsinki), the same window as Measurements. Weather is collected continuously and belongs to no session; the saved planning, start and departure weather above is unchanged.${pending}`;
    const list = byId('sessionWeatherSources');
    list.replaceChildren();
    const sentences = [];
    if (!data.collector.configured) sentences.push('Station collection is not configured on this server.');
    else if (!data.collector.running) sentences.push('The station collector is not running on this server.');
    data.stations.forEach(station => list.append(stationItem(station, days, sentences)));
    if (data.forecast.configured) list.append(forecastItem(data.forecast, sentences));
    else sentences.push('Forecast collection is not configured on this server.');
    byId('sessionWeatherStatus').textContent = sentences.join(' ');
    exportLinks(data.exports);
  }
  async function load() {
    const retry = byId('sessionWeatherRetry');
    retry.hidden = true;
    section.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(`/api/car_heater/sessions/${encodeURIComponent(sessionId)}/weather`, {credentials: 'same-origin'});
      if (!response.ok) throw new Error();
      render(await response.json());
    } catch (_) {
      byId('sessionWeatherWindow').textContent = 'Weather evidence could not be loaded. Check your sign-in and retry.';
      retry.hidden = false;
    } finally {
      section.setAttribute('aria-busy', 'false');
    }
  }
  byId('sessionWeatherRetry').addEventListener('click', load);
  load();
})();
