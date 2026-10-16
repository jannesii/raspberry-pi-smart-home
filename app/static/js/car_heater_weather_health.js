(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const FIELDS = {
    t2m: 'Temperature', rh: 'Humidity', td: 'Dew point', ws_10min: 'Wind speed',
    wd_10min: 'Wind direction', wg_10min: 'Gusts', n_man: 'Cloud cover',
    r_1h: 'Precipitation (1 h)', ri_10min: 'Precipitation intensity', wawa: 'Weather type',
    snow_aws: 'Snow depth', vis: 'Visibility',
    Temperature: 'Temperature', DewPoint: 'Dew point', Humidity: 'Humidity',
    WindSpeedMS: 'Wind speed', WindDirection: 'Wind direction', WindGust: 'Gusts',
    TotalCloudCover: 'Cloud cover', RadiationLW: 'Long-wave radiation',
    RadiationGlobal: 'Global radiation', Precipitation1h: 'Precipitation (1 h)',
    WeatherSymbol3: 'Weather symbol'
  };
  const format = value => value ? new Date(value).toLocaleString('en-GB', {
    timeZone: 'Europe/Helsinki', hour12: false, day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit'
  }) : '—';
  const fieldList = names => names && names.length ? names.map(n => FIELDS[n] || n).join(', ') : '—';
  function reason(attempt) {
    if (!attempt || !attempt.error) return 'unknown error';
    if (attempt.error === 'timeout') return 'FMI timed out';
    if (attempt.error === 'http_error') return `FMI returned HTTP ${attempt.http_status ?? '—'}`;
    if (attempt.error === 'malformed_reply') return 'reply could not be read';
    return 'connection failed';
  }
  function state(source) {
    const last = source.last_attempt;
    if (!last) return 'No collection yet';
    if (source.stale) return `Not collecting since ${format(last.retrieved_at)}`;
    if (last.status !== 'ok') return `Last collection failed: ${reason(last)}`;
    return 'Collecting';
  }
  function node(tag, text, cls) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  function row(list, label, value) {
    list.append(node('dt', label));
    const dd = node('dd', null);
    if (value instanceof Node) dd.append(value); else dd.textContent = value;
    list.append(dd);
  }
  function item(target, sentences, name, source, kind) {
    const el = node('li', null, 'weather-health-item');
    const text = state(source);
    const healthy = text === 'Collecting';
    el.append(node('strong', name), node('span', text, healthy ? 'weather-health-state' : 'weather-health-state weather-health-problem'));
    const details = node('dl', null);
    const success = source.last_success;
    const summary = (success && success.summary) || {};
    row(details, kind === 'forecast' ? 'Last check' : 'Last collection', format(success && success.retrieved_at));
    const failure = source.last_failure;
    row(details, 'Last failure', failure ? `${format(failure.retrieved_at)} · ${reason(failure)}` : '—');
    if (source.consecutive_failures) row(details, 'Failures since success', String(source.consecutive_failures));
    if (kind === 'forecast') {
      const version = (summary.versions || [])[0];
      row(details, 'Model run', version && version.analysis_time ? `${format(version.analysis_time)}${version.revision ? ` · revision ${version.revision}` : ''}` : '—');
      row(details, 'Hours received', summary.valid_start ? `${format(summary.valid_start)} – ${format(summary.valid_end)}` : '—');
      const gaps = source.unobserved_24h;
      row(details, 'Unchecked stretches (24 h)', gaps && gaps.count && gaps.latest ? `${gaps.count} · latest ${format(gaps.latest.start)} – ${format(gaps.latest.end)}` : '—');
    }
    row(details, 'Fields with values', fieldList(summary.fields_with_values));
    row(details, 'Received without values', fieldList(summary.fields_without_values));
    row(details, 'Not returned', fieldList(summary.fields_absent));
    if (source.archive_error) row(details, 'Archive write failed', `${format(source.archive_error.at)} · retrying next cycle`);
    if (success && success.reply_id) {
      const link = node('a', 'Download original reply');
      link.href = `/api/car_heater/weather/replies/${encodeURIComponent(success.reply_id)}`;
      link.setAttribute('download', '');
      row(details, 'Source evidence', link);
    }
    el.append(details);
    target.append(el);
    sentences.push(`${name}: ${text}.`);
  }
  function pointName(source) {
    const at = source.requested_location;
    if (!at || typeof at.lat !== 'number' || typeof at.lon !== 'number') return `Parking-point forecast (${source.source_id})`;
    return `Parking-point forecast (${at.lat.toFixed(4)} N, ${at.lon.toFixed(4)} E)`;
  }
  function render(data) {
    const target = byId('weatherHealthList');
    target.replaceChildren();
    const sentences = [];
    (data.sources || []).forEach(source => {
      item(target, sentences, `${source.station_name || 'Station'} (${source.source_id})`, source, 'observation');
    });
    const forecast = data.forecast || {};
    (forecast.sources || []).forEach(source => item(target, sentences, pointName(source), source, 'forecast'));
    if (forecast.collector && forecast.collector.configured === false) sentences.push('Forecast collection is not configured on this server.');
    else if (forecast.collector && !forecast.collector.running) sentences.unshift('The forecast collector is not running on this server.');
    if (data.collector && !data.collector.running) sentences.unshift('The collector is not running on this server.');
    byId('weatherHealthStatus').textContent = sentences.length ? sentences.join(' ') : 'No weather sources configured.';
  }
  async function load() {
    const button = byId('btnRefreshWeatherHealth');
    if (button) button.disabled = true;
    try {
      const response = await fetch('/api/car_heater/weather/health', {credentials: 'same-origin'});
      if (!response.ok) throw new Error();
      render(await response.json());
    } catch (_) {
      byId('weatherHealthStatus').textContent = 'Collection status could not be loaded. Check your sign-in and try again.';
    } finally {
      if (button) button.disabled = false;
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    const list = byId('weatherHealthList');
    // Only the Settings & diagnostics view shows collection health.
    if (!list || !byId('weatherHealthStatus') || list.closest('[hidden]')) return;
    byId('btnRefreshWeatherHealth')?.addEventListener('click', load);
    load();
  });
})();
