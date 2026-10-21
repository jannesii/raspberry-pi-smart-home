/* Permanent Diagnostics history. Uses the same evidence renderer as Sessions. */
(() => {
  'use strict';
  const SERIES = [
    {key: 'glass_reference_temp_c', label: 'Glass reference', sensor: 'glass_reference', field: 'temperature_c', color: '#e9b378'},
    {key: 'glass_comparison_temp_c', label: 'Glass comparison', sensor: 'glass_comparison', field: 'temperature_c', color: '#a0bddb', dash: '7 4'},
    {key: 'cabin_temp_c', label: 'Cabin SHT45', sensor: 'cabin', field: 'temperature_c', color: '#a3ccba', dash: '2 4'},
    {key: 'ambient_temp', label: 'Ambient', color: '#e4cb95', dash: '9 3 2 3'},
    {key: 'device_temp_c', label: 'Device', color: '#d9accc', dash: '10 5'},
    {key: 'cabin_humidity_pct', label: 'Cabin humidity', sensor: 'cabin', field: 'humidity_pct', color: '#a0bddb'},
    {key: 'instant_power_w', label: 'Heater power', color: '#e9b378'},
    {key: 'voltage_v', label: 'Voltage', color: '#a3ccba'},
    {key: 'current_a', label: 'Current', color: '#d9accc'},
  ];
  const GROUPS = [
    {title: 'Glass, cabin and device temperatures', unit: '°C', keys: SERIES.slice(0, 5).map(s => s.key)},
    {title: 'Cabin humidity', unit: '%', keys: ['cabin_humidity_pct'], zero: true},
    {title: 'Heater power', unit: 'W', keys: ['instant_power_w'], zero: true},
    {title: 'Voltage', unit: 'V', keys: ['voltage_v']},
    {title: 'Current', unit: 'A', keys: ['current_a'], zero: true},
  ];
  const STORAGE = 'car_heater_chart_visibility';
  const today = () => new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Helsinki', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
  const finite = v => typeof v === 'number' && Number.isFinite(v) ? v : null;
  const stamp = value => {
    if (!value) return NaN;
    return Date.parse(value.replace(/([+-]\d{2})$/, '$1:00').replace(' ', 'T'));
  };

  function init() {
    const modal = document.getElementById('carHeaterChartModal');
    const input = document.getElementById('carHeaterChartDate');
    const state = document.getElementById('carHeaterChartState');
    const container = document.getElementById('carHeaterDetailedCharts');
    const toggles = document.getElementById('carHeaterChartToggles');
    if (!modal || !input || !state || !container || !toggles) return;
    let visibility = {};
    try { visibility = JSON.parse(localStorage.getItem(STORAGE)) || {}; } catch { /* optional preference */ }
    let data = null;
    let request = null;
    let trigger = null;
    input.value = today();

    function render() {
      container.replaceChildren();
      if (!data) return;
      const times = [...(data.rows || []).map(row => stamp(row.timestamp)), ...Object.values(data.sensor_series || {}).flatMap(rows => rows.map(row => stamp(row.t)))].filter(Number.isFinite);
      const first = times.reduce((value, t) => Math.min(value, t), Infinity);
      const last = times.reduce((value, t) => Math.max(value, t), -Infinity);
      const start = times.length ? Math.max(stamp(data.start), first - 30000) : stamp(data.start);
      const end = times.length ? Math.min(stamp(data.end), last + 30000) : stamp(data.end);
      GROUPS.forEach(group => {
        const series = SERIES.filter(s => group.keys.includes(s.key) && visibility[s.key] !== false).map(s => {
          const rows = s.sensor ? data.sensor_series?.[s.sensor] || [] : data.rows || [];
          const points = rows.map(row => ({
            x: stamp(s.sensor ? row.t : row.timestamp),
            y: finite(row[s.sensor ? s.field : s.key]),
            late: row.state === 'stale',
          })).filter(p => Number.isFinite(p.x)).sort((a, b) => a.x - b.x);
          return {...s, points};
        });
        const panel = document.createElement('section');
        panel.className = 'review-chart';
        panel.dataset.title = group.title;
        panel.dataset.unit = group.unit;
        container.append(panel);
        window.CarHeaterSessionCharts.render(panel, {
          series, start, end,
          gapMs: (data.max_gap_seconds || 60) * 1000, zero: group.zero,
          emptyText: series.length ? `No ${group.title.toLowerCase()} measurements for this date. Missing data is not zero.` : 'All series in this plot are hidden. Use Series visibility to show them.',
        });
      });
    }

    SERIES.forEach(s => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = s.label;
      button.setAttribute('aria-pressed', String(visibility[s.key] !== false));
      button.addEventListener('click', () => {
        visibility[s.key] = visibility[s.key] === false;
        button.setAttribute('aria-pressed', String(visibility[s.key]));
        try { localStorage.setItem(STORAGE, JSON.stringify(visibility)); } catch { /* optional preference */ }
        render();
      });
      toggles.append(button);
    });

    async function load() {
      if (!input.reportValidity()) return;
      request?.abort();
      const active = new AbortController();
      request = active;
      data = null;
      container.replaceChildren();
      state.textContent = `Loading ${input.value}…`;
      const energy = document.getElementById('carHeaterChartEnergyTotal');
      energy.textContent = 'Recorded energy change: — Wh';
      try {
        const response = await fetch(`/api/car_heater/history?date=${encodeURIComponent(input.value)}`, {cache: 'no-store', signal: active.signal});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        if (request !== active) return;
        data = result;
        render();
        state.textContent = `Loaded ${input.value} · Helsinki time. Empty plots mean no available readings.`;
        energy.textContent = `Recorded energy change: ${finite(data.energy_today_wh) == null ? '—' : data.energy_today_wh.toFixed(1)} Wh`;
      } catch (error) {
        if (error.name === 'AbortError' || request !== active) return;
        state.textContent = 'Measurements unavailable. Choose Load date to retry.';
        console.warn('Diagnostics history unavailable:', error.message);
      }
    }

    document.getElementById('btnOpenCarHeaterCharts')?.addEventListener('click', event => {
      trigger = event.currentTarget;
      modal.showModal();
      document.getElementById('carHeaterChartClose').focus();
      load();
    });
    document.getElementById('carHeaterChartClose')?.addEventListener('click', () => modal.close());
    modal.addEventListener('close', () => { request?.abort(); trigger?.focus(); });
    document.getElementById('carHeaterChartDateForm')?.addEventListener('submit', event => { event.preventDefault(); load(); });
    [[-1, 'carHeaterChartPrev'], [1, 'carHeaterChartNext']].forEach(([delta, id]) => {
      document.getElementById(id)?.addEventListener('click', () => {
        const day = new Date(`${input.value}T12:00:00Z`);
        if (Number.isNaN(day.getTime())) return;
        day.setUTCDate(day.getUTCDate() + delta);
        input.value = day.toISOString().slice(0, 10);
        load();
      });
    });
    document.getElementById('carHeaterChartToday')?.addEventListener('click', () => { input.value = today(); load(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
