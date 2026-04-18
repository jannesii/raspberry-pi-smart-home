(() => {
  console.log('🧊 temperatures.js loaded');

  const BOOTSTRAP = window.TEMPERATURES_BOOTSTRAP || {};
  const STALE_MINUTES = 10;
  const STALE_MS = STALE_MINUTES * 60 * 1000;
  const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
  const DEFAULT_CHART_AVG_MINUTES = 10;
  const OUTSIDE_LOCATION_NAMES = Array.isArray(BOOTSTRAP.outside_location_names)
    ? BOOTSTRAP.outside_location_names
    : ['Rengonharju', 'Pelmaa'];
  const OUTSIDE_LOCATION_ALIASES = OUTSIDE_LOCATION_NAMES
    .map(name => String(name || '').trim().toLowerCase())
    .filter(Boolean);

  const state = {
    rooms: new Map(),
    search: '',
    filterMode: 'indoor',
    sortKey: 'name',
    sortDir: 'asc',
    selectedKey: null,
    detailOpen: false,
    currentDate: new Date(),
    chartTemp: null,
    chartHum: null,
    chartRequestToken: 0,
    controlLocations: [],
    outsideSummary: null,
    ac: {
      isOn: null,
      thermostatEnabled: null,
      sleepEnabled: null,
      sleepActive: null,
      sleepOverrideUntil: null,
      mode: null,
      fanSpeed: null,
    },
  };

  const dom = {};

  function normalizeText(value) {
    return String(value || '').trim();
  }

  function normalizeKey(value) {
    return normalizeText(value).toLowerCase();
  }

  function isOutsideLocation(name) {
    const normalized = normalizeKey(name);
    return Boolean(normalized) && OUTSIDE_LOCATION_ALIASES.some(alias => normalized === alias);
  }

  function isStaleTimestamp(ts) {
    const time = ts ? new Date(ts).getTime() : NaN;
    return !Number.isFinite(time) || (Date.now() - time) > STALE_MS;
  }

  function fmtTemp(value, digits = 1) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(digits)} °C` : '—';
  }

  function fmtHum(value, digits = 0) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(digits)} %` : '—';
  }

  function fmtCompactTemp(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(1)}°C` : '—';
  }

  function fmtCompactHum(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(0)}%` : '—';
  }

  function fmtTimestamp(ts) {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return '—';
    }
  }

  function fmtDateLabel(date) {
    try {
      return date.toLocaleDateString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
      });
    } catch {
      return '—';
    }
  }

  function fmtFreshness(ts) {
    if (!ts) return 'No timestamp';
    const time = new Date(ts).getTime();
    if (!Number.isFinite(time)) return 'No timestamp';
    const ageMs = Math.max(0, Date.now() - time);
    const ageMin = Math.round(ageMs / 60000);
    if (ageMs <= STALE_MS) {
      return ageMin <= 1 ? 'Fresh now' : `Fresh · ${ageMin} min ago`;
    }
    return ageMin < 120 ? `Stale · ${ageMin} min old` : `Stale · ${Math.round(ageMin / 60)} h old`;
  }

  function getTempToneClass(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    if (n <= 19) return 'temp-cold';
    if (n <= 24) return 'temp-good';
    if (n <= 27) return 'temp-warm';
    return 'temp-hot';
  }

  function applyTempTone(el, value) {
    if (!el) return;
    el.classList.remove('temp-cold', 'temp-good', 'temp-warm', 'temp-hot');
    const tone = getTempToneClass(value);
    if (tone) el.classList.add(tone);
  }

  function normalizeRoom(entry) {
    const name = normalizeText(entry && (entry.location || entry.name));
    const temp = entry ? (entry.temp ?? entry.temperature_c ?? entry.temperature ?? null) : null;
    const hum = entry ? (entry.hum ?? entry.humidity_pct ?? entry.humidity ?? null) : null;
    const ts = entry ? (entry.timestamp ?? entry.ts ?? null) : null;
    const key = normalizeKey(name);
    return {
      key,
      name: name || '—',
      temp,
      hum,
      ts,
      isOutside: isOutsideLocation(name),
    };
  }

  function setRoom(entry) {
    const room = normalizeRoom(entry);
    if (!room.key) return;
    state.rooms.set(room.key, room);
  }

  function getRooms() {
    return Array.from(state.rooms.values());
  }

  function getSelectedRoom() {
    return state.selectedKey ? state.rooms.get(state.selectedKey) || null : null;
  }

  function getVisibleRooms() {
    const search = normalizeKey(state.search);
    return getRooms()
      .filter(room => {
        if (search && !normalizeKey(room.name).includes(search)) return false;
        if (state.filterMode === 'indoor') return !room.isOutside;
        if (state.filterMode === 'outdoor') return room.isOutside;
        if (state.filterMode === 'stale') return isStaleTimestamp(room.ts);
        return true;
      })
      .sort(compareRooms);
  }

  function compareRooms(a, b) {
    const dir = state.sortDir === 'desc' ? -1 : 1;
    if (state.sortKey === 'name') return a.name.localeCompare(b.name) * dir;

    let av;
    let bv;
    if (state.sortKey === 'temp') {
      av = Number(a.temp);
      bv = Number(b.temp);
    } else if (state.sortKey === 'hum') {
      av = Number(a.hum);
      bv = Number(b.hum);
    } else {
      av = Date.parse(a.ts || 0);
      bv = Date.parse(b.ts || 0);
    }

    const aValid = Number.isFinite(av);
    const bValid = Number.isFinite(bv);
    if (!aValid && !bValid) return 0;
    if (!aValid) return 1 * dir;
    if (!bValid) return -1 * dir;
    return (av - bv) * dir;
  }

  function getIndoorRooms() {
    return getRooms().filter(room => !room.isOutside);
  }

  function getOutdoorRooms() {
    return getRooms().filter(room => room.isOutside);
  }

  function chooseInitialRoom() {
    const indoors = getIndoorRooms().sort((a, b) => a.name.localeCompare(b.name));
    if (indoors.length) return indoors[0];
    const all = getRooms().sort((a, b) => a.name.localeCompare(b.name));
    return all.length ? all[0] : null;
  }

  function isMobileLayout() {
    return window.innerWidth <= 900;
  }

  function setBodyScrollLock(locked) {
    document.body.style.overflow = locked ? 'hidden' : '';
  }

  function updateDetailVisibility() {
    if (!dom.detailPane || !dom.detailBackdrop) return;
    const mobileOpen = isMobileLayout() && state.detailOpen;
    dom.detailPane.classList.toggle('is-open', mobileOpen);
    dom.detailBackdrop.classList.toggle('is-open', mobileOpen);
    setBodyScrollLock(mobileOpen);
  }

  function openDetail() {
    state.detailOpen = true;
    updateDetailVisibility();
  }

  function closeDetail() {
    state.detailOpen = false;
    updateDetailVisibility();
  }

  function ensureSelectedRoom() {
    if (state.selectedKey && state.rooms.has(state.selectedKey)) return;
    const initial = chooseInitialRoom();
    state.selectedKey = initial ? initial.key : null;
  }

  function setSelectedRoom(key, options = {}) {
    if (!key || !state.rooms.has(key)) return;
    const nextRoom = state.rooms.get(key);
    const hasChanged = state.selectedKey !== key;
    state.selectedKey = key;
    if (hasChanged) state.currentDate = new Date();
    if (options.openDetail) openDetail();
    renderRoomList();
    renderDetailShell(nextRoom);
    if (hasChanged || options.force) {
      fetchRoomHistory();
    }
  }

  function renderOverview() {
    const indoors = getIndoorRooms();
    const outdoors = getOutdoorRooms();
    const temps = indoors.map(room => Number(room.temp)).filter(Number.isFinite);
    const staleCount = getRooms().filter(room => isStaleTimestamp(room.ts)).length;

    const avg = temps.length ? temps.reduce((sum, temp) => sum + temp, 0) / temps.length : null;
    const warmest = indoors
      .filter(room => Number.isFinite(Number(room.temp)))
      .sort((a, b) => Number(b.temp) - Number(a.temp))[0] || null;
    const coolest = indoors
      .filter(room => Number.isFinite(Number(room.temp)))
      .sort((a, b) => Number(a.temp) - Number(b.temp))[0] || null;

    dom.overviewIndoorAvg.textContent = avg == null ? '—' : fmtCompactTemp(avg);
    dom.overviewWarmest.textContent = warmest ? `${warmest.name} · ${fmtCompactTemp(warmest.temp)}` : '—';
    dom.overviewCoolest.textContent = coolest ? `${coolest.name} · ${fmtCompactTemp(coolest.temp)}` : '—';
    dom.overviewStale.textContent = `${staleCount} sensor${staleCount === 1 ? '' : 's'}`;

    applyTempTone(dom.overviewIndoorAvg, avg);
    applyTempTone(dom.overviewWarmest, warmest ? warmest.temp : null);
    applyTempTone(dom.overviewCoolest, coolest ? coolest.temp : null);

    if (state.ac.isOn === true) {
      dom.overviewAcState.textContent = 'Running';
    } else if (state.ac.isOn === false) {
      dom.overviewAcState.textContent = 'Stopped';
    } else {
      dom.overviewAcState.textContent = 'Unknown';
    }

    if (outdoors.length) {
      const outdoorTemps = outdoors.map(room => Number(room.temp)).filter(Number.isFinite);
      if (outdoorTemps.length) {
        const min = Math.min(...outdoorTemps);
        const max = Math.max(...outdoorTemps);
        dom.overviewOutside.textContent = `${outdoors.length} sensors · ${min.toFixed(1)} to ${max.toFixed(1)}°C`;
      } else {
        dom.overviewOutside.textContent = `${outdoors.length} sensors`;
      }
    } else {
      dom.overviewOutside.textContent = '—';
    }
  }

  function renderRoomCount(visibleCount) {
    const label = state.filterMode === 'indoor'
      ? 'indoor'
      : state.filterMode === 'outdoor'
        ? 'outdoor'
        : 'stale';
    dom.roomCount.textContent = `${visibleCount} ${label} room${visibleCount === 1 ? '' : 's'}`;
  }

  function createFreshDot(isFresh) {
    const dot = document.createElement('span');
    dot.className = `fresh-dot ${isFresh ? 'is-fresh' : 'is-stale'}`;
    dot.setAttribute('aria-hidden', 'true');
    return dot;
  }

  function createRoomButton(room) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'room-button';
    button.dataset.roomKey = room.key;
    button.classList.toggle('is-selected', state.selectedKey === room.key);
    button.classList.toggle('is-stale', isStaleTimestamp(room.ts));
    button.setAttribute('aria-pressed', state.selectedKey === room.key ? 'true' : 'false');

    const header = document.createElement('div');
    header.className = 'room-button-header';

    const nameWrap = document.createElement('div');
    nameWrap.className = 'room-name';
    nameWrap.appendChild(createFreshDot(!isStaleTimestamp(room.ts)));

    const nameText = document.createElement('span');
    nameText.textContent = room.name;
    nameWrap.appendChild(nameText);

    const temp = document.createElement('strong');
    temp.className = 'room-temp';
    temp.textContent = fmtCompactTemp(room.temp);
    applyTempTone(temp, room.temp);

    const humidity = document.createElement('div');
    humidity.className = 'room-humidity';
    humidity.textContent = fmtCompactHum(room.hum);

    const meta = document.createElement('div');
    meta.className = 'room-meta';
    meta.innerHTML = `
      <span>${room.isOutside ? 'Outdoor sensor' : 'Indoor sensor'}</span>
      <span>${fmtFreshness(room.ts)}</span>
      <span>${fmtTimestamp(room.ts)}</span>
    `;

    const footer = document.createElement('div');
    footer.className = 'room-button-footer';
    footer.appendChild(humidity);
    footer.appendChild(meta);

    header.appendChild(nameWrap);
    header.appendChild(temp);
    button.appendChild(header);
    button.appendChild(footer);

    button.addEventListener('click', () => {
      setSelectedRoom(room.key, { openDetail: isMobileLayout() });
    });

    return button;
  }

  function renderRoomList() {
    const rooms = getVisibleRooms();
    dom.roomList.innerHTML = '';
    renderRoomCount(rooms.length);

    if (!rooms.length) {
      dom.roomListEmpty.hidden = false;
      return;
    }

    dom.roomListEmpty.hidden = true;
    rooms.forEach(room => {
      dom.roomList.appendChild(createRoomButton(room));
    });
  }

  function renderOutsideStrip() {
    const rooms = getOutdoorRooms().sort((a, b) => a.name.localeCompare(b.name));
    dom.outsideStrip.innerHTML = '';
    dom.outsideStripSection.hidden = rooms.length === 0 || state.filterMode !== 'indoor';

    rooms.forEach(room => {
      const wrapper = document.createElement('article');
      wrapper.className = 'outside-sensor';

      const button = document.createElement('button');
      button.type = 'button';
      button.addEventListener('click', () => {
        setSelectedRoom(room.key, { openDetail: isMobileLayout() });
      });

      const name = document.createElement('div');
      name.className = 'outside-name';
      name.textContent = room.name;

      const reading = document.createElement('div');
      reading.className = 'outside-reading';

      const temp = document.createElement('strong');
      temp.textContent = fmtCompactTemp(room.temp);
      applyTempTone(temp, room.temp);

      const hum = document.createElement('span');
      hum.textContent = fmtCompactHum(room.hum);
      hum.className = 'outside-meta';

      const meta = document.createElement('div');
      meta.className = 'outside-meta';
      meta.textContent = `${fmtFreshness(room.ts)} · ${fmtTimestamp(room.ts)}`;

      reading.appendChild(temp);
      reading.appendChild(hum);
      button.appendChild(name);
      button.appendChild(reading);
      button.appendChild(meta);
      wrapper.appendChild(button);
      dom.outsideStrip.appendChild(wrapper);
    });
  }

  function renderDetailShell(room) {
    if (!room) {
      dom.detailRoomName.textContent = 'Select a room';
      dom.detailRoomMeta.textContent = 'Waiting for data';
      dom.detailFreshness.textContent = '—';
      dom.detailFreshness.classList.remove('state-on', 'state-off', 'state-idle', 'state-unknown');
      dom.detailFreshness.classList.add('state-unknown');
      dom.detailCurrentTemp.textContent = '—';
      dom.detailCurrentHum.textContent = '—';
      applyTempTone(dom.detailCurrentTemp, null);
      return;
    }

    dom.detailRoomName.textContent = room.name;
    dom.detailRoomMeta.textContent = `${room.isOutside ? 'Outdoor sensor' : 'Indoor sensor'} · Updated ${fmtTimestamp(room.ts)}`;
    dom.detailFreshness.textContent = fmtFreshness(room.ts);
    dom.detailFreshness.classList.remove('state-on', 'state-off', 'state-idle', 'state-unknown');
    dom.detailFreshness.classList.add(isStaleTimestamp(room.ts) ? 'state-idle' : 'state-on');

    dom.detailCurrentTemp.textContent = fmtTemp(room.temp);
    dom.detailCurrentHum.textContent = fmtHum(room.hum);
    applyTempTone(dom.detailCurrentTemp, room.temp);
  }

  function setDetailStat(el, value, formatter, toneValue) {
    if (!el) return;
    el.textContent = formatter(value);
    applyTempTone(el, toneValue);
  }

  function setChartSummary(rows) {
    const temps = rows.map(row => Number(row.temperature ?? row.temperature_c)).filter(Number.isFinite);
    const hums = rows.map(row => Number(row.humidity ?? row.humidity_pct)).filter(Number.isFinite);

    const avgTemp = temps.length ? temps.reduce((sum, value) => sum + value, 0) / temps.length : null;
    const avgHum = hums.length ? hums.reduce((sum, value) => sum + value, 0) / hums.length : null;
    const minTemp = temps.length ? Math.min(...temps) : null;
    const maxTemp = temps.length ? Math.max(...temps) : null;
    const minHum = hums.length ? Math.min(...hums) : null;
    const maxHum = hums.length ? Math.max(...hums) : null;

    setDetailStat(dom.detailDailyTempAvg, avgTemp, fmtCompactTemp, avgTemp);
    setDetailStat(dom.detailDailyTempMin, minTemp, fmtCompactTemp, minTemp);
    setDetailStat(dom.detailDailyTempMax, maxTemp, fmtCompactTemp, maxTemp);

    dom.detailDailyHumAvg.textContent = fmtCompactHum(avgHum);
    dom.detailDailyHumMin.textContent = fmtCompactHum(minHum);
    dom.detailDailyHumMax.textContent = fmtCompactHum(maxHum);
  }

  function formatDateISO(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function addDays(date, delta) {
    const next = new Date(date);
    next.setDate(next.getDate() + delta);
    return next;
  }

  function aggregateByMinutes(rows, minutes) {
    const size = Number(minutes);
    if (!size || size <= 1) {
      return {
        labels: rows.map(row => String(row.timestamp || '').slice(11, 16)),
        temps: rows.map(row => {
          const value = Number(row.temperature ?? row.temperature_c);
          return Number.isFinite(value) ? value : null;
        }),
        hums: rows.map(row => {
          const value = Number(row.humidity ?? row.humidity_pct);
          return Number.isFinite(value) ? value : null;
        }),
      };
    }

    const buckets = new Map();
    rows.forEach(row => {
      const time = new Date(row.timestamp);
      if (Number.isNaN(time.getTime())) return;
      const minute = time.getHours() * 60 + time.getMinutes();
      const bucketKey = Math.floor(minute / size) * size;
      const bucket = buckets.get(bucketKey) || { tempSum: 0, humSum: 0, tempCount: 0, humCount: 0 };
      const temp = Number(row.temperature ?? row.temperature_c);
      const hum = Number(row.humidity ?? row.humidity_pct);
      if (Number.isFinite(temp)) {
        bucket.tempSum += temp;
        bucket.tempCount += 1;
      }
      if (Number.isFinite(hum)) {
        bucket.humSum += hum;
        bucket.humCount += 1;
      }
      buckets.set(bucketKey, bucket);
    });

    const labels = [];
    const temps = [];
    const hums = [];
    Array.from(buckets.keys()).sort((a, b) => a - b).forEach(key => {
      const bucket = buckets.get(key);
      const hour = String(Math.floor(key / 60)).padStart(2, '0');
      const minute = String(key % 60).padStart(2, '0');
      labels.push(`${hour}:${minute}`);
      temps.push(bucket.tempCount ? bucket.tempSum / bucket.tempCount : null);
      hums.push(bucket.humCount ? bucket.humSum / bucket.humCount : null);
    });
    return { labels, temps, hums };
  }

  function getTempAxisConfig(room) {
    if (room && room.isOutside) {
      return { min: -30, max: 10, beginAtZero: false };
    }
    return { min: 18, max: 26, beginAtZero: false };
  }

  function createLineChart(ctx, label, color, yAxis) {
    return new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          label,
          data: [],
          borderColor: color,
          backgroundColor: color,
          pointRadius: 0,
          pointHoverRadius: 3,
          spanGaps: true,
          borderWidth: 2,
          tension: 0.28,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
          legend: { display: false },
          decimation: {
            enabled: true,
            algorithm: 'min-max',
            samples: 240,
            threshold: 480,
          },
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.06)' },
            ticks: { color: '#96a4aa' },
          },
          y: {
            ...yAxis,
            grid: { color: 'rgba(255, 255, 255, 0.06)' },
            ticks: { color: '#96a4aa' },
          },
        },
      },
    });
  }

  function updateCharts(room, rows) {
    if (typeof Chart !== 'function') return;
    const tempCtx = dom.detailChartTemp.getContext('2d');
    const humCtx = dom.detailChartHum.getContext('2d');
    const aggregated = aggregateByMinutes(rows, DEFAULT_CHART_AVG_MINUTES);
    const tempAxis = getTempAxisConfig(room);

    if (!state.chartTemp) {
      state.chartTemp = createLineChart(tempCtx, 'Temperature', '#82bfff', tempAxis);
    }
    if (!state.chartHum) {
      state.chartHum = createLineChart(humCtx, 'Humidity', '#6be2a8', { beginAtZero: false });
    }

    state.chartTemp.data.labels = aggregated.labels;
    state.chartTemp.data.datasets[0].data = aggregated.temps;
    state.chartTemp.options.scales.y = {
      ...state.chartTemp.options.scales.y,
      ...tempAxis,
    };
    state.chartTemp.update();

    state.chartHum.data.labels = aggregated.labels;
    state.chartHum.data.datasets[0].data = aggregated.hums;
    state.chartHum.update();

    dom.detailTempChartMeta.textContent = rows.length
      ? `${DEFAULT_CHART_AVG_MINUTES}-minute average`
      : 'No data';
    dom.detailHumChartMeta.textContent = rows.length
      ? `${DEFAULT_CHART_AVG_MINUTES}-minute average`
      : 'No data';
  }

  async function fetchRoomHistory() {
    const room = getSelectedRoom();
    if (!room) return;

    dom.detailDateLabel.textContent = fmtDateLabel(state.currentDate);
    dom.detailNextDay.disabled = formatDateISO(state.currentDate) >= formatDateISO(new Date());

    const requestToken = state.chartRequestToken + 1;
    state.chartRequestToken = requestToken;
    const url = `/api/esp32_temphum?date=${encodeURIComponent(formatDateISO(state.currentDate))}&location=${encodeURIComponent(room.name)}`;

    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      });
      const rows = response.ok ? await response.json() : [];
      if (requestToken !== state.chartRequestToken) return;
      console.log('📈 History loaded for', room.name, rows.length);
      setChartSummary(Array.isArray(rows) ? rows : []);
      updateCharts(room, Array.isArray(rows) ? rows : []);
      dom.detailEmptyState.hidden = Array.isArray(rows) && rows.length > 0;
    } catch (error) {
      if (requestToken !== state.chartRequestToken) return;
      console.error('❌ Failed to fetch room history:', error);
      setChartSummary([]);
      updateCharts(room, []);
      dom.detailEmptyState.hidden = false;
    }
  }

  function renderFilterState() {
    [dom.filterIndoor, dom.filterOutdoor, dom.filterStale].forEach(button => {
      if (!button) return;
      button.classList.toggle('is-active', button.dataset.filter === state.filterMode);
    });

    [dom.sortName, dom.sortTemp, dom.sortHum, dom.sortUpdated].forEach(button => {
      if (!button) return;
      button.classList.toggle('active', button.dataset.key === state.sortKey);
    });

    dom.sortDir.textContent = state.sortDir === 'asc' ? '↑' : '↓';
  }

  function renderAll() {
    ensureSelectedRoom();
    renderFilterState();
    renderOverview();
    renderOutsideStrip();
    renderRoomList();
    renderDetailShell(getSelectedRoom());
    updateDetailVisibility();
    renderControlLocationButtons();
  }

  function syncSelectionToVisibleRooms() {
    const visibleRooms = getVisibleRooms();
    if (!visibleRooms.length) return;
    if (!visibleRooms.some(room => room.key === state.selectedKey)) {
      setSelectedRoom(visibleRooms[0].key, { force: true });
    }
  }

  function renderControlLocationButtons() {
    if (!dom.ctrlLocContainer) return;
    const roomNames = getRooms().map(room => room.name).sort((a, b) => a.localeCompare(b));
    dom.ctrlLocContainer.innerHTML = '';

    roomNames.forEach(name => {
      const key = normalizeKey(name);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'sensor-chip';
      button.textContent = name;
      button.dataset.location = name;
      button.classList.toggle('is-active', state.controlLocations.includes(name));
      button.addEventListener('click', () => {
        const active = new Set(state.controlLocations);
        if (active.has(name)) {
          if (active.size <= 1) return;
          active.delete(name);
        } else {
          active.add(name);
        }
        state.controlLocations = roomNames.filter(roomName => active.has(roomName));
        renderControlLocationButtons();
        emitSocketAction({ action: 'set_control_locations', locations: state.controlLocations });
      });
      dom.ctrlLocContainer.appendChild(button);
    });
  }

  function setActiveButtons(buttons, value) {
    const normalized = normalizeKey(value);
    buttons.forEach(button => {
      if (!button) return;
      button.classList.toggle('active', normalizeKey(button.dataset.value) === normalized);
    });
  }

  function setStatusPill(pill, text, stateClass) {
    if (!pill) return;
    pill.textContent = text;
    pill.classList.remove('state-on', 'state-off', 'state-idle', 'state-unknown');
    pill.classList.add(stateClass);
  }

  function setSleepScheduleFields(schedule, fallbackStart, fallbackStop) {
    if (schedule && typeof schedule === 'object') {
      DAYS.forEach(day => {
        const row = schedule[day] || {};
        const startInput = document.getElementById(`sleep_${day}_start`);
        const stopInput = document.getElementById(`sleep_${day}_stop`);
        if (startInput) startInput.value = row.start ? String(row.start).slice(0, 5) : '';
        if (stopInput) stopInput.value = row.stop ? String(row.stop).slice(0, 5) : '';
      });
      return;
    }

    DAYS.forEach(day => {
      const startInput = document.getElementById(`sleep_${day}_start`);
      const stopInput = document.getElementById(`sleep_${day}_stop`);
      if (startInput) startInput.value = fallbackStart || '';
      if (stopInput) stopInput.value = fallbackStop || '';
    });
  }

  function setAvgPills(data) {
    if (!data) {
      dom.avgCoolingPill.textContent = 'Cooling —';
      dom.avgHeatingPill.textContent = 'Heating —';
      return;
    }

    const coolingRate = Number(data.cooling_rate_c_per_h);
    const heatingRate = Number(data.heating_rate_c_per_h);
    const coolingPower = Number(data.cooling_power_w);
    const heatingPower = Number(data.heating_power_w);

    dom.avgCoolingPill.textContent = Number.isFinite(coolingRate)
      ? `Cooling ${Math.abs(coolingRate).toFixed(2)} °C/h${Number.isFinite(coolingPower) ? ` · ${Math.round(coolingPower)} W` : ''}`
      : 'Cooling —';
    dom.avgHeatingPill.textContent = Number.isFinite(heatingRate)
      ? `Heating ${heatingRate.toFixed(2)} °C/h${Number.isFinite(heatingPower) ? ` · ${Math.round(heatingPower)} W` : ''}`
      : 'Heating —';
  }

  function syncAcStatus(data) {
    if (!data) return;
    state.ac.isOn = Object.prototype.hasOwnProperty.call(data, 'is_on') ? !!data.is_on : state.ac.isOn;
    state.ac.thermostatEnabled = Object.prototype.hasOwnProperty.call(data, 'thermo_active')
      ? !!data.thermo_active
      : Object.prototype.hasOwnProperty.call(data, 'thermostat_enabled')
        ? !!data.thermostat_enabled
        : state.ac.thermostatEnabled;
    state.ac.sleepEnabled = Object.prototype.hasOwnProperty.call(data, 'sleep_enabled')
      ? !!data.sleep_enabled
      : state.ac.sleepEnabled;
    state.ac.sleepActive = Object.prototype.hasOwnProperty.call(data, 'sleep_time_active')
      ? !!data.sleep_time_active
      : state.ac.sleepActive;
    state.ac.sleepOverrideUntil = Object.prototype.hasOwnProperty.call(data, 'sleep_override_until')
      ? data.sleep_override_until
      : state.ac.sleepOverrideUntil;
    state.ac.mode = data.mode || state.ac.mode;
    state.ac.fanSpeed = data.fan_speed || state.ac.fanSpeed;

    if (state.ac.isOn === true) {
      setStatusPill(dom.acStatusPill, 'AC on', 'state-on');
      dom.btnAcPowerToggle.textContent = 'Turn AC off';
    } else if (state.ac.isOn === false) {
      setStatusPill(dom.acStatusPill, 'AC off', 'state-off');
      dom.btnAcPowerToggle.textContent = 'Turn AC on';
    } else {
      setStatusPill(dom.acStatusPill, 'AC —', 'state-unknown');
      dom.btnAcPowerToggle.textContent = 'Toggle AC';
    }

    if (state.ac.thermostatEnabled === true) {
      setStatusPill(dom.thermoStatusPill, 'Thermostat on', 'state-on');
      dom.btnThermoToggle.textContent = 'Disable thermostat';
    } else if (state.ac.thermostatEnabled === false) {
      setStatusPill(dom.thermoStatusPill, 'Thermostat off', 'state-off');
      dom.btnThermoToggle.textContent = 'Enable thermostat';
    } else {
      setStatusPill(dom.thermoStatusPill, 'Thermostat —', 'state-unknown');
      dom.btnThermoToggle.textContent = 'Toggle thermostat';
    }

    if (state.ac.sleepEnabled === true && state.ac.sleepActive === true) {
      setStatusPill(dom.sleepStatusPill, 'Sleep now', 'state-on');
    } else if (state.ac.sleepEnabled === true) {
      const suffix = state.ac.sleepOverrideUntil ? ` · until ${String(state.ac.sleepOverrideUntil).slice(0, 5)}` : '';
      setStatusPill(dom.sleepStatusPill, `Sleep enabled${suffix}`, 'state-idle');
    } else if (state.ac.sleepEnabled === false) {
      setStatusPill(dom.sleepStatusPill, 'Sleep off', 'state-off');
    } else {
      setStatusPill(dom.sleepStatusPill, 'Sleep —', 'state-unknown');
    }

    dom.btnSleepToggleMain.textContent = state.ac.sleepEnabled ? 'Disable sleep' : 'Enable sleep';
    dom.btnSleepToggle.textContent = state.ac.sleepEnabled ? 'Disable sleep' : 'Enable sleep';

    setActiveButtons([dom.modeCold, dom.modeWet, dom.modeWind], state.ac.mode);
    setActiveButtons([dom.fanLow, dom.fanHigh], state.ac.fanSpeed);

    if (Object.prototype.hasOwnProperty.call(data, 'setpoint_c')) {
      const setpoint = Number(data.setpoint_c);
      if (Number.isFinite(setpoint)) dom.setpointC.value = setpoint.toFixed(1);
    }
    if (Object.prototype.hasOwnProperty.call(data, 'neg_hysteresis')) {
      const value = Number(data.neg_hysteresis);
      if (Number.isFinite(value)) dom.thermoHysteresisNeg.value = value.toFixed(1);
    }
    if (Object.prototype.hasOwnProperty.call(data, 'pos_hysteresis')) {
      const value = Number(data.pos_hysteresis);
      if (Number.isFinite(value)) dom.thermoHysteresisPos.value = value.toFixed(1);
    }
    if (Object.prototype.hasOwnProperty.call(data, 'min_on_s')) dom.thermoMinOnS.value = parseInt(data.min_on_s, 10) || 0;
    if (Object.prototype.hasOwnProperty.call(data, 'min_off_s')) dom.thermoMinOffS.value = parseInt(data.min_off_s, 10) || 0;
    if (Object.prototype.hasOwnProperty.call(data, 'poll_interval_s')) dom.thermoPollS.value = parseInt(data.poll_interval_s, 10) || 15;
    if (Object.prototype.hasOwnProperty.call(data, 'smooth_window')) dom.thermoSmoothWindow.value = parseInt(data.smooth_window, 10) || 1;
    if (Object.prototype.hasOwnProperty.call(data, 'max_stale_s')) dom.thermoMaxStaleS.value = data.max_stale_s == null ? '' : parseInt(data.max_stale_s, 10);

    let controlLocations = [];
    try {
      const raw = typeof data.control_locations === 'string'
        ? JSON.parse(data.control_locations)
        : data.control_locations;
      if (Array.isArray(raw)) controlLocations = raw.map(value => String(value));
    } catch {
      controlLocations = [];
    }
    if (!controlLocations.length) {
      const initialRoom = chooseInitialRoom();
      controlLocations = initialRoom ? [initialRoom.name] : [];
    }
    state.controlLocations = controlLocations;
    renderControlLocationButtons();

    let sleepSchedule = data.sleep_schedule;
    try {
      if (typeof sleepSchedule === 'string') sleepSchedule = JSON.parse(sleepSchedule);
    } catch {
      sleepSchedule = null;
    }
    setSleepScheduleFields(
      sleepSchedule,
      data.sleep_start ? String(data.sleep_start).slice(0, 5) : '',
      data.sleep_stop ? String(data.sleep_stop).slice(0, 5) : '',
    );

    renderOverview();
  }

  function emitSocketAction(payload) {
    if (!window.socket) return;
    window.socket.emit('ac_control', payload);
  }

  async function fetchAcStatus() {
    try {
      const response = await fetch('/api/ac/status');
      if (!response.ok) {
        console.warn('AC status fetch failed:', response.status);
        return;
      }
      const data = await response.json();
      syncAcStatus(data);
    } catch (error) {
      console.error('❌ AC status fetch failed:', error);
    }
  }

  async function fetchAvgRates() {
    try {
      const response = await fetch('/api/hvac/avg_rates_today');
      if (!response.ok) {
        console.warn('Average rates fetch failed:', response.status);
        setAvgPills(null);
        return;
      }
      const data = await response.json();
      setAvgPills(data);
    } catch (error) {
      console.error('❌ Failed to fetch average rates:', error);
      setAvgPills(null);
    }
  }

  async function fetchOutsideToday() {
    const names = OUTSIDE_LOCATION_NAMES.map(name => normalizeText(name)).filter(Boolean);
    if (!names.length) {
      dom.outsideTempRange.textContent = 'Temp —';
      dom.outsideHumRange.textContent = 'Humidity —';
      return;
    }

    try {
      const responses = await Promise.all(
        names.map(name => fetch(`/api/esp32_temphum?location=${encodeURIComponent(name)}`))
      );
      const rows = [];
      for (const response of responses) {
        if (!response.ok) continue;
        const data = await response.json();
        if (Array.isArray(data)) rows.push(...data);
      }

      const temps = rows.map(row => Number(row.temperature ?? row.temperature_c)).filter(Number.isFinite);
      const hums = rows.map(row => Number(row.humidity ?? row.humidity_pct)).filter(Number.isFinite);

      dom.outsideTempRange.textContent = temps.length
        ? `Temp ${Math.min(...temps).toFixed(1)} to ${Math.max(...temps).toFixed(1)}°C`
        : 'Temp —';
      dom.outsideHumRange.textContent = hums.length
        ? `Humidity ${Math.round(Math.min(...hums))} to ${Math.round(Math.max(...hums))}%`
        : 'Humidity —';
    } catch (error) {
      console.error('❌ Failed to fetch outside stats:', error);
      dom.outsideTempRange.textContent = 'Temp —';
      dom.outsideHumRange.textContent = 'Humidity —';
    }
  }

  function buildSleepSchedule() {
    return DAYS.reduce((schedule, day) => {
      const start = document.getElementById(`sleep_${day}_start`);
      const stop = document.getElementById(`sleep_${day}_stop`);
      schedule[day] = {
        start: start && start.value ? start.value : null,
        stop: stop && stop.value ? stop.value : null,
      };
      return schedule;
    }, {});
  }

  function debounce(fn, waitMs) {
    let timer = null;
    return (...args) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => fn(...args), waitMs);
    };
  }

  function bindControls() {
    dom.roomSearchInput.addEventListener('input', event => {
      state.search = event.target.value || '';
      syncSelectionToVisibleRooms();
      renderRoomList();
    });

    [dom.filterIndoor, dom.filterOutdoor, dom.filterStale].forEach(button => {
      button.addEventListener('click', () => {
        state.filterMode = button.dataset.filter || 'indoor';
        renderFilterState();
        syncSelectionToVisibleRooms();
        renderOutsideStrip();
        renderRoomList();
      });
    });

    [dom.sortName, dom.sortTemp, dom.sortHum, dom.sortUpdated].forEach(button => {
      button.addEventListener('click', () => {
        state.sortKey = button.dataset.key || 'name';
        renderFilterState();
        renderRoomList();
      });
    });

    dom.sortDir.addEventListener('click', () => {
      state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
      renderFilterState();
      renderRoomList();
    });

    dom.btnAcPowerToggle.addEventListener('click', () => {
      const action = state.ac.isOn ? 'power_off' : 'power_on';
      emitSocketAction({ action });
    });

    dom.btnThermoToggle.addEventListener('click', () => {
      const action = state.ac.thermostatEnabled ? 'thermostat_disable' : 'thermostat_enable';
      emitSocketAction({ action });
    });

    dom.btnSleepToggleMain.addEventListener('click', () => {
      emitSocketAction({ action: 'set_sleep_enabled', value: !state.ac.sleepEnabled });
    });

    dom.btnSleepToggle.addEventListener('click', () => {
      emitSocketAction({ action: 'set_sleep_enabled', value: !state.ac.sleepEnabled });
    });

    dom.btnSetpointDec.addEventListener('click', () => {
      const current = Number(dom.setpointC.value);
      const base = Number.isFinite(current) ? current : 22;
      const next = Math.max(5, Math.min(35, base - 0.5));
      dom.setpointC.value = next.toFixed(1);
      emitSocketAction({ action: 'set_setpoint', value: next });
    });

    dom.btnSetpointInc.addEventListener('click', () => {
      const current = Number(dom.setpointC.value);
      const base = Number.isFinite(current) ? current : 22;
      const next = Math.max(5, Math.min(35, base + 0.5));
      dom.setpointC.value = next.toFixed(1);
      emitSocketAction({ action: 'set_setpoint', value: next });
    });

    dom.setpointC.addEventListener('change', () => {
      const value = Number(dom.setpointC.value);
      if (Number.isFinite(value)) emitSocketAction({ action: 'set_setpoint', value });
    });

    dom.modeCold.addEventListener('click', () => emitSocketAction({ action: 'set_mode', value: 'cold' }));
    dom.modeWet.addEventListener('click', () => emitSocketAction({ action: 'set_mode', value: 'wet' }));
    dom.modeWind.addEventListener('click', () => emitSocketAction({ action: 'set_mode', value: 'wind' }));
    dom.fanLow.addEventListener('click', () => emitSocketAction({ action: 'set_fan_speed', value: 'low' }));
    dom.fanHigh.addEventListener('click', () => emitSocketAction({ action: 'set_fan_speed', value: 'high' }));

    const emitThermostatSplit = debounce(() => {
      const pos = Number(dom.thermoHysteresisPos.value);
      const neg = Number(dom.thermoHysteresisNeg.value);
      if (Number.isFinite(pos) && Number.isFinite(neg)) {
        emitSocketAction({ action: 'set_hysteresis_split', pos, neg });
      }
    }, 250);

    dom.thermoHysteresisPos.addEventListener('input', emitThermostatSplit);
    dom.thermoHysteresisNeg.addEventListener('input', emitThermostatSplit);
    dom.thermoHysteresisPos.addEventListener('change', emitThermostatSplit);
    dom.thermoHysteresisNeg.addEventListener('change', emitThermostatSplit);

    const simpleNumberEmitters = [
      [dom.thermoMinOnS, 'set_min_on_s', value => parseInt(value, 10)],
      [dom.thermoMinOffS, 'set_min_off_s', value => parseInt(value, 10)],
      [dom.thermoPollS, 'set_poll_interval_s', value => parseInt(value, 10)],
      [dom.thermoSmoothWindow, 'set_smooth_window', value => parseInt(value, 10)],
      [dom.thermoMaxStaleS, 'set_max_stale_s', value => (value === '' ? null : parseInt(value, 10))],
    ];

    simpleNumberEmitters.forEach(([input, action, parser]) => {
      input.addEventListener('change', () => {
        const value = parser(input.value);
        if (value === null || Number.isFinite(value)) {
          emitSocketAction({ action, value });
        }
      });
    });

    dom.btnSleepDisableFor.addEventListener('click', () => {
      const minutes = parseInt(dom.sleepDisableMinutes.value, 10);
      if (Number.isFinite(minutes) && minutes > 0) {
        emitSocketAction({ action: 'disable_sleep_for', minutes });
      }
    });

    const emitSleepSchedule = debounce(() => {
      emitSocketAction({ action: 'set_sleep_schedule', schedule: buildSleepSchedule() });
    }, 250);

    DAYS.forEach(day => {
      const start = document.getElementById(`sleep_${day}_start`);
      const stop = document.getElementById(`sleep_${day}_stop`);
      if (start) {
        start.addEventListener('input', emitSleepSchedule);
        start.addEventListener('change', emitSleepSchedule);
      }
      if (stop) {
        stop.addEventListener('input', emitSleepSchedule);
        stop.addEventListener('change', emitSleepSchedule);
      }
    });

    dom.detailPrevDay.addEventListener('click', () => {
      state.currentDate = addDays(state.currentDate, -1);
      fetchRoomHistory();
    });

    dom.detailNextDay.addEventListener('click', () => {
      state.currentDate = addDays(state.currentDate, 1);
      fetchRoomHistory();
    });

    dom.detailClose.addEventListener('click', closeDetail);
    dom.detailBackdrop.addEventListener('click', closeDetail);

    window.addEventListener('resize', () => {
      if (!isMobileLayout()) {
        closeDetail();
      } else {
        updateDetailVisibility();
      }
    });
  }

  function initSocketHandlers() {
    if (!window.socket) return;

    window.socket.on('esp32_temphum', data => {
      console.log('📡 esp32_temphum', data);
      setRoom(data);
      renderAll();
      if (state.selectedKey === normalizeKey(data && (data.location || data.name))) {
        renderDetailShell(getSelectedRoom());
      }
      if (isOutsideLocation(data && (data.location || data.name))) {
        fetchOutsideToday();
      }
    });

    window.socket.on('ac_status', data => {
      console.log('📡 ac_status', data);
      syncAcStatus(data);
    });

    window.socket.on('ac_state', data => {
      console.log('📡 ac_state', data);
      syncAcStatus(data);
    });

    window.socket.on('thermostat_status', data => {
      console.log('📡 thermostat_status', data);
      syncAcStatus(data);
    });

    window.socket.on('sleep_status', data => {
      console.log('📡 sleep_status', data);
      syncAcStatus(data);
    });

    window.socket.on('thermo_config', data => {
      console.log('📡 thermo_config', data);
      syncAcStatus(data);
    });
  }

  function cacheDom() {
    Object.assign(dom, {
      overviewIndoorAvg: document.getElementById('overviewIndoorAvg'),
      overviewWarmest: document.getElementById('overviewWarmest'),
      overviewCoolest: document.getElementById('overviewCoolest'),
      overviewStale: document.getElementById('overviewStale'),
      overviewAcState: document.getElementById('overviewAcState'),
      overviewOutside: document.getElementById('overviewOutside'),
      roomCount: document.getElementById('roomCount'),
      roomSearchInput: document.getElementById('roomSearchInput'),
      filterIndoor: document.getElementById('filterIndoor'),
      filterOutdoor: document.getElementById('filterOutdoor'),
      filterStale: document.getElementById('filterStale'),
      sortName: document.getElementById('sortName'),
      sortTemp: document.getElementById('sortTemp'),
      sortHum: document.getElementById('sortHum'),
      sortUpdated: document.getElementById('sortUpdated'),
      sortDir: document.getElementById('sortDir'),
      roomList: document.getElementById('roomList'),
      roomListEmpty: document.getElementById('roomListEmpty'),
      outsideStripSection: document.getElementById('outsideStripSection'),
      outsideStrip: document.getElementById('outsideStrip'),
      outsideTempRange: document.getElementById('outsideTempRange'),
      outsideHumRange: document.getElementById('outsideHumRange'),
      detailPane: document.getElementById('detailPane'),
      detailBackdrop: document.getElementById('detailBackdrop'),
      detailClose: document.getElementById('detailClose'),
      detailRoomName: document.getElementById('detailRoomName'),
      detailRoomMeta: document.getElementById('detailRoomMeta'),
      detailFreshness: document.getElementById('detailFreshness'),
      detailCurrentTemp: document.getElementById('detailCurrentTemp'),
      detailCurrentHum: document.getElementById('detailCurrentHum'),
      detailDailyTempAvg: document.getElementById('detailDailyTempAvg'),
      detailDailyTempMin: document.getElementById('detailDailyTempMin'),
      detailDailyTempMax: document.getElementById('detailDailyTempMax'),
      detailDailyHumAvg: document.getElementById('detailDailyHumAvg'),
      detailDailyHumMin: document.getElementById('detailDailyHumMin'),
      detailDailyHumMax: document.getElementById('detailDailyHumMax'),
      detailPrevDay: document.getElementById('detailPrevDay'),
      detailNextDay: document.getElementById('detailNextDay'),
      detailDateLabel: document.getElementById('detailDateLabel'),
      detailTempChartMeta: document.getElementById('detailTempChartMeta'),
      detailHumChartMeta: document.getElementById('detailHumChartMeta'),
      detailChartTemp: document.getElementById('detailChartTemp'),
      detailChartHum: document.getElementById('detailChartHum'),
      detailEmptyState: document.getElementById('detailEmptyState'),
      acStatusPill: document.getElementById('acStatusPill'),
      thermoStatusPill: document.getElementById('thermoStatusPill'),
      sleepStatusPill: document.getElementById('sleepStatusPill'),
      btnAcPowerToggle: document.getElementById('btnAcPowerToggle'),
      btnSetpointDec: document.getElementById('btnSetpointDec'),
      btnSetpointInc: document.getElementById('btnSetpointInc'),
      setpointC: document.getElementById('setpointC'),
      modeCold: document.getElementById('modeCold'),
      modeWet: document.getElementById('modeWet'),
      modeWind: document.getElementById('modeWind'),
      fanLow: document.getElementById('fanLow'),
      fanHigh: document.getElementById('fanHigh'),
      btnThermoToggle: document.getElementById('btnThermoToggle'),
      btnSleepToggleMain: document.getElementById('btnSleepToggle-main'),
      avgCoolingPill: document.getElementById('avgCoolingPill'),
      avgHeatingPill: document.getElementById('avgHeatingPill'),
      ctrlLocContainer: document.getElementById('ctrlLocContainer'),
      thermoHysteresisNeg: document.getElementById('thermoHysteresisNeg'),
      thermoHysteresisPos: document.getElementById('thermoHysteresisPos'),
      thermoMinOnS: document.getElementById('thermoMinOnS'),
      thermoMinOffS: document.getElementById('thermoMinOffS'),
      thermoPollS: document.getElementById('thermoPollS'),
      thermoSmoothWindow: document.getElementById('thermoSmoothWindow'),
      thermoMaxStaleS: document.getElementById('thermoMaxStaleS'),
      btnSleepToggle: document.getElementById('btnSleepToggle'),
      sleepDisableMinutes: document.getElementById('sleepDisableMinutes'),
      btnSleepDisableFor: document.getElementById('btnSleepDisableFor'),
    });
  }

  function bootstrapRooms() {
    const locations = Array.isArray(BOOTSTRAP.locations) ? BOOTSTRAP.locations : [];
    locations.forEach(setRoom);
    const initial = chooseInitialRoom();
    state.selectedKey = initial ? initial.key : null;
  }

  document.addEventListener('DOMContentLoaded', () => {
    cacheDom();
    bootstrapRooms();
    renderAll();
    bindControls();
    initSocketHandlers();
    fetchAcStatus();
    fetchAvgRates();
    fetchOutsideToday();
    fetchRoomHistory();
    setInterval(fetchAvgRates, 5 * 60 * 1000);
    setInterval(fetchOutsideToday, 5 * 60 * 1000);
  });
})();
