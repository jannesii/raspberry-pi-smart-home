/* THROWAWAY, synthetic data only. No fetch, Socket.IO, device calls or persistence. */
'use strict';
const params = new URLSearchParams(location.search);
const layouts = { A: 'Departure first + timeline', B: 'Timeline workspace', C: 'Control console' };
const scenarios = ['scheduled', 'heating', 'disconnected', 'review', 'capture'];
let variant = Object.hasOwn(layouts, params.get('variant')) ? params.get('variant') : 'A';
let state;
let stopTimer;
let restartTarget;
const workspace = document.querySelector('#workspace');
const button = (text, action, cls = '', disabled = false) => `<button type="button" class="${cls}" data-action="${action}" ${disabled ? 'disabled' : ''}>${text}</button>`;
function reset(scenario) {
  clearTimeout(stopTimer);
  state = { scenario, view: scenario === 'review' ? 'review' : scenario === 'capture' ? 'capture' : 'heating', relay: ['heating', 'review', 'capture'].includes(scenario) ? 'on' : scenario === 'disconnected' ? 'unknown' : 'off', mode: 'departure', scheduled: true, date: '2026-10-08', time: '07:00', duration: 60, capturing: false, nextPhoto: 600, photos: 3, lens: '0.5×', outcome: 'Clear · no scraping', session: 'latest', toast: '', target: 15, hysteresis: 1, frequency: 10 };
  syncUrl();
  render();
}
function syncUrl() {
  const url = new URL(location.href);
  url.searchParams.set('variant', variant);
  url.searchParams.set('state', state.scenario);
  history.replaceState(null, '', url);
}
function heaterStatus() {
  if (state.relay === 'unknown') return '<div class="status unknown"><span class="dot"></span>Heater state unknown</div>';
  if (state.relay === 'stopping') return '<div class="status active"><span class="dot"></span>Stopping… awaiting device confirmation</div>';
  return `<div class="status ${state.relay === 'on' ? 'active' : ''}"><span class="dot"></span>${state.relay === 'on' ? `${state.mode === 'manual' ? 'Manual heating' : state.mode === 'comfort' ? 'Comfort heating' : 'Heating'} · confirmed ON` : 'Heater OFF · confirmed'}</div>`;
}
function lockReason() { return state.relay === 'unknown' ? 'Unavailable until the device reconnects and confirms its state.' : state.relay === 'stopping' ? 'Wait for confirmed OFF before starting another mode.' : state.relay === 'on' ? 'Stop the active session before starting another heating mode.' : ''; }
function tabs() { return `<nav class="tabs" aria-label="Car heater">${['Heating', 'Sessions', 'Settings'].map(label => `<button data-action="view-${label.toLowerCase()}" ${state.view === label.toLowerCase() || (label === 'Sessions' && ['review','capture','outcome'].includes(state.view)) ? 'aria-current="page"' : ''}>${label}</button>`).join('')}</nav>`; }
function header() { return `<header class="page-top"><div><p class="eyebrow">Your car · DEFA Termini II 1700</p><h1>Car Heater</h1></div>${tabs()}</header>`; }
function timeAt(offset) {
  const [hours, minutes] = state.time.split(':').map(Number);
  const total = (hours * 60 + minutes + offset + 1440) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}
