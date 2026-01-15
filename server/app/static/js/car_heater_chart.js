(() => {
  const SERIES = [
    { key: 'instant_power_w', label: 'Instant power (W)', color: '#ff8a3c', axis: 'yPower' },
    { key: 'voltage_v', label: 'Voltage (V)', color: '#5ac8fa', axis: 'yVoltage' },
    { key: 'current_a', label: 'Current (A)', color: '#8cf86a', axis: 'yCurrent' },
    { key: 'ambient_temp', label: 'Ambient temp (°C)', color: '#ffd54f', axis: 'yTemp' },
    { key: 'device_temp_c', label: 'Device temp (°C)', color: '#f06292', axis: 'yTemp' },
  ];

  const STORAGE_KEY = 'car_heater_chart_visibility';
  const DEFAULT_AGG_MINUTES = Number(window?.CAR_HEATER_CHART_AGG_MINUTES ?? 10);

  const AXIS_OPTIONS = {
    yTemp: {
      type: 'linear',
      position: 'left',
      offset: true,
      grid: { color: 'rgba(255,255,255,0.08)' },
      title: { display: true, text: 'Temperature (°C)' },
      ticks: { color: '#fff' },
    },
    yPower: {
      type: 'linear',
      position: 'right',
      grid: { drawOnChartArea: false },
      title: { display: true, text: 'Power (W)' },
      ticks: { color: '#ff8a3c' },
    },
    yVoltage: {
      type: 'linear',
      position: 'right',
      grid: { drawOnChartArea: false },
      offset: true,
      title: { display: true, text: 'Voltage (V)' },
      ticks: { color: '#5ac8fa' },
    },
    yCurrent: {
      type: 'linear',
      position: 'left',
      grid: { drawOnChartArea: false },
      offset: true,
      title: { display: true, text: 'Current (A)' },
      ticks: { color: '#8cf86a' },
    },
  };

  const LABEL_LOCALE = navigator.language || 'fi-FI';

  function loadVisibilityState() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (!stored) return {};
      return JSON.parse(stored) || {};
    } catch (err) {
      console.warn('Unable to read chart visibility state', err);
      return {};
    }
  }

  function saveVisibilityState(state) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (err) {
      console.warn('Unable to save chart visibility state', err);
    }
  }

  function formatDateISO(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }

  function formatDateHuman(date) {
    return date.toLocaleDateString(LABEL_LOCALE, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
  }

  function addDays(date, delta) {
    const copy = new Date(date);
    copy.setDate(copy.getDate() + delta);
    return copy;
  }

  function normalizeValue(value) {
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function aggregateRows(rows, minutes) {
    const keys = SERIES.map(series => series.key);
    const dataByKey = keys.reduce((acc, key) => { acc[key] = []; return acc; }, {});
    const labels = [];

    if (!rows || rows.length === 0) {
      return { labels, dataByKey };
    }

    if (!minutes || minutes <= 1) {
      for (const row of rows) {
        const ts = new Date(row.timestamp);
        if (Number.isNaN(ts)) continue;
        const hh = String(ts.getHours()).padStart(2, '0');
        const mm = String(ts.getMinutes()).padStart(2, '0');
        labels.push(`${hh}:${mm}`);
        keys.forEach(key => {
          dataByKey[key].push(normalizeValue(row[key]));
        });
      }
      return { labels, dataByKey };
    }

    const buckets = new Map();
    for (const row of rows) {
      const ts = new Date(row.timestamp);
      if (Number.isNaN(ts)) continue;
      const minutesOfDay = ts.getHours() * 60 + ts.getMinutes();
      const bucketStart = Math.floor(minutesOfDay / minutes) * minutes;
      let bucket = buckets.get(bucketStart);
      if (!bucket) {
        bucket = { ts, sums: {}, counts: {} };
        buckets.set(bucketStart, bucket);
      }
      bucket.ts = ts;
      keys.forEach(key => {
        const val = normalizeValue(row[key]);
        if (val === null) return;
        bucket.sums[key] = (bucket.sums[key] || 0) + val;
        bucket.counts[key] = (bucket.counts[key] || 0) + 1;
      });
    }

    const sorted = [...buckets.entries()].sort((a, b) => a[0] - b[0]);
    for (const [, bucket] of sorted) {
      const ts = bucket.ts;
      const hh = String(ts.getHours()).padStart(2, '0');
      const mm = String(ts.getMinutes()).padStart(2, '0');
      labels.push(`${hh}:${mm}`);
      keys.forEach(key => {
        const sum = bucket.sums[key] || 0;
        const count = bucket.counts[key] || 0;
        dataByKey[key].push(count ? sum / count : null);
      });
    }

    return { labels, dataByKey };
  }

  function computeAxisRange(values) {
    const nums = values.filter(Number.isFinite);
    if (!nums.length) return null;
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const padding = (max - min) * 0.1 || (Math.abs(min) * 0.1) || 1;
    return { min: min - padding, max: max + padding };
  }

  function init() {
    const modal = document.getElementById('carHeaterChartModal');
    const canvas = document.getElementById('carHeaterChartCanvas');
    const togglesBar = document.getElementById('carHeaterChartToggles');
    const dateLabel = document.getElementById('carHeaterChartCurrentDate');
    const prevBtn = document.getElementById('carHeaterChartPrev');
    const nextBtn = document.getElementById('carHeaterChartNext');
    const todayBtn = document.getElementById('carHeaterChartToday');
    const closeBtn = document.getElementById('carHeaterChartClose');
    const openBtn = document.getElementById('btnOpenCarHeaterCharts');
    if (!modal || !canvas || !togglesBar || !dateLabel) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let currentDate = new Date();
    let chart = null;
    let visibilityState = loadVisibilityState();

    function setDateLabel(date) {
      dateLabel.textContent = formatDateHuman(date);
    }

    function ensureVisible(fn) {
      requestAnimationFrame(() => requestAnimationFrame(fn));
    }

    function openModal() {
      modal.style.display = 'flex';
    }

    function closeModal() {
      modal.style.display = 'none';
    }

    function createToggles() {
      togglesBar.innerHTML = '';
      SERIES.forEach(series => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'toggle-pill';
        btn.dataset.key = series.key;
        const visible = visibilityState[series.key] !== false;
        if (visible) btn.classList.add('active');
        btn.setAttribute('aria-pressed', visible ? 'true' : 'false');
        btn.innerHTML = `<span class="pill-chip" style="background:${series.color}"></span><span>${series.label}</span>`;
        btn.addEventListener('click', () => {
          const next = !(visibilityState[series.key] !== false);
          visibilityState[series.key] = next;
          btn.classList.toggle('active', next);
          btn.setAttribute('aria-pressed', next ? 'true' : 'false');
          saveVisibilityState(visibilityState);
          if (chart) {
            setDatasetVisibility(series.key, next);
          }
        });
        togglesBar.appendChild(btn);
      });
    }

    function setDatasetVisibility(key, visible) {
      if (!chart) return;
      const dataset = chart.data.datasets.find(ds => ds.metaKey === key || ds.label === key);
      if (!dataset) return;
      dataset.hidden = !visible;
      chart.update();
    }

    function ensureChartCreated() {
      if (chart) return;
      chart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: [],
          datasets: SERIES.map(series => ({
            label: series.label,
            metaKey: series.key,
            data: [],
            borderColor: series.color,
            tension: 0.25,
            borderWidth: 2,
            pointRadius: 0,
            spanGaps: true,
            yAxisID: series.axis,
            hidden: visibilityState[series.key] === false,
          })),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'nearest', intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label(context) {
                  const value = context.parsed.y;
                  if (!Number.isFinite(value)) return context.dataset.label || '';
                  return `${context.dataset.label}: ${formatTooltipValue(context.dataset.metaKey, value)}`;
                },
              },
            },
          },
          scales: AXIS_OPTIONS,
        },
      });
    }

    function formatTooltipValue(seriesKey, value) {
      const series = SERIES.find(s => s.key === seriesKey);
      if (!series) return value.toFixed(2);
      if (seriesKey === 'current_a') {
        return `${value.toFixed(2)} A`;
      }
      if (seriesKey === 'instant_power_w') {
        return `${value.toFixed(1)} W`;
      }
      if (seriesKey === 'voltage_v') {
        return `${value.toFixed(1)} V`;
      }
      return `${value.toFixed(1)}${series.label.includes('°C') ? ' °C' : ''}`;
    }

    const energyEl = document.getElementById('carHeaterChartEnergyTotal');

    function updateAxes(dataByKey) {
      Object.entries(AXIS_OPTIONS).forEach(([axisId]) => {
        if (!chart?.options.scales) return;
        const axisValues = [];
        SERIES.filter(series => series.axis === axisId).forEach(series => {
          axisValues.push(...(dataByKey[series.key] || []));
        });
        const range = computeAxisRange(axisValues);
        if (range) {
          chart.options.scales[axisId].min = range.min;
          chart.options.scales[axisId].max = range.max;
        } else {
          delete chart.options.scales[axisId].min;
          delete chart.options.scales[axisId].max;
        }
      });
    }

    function setEnergySummary(value) {
      if (!energyEl) return;
      const numeric = normalizeValue(value);
      energyEl.textContent = Number.isFinite(numeric)
        ? `Energy today: ${numeric.toFixed(1)} Wh`
        : 'Energy today: – Wh';
    }

    function renderData(rows) {
      ensureChartCreated();
      const { labels, dataByKey } = aggregateRows(rows, DEFAULT_AGG_MINUTES);
      chart.data.labels = labels;
      chart.data.datasets.forEach(dataset => {
        const values = dataByKey[dataset.metaKey] || [];
        dataset.data = values;
        dataset.hidden = visibilityState[dataset.metaKey] === false;
      });
      updateAxes(dataByKey);
      chart.update();
      chart.resize();
    }

    async function fetchAndRender() {
      const dateStr = formatDateISO(currentDate);
      setDateLabel(currentDate);
      try {
        const resp = await fetch(`/api/car_heater/history?date=${encodeURIComponent(dateStr)}`, { cache: 'no-store' });
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }
      const data = await resp.json();
      let rows = [];
      let energyValue = null;
      if (Array.isArray(data)) {
        rows = data;
      } else if (data && Array.isArray(data.rows)) {
        rows = data.rows;
        energyValue = data.energy_today_wh;
      }
      renderData(rows);
      setEnergySummary(energyValue);
    } catch (err) {
      console.error('Failed to load car heater chart data:', err);
      renderData([]);
      setEnergySummary(null);
    }
    }

    function openAndFetch(options) {
      if (options?.resetDate) {
        currentDate = new Date();
      }
      openModal();
      ensureVisible(() => fetchAndRender());
    }

    prevBtn?.addEventListener('click', () => {
      currentDate = addDays(currentDate, -1);
      fetchAndRender();
    });
    nextBtn?.addEventListener('click', () => {
      currentDate = addDays(currentDate, 1);
      fetchAndRender();
    });
    todayBtn?.addEventListener('click', () => {
      currentDate = new Date();
      fetchAndRender();
    });
    closeBtn?.addEventListener('click', closeModal);
    modal.addEventListener('click', (evt) => {
      if (evt.target === modal) closeModal();
    });
    openBtn?.addEventListener('click', () => openAndFetch({ resetDate: true }));

    createToggles();
    setDateLabel(currentDate);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
