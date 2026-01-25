console.log('🧊 temperatures.js loaded');

const STALE_MINUTES = 10; // mark readings stale when older than this
const STALE_MS = STALE_MINUTES * 60 * 1000;
const OUTSIDE_LOCATION_NAME = 'Outside'; // treated as outside sensor (FMI weather data)

const state = {
  items: [],  // { name, temp, hum, ts }
  filter: '',
  sortKey: 'name', // 'name' | 'temp' | 'hum' | 'updated'
  sortDir: 'asc',  // 'asc' | 'desc'
};

function slugify(s){
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/(^-|-$)/g,'');
}

function fmtTemp(v){
  const n = Number(v);
  return Number.isFinite(n) ? `${n.toFixed(1)} °C` : '-';
}
function fmtHum(v){
  const n = Number(v);
  return Number.isFinite(n) ? `${n.toFixed(0)} %` : '-';
}

function fmtTime(ts){
  // Returns a readable local timestamp without a label
  if (!ts) return '—';
  try {
    const d = ts instanceof Date ? ts : new Date(ts);
    // Prefer a concise local string; include date + time
    return d.toLocaleString(undefined, {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
  } catch {
    return '—';
  }
}

function createTile(id, name, temp=null, hum=null, ts=null){
  const tile = document.createElement('div');
  tile.className = 'tile';
  tile.id = id;
  tile.innerHTML = `
    <div class="loc"><span class="fresh-dot dot-stale" aria-hidden="true"></span><span class="loc-name"></span></div>
    <div class="measures">
      <span class="measure temp">-</span>
      <span class="sep">·</span>
      <span class="measure hum">-</span>
    </div>
    <div class="timestamp">—</div>
  `;
  tile.querySelector('.loc-name').textContent = name || '—';
  tile.querySelector('.temp').textContent = fmtTemp(temp);
  tile.querySelector('.hum').textContent  = fmtHum(hum);
  const tsEl = tile.querySelector('.timestamp');
  if (tsEl) tsEl.textContent = fmtTime(ts);

  // Click handler opens charts for this location
  tile.addEventListener('click', (e) => {
    e.stopPropagation();
    console.log(`🖱️ Tile clicked: ${name} (id=${id})`);
    if (typeof openAndRender === 'function') {
      openAndRender(name);
    } else if (typeof window.openChartsForLocation === 'function') {
      window.openChartsForLocation(name);
    }
  });

  return tile;
}

function extractNameTempHum(entry){
  // Supports both initial payload {location|name, temp, hum}
  // and live events {location|name, temperature|temperature_c}, {humidity|humidity_pct}
  if (entry == null) return { name: '—', temp: null, hum: null };

  if (typeof entry === 'string') {
    return { name: entry.trim(), temp: null, hum: null };
  }

  const name = String(entry.location || entry.name || '—').trim();

  const temp = (
    entry.temp ??
    entry.temperature_c ??
    entry.temperature ??
    null
  );

  const hum = (
    entry.hum ??
    entry.humidity_pct ??
    entry.humidity ??
    null
  );

  const ts = (entry.timestamp ?? entry.ts ?? null);

  return { name, temp, hum, ts };
}


function renderItems(locations){
  const grid = document.getElementById('locationsGrid');
  const outGrid = document.getElementById('outsideGrid');
  if(!grid) return;

  // allow passing either raw list or using state
  const locs = Array.isArray(locations) ? locations : (state.items || []);
  grid.innerHTML = '';
  if (outGrid) outGrid.innerHTML = '';

  // Apply filter
  const f = (state.filter || '').trim().toLowerCase();
  let list = locs.map(extractNameTempHum);
  if (f) list = list.filter(x => (x.name || '').toLowerCase().includes(f));

  // Apply sort
  const key = state.sortKey;
  const dir = state.sortDir === 'desc' ? -1 : 1;
  list.sort((a,b) => {
    const av = key === 'name' ? (a.name||'')
               : key === 'temp' ? (Number(a.temp))
               : key === 'hum' ? (Number(a.hum))
               : Date.parse(a.ts || 0);
    const bv = key === 'name' ? (b.name||'')
               : key === 'temp' ? (Number(b.temp))
               : key === 'hum' ? (Number(b.hum))
               : Date.parse(b.ts || 0);
    if (key === 'name') return av.localeCompare(bv) * dir;
    const an = Number(av), bn = Number(bv);
    if (!Number.isFinite(an) && !Number.isFinite(bn)) return 0;
    if (!Number.isFinite(an)) return 1 * dir;
    if (!Number.isFinite(bn)) return -1 * dir;
    return (an - bn) * dir;
  });

  // Split into inside vs outside (Parveke)
  const outsideList = list.filter(x => (x.name || '').trim() === OUTSIDE_LOCATION_NAME);
  const insideList  = list.filter(x => (x.name || '').trim() !== OUTSIDE_LOCATION_NAME);

  // Render inside tiles to main grid
  for (const loc of insideList){
    const { name, temp, hum, ts } = loc;
    const id = 'loc-' + slugify(name || 'default');
    const tile = createTile(id, name, temp, hum, ts);
    applyFreshness(tile, ts);
    grid.appendChild(tile);
  }
  // Render outside tiles (if any) to outside grid
  if (outGrid){
    for (const loc of outsideList){
      const { name, temp, hum, ts } = loc;
      const id = 'loc-' + slugify(name || 'default');
      const tile = createTile(id, name, temp, hum, ts);
      applyFreshness(tile, ts);
      outGrid.appendChild(tile);
    }
  }
}

function ensureTile(name){
  const id = 'loc-' + slugify(name || 'default');
  let tile = document.getElementById(id);
  if (!tile) {
    const grid = document.getElementById('locationsGrid');
    if (!grid) return null;
    tile = createTile(id, name);
    grid.appendChild(tile);
  } else {
    const le = tile.querySelector('.loc-name');
    if (le) le.textContent = name || '—';
  }
  return tile;
}

function updateTile(data){
  const { name, temp, hum, ts } = extractNameTempHum(data);
  if (!name) return;
  // Update state item
  let found = false;
  for (let i=0; i<state.items.length; i++){
    const it = state.items[i];
    const nm = String(it.location || it.name || '').trim();
    if (nm === name){
      state.items[i] = { name, temp, hum, ts: ts || new Date().toISOString() };
      found = true;
      break;
    }
  }
  if (!found){
    state.items.push({ name, temp, hum, ts: ts || new Date().toISOString() });
  }
  // Re-render grid and summary to respect sorting/filtering
  renderItems(state.items);
  updateSummary(state.items);
  // If outside updated, refresh outside stats as well
  if ((name || '').trim() === OUTSIDE_LOCATION_NAME){
    refreshOutsideStatsSoon();
  }
}

function initSIO(){
  socket.on('esp32_temphum', data => {
    console.log('📡 Received esp32_temphum:', data);
    updateTile(data);
    // Optionally refresh averages on new data bursts
    refreshAvgRatesSoon();
  });
  socket.on('ac_status', data => {
    console.log('📡 Received ac_status:', data);
    if (!data) return;
    updateACIndicator(data.is_on);
  });
  socket.on('ac_state', data => {
    console.log('📡 Received ac_state:', data);
    if (!data) return;
    if (data.mode) setModeUI(data.mode);
    if (data.fan_speed) setFanUI(data.fan_speed);
  });
  socket.on('thermostat_status', data => {
    console.log('📡 Received thermostat_status:', data);
    if (!data) return;
    const enabled = (data && 'thermo_active' in data)
      ? !!data.thermo_active
      : !!data.enabled;
    updateThermoIndicator(enabled);
  });
  socket.on('sleep_status', data => {
    console.log('📡 Received sleep_status:', data);
    if (!data) return;
    setSleepUI(data);
  });
  socket.on('thermo_config', data => {
    console.log('📡 Received thermo_config:', data);
    if (!data) return;
    setThermoConfigUI(data);
  });
}

async function fetchACStatus(){
  try{
    const resp = await fetch('/api/ac/status');
    if(!resp.ok){
      console.warn('AC status fetch failed:', resp.status);
      updateACIndicator(null);
      updateThermoIndicator(null);
      return;
    }
    const data = await resp.json();
    updateACIndicator(data && 'is_on' in data ? data.is_on : null);
    // Prefer new DB-backed thermo_active, fallback to legacy thermostat_enabled
    const thermoEn = (data && 'thermo_active' in data)
      ? data.thermo_active
      : (data && 'thermostat_enabled' in data ? data.thermostat_enabled : null);
    updateThermoIndicator(thermoEn);
    if (data && data.mode) setModeUI(data.mode);
    if (data && data.fan_speed) setFanUI(data.fan_speed);
    setSleepUI(data);
    setThermoConfigUI(data);
  }catch(err){
    console.error('AC status error:', err);
    updateACIndicator(null);
    updateThermoIndicator(null);
  }
}

function updateACIndicator(isOn){
  const pill = document.getElementById('acStatusPill');
  const btn  = document.getElementById('btnAcPowerToggle');
  if(!pill) return;
  pill.classList.remove('ac-on','ac-off','ac-unknown');
  if(isOn === true){
    pill.classList.add('ac-on');
    pill.textContent = 'ON';
    if (btn) btn.textContent = 'Turn AC Off';
  } else if(isOn === false){
    pill.classList.add('ac-off');
    pill.textContent = 'OFF';
    if (btn) btn.textContent = 'Turn AC On';
  } else {
    pill.classList.add('ac-unknown');
    pill.textContent = 'Unknown';
    if (btn) btn.textContent = 'Toggle AC';
  }
}

function updateThermoIndicator(enabled){
  const pill = document.getElementById('thermoStatusPill');
  const btn  = document.getElementById('btnThermoToggle');
  if(pill){
    pill.classList.remove('ac-on','ac-off','ac-unknown');
    if(enabled === true){
      pill.classList.add('ac-on');
      pill.textContent = 'Thermostat ON';
    } else if(enabled === false){
      pill.classList.add('ac-off');
      pill.textContent = 'Thermostat OFF';
    } else {
      pill.classList.add('ac-unknown');
      pill.textContent = 'Thermostat —';
    }
  }
  if(btn){
    btn.textContent = (enabled === true) ? 'Disable Thermostat' : 'Enable Thermostat';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const locations = Array.isArray(window.LOCATIONS) ? window.LOCATIONS : [];
  // normalize into state
  state.items = locations.map(extractNameTempHum);
  renderItems(state.items);
  updateSummary(state.items);
  initSIO();
  fetchACStatus();
  fetchAvgRates();
  fetchOutsideToday();
  // Periodic refresh every 5 minutes
  setInterval(fetchAvgRates, 5 * 60 * 1000);
  setInterval(fetchOutsideToday, 5 * 60 * 1000);

  // Wire control buttons
  const btnAc = document.getElementById('btnAcPowerToggle');
  const btnTh = document.getElementById('btnThermoToggle');
  if(btnAc){
    btnAc.addEventListener('click', () => {
      const pill = document.getElementById('acStatusPill');
      const isOn = pill && pill.classList.contains('ac-on');
      socket.emit('ac_control', { action: isOn ? 'power_off' : 'power_on' });
    });
  }
  if(btnTh){
    btnTh.addEventListener('click', () => {
      const pill = document.getElementById('thermoStatusPill');
      const enabled = pill && pill.classList.contains('ac-on');
      socket.emit('ac_control', { action: enabled ? 'thermostat_disable' : 'thermostat_enable' });
    });
  }

  // Wire segmented controls
  const modeCold = document.getElementById('modeCold');
  const modeWet  = document.getElementById('modeWet');
  const modeWind = document.getElementById('modeWind');
  const fanLow   = document.getElementById('fanLow');
  const fanHigh  = document.getElementById('fanHigh');

  // UI helpers defined at top-level

  if (modeCold) modeCold.addEventListener('click', () => { socket.emit('ac_control', { action: 'set_mode', value: 'cold' }); setModeUI('cold'); });
  if (modeWet)  modeWet.addEventListener('click',  () => { socket.emit('ac_control', { action: 'set_mode', value: 'wet'  }); setModeUI('wet');  });
  if (modeWind) modeWind.addEventListener('click', () => { socket.emit('ac_control', { action: 'set_mode', value: 'wind' }); setModeUI('wind'); });
  if (fanLow)   fanLow.addEventListener('click',   () => { socket.emit('ac_control', { action: 'set_fan_speed', value: 'low'  }); setFanUI('low');  });
  if (fanHigh)  fanHigh.addEventListener('click',  () => { socket.emit('ac_control', { action: 'set_fan_speed', value: 'high' }); setFanUI('high'); });

  // Filter & sort
  const filterInput = document.getElementById('filterInput');
  const sortNameBtn = document.getElementById('sortName');
  const sortTempBtn = document.getElementById('sortTemp');
  const sortHumBtn  = document.getElementById('sortHum');
  const sortUpdBtn  = document.getElementById('sortUpdated');
  const sortDirBtn  = document.getElementById('sortDir');

  function setSortKey(key){
    state.sortKey = key;
    [sortNameBtn, sortTempBtn, sortHumBtn, sortUpdBtn].forEach(b => b && b.classList.toggle('active', b.dataset.key === key));
    renderItems(state.items);
  }
  function toggleSortDir(){
    state.sortDir = (state.sortDir === 'asc') ? 'desc' : 'asc';
    sortDirBtn.textContent = (state.sortDir === 'asc') ? '↑' : '↓';
    renderItems(state.items);
  }
  if (filterInput){
    filterInput.addEventListener('input', () => { state.filter = filterInput.value || ''; renderItems(state.items); });
  }
  if (sortNameBtn) sortNameBtn.addEventListener('click', () => setSortKey('name'));
  if (sortTempBtn) sortTempBtn.addEventListener('click', () => setSortKey('temp'));
  if (sortHumBtn)  sortHumBtn.addEventListener('click', () => setSortKey('hum'));
  if (sortUpdBtn)  sortUpdBtn.addEventListener('click', () => setSortKey('updated'));
  if (sortDirBtn)  sortDirBtn.addEventListener('click', toggleSortDir);
  // initialize sort UI
  setSortKey(state.sortKey);
  if (sortDirBtn) sortDirBtn.textContent = (state.sortDir === 'asc') ? '↑' : '↓';


  const btnSleepToggle = document.getElementById('btnSleepToggle-main');
  const sleepPill    = document.getElementById('sleepStatusPill');

  if (btnSleepToggle){
    btnSleepToggle.addEventListener('click', () => {
      const enabled = /enable/i.test(btnSleepToggle.textContent || '');
      socket.emit('ac_control', { action: 'set_sleep_enabled', value: enabled });
      btnSleepToggle.textContent = enabled ? 'Disable Sleep' : 'Enable Sleep';
    });
  }



  // Prefer modal fields when present
  const sp = document.getElementById('modalSetpointC')
  const spMain = document.getElementById('setpointC');
  const hy = document.getElementById('hysteresis') || document.getElementById('modalHysteresis');
  const hyPos = document.getElementById('modalHysteresisPos') || document.getElementById('hysteresisPos');
  const hyNeg = document.getElementById('modalHysteresisNeg') || document.getElementById('hysteresisNeg');
  const btnThermoSave = document.getElementById('btnThermoSave');
  const btnSpDec = document.getElementById('btnSetpointDec');
  const btnSpInc = document.getElementById('btnSetpointInc');
  if (spMain) {
    spMain.addEventListener('change', () => {
      const v = parseFloat(spMain.value);
      if (!Number.isNaN(v)) socket.emit('ac_control', { action: 'set_setpoint', value: v });
    });

  }
  if (btnThermoSave && sp){
    btnThermoSave.addEventListener('click', () => {
      const setpoint = parseFloat(sp.value);
      if (!Number.isNaN(setpoint)){
        socket.emit('ac_control', { action: 'set_setpoint', value: setpoint });
      }
      const hasSplit = hyPos && hyNeg;
      if (hasSplit){
        const pos = parseFloat(hyPos.value);
        const neg = parseFloat(hyNeg.value);
        if (!Number.isNaN(pos) && !Number.isNaN(neg)){
          socket.emit('ac_control', { action: 'set_hysteresis_split', pos, neg });
        }
      } else if (hy) {
        const hyst = parseFloat(hy.value);
        if (!Number.isNaN(hyst)){
          socket.emit('ac_control', { action: 'set_hysteresis', value: hyst });
        }
      }
    });
  }
  // Quick +/- for setpoint
  function clamp(n, min, max){ return Math.min(max, Math.max(min, n)); }
  function stepSetpoint(delta){
    if (!sp) return;
    const cur = parseFloat(sp.value);
    const step = parseFloat(sp.step || '0.5') || 0.5;
    const min = parseFloat(sp.min || '5') || 5;
    const max = parseFloat(sp.max || '35') || 35;
    const base = Number.isFinite(cur) ? cur : 22.0;
    const next = clamp(base + delta*step, min, max);
    sp.value = next.toFixed(1);
    socket.emit('ac_control', { action: 'set_setpoint', value: next });
  }
  if (btnSpDec) btnSpDec.addEventListener('click', () => stepSetpoint(-1));
  if (btnSpInc) btnSpInc.addEventListener('click', () => stepSetpoint(+1));

});

function setSelectValue(id, value){
  const el = document.getElementById(id);
  if(!el) return;
  const val = String(value || '').toLowerCase();
  for (const opt of el.options){
    if (opt.value === val){ el.value = val; return; }
  }
}

// Segmented control helpers (top-level so socket handlers can call them)
function setActiveBtns(btns, val){
  const v = String(val || '').toLowerCase();
  (btns || []).forEach(b => {
    if (!b) return;
    const match = (String(b.dataset.value || '').toLowerCase() === v);
    b.classList.toggle('active', match);
  });
}
function setModeUI(mode){
  const cold = document.getElementById('modeCold');
  const wet  = document.getElementById('modeWet');
  const wind = document.getElementById('modeWind');
  setActiveBtns([cold, wet, wind], mode);
}
function setFanUI(fan){
  const low  = document.getElementById('fanLow');
  const high = document.getElementById('fanHigh');
  setActiveBtns([low, high], fan);
}

function setSleepUI(data){
  if (!data) return;
  const pill = document.getElementById('sleepStatusPill');
  if (!pill) return;
  const enabled = ('sleep_enabled' in data) ? !!data.sleep_enabled : null;
  const activeNow = ('sleep_time_active' in data) ? !!data.sleep_time_active : null;
  const sleepOverrideUntil = ('sleep_override_until' in data) ? data.sleep_override_until : null;

  const btnSleepToggleMain = document.getElementById('btnSleepToggle-main');
  const btnSleepToggle = document.getElementById('btnSleepToggle');
  if (btnSleepToggle) { btnSleepToggle.textContent = enabled ? 'Disable Sleep' : 'Enable Sleep'; }
  if (btnSleepToggleMain) { btnSleepToggleMain.textContent = enabled ? 'Disable Sleep' : 'Enable Sleep'; }

  pill.classList.remove('ac-on','ac-off','ac-unknown','ac-idle');

  if (enabled === false){
    // Disabled -> red
    pill.classList.add('ac-off');
    pill.textContent = 'Sleep OFF';
    return;
  }
  if (enabled === true){
    if (activeNow === true){
      // Enabled and in window -> green
      pill.classList.add('ac-on');
      pill.textContent = 'Sleep NOW';
    } else if (activeNow === false){
      // Enabled but not in window -> yellow
      pill.classList.add('ac-idle');
      pill.textContent = 'Sleep Enabled' + (sleepOverrideUntil ? ` - ${asTimeValue(sleepOverrideUntil)}` : '');
    } else {
      pill.classList.add('ac-unknown');
      pill.textContent = 'Sleep —';
    }
    return;
  }
  // Unknown state
  pill.classList.add('ac-unknown');
  pill.textContent = 'Sleep —';
}

function asTimeValue(s){
  // Accept HH:MM, optional seconds; normalize to HH:MM
  if (!s) return '';
  try{
    const m = String(s).match(/^(\d{1,2}):(\d{2})/);
    if (!m) return '';
    const hh = String(m[1]).padStart(2,'0');
    const mm = String(m[2]).padStart(2,'0');
    return `${hh}:${mm}`;
  }catch{ return ''; }
}

function setThermoConfigUI(data){
  const spMain = document.getElementById('setpointC');
  const spModal = document.getElementById('modalSetpointC');
  const hyPosMain = document.getElementById('hysteresisPos');
  const hyNegMain = document.getElementById('hysteresisNeg');
  const hyPosModal = document.getElementById('modalHysteresisPos');
  const hyNegModal = document.getElementById('modalHysteresisNeg');
  const minOnEl = document.getElementById('modalMinOnS');
  const minOffEl= document.getElementById('modalMinOffS');
  const pollEl  = document.getElementById('modalPollS');
  const smoothEl= document.getElementById('modalSmoothWindow');
  const staleEl = document.getElementById('modalMaxStaleS');
  if ('setpoint_c' in data){
    const v = parseFloat(data.setpoint_c);
    if (!Number.isNaN(v)){
      if (spMain) spMain.value = v.toFixed(1);
      if (spModal) spModal.value = v.toFixed(1);
    }
  }
  if ('pos_hysteresis' in data){
    const v = parseFloat(data.pos_hysteresis);
    if (!Number.isNaN(v)){
      if (hyPosMain) hyPosMain.value = v.toFixed(1);
      if (hyPosModal) hyPosModal.value = v.toFixed(1);
    }
  }
  if ('neg_hysteresis' in data){
    const v = parseFloat(data.neg_hysteresis);
    if (!Number.isNaN(v)){
      if (hyNegMain) hyNegMain.value = v.toFixed(1);
      if (hyNegModal) hyNegModal.value = v.toFixed(1);
    }
  }
  if ('min_on_s' in data && minOnEl) minOnEl.value = parseInt(data.min_on_s)||0;
  if ('min_off_s' in data && minOffEl) minOffEl.value = parseInt(data.min_off_s)||0;
  if ('poll_interval_s' in data && pollEl) pollEl.value = parseInt(data.poll_interval_s)||15;
  if ('smooth_window' in data && smoothEl) smoothEl.value = parseInt(data.smooth_window)||1;
  if ('max_stale_s' in data && staleEl) staleEl.value = (data.max_stale_s == null ? '' : parseInt(data.max_stale_s));
}

// --- Avg rates / power ---
let _avgRatesTimer = null;
function refreshAvgRatesSoon(){
  if (_avgRatesTimer) return;
  _avgRatesTimer = setTimeout(() => { _avgRatesTimer = null; fetchAvgRates(); }, 3000);
}

async function fetchAvgRates(){
  try{
    const resp = await fetch('/api/hvac/avg_rates_today');
    if(!resp.ok){
      console.warn('avg rates fetch failed:', resp.status);
      setAvgPills(null);
      return;
    }
    const data = await resp.json();
    setAvgPills(data);
  }catch(err){
    console.error('avg rates error:', err);
    setAvgPills(null);
  }
}

function setAvgPills(data){
  const coolEl = document.getElementById('avgCoolingPill');
  const heatEl = document.getElementById('avgHeatingPill');
  if(!coolEl || !heatEl) return;

  if(!data){
    coolEl.textContent = 'Cooling: —';
    heatEl.textContent = 'Heat: —';
    return;
  }
  const cr = data.cooling_rate_c_per_h;
  const hr = data.heating_rate_c_per_h;
  const cp = data.cooling_power_w;
  const hp = data.heating_power_w;
  const fmtRate = (v, sign='+/-') => {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    return `${n.toFixed(2)} °C/h`;
  };
  const fmtPower = (w) => {
    if (w === null || w === undefined) return '';
    const n = Number(w);
    if (!Number.isFinite(n)) return '';
    return ` (${Math.round(n)} W)`;
  };
  // Cooling rate is typically negative; display absolute magnitude
  const coolText = (cr == null ? '—' : `${Math.abs(cr).toFixed(2)} °C/h`) + (cp != null ? ` (${Math.round(cp)} W)` : '');
  const heatText = fmtRate(hr) + fmtPower(hp);
  coolEl.textContent = `Cooling: ${coolText}`;
  heatEl.textContent = `Heat: ${heatText}`;
}

// --- Summary (Avg/Min/Max across tiles) ---
function updateSummary(items){
  const avgEl = document.getElementById('avgTempPill');
  const minEl = document.getElementById('minTempPill');
  const maxEl = document.getElementById('maxTempPill');
  if (!avgEl || !minEl || !maxEl) return;

  // Exclude outside sensor from summary
  const inItems = (Array.isArray(items) ? items : []).filter(it => {
    const n = String(it.name || it.location || '').trim();
    return n !== OUTSIDE_LOCATION_NAME;
  });

  const vals = inItems.map(x => Number(x.temp)).filter(Number.isFinite);
  if (!vals.length){
    avgEl.textContent = 'AVG: —';
    minEl.textContent = 'Min: —';
    maxEl.textContent = 'Max: —';
    return;
  }
  const avg = vals.reduce((a,b)=>a+b,0) / vals.length;
  let minV = Infinity, maxV = -Infinity, minN='—', maxN='—';
  for (const it of inItems){
    const t = Number(it.temp);
    if (Number.isFinite(t)){
      if (t < minV){ minV = t; minN = it.name || it.location || '—'; }
      if (t > maxV){ maxV = t; maxN = it.name || it.location || '—'; }
    }
  }
  avgEl.textContent = `AVG: ${avg.toFixed(1)}°C`;
  minEl.textContent = `Min: ${Number.isFinite(minV) ? minV.toFixed(1)+'°C' : '—'} (${minN})`;
  maxEl.textContent = `Max: ${Number.isFinite(maxV) ? maxV.toFixed(1)+'°C' : '—'} (${maxN})`;
}

function applyFreshness(tile, ts){
  try{
    const dot = tile.querySelector('.fresh-dot');
    const t = ts ? new Date(ts).getTime() : NaN;
    const fresh = Number.isFinite(t) && (Date.now() - t) <= STALE_MS;
    tile.classList.toggle('stale', !fresh);
    if (dot){
      dot.classList.remove('dot-fresh','dot-stale');
      dot.classList.add(fresh ? 'dot-fresh' : 'dot-stale');
    }
  }catch{}
}

function applyTempColor(el, temp){
  if (!el) return;
  const n = Number(temp);
  el.classList.remove('temp-cold','temp-good','temp-warm','temp-hot');
  if (!Number.isFinite(n)) return;
  if (n <= 19) el.classList.add('temp-cold');
  else if (n <= 24) el.classList.add('temp-good');
  else if (n <= 27) el.classList.add('temp-warm');
  else el.classList.add('temp-hot');
}

// --- Outside (Parveke) daily stats ---
let _outsideTimer = null;
function refreshOutsideStatsSoon(){
  if (_outsideTimer) return;
  _outsideTimer = setTimeout(() => { _outsideTimer = null; fetchOutsideToday(); }, 2000);
}

async function fetchOutsideToday(){
  try{
    const url = `/api/esp32_temphum?location=${encodeURIComponent(OUTSIDE_LOCATION_NAME)}`;
    const resp = await fetch(url);
    if(!resp.ok){
      console.warn('outside stats fetch failed:', resp.status);
      setOutsideStats(null);
      return;
    }
    const data = await resp.json();
    setOutsideStats(data);
  }catch(err){
    console.error('outside stats error:', err);
    setOutsideStats(null);
  }
}

function setOutsideStats(rows){
  const tEl = document.getElementById('outsideTempRange');
  const hEl = document.getElementById('outsideHumRange');
  if (!tEl || !hEl) return;

  if (!Array.isArray(rows) || rows.length === 0){
    tEl.textContent = 'Temp: —';
    hEl.textContent = 'Hum: —';
    return;
  }
  let tMin = +Infinity, tMax = -Infinity;
  let hMin = +Infinity, hMax = -Infinity;
  for (const r of rows){
    const t = Number(r && (r.temperature_c ?? r.temperature));
    const h = Number(r && (r.humidity_pct ?? r.humidity));
    if (Number.isFinite(t)){
      if (t < tMin) tMin = t;
      if (t > tMax) tMax = t;
    }
    if (Number.isFinite(h)){
      if (h < hMin) hMin = h;
      if (h > hMax) hMax = h;
    }
  }
  const tOk = Number.isFinite(tMin) && Number.isFinite(tMax);
  const hOk = Number.isFinite(hMin) && Number.isFinite(hMax);
  tEl.textContent = tOk ? `Temp: ${tMin.toFixed(1)} - ${tMax.toFixed(1)}°C` : 'Temp: —';
  hEl.textContent = hOk ? `Hum: ${Math.round(hMin)} - ${Math.round(hMax)}%` : 'Hum: —';
}