function timeline() {
  return `<div class="timeline"><div><small>Heating starts</small><strong>${timeAt(-150)}</strong></div><div><small>Departure</small><strong>${state.time}</strong></div><div><small>Latest finish</small><strong>${timeAt(30)}</strong></div></div>`;
}
function stopControl() {
  return button(state.relay === 'stopping' ? 'Stopping…' : state.relay === 'unknown' ? 'Request OFF' : 'Stop heating', 'stop', 'danger', state.relay === 'stopping');
}
function departure() {
  const active = state.relay === 'on';
  return `<section class="departure"><div><p class="eyebrow">${state.scheduled ? 'Next departure' : 'Plan your next departure'}</p><div class="big-time">${state.time}</div><p class="departure-date">${new Intl.DateTimeFormat('en-GB', { day:'numeric', month:'short', year:'numeric', timeZone:'Europe/Helsinki' }).format(new Date(`${state.date}T12:00:00+03:00`))}</p><div style="margin-top:20px">${heaterStatus()}</div><p class="support">${state.relay === 'unknown' ? 'Device last seen 18 minutes ago.' : 'Device updated 12 seconds ago · sample clock 06:10'}</p></div>${timeline()}</section>
  ${state.relay === 'unknown' ? '<div class="notice bad">Connection lost. The last reported state was ON. OFF has not been confirmed; current measurements are unavailable.</div>' : active ? `<div class="notice">${state.mode !== 'departure' ? `${state.activeDuration} min remaining in this sample ${state.mode} session · latest finish ${state.manualFinish} · no restart extends this limit.` : `80 min until the latest finish. Departure heating lasts 150 min, with up to 30 min of grace within the 180 min limit.`}</div>` : state.relay === 'stopping' ? '<div class="notice">OFF requested. Heating may still be running until the device confirms.</div>' : `<p class="micro">FMI sample observation −6.0°C · observed 05:55 · duration rule: 150 min.<br>Finish includes up to 30 min of departure grace. Clearing is not guaranteed.</p>`}
  ${state.relay !== 'off' ? `<div class="actions">${stopControl()}${button('Observe windshield', 'capture')}</div>` : ''}`;
}
function plan() {
  return `<section class="section"><h2>Departure plan</h2><div class="form-row"><label>Date<input id="date" type="date" value="${state.date}"></label><label>Time<input id="time" type="text" inputmode="numeric" pattern="([01][0-9]|2[0-3]):[0-5][0-9]" maxlength="5" aria-label="Departure time, 24-hour HH:MM" value="${state.time}"></label>${button('Tomorrow', 'tomorrow')}</div><p class="support">Preview: ${timeAt(-150)} → ${state.time} departure → ${timeAt(30)} latest finish.</p><div class="actions">${button(state.scheduled ? 'Save changes' : 'Schedule heating', 'schedule', 'primary', state.relay !== 'off')}${button('Cancel plan', 'cancel-plan', '', !state.scheduled || state.relay !== 'off')}</div>${lockReason() ? `<p class="manual-help">${lockReason()}</p>` : '<p class="support">One upcoming departure · local time in Helsinki.</p>'}</section>`;
}
function manual() {
  return `<section class="section"><h2>Manual heating</h2><p>Choose a duration. Heating stops at the limit.</p><div class="duration-row">${[30,60,120,180].map(n => button(`${n} min`, `duration-${n}`, state.duration === n ? 'selected' : '')).join('')}</div>${button(`Start ${state.duration} min`, 'start', 'primary', state.relay !== 'off')}<p class="manual-help">${lockReason() || 'Maximum continuous heating: 180 minutes.'}</p><details class="comfort"><summary>Comfort heating</summary><p class="manual-help">Maintain cabin temperature while this mode is active. The continuous heating limit still applies.</p><label>Target °C<input id="target" type="number" value="${state.target}" min="-10" max="30"></label><div class="actions">${button('Start comfort heating', 'comfort', '', state.relay !== 'off')}</div></details></section>`;
}
function measurements() {
  const missing = state.relay === 'unknown';
  return `<section class="section"><h2>Conditions</h2><div class="weather"><div><strong>−6.0°C</strong><p>Outside · FMI observed 05:55</p></div><p>Wind 3.1 m/s<br>Duration rule 150 min</p></div><div class="measurements" style="margin-top:22px">${[['Cabin', '8.2°C'],['Glass · driver', '−0.8°C'],['Glass · center', '0.4°C']].map(([label,value])=>`<div class="measurement"><small>${label}</small><strong>${missing ? '—' : value}</strong></div>`).join('')}</div><p class="support">Humidity ${missing ? '—' : '72%'} · heater power ${missing ? '—' : state.relay === 'off' ? '0 W' : '1,502 W'}<br>Glass indicator is experimental; confirm clearing visually.</p>${button('Review current session →', 'review', 'link')}</section>`;
}
function VariantA() { return `<div class="split"><div>${departure()}${plan()}</div><aside class="side">${manual()}${measurements()}</aside></div>`; }
function VariantB() { return `${departure()}<div class="lower">${plan()}${manual()}${measurements()}</div>`; }
function VariantC() { return `<div class="control-plane">${departure()}</div><div class="lower">${manual()}${plan()}</div>${measurements()}`; }
function sessions() {
  const rows = [['latest','8 Oct · 07:00','150 min heating',state.outcome,'3 sample photos'],['missing','7 Oct · 07:30','120 min heating','Outcome not recorded','No photos'],['previous','6 Oct · 07:00','180 min heating','Scraping needed','5 sample photos']];
  return `<div class="list-heading"><div><p class="eyebrow">Your winter observations</p><h2>Sessions</h2></div><label>Filter by date<input id="filterDate" type="date"></label></div><div id="sessionList">${rows.map(([id,date,duration,outcome,photos])=>`<button class="session-row" data-action="session-${id}" data-date="2026-10-${id==='latest'?'08':id==='missing'?'07':'06'}"><span><strong>${date}</strong><small>Departure heating</small></span><span>${duration}</span><span class="${id==='missing'?'pending':'outcome'}">${outcome}<small>${photos}</small></span><span aria-hidden="true">→</span></button>`).join('')}</div><p id="noSessions" hidden class="support">No sessions on this date.</p>`;
}
function graph(type = 'temperature') {
  const temp = type === 'temperature';
  return `<svg class="chart ${temp ? '' : 'chart-small'}" viewBox="0 0 600 ${temp ? 230 : 110}" role="img" aria-label="Synthetic ${type} time series; illustration only"><g stroke="#363b40" stroke-width="1">${[30,70,110,...(temp?[150,190]:[])].map(y=>`<path d="M40 ${y}H595"/>`).join('')}</g><g fill="#a7afb7" font-size="12"><text x="0" y="35">${temp?'15°C':type==='humidity'?'80%':'1.7kW'}</text><text x="0" y="${temp?195:105}">${temp?'−10°C':type==='humidity'?'50%':'0'}</text></g><path d="${temp?'M40 183L100 174L160 156L220 129L280 105L340 86L400 73L460 60L520 47L595 38':type==='humidity'?'M40 25L100 30L160 38L220 48L280 53L340 60L400 67L460 69L520 73L595 76':'M40 100L42 32L160 33L280 32L400 33L520 32L593 32L595 100'}" fill="none" stroke="#a0bddb" stroke-width="3"/>${temp?'<path d="M40 188L100 181L160 174L220 163L280 152L340 146L400 135L460 127L520 117L595 109" fill="none" stroke="#e9b378" stroke-width="3"/><path d="M40 185L100 178L160 168L220 157L280 145L340 135L400 126L460 115L520 103L595 95" fill="none" stroke="#a3ccba" stroke-width="3"/>':''}</svg><div class="chart-labels"><span>04:30</span><span>05:30</span><span>06:30</span><span>07:00</span></div>`;
}
function windshield(clear = false) {
  return `<svg viewBox="0 0 700 440" preserveAspectRatio="xMidYMid slice" role="img" aria-label="Illustrated sample windshield, ${clear?'mostly clear':'partially frosted'}; not a real photo"><rect width="700" height="440" fill="#28343b"/><path d="M0 240L140 177L280 218L385 150L555 217L700 180V380H0" fill="#53646b"/><path d="M0 277L130 263L390 258L700 278V395H0" fill="#c1cdcf"/><path d="M0 320L210 298L370 302L700 330V420H0" fill="#a3b1b5"/><g fill="#738487">${Array.from({length:12},(_,i)=>`<path d="M${i*70} 150l-18 115h36z"/>`).join('')}</g><path d="M-15 430L33 70Q350-80 667 70L720 430" fill="none" stroke="#101719" stroke-width="55"/><path d="M30 360Q340 323 680 361L705 440H0" fill="#171e23"/><path d="M140 348L400 303M545 342L652 309" stroke="#14191b" stroke-width="9"/><path d="M50 55Q345-55 650 55L657 315Q560 242 420 300Q215 240 48 321Z" fill="#d8e4e7" opacity="${clear?'.13':'.53'}"/><g fill="none" stroke="#eff8fb" stroke-width="1" opacity="${clear?'.14':'.55'}">${Array.from({length:22},(_,i)=>`<path d="M${45+i*29} 90l-18 110l30 22l-15 62M${45+i*29} 145l20-15l-13-30"/>`).join('')}</g></svg>`;
}
function review() {
  const missing = state.session === 'missing';
  return `${button('← All sessions', 'view-sessions', 'link')}<div class="review-head"><div><p class="eyebrow">Departure · ${missing?'7':'8'} Oct 2026 · ${missing?'07:30':'07:00'}</p><h2 class="outcome-title">${missing?'Outcome not recorded':state.session==='previous'?'Scraping needed':state.outcome}</h2><p>${missing?'No observation was recorded.': 'Driver observation · recorded at 07:02'}${state.session==='latest'?' · sample session':''}</p></div><div class="actions">${button('Record outcome', 'outcome')}${button('Observe windshield', 'capture', 'primary')}</div></div><div class="review-grid"><div><section class="section"><h3>Heating timeline</h3>${missing ? '<p>05:30 start → 07:30 departure → 08:00 latest finish</p>' : state.session==='previous' ? '<p>04:00 start → 07:00 departure and latest finish</p>' : timeline()}<p class="micro">${missing ? '120' : state.session==='previous' ? '180' : '150'} min before departure · ${state.session==='previous' ? 'no grace available' : 'up to 30 min grace'} · 180 min maximum.</p></section><section class="section"><h3>Temperatures</h3>${missing?'<p>— No sensor measurements available.</p>':`<div class="legend"><span>Cabin</span><span>Driver glass</span><span>Center glass</span></div>${graph()}`}<p class="support">Experimental glass indicator. Temperature alone does not confirm defrost readiness.</p></section><section class="section"><h3>Cabin humidity</h3>${missing?'<p>— No humidity measurements available.</p>':graph('humidity')}</section><section class="section"><h3>Heater power</h3>${missing?'<p>— No power measurements available.</p>':graph('power')}</section></div><aside><section class="section"><h3>Photos</h3>${missing?'<p>No photos recorded.</p>':`<div class="photo-grid">${['06:30','06:40','06:50'].map((time,i)=>`<figure class="photo-thumb">${windshield(i===2)}<figcaption><p>${time} · sample illustration</p></figcaption></figure>`).join('')}</div><p class="micro">First clear sample: 06:50. Clearing happened at or before this photo; exact time is unknown.</p>`}</section><section class="section"><h3>Session facts</h3><p>Trigger: departure<br>Outside: −6.0°C (FMI)<br>Duration rule: 150 min<br>Limit: 180 min<br>Heater: Termini II · setting II</p><p class="support">All charts and photos in this prototype are synthetic.</p></section></aside></div>`;
}
function settings() {
  return `<p class="eyebrow">Configuration & maintenance</p><h2 style="margin-bottom:25px">Settings</h2><section class="settings-group"><div><h3>Heating preferences</h3><p>Departure rules and manual defaults.</p></div><div class="settings-fields"><label>Default manual duration<select id="defaultDuration">${[30,60,120,180].map(n=>`<option ${n===state.duration?'selected':''} value="${n}">${n} minutes</option>`).join('')}</select></label><p class="micro">Duration table: 0°C → 90 min; −5°C → 120 min; −10°C → 150 min; ≤−15°C → 180 min. Invalid weather uses 180 min before departure.</p><p class="micro">Continuous limit: 180 min · departure grace: up to 30 min within the limit.</p></div></section><section class="settings-group"><div><h3>Installation & devices</h3><p>Cabin equipment and sensor identity.</p></div><div class="settings-fields"><p>DEFA Termini II 1700 · setting II<br>Shelly Outdoor Plug S Gen3<br>XIAO ESP32-C3 · powered from DEFA mains</p><p class="micro">Driver glass: TMP117 · 0x48<br>Center glass: TMP117 · 0x49<br>Cabin humidity: SHT45 · 0x44</p>${heaterStatus()}<p class="micro">Compatible software versions: — not available in the sample.</p></div></section><section class="settings-group"><div><h3>Calibration</h3><p>Experimental model parameters and comfort heating.</p></div><div class="settings-fields"><label>Comfort hysteresis °C<input id="hysteresis" type="number" value="${state.hysteresis}" step="0.5" min="0.5" max="5"></label><details><summary>Advanced calibration</summary><p class="micro">Experimental calibration does not control first-frost departure timing.</p><label>Automatic calibration<select><option>Disabled</option><option>Enabled</option></select></label><label>Glass indicator °C<input type="number" value="3"></label><p class="micro">Experimental display threshold: +3°C for 10 min. Winter observations are still needed.</p></details></div></section><section class="settings-group"><div><h3>Diagnostics</h3><p>Connection, commands, logs and restarts.</p></div><div class="settings-fields"><p>Last device message: ${state.relay==='unknown'?'18 minutes ago':'12 seconds ago'}<br>Command status: ${state.relay==='stopping'?'OFF requested · awaiting confirmation':'No pending command'}<br>Transport: ${state.relay==='unknown'?'Disconnected':'WebSocket · sample'}</p><div class="actions">${button('View sample logs', 'logs')}${button('Restart ESP', 'restart-esp')}${button('Restart Shelly', 'restart-shelly')}</div></div></section><div class="actions">${button('Save sample preferences', 'save-settings', 'primary')}</div>`;
}
function capture() {
  const count = `${String(Math.floor(state.nextPhoto / 60)).padStart(2,'0')}:${String(state.nextPhoto % 60).padStart(2,'0')}`;
  return `${button('← Session review', 'review', 'link')}<div class="capture-layout"><div><p class="eyebrow">Observation · 8 Oct · 07:00 departure</p><h2 style="margin-bottom:20px">Watch the windshield</h2><div class="camera-preview">${windshield()}<div class="camera-guide"></div><div class="camera-caption"><span>Sample preview · camera not accessed</span><span>${state.lens} simulated</span></div><p>Keep the driver’s viewing area in frame.</p></div><div class="actions">${button('0.5× wide', 'wide', state.lens==='0.5×'?'selected':'')}${button('1× fallback', 'normal', state.lens==='1×'?'selected':'')}</div><p class="support">The finished capture tool will prefer ultra-wide when the browser exposes it, with a regular-camera fallback. Keep it open in the foreground.</p></div><aside class="capture-sidebar"><p class="eyebrow">${state.capturing?'Observation running':'Ready to observe'}</p><h2>${state.capturing?'Keep this page open':'A photo every 10 min'}</h2><p>Suggested window: 30–60 minutes around clearing.</p><div class="countdown" id="countdown">${state.capturing?count:'—'}</div><p class="micro">${state.capturing?'Until next simulated photo':'Start to begin the sample countdown'}</p><div class="actions">${button(state.capturing?'Running':'Start observation', 'start-capture', 'primary', state.capturing)}${button('Capture now', 'capture-now')}</div><div class="capture-log"><p><span>Saved sample photos</span><strong id="photoCount">${state.photos}</strong></p><p><span>Upload state</span><strong id="uploadState">Simulated · saved</strong></p></div>${button('Finish observation', 'finish-observation')}<p class="support">Ends capture and opens outcome entry. Heating keeps its planned finish.</p><section class="section">${heaterStatus()}<p class="support">Heating and observation are separate.</p><div class="actions">${stopControl()}</div></section></aside></div>`;
}
function outcome() {
  return `<div class="outcome-form">${button('← Session review', 'review', 'link')}<p class="eyebrow" style="margin-top:22px">Observation finished</p><h2>How was the windshield?</h2><p>At departure, was the driver’s viewing area clear enough to drive?</p>${['Clear · no scraping','Clear · wipers needed','Scraping needed','Not observed'].map((label,i)=>button(label,`outcome-${i}`,'choice '+(state.outcome===label?'selected':''))).join('')}<div class="actions">${button('Save outcome', 'save-outcome','primary')}</div><p class="support">Recording an outcome does not change heating.<br>${state.relay==='on'?'Heating is still ON.':state.relay==='off'?'Heater is confirmed OFF.':'Heater state is not confirmed.'}</p></div>`;
}
function debug() {
  document.querySelector('#debug').textContent = JSON.stringify({ layout: variant, ...state, data: 'synthetic', externalRequests: 0 }, null, 2);
}
function render() {
  const body = state.view === 'heating' ? ({A:VariantA,B:VariantB,C:VariantC}[variant])() : ({sessions,review,settings,capture,outcome}[state.view])();
  workspace.className = `variant-${variant.toLowerCase()}`;
  workspace.innerHTML = `${header()}<div class="workspace-body">${state.toast ? `<p class="toast" role="status">${state.toast}</p>` : ''}${body}</div>`;
  document.querySelector('#variantLabel').textContent = `${variant} · ${layouts[variant]}`;
  document.querySelector('#scenario').value = state.scenario;
  debug();
}
function cycle(delta) {
  variant = ['A','B','C'][(['A','B','C'].indexOf(variant)+delta+3)%3];
  syncUrl(); render();
  document.querySelector('#announcement').textContent = `${variant}: ${layouts[variant]}`;
}
document.querySelector('#previous').onclick = () => cycle(-1);
document.querySelector('#next').onclick = () => cycle(1);
document.querySelector('#scenario').onchange = event => reset(event.target.value);
document.addEventListener('keydown', event => {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || document.querySelector('#restartDialog').open || event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) return;
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); cycle(event.key==='ArrowLeft'?-1:1); }
});
document.querySelector('#menuToggle').onclick = event => {
  const open = document.querySelector('.navbar').classList.toggle('open');
  event.currentTarget.setAttribute('aria-expanded', open);
};
document.addEventListener('change', event => {
  const id = event.target.id;
  if (['date','time'].includes(id)) { if (!event.target.checkValidity() || !event.target.value) { event.target.reportValidity(); return; } state[id] = event.target.value; render(); }
  if (id==='target') state.target = Number(event.target.value);
  if (id==='hysteresis') state.hysteresis = Number(event.target.value);
  if (id==='defaultDuration') state.duration = Number(event.target.value);
  if (id==='filterDate') {
    let visible = 0;
    document.querySelectorAll('.session-row').forEach(row => { row.hidden = !!event.target.value && row.dataset.date !== event.target.value; row.style.display = row.hidden ? 'none' : ''; if (!row.hidden) visible++; });
    document.querySelector('#noSessions').hidden = visible > 0;
  }
  debug();
});
document.addEventListener('click', event => {
  const control = event.target.closest('[data-action]');
  if (!control || control.disabled) return;
  const action = control.dataset.action;
  state.toast = '';
  if (action.startsWith('view-')) state.view = action.slice(5);
  if (action.startsWith('duration-')) state.duration = Number(action.slice(9));
  if (action === 'tomorrow') { state.date='2026-10-08'; state.toast='Tomorrow selected · review the plan before saving.'; }
  if (action==='schedule' && state.relay==='off') { state.scheduled=true; state.toast='Departure plan saved in this preview.'; }
  if (action==='cancel-plan' && state.relay==='off') { state.scheduled=false; state.toast='Upcoming departure plan cancelled in this preview.'; }
  if ((action==='start' || action==='comfort') && state.relay==='off') {
    state.relay='on'; state.mode=action==='comfort'?'comfort':'manual';
    const minutes = action==='comfort'?180:state.duration;
    state.activeDuration=minutes;
    state.manualFinish=`${String(6+Math.floor((10+minutes)/60)).padStart(2,'0')}:${String((10+minutes)%60).padStart(2,'0')}`;
    state.toast=`Sample ${state.mode} heating started. Existing departure retained; overlapping starts would be skipped.`;
  }
  if (action==='stop') {
    const disconnected=state.relay==='unknown'; state.relay='stopping';
    stopTimer=setTimeout(()=>{ state.relay=disconnected?'unknown':'off'; state.toast=disconnected?'OFF could not be confirmed. The device is still disconnected.':'Device confirmed OFF in this simulation.'; render(); },1600);
  }
  if (action==='review') { state.view='review'; state.session='latest'; }
  if (action.startsWith('session-')) { state.session=action.slice(8); state.view='review'; }
  if (action==='capture') { state.view='capture'; state.session='latest'; }
  if (action==='start-capture') { state.capturing=true; state.nextPhoto=600; state.toast='Sample observation started. No real camera or uploads.'; }
  if (action==='capture-now') { state.photos++; state.toast='Sample photo captured and upload simulated.'; }
  if (action==='wide') state.lens='0.5×';
  if (action==='normal') state.lens='1×';
  if (action==='finish-observation') { state.capturing=false; state.view='outcome'; }
  if (action==='outcome') state.view='outcome';
  if (action.startsWith('outcome-')) state.outcome=['Clear · no scraping','Clear · wipers needed','Scraping needed','Not observed'][Number(action.slice(8))];
  if (action==='save-outcome') { state.view='review'; state.toast='Sample outcome saved. Heating was not changed.'; }
  if (action==='save-settings') state.toast='Sample preferences saved for this browser session only.';
  if (action==='logs') state.toast='06:10:00 · telemetry received; 06:10:01 · relay ON confirmed (synthetic log).';
  if (action==='restart-esp' || action==='restart-shelly') { restartTarget=control; document.querySelector('#restartDialog').showModal(); return; }
  if (action==='close-dialog' || action==='restart') { document.querySelector('#restartDialog').close(); if(action==='restart') state.toast='Restart simulated. No device command was sent.'; if(restartTarget) restartTarget.focus(); if(action==='close-dialog') return; }
  render();
  const replacement = workspace.querySelector(`[data-action="${action}"]`);
  if (replacement && !replacement.disabled) replacement.focus({preventScroll:true});
});
setInterval(()=>{
  if(!state.capturing) return;
  state.nextPhoto--;
  if(state.nextPhoto<=0) {state.photos++;state.nextPhoto=600;}
  if(state.view==='capture') {
    document.querySelector('#countdown').textContent=`${String(Math.floor(state.nextPhoto/60)).padStart(2,'0')}:${String(state.nextPhoto%60).padStart(2,'0')}`;
    document.querySelector('#photoCount').textContent=state.photos;
  }
  debug();
},1000);
reset(scenarios.includes(params.get('state')) ? params.get('state') : 'scheduled');
