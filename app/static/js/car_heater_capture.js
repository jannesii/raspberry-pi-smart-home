/* Observation capture: photos and evidence only. Stop heating is the one heater
 * action here and never ends capture; Finish never calls heater controls. */
(() => {
  'use strict';
  const page = document.querySelector('main.heater-capture');
  if (!page) return;
  const sessionId = page.dataset.sessionId;
  const sessionUrl = '/api/car_heater/sessions/' + encodeURIComponent(sessionId);
  const INTERVAL_MS = 10 * 60 * 1000;
  // A scheduled photo later than this is recorded as missed, never caught up.
  const LATE_LIMIT_MS = 2 * 60 * 1000;
  const CAMERA_KEY = 'carHeaterCaptureCamera';
  const timeZone = 'Europe/Helsinki';
  const $ = id => document.getElementById(id);
  const video = $('capturePreview');
  const select = $('cameraSelect');
  const log = $('captureLog');

  let stream = null;
  let cameraLabel = null;
  let startedAt = null;      // ms; null while not observing
  let nextSequence = 0;
  let wakeLock = null;
  let ticker = null;
  let uploading = false;
  let finishing = false;
  let pauseReason = null;   // why scheduled photos could not be taken
  let observationId = null;
  let observationStartedAt = null;
  let finishedAt = null;
  const record = {saving: false, dirty: false, state: 'none'};
  let ending = false;        // Finish in progress: no new scheduled photos
  // Bounded waits so one hung request on a weak phone connection cannot block Finish.
  const timeout = ms => (typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(ms) : undefined);
  const entries = [];         // photos and missed slots, in capture order

  const clock = value => new Date(value).toLocaleTimeString('en-GB', {hour12: false, timeZone});
  const shortClock = value => new Date(value).toLocaleTimeString('en-GB', {hour12: false, timeZone, hour: '2-digit', minute: '2-digit'});
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const uuid = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const h = [...b].map(x => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  };
  const storage = {
    get() { try { return localStorage.getItem(CAMERA_KEY) || ''; } catch (_) { return ''; } },
    set(value) { try { localStorage.setItem(CAMERA_KEY, value); } catch (_) { /* per-device convenience only */ } },
  };

  // ---- Session context -------------------------------------------------
  async function loadContext() {
    try {
      const response = await fetch(sessionUrl, {credentials: 'same-origin'});
      if (!response.ok) throw new Error();
      const row = await response.json();
      const departure = row.requested_departure_at
        ? 'Departure ' + new Date(row.requested_departure_at).toLocaleString('en-GB', {hour12: false, timeZone, dateStyle: 'medium', timeStyle: 'short'})
        : 'No requested departure';
      $('captureSessionContext').textContent = `${departure} · ${row.trigger.replaceAll('_', ' ')} session · ${row.photos.length} saved photo${row.photos.length === 1 ? '' : 's'}`;
    } catch (_) {
      $('captureSessionContext').textContent = 'Session details unavailable. Photos still upload to this session.';
    }
  }

  // ---- Camera ------------------------------------------------------------
  function cameraMessage(text) { $('cameraStatus').textContent = text; }
  function stopStream() {
    if (stream) stream.getTracks().forEach(track => { track.onended = null; track.stop(); });
    stream = null;
    video.srcObject = null;
  }
  async function open(constraints) {
    const media = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      width: {ideal: 1920}, height: {ideal: 1440}, ...constraints,
    }});
    stopStream();
    stream = media;
    const track = stream.getVideoTracks()[0];
    cameraLabel = track?.label || null;
    track.onended = () => cameraLost('Camera stopped. Choose Enable camera to restart it.');
    video.srcObject = stream;
    await video.play().catch(() => {});
    $('cameraEmpty').hidden = true;
    return track;
  }
  function permissionText(error) {
    if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') {
      return 'Camera permission denied. Allow camera access for this site in the browser settings, then choose Enable camera.';
    }
    if (error?.name === 'NotFoundError') return 'No camera was found on this device.';
    if (error?.name === 'NotReadableError') return 'The camera is in use by another app. Close it, then choose Enable camera.';
    return 'The camera could not start. Choose Enable camera to try again.';
  }
  async function listCameras(activeId) {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput');
    select.replaceChildren(...devices.map((device, index) => {
      const option = new Option(device.label || `Camera ${index + 1}`, device.deviceId);
      option.selected = device.deviceId === activeId;
      return option;
    }));
    select.disabled = devices.length < 2;
    return devices;
  }
  async function enableCamera() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      cameraMessage('Camera access needs the HTTPS address of this app.');
      return;
    }
    const button = $('cameraEnable');
    button.disabled = true;
    cameraMessage('Requesting camera…');
    try {
      const saved = storage.get();
      let track;
      let note = '';
      try {
        track = await open(saved ? {deviceId: {exact: saved}} : {facingMode: {ideal: 'environment'}});
      } catch (error) {
        if (!saved || ['NotAllowedError', 'SecurityError'].includes(error.name)) throw error;
        track = await open({facingMode: {ideal: 'environment'}});
        note = 'The saved camera is unavailable; using the rear camera. ';
      }
      const devices = await listCameras(track.getSettings().deviceId);
      const ultra = devices.find(d => /ultra\s*-?\s*wide/i.test(d.label));
      if (!saved && ultra && ultra.deviceId !== track.getSettings().deviceId) {
        try {
          track = await open({deviceId: {exact: ultra.deviceId}});
          note = 'Using the ultra-wide camera. ';
        } catch (_) {
          track = await open({facingMode: {ideal: 'environment'}});
          note = 'The ultra-wide camera could not start; using the regular rear camera. ';
        }
        await listCameras(track.getSettings().deviceId);
      } else if (!ultra) {
        note += 'Ultra-wide is not exposed by this browser; using ' + (cameraLabel || 'the rear camera') + '. ';
      }
      cameraMessage(`${note}Preview live: ${cameraLabel || 'camera'}. Check the framing.`);
      button.textContent = 'Restart camera';
    } catch (error) {
      stopStream();
      $('cameraEmpty').hidden = false;
      cameraMessage(permissionText(error));
      if (startedAt !== null) interrupted('Camera unavailable: scheduled photos are paused.');
    } finally {
      button.disabled = false;
      render();
    }
  }
  async function chooseCamera() {
    if (!select.value) return;
    try {
      const track = await open({deviceId: {exact: select.value}});
      storage.set(track.getSettings().deviceId || select.value);
      cameraMessage(`Preview live: ${cameraLabel || 'camera'}. Check the framing.`);
    } catch (error) {
      cameraMessage('That camera could not start. ' + permissionText(error));
      try { await open({facingMode: {ideal: 'environment'}}); cameraMessage('Using the regular rear camera instead.'); }
      catch (_) { cameraLost(permissionText(error)); }
    }
    render();
  }
  function cameraLive() {
    return Boolean(stream && stream.getVideoTracks().some(t => t.readyState === 'live') && video.videoWidth);
  }
  function cameraLost(text) {
    stopStream();
    $('cameraEmpty').hidden = false;
    cameraMessage(text);
    if (startedAt !== null) interrupted('Camera stopped: scheduled photos are paused until the camera restarts.');
    render();
  }

  // ---- Wake lock -----------------------------------------------------------
  async function keepAwake() {
    if (!('wakeLock' in navigator)) {
      $('wakeLockStatus').textContent = 'This browser cannot keep the screen on. Set Auto-Lock to Never while observing.';
      return;
    }
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      $('wakeLockStatus').textContent = 'Screen kept awake while this page is visible.';
      wakeLock.addEventListener('release', () => {
        wakeLock = null;
        if (startedAt !== null) $('wakeLockStatus').textContent = 'Screen may lock. Keep this page visible.';
      });
    } catch (_) {
      $('wakeLockStatus').textContent = 'Screen wake lock refused. Keep the screen on manually.';
    }
  }
  function releaseWake() {
    if (wakeLock) wakeLock.release().catch(() => {});
    wakeLock = null;
  }

  // ---- Capture and schedule ---------------------------------------------
  function interrupted(text) {
    const alert = $('captureInterruption');
    alert.textContent = text;
    alert.hidden = false;
  }
  function dueAt(sequence) { return startedAt + sequence * INTERVAL_MS; }
  function frame() {
    if (!cameraLive()) return Promise.resolve(null);
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
  }
  async function capture(kind, sequence = null) {
    const capturedAt = Date.now();
    const blob = await frame();
    if (!blob) {
      if (kind === 'scheduled') missed(sequence, 'camera_unavailable');
      else cameraMessage('No photo: the camera preview is not live.');
      render();
      return;
    }
    entries.push({
      id: uuid(), kind, sequence, blob, capturedAt, state: 'pending', error: '',
      dueAt: kind === 'scheduled' ? dueAt(sequence) : null,
      observationStartedAt, camera: cameraLabel,
    });
    render();
    upload();
  }
  function missed(sequence, reason) {
    entries.push({kind: 'missed', sequence, dueAt: dueAt(sequence), reason, observationId});
    saveObservation();
  }
  function tick() {
    if (startedAt === null || ending) return;
    const now = Date.now();
    if (document.visibilityState === 'visible') {
      while (startedAt !== null && dueAt(nextSequence) <= now) {
        if (now - dueAt(nextSequence) > LATE_LIMIT_MS) {
          missed(nextSequence++, pauseReason || 'capture_delayed');
        } else if (cameraLive()) {
          capture('scheduled', nextSequence++);
        } else {
          break;  // Wait for the camera until the late limit, then record a miss.
        }
      }
      pauseReason = cameraLive() ? null : 'camera_unavailable';
    }
    render();
  }
  function startObservation() {
    if (startedAt !== null || !cameraLive()) return;
    startedAt = observationStartedAt = Date.now();
    observationId = uuid();
    nextSequence = 0;
    $('captureInterruption').hidden = true;
    keepAwake();
    saveObservation();
    resume();
    $('captureNow').focus();
  }
  function resume() {
    if (startedAt === null || ticker !== null) return;
    ticker = setInterval(tick, 1000);
    tick();
  }
  function stopObservation() {
    clearInterval(ticker);
    ticker = null;
    startedAt = null;
    releaseWake();
    stopStream();
  }

  // ---- Uploads ---------------------------------------------------------------
  async function upload() {
    if (uploading) return;
    const entry = entries.find(e => e.state === 'pending');
    if (!entry) return;
    uploading = true;
    entry.state = 'uploading';
    render();
    const query = new URLSearchParams({
      captured_at: new Date(entry.capturedAt).toISOString(),
      capture_kind: entry.kind,
      observation_started_at: new Date(entry.observationStartedAt).toISOString(),
    });
    if (entry.observationStartedAt === null) query.delete('observation_started_at');
    if (entry.dueAt !== null) query.set('due_at', new Date(entry.dueAt).toISOString());
    if (entry.sequence !== null) query.set('sequence', String(entry.sequence));
    if (entry.camera) query.set('camera_label', entry.camera.slice(0, 200));
    try {
      const response = await fetch(`${sessionUrl}/photos/${entry.id}?${query}`, {
        method: 'PUT', credentials: 'same-origin', body: entry.blob, signal: timeout(60000),
        headers: {'Content-Type': 'image/jpeg', 'X-CSRFToken': csrf()},
      });
      let data = null;
      try { data = await response.json(); } catch (_) { /* reported below */ }
      // Only the server's acknowledgement of this id means the image is persisted.
      if (!response.ok || data?.id !== entry.id) {
        throw new Error(data?.error || `Upload failed (HTTP ${response.status}). Reload to check your sign-in.`);
      }
      entry.state = 'saved';
      entry.blob = null;
    } catch (error) {
      entry.state = 'failed';
      entry.error = error?.name === 'TimeoutError' ? 'Upload timed out'
        : error instanceof TypeError ? 'Network unavailable' : error.message;
    } finally {
      uploading = false;
      render();
      upload();
    }
  }
  function retryFailed() {
    entries.filter(e => e.state === 'failed').forEach(e => { e.state = 'pending'; e.error = ''; });
    if (record.state === 'failed') saveObservation();
    render();
    upload();
  }
  // The window and missed slots are evidence too; the server merges each update.
  async function saveObservation() {
    if (!observationId) return;
    if (record.saving) { record.dirty = true; return; }
    record.saving = true;
    record.dirty = false;
    const body = {
      started_at: new Date(observationStartedAt).toISOString(),
      finished_at: finishedAt === null ? null : new Date(finishedAt).toISOString(),
      missed: entries.filter(e => e.kind === 'missed' && e.observationId === observationId).map(e => ({
        sequence: e.sequence, due_at: new Date(e.dueAt).toISOString(), reason: e.reason,
      })),
    };
    try {
      const response = await fetch(`${sessionUrl}/observations/${observationId}`, {
        method: 'PUT', credentials: 'same-origin', body: JSON.stringify(body), signal: timeout(20000),
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
      });
      const data = await response.json().catch(() => null);
      record.state = response.ok && data?.id === observationId ? 'saved' : 'failed';
    } catch (_) {
      record.state = 'failed';
    } finally {
      record.saving = false;
      if (record.dirty) saveObservation(); else render();
    }
  }

  // ---- Finish ----------------------------------------------------------------
  const unsaved = () => entries.filter(e => e.state && e.state !== 'saved');
  function leave() {
    stopObservation();
    finishing = true;
    location.assign(`/car_heater/sessions/${encodeURIComponent(sessionId)}?observation=finished#outcomeHeading`);
  }
  const busy = () => record.saving || entries.some(e => e.state === 'pending' || e.state === 'uploading');
  function settled() {
    return new Promise(resolve => {
      const wait = setInterval(() => { if (!busy()) { clearInterval(wait); resolve(); } }, 250);
    });
  }
  function ask(heading, text) {
    $('finishDialogHeading').textContent = heading;
    $('finishDialogText').textContent = text + ' Unsaved evidence is lost when you leave.';
    $('captureFinishDialog').showModal();
    $('finishRetry').focus();
  }
  // Finish stops scheduled capture first; it never calls heater controls.
  // skip.photos / skip.record: the observer chose to leave without that evidence.
  async function finish(skip = {}) {
    ending = true;
    clearInterval(ticker);
    ticker = null;
    $('captureFinish').disabled = true;
    try {
      if (busy()) {
        $('captureSummary').textContent = 'Waiting for uploads to finish before ending the observation…';
        await settled();
      }
      const failed = unsaved().length;
      if (failed && !skip.photos) {
        ask('Some photos are not saved', `${failed} photo${failed === 1 ? ' is' : 's are'} not saved on the server.`);
        return;
      }
      if (observationId) {
        // Abandoned scheduled photos stay visible in review as missed, not as evidence.
        entries.filter(e => e.kind === 'scheduled' && e.state === 'failed' && e.observationStartedAt === observationStartedAt)
          .filter(e => !entries.some(m => m.kind === 'missed' && m.observationId === observationId && m.sequence === e.sequence))
          .forEach(e => entries.push({kind: 'missed', sequence: e.sequence, dueAt: e.dueAt, reason: 'upload_failed', observationId}));
        finishedAt = finishedAt ?? Date.now();
        await saveObservation();
        await settled();
        if (record.state !== 'saved' && !skip.record) {
          ask('Observation record not saved', 'The observation record (start, finish and missed photos) is not saved.');
          return;
        }
      }
      leave();
    } finally {
      $('captureFinish').disabled = false;
    }
  }
  function keepObserving() {
    if ($('captureFinishDialog').open) $('captureFinishDialog').close();
    ending = false;
    if (finishedAt !== null) {
      // The finish may have reached the server even though no reply arrived, and
      // a recorded finish is never cleared. Continue as a new observation window.
      finishedAt = null;
      startedAt = observationStartedAt = Date.now();
      observationId = uuid();
      nextSequence = 0;
      record.state = 'none';
      interrupted('Continuing as a new observation window; a photo is taken now.');
      saveObservation();
    }
    resume();
  }

  // ---- Heating (independent) ----------------------------------------------
  let stopping = 0;
  async function heating(method = 'GET') {
    const response = await fetch('/api/car_heater/bounded', {method, credentials: 'same-origin', headers: {'X-CSRFToken': csrf()}});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('Heating status unavailable; relay state unknown.'); }
    if (!response.ok) throw new Error(data?.error || 'Heating status unavailable; relay state unknown.');
    return data;
  }
  function renderHeating(run) {
    const text = $('captureHeating');
    if (!run) { text.textContent = 'No bounded heating session is active on this server.'; return; }
    const relay = run.relay_on === true ? 'relay confirmed ON' : run.relay_on === false ? 'relay confirmed OFF' : 'relay state unknown';
    const off = run.off_state === 'confirmed' ? 'Stop confirmed'
      : run.off_state === 'failed_or_unknown' ? 'Stop failed or unknown; retrying'
      : run.off_state === 'pending' ? 'Stopping; waiting for relay confirmation'
      : run.phase === 'stopped' ? 'Heating stopped' : 'Heating active';
    text.textContent = `${off} · ${relay} · latest finish ${shortClock(run.finish_at * 1000)}.`;
  }
  async function refreshHeating() {
    if (stopping || document.hidden) return;
    try { renderHeating(await heating()); }
    catch (error) { $('captureHeating').textContent = error.message; }
  }
  async function stopHeating() {
    stopping += 1;
    $('captureHeating').textContent = 'Requesting Stop; relay state unknown…';
    try { renderHeating(await heating('DELETE')); }
    catch (error) { $('captureHeating').textContent = error.message + ' Stop heating remains available.'; }
    finally { stopping -= 1; }
  }

  // ---- Rendering -------------------------------------------------------------
  const stateText = {pending: 'Waiting to upload', uploading: 'Uploading…', saved: 'Saved', failed: 'Not saved'};
  function render() {
    const observing = startedAt !== null;
    const live = cameraLive();
    $('captureStart').disabled = observing || !live;
    $('captureStart').textContent = observing ? 'Observation running' : 'Start observation';
    $('captureNow').disabled = !live;
    $('captureState').textContent = observing ? (live ? 'OBSERVATION RUNNING' : 'OBSERVATION PAUSED') : 'READY TO OBSERVE';
    if (observing) {
      const remaining = Math.max(0, dueAt(nextSequence) - Date.now());
      const seconds = Math.ceil(remaining / 1000);
      $('captureCountdown').textContent = live && !document.hidden
        ? `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
        : 'Paused';
      $('captureElapsed').textContent = `Observing since ${shortClock(startedAt)} · ${Math.floor((Date.now() - startedAt) / 60000)} min · next photo due ${shortClock(dueAt(nextSequence))}`;
    } else {
      $('captureCountdown').textContent = '—';
    }
    const photos = entries.filter(e => e.kind !== 'missed');
    const count = state => photos.filter(e => e.state === state).length;
    const saved = count('saved');
    const inFlight = count('pending') + count('uploading');
    const failed = count('failed');
    const missedCount = entries.length - photos.length;
    const summary = photos.length || missedCount
      ? [`${saved} saved`, inFlight && `${inFlight} uploading`, failed && `${failed} not saved`, missedCount && `${missedCount} missed`,
        record.state === 'failed' && 'observation record not saved'].filter(Boolean).join(' · ')
      : 'No photos taken yet.';
    if ($('captureSummary').textContent !== summary) $('captureSummary').textContent = summary;
    $('captureRetry').hidden = !failed;
    log.replaceChildren(...(entries.length ? entries.map(entry => {
      const item = document.createElement('li');
      if (entry.kind === 'missed') {
        item.className = 'capture-missed';
        item.textContent = `Scheduled ${shortClock(entry.dueAt)} · missed (${entry.reason.replaceAll('_', ' ')}); no catch-up photo`;
      } else {
        item.className = 'capture-' + entry.state;
        const timing = entry.kind === 'scheduled' ? `scheduled ${shortClock(entry.dueAt)}` : 'manual';
        item.textContent = `${clock(entry.capturedAt)} · ${timing} · ${stateText[entry.state]}${entry.error ? ': ' + entry.error : ''}`;
      }
      return item;
    }) : [Object.assign(document.createElement('li'), {textContent: 'No photos yet.'})]));
  }

  // ---- Events ----------------------------------------------------------------
  $('cameraEnable').addEventListener('click', enableCamera);
  select.addEventListener('change', chooseCamera);
  video.addEventListener('loadedmetadata', render);
  $('captureStart').addEventListener('click', startObservation);
  $('captureNow').addEventListener('click', () => capture('manual'));
  $('captureRetry').addEventListener('click', retryFailed);
  $('captureFinish').addEventListener('click', () => finish());
  $('captureStopHeating').addEventListener('click', stopHeating);
  $('finishRetry').addEventListener('click', () => {
    if (finishedAt !== null) { $('captureFinishDialog').close(); finish(); return; }  // retry the record save
    keepObserving();
    retryFailed();
  });
  $('finishAnyway').addEventListener('click', () => {
    $('captureFinishDialog').close();
    finish(finishedAt === null ? {photos: true} : {photos: true, record: true});
  });
  $('finishCancel').addEventListener('click', keepObserving);
  // Escape dismisses the dialog like Keep observing, so capture never silently stalls.
  $('captureFinishDialog').addEventListener('cancel', event => { event.preventDefault(); keepObserving(); });
  $('captureFinishDialog').addEventListener('close', () => { if (!finishing) $('captureFinish').focus(); });
  document.addEventListener('visibilitychange', () => {
    if (startedAt === null) return;
    if (document.hidden) {
      pauseReason = 'page_hidden';
      interrupted(`Paused at ${clock(Date.now())}: the page was hidden. Scheduled photos are skipped until it is visible again.`);
      return;
    }
    interrupted(`Page visible again at ${clock(Date.now())}. Missed scheduled photos are listed below.`);
    if (ending) return;
    if (!wakeLock) keepAwake();
    if (!cameraLive()) enableCamera();
    tick();
    refreshHeating();
  });
  window.addEventListener('online', () => { if (entries.some(e => e.state === 'failed')) retryFailed(); });
  window.addEventListener('beforeunload', event => {
    if (!finishing && (startedAt !== null || unsaved().length || record.state === 'failed')) { event.preventDefault(); event.returnValue = ''; }
  });
  loadContext();
  refreshHeating();
  setInterval(refreshHeating, 5000);
  render();
})();
