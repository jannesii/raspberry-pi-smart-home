/**
 * Spoken announcements: browser preview and explicit speaker tests.
 */

(function() {
  const API = '/api/announcements';
  const ACTIVE = ['requested', 'dispatching', 'dispatched', 'playing'];
  const STATUS_TEXT = {
    requested: 'Waiting to start',
    dispatching: 'Sending to speaker',
    dispatched: 'Sent – waiting for a playback report',
    playing: 'Speaker reports playing',
    finished: 'Speaker reported playback finished',
    failed: 'Failed',
    unknown: 'Outcome unknown',
    stopped: 'Stopped',
  };
  const REASON_TEXT = {
    speaker_playing: 'The speaker was playing something, so the test did not interrupt it.',
    speaker_state_unknown: 'The speaker state was unclear, so the test did not start.',
    speaker_unavailable: 'The speaker did not respond. Check that it is powered on and on the network.',
    device_identity_mismatch: 'A different Cast device answered at this address; check the configured device id.',
    media_origin_not_configured: 'Speaker media address (ANNOUNCEMENT_MEDIA_ORIGIN) is not configured.',
    cast_unavailable: 'Cast support is not installed or not available on the server.',
    target_not_configured: 'This speaker is no longer configured.',
    queue_expired: 'Waited too long behind another announcement on this speaker.',
    piper_not_installed: 'Local speech engine (Piper) is not installed.',
    fi_voice_missing: 'The Finnish voice model is missing.',
    en_voice_missing: 'The English voice model is missing.',
    synthesis_failed: 'Speech generation failed.',
    synthesis_timeout: 'Speech generation took too long.',
    interrupted_by_restart: 'The server restarted while this test was in progress; it was not replayed.',
    no_playback_evidence: 'The speaker never reported playback.',
    no_finish_evidence: 'The speaker never reported the end of playback.',
    connection_lost: 'The connection to the speaker was lost.',
    load_uncertain: 'The speaker may or may not have received the audio.',
    interrupted: 'Something else took over the speaker during playback.',
    stopped_externally: 'Playback was stopped from another app or the speaker.',
    stop_requested: 'Stopped on request.',
    cancelled_before_dispatch: 'Cancelled before it reached the speaker.',
    finished_without_observed_start: 'Finished, but the start of playback was not observed.',
  };
  const RESTORE_TEXT = {
    restored: 'Restored',
    skipped_newer_intent: 'Left as changed by you',
    not_needed: 'Not needed',
    failed: 'Restore failed',
    unknown: 'Unknown',
  };

  let targets = [];
  let pollTimer = null;
  let previewToken = 0;

  function byId(id) {
    return document.getElementById(id);
  }

  function csrfToken() {
    const input = byId('csrfToken');
    return input instanceof HTMLInputElement ? input.value : '';
  }

  function fmtTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat('fi-FI', {
      timeZone: 'Europe/Helsinki',
      day: 'numeric',
      month: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(date);
  }

  function fmtVolume(value) {
    const number = Number(value);
    return value === null || value === undefined || !Number.isFinite(number)
      ? '—'
      : `${Math.round(number * 100)} %`;
  }

  function showMessage(text, isError) {
    const box = byId('announceMessage');
    if (!box) return;
    box.textContent = text || '';
    box.style.display = text ? 'block' : 'none';
    box.classList.toggle('is-error', Boolean(isError));
  }

  async function api(path, options) {
    const opts = Object.assign({ method: 'GET', credentials: 'same-origin' }, options || {});
    const headers = Object.assign({}, opts.headers || {});
    if (opts.method !== 'GET') {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRFToken'] = csrfToken();
    }
    opts.headers = headers;
    const response = await fetch(API + path, opts);
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok || data.ok === false) {
      const error = new Error((data && (data.message || data.error)) || `HTTP ${response.status}`);
      error.field = data && data.field;
      throw error;
    }
    return data;
  }

  function readinessText(part, fallback) {
    if (!part) return '—';
    if (part.available) return `Available (${part.engine || fallback})`;
    return `Unavailable – ${REASON_TEXT[part.reason] || part.reason || 'unknown reason'}`;
  }

  function renderStatus(status) {
    const synth = byId('announceStatusSynth');
    const cast = byId('announceStatusCast');
    const media = byId('announceStatusMedia');
    if (synth) synth.textContent = readinessText(status.synthesizer, 'speech');
    if (cast) cast.textContent = readinessText(status.cast, 'cast');
    if (media) {
      media.textContent = status.media_origin && status.media_origin.configured
        ? status.media_origin.origin
        : 'Not configured – speaker tests cannot fetch audio';
    }
    targets = Array.isArray(status.targets) ? status.targets : [];
    const container = byId('announceTargets');
    if (!container) return;
    const previous = (document.querySelector('input[name="announceTarget"]:checked') || {}).value;
    container.replaceChildren();
    targets.forEach((target, index) => {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = 'announceTarget';
      input.value = target.key;
      input.checked = previous ? previous === target.key : index === 0;
      const text = document.createElement('span');
      text.textContent = target.name;
      const host = document.createElement('small');
      host.textContent = target.host || '—';
      text.appendChild(host);
      label.append(input, text);
      container.appendChild(label);
    });
    const defaults = status.defaults || {};
    const textArea = byId('announceText');
    if (textArea && defaults.max_text_length) textArea.maxLength = defaults.max_text_length;
  }

  async function loadStatus() {
    try {
      renderStatus(await api('/status'));
    } catch (error) {
      showMessage(`Could not load readiness: ${error.message}`, true);
    }
  }

  function formValues() {
    const text = byId('announceText');
    const language = byId('announceLanguage');
    const chime = byId('announceChime');
    const volume = byId('announceVolume');
    const target = document.querySelector('input[name="announceTarget"]:checked');
    return {
      text: text ? text.value : '',
      language: language ? language.value : 'fi',
      chime_percent: chime ? Number(chime.value) : undefined,
      volume_percent: volume ? Number(volume.value) : undefined,
      target: target ? target.value : null,
    };
  }

  function setFieldError(message) {
    const error = byId('announceTextError');
    const text = byId('announceText');
    if (error) error.textContent = message || '';
    if (text) text.setAttribute('aria-invalid', message ? 'true' : 'false');
    if (message && text) text.focus();
  }

  function validText(values) {
    if (!values.text.trim()) {
      setFieldError('Write the announcement text first.');
      return false;
    }
    setFieldError('');
    return true;
  }

  function handleError(error, context) {
    if (error.field === 'text') setFieldError(error.message);
    showMessage(`${context}: ${error.message}`, true);
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  async function runPreview() {
    const button = byId('announcementPreviewButton');
    const state = byId('announcePreviewState');
    const audio = byId('announcePreviewAudio');
    const values = formValues();
    if (!validText(values)) return;
    const token = ++previewToken;
    if (button) button.disabled = true;
    if (state) state.textContent = 'Preparing audio…';
    showMessage('');
    try {
      let { preview } = await api('/previews', {
        method: 'POST',
        body: JSON.stringify({
          text: values.text,
          language: values.language,
          chime_percent: values.chime_percent,
        }),
      });
      const deadline = Date.now() + 90000;
      while (preview.status === 'preparing' && Date.now() < deadline) {
        await sleep(700);
        preview = (await api(`/previews/${encodeURIComponent(preview.id)}`)).preview;
      }
      if (token !== previewToken) return;
      if (preview.status !== 'ready') {
        const reason = REASON_TEXT[preview.reason] || preview.reason || 'still preparing';
        throw new Error(reason);
      }
      if (audio instanceof HTMLAudioElement) {
        audio.src = preview.audio_url;
        audio.hidden = false;
        audio.play().catch(() => {});
      }
      if (state) {
        state.textContent = `Ready: ${preview.voice || 'voice —'}, ${preview.duration_s ?? '—'} s. Plays in this browser only.`;
      }
    } catch (error) {
      if (state) state.textContent = 'Preview not available.';
      handleError(error, 'Preview failed');
    } finally {
      if (button) button.disabled = false;
    }
  }

  function requestId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID();
    }
    return `req-${Date.now()}-${Math.random().toString(16).slice(2, 12)}`;
  }

  async function runTest() {
    const button = byId('announcementTestButton');
    const values = formValues();
    if (!validText(values)) return;
    if (!values.target) {
      showMessage('Choose a speaker for the test.', true);
      return;
    }
    if (button) button.disabled = true;
    showMessage('');
    try {
      await api('/tests', {
        method: 'POST',
        body: JSON.stringify(Object.assign({ request_id: requestId() }, values)),
      });
      const name = (targets.find(t => t.key === values.target) || {}).name || values.target;
      showMessage(`Speaker test requested on ${name}.`);
      await loadTests();
    } catch (error) {
      handleError(error, 'Speaker test not requested');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function stopTest(testId, button) {
    if (button) button.disabled = true;
    try {
      await api(`/tests/${encodeURIComponent(testId)}/stop`, { method: 'POST', body: '{}' });
      showMessage('Stop requested. The result below shows what the speaker reported.');
      await loadTests();
    } catch (error) {
      handleError(error, 'Stop not requested');
      if (button) button.disabled = false;
    }
  }

  function fact(list, label, value) {
    const wrap = document.createElement('div');
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value || '—';
    wrap.append(dt, dd);
    list.appendChild(wrap);
  }

  function stopText(attempt) {
    if (!attempt.stop_requested_at) return '—';
    const outcome = {
      stopped: 'Speaker stopped',
      failed: 'Stop failed',
      unknown: 'Stop outcome unknown',
      not_dispatched: 'Cancelled before sending',
    }[attempt.stop_outcome] || 'Requested – waiting';
    return `${outcome} (${fmtTime(attempt.stop_observed_at || attempt.stop_requested_at)})`;
  }

  function renderTests(tests) {
    const list = byId('announceTestsList');
    const count = byId('announceTestsCount');
    if (count) count.textContent = `${tests.length} ${tests.length === 1 ? 'test' : 'tests'}`;
    if (!list) return;
    list.replaceChildren();
    if (!tests.length) {
      const empty = document.createElement('li');
      empty.className = 'announce-empty';
      empty.textContent = 'No speaker tests yet.';
      list.appendChild(empty);
      return;
    }
    tests.forEach(item => {
      const attempt = item.attempt || {};
      const test = item.test || {};
      const li = document.createElement('li');
      li.className = 'announce-test';
      li.dataset.testId = test.id || '';
      const head = document.createElement('div');
      head.className = 'announce-test-head';
      const title = document.createElement('strong');
      title.textContent = `${attempt.target_name || test.target_key || '—'} · ${(test.language || '—').toUpperCase()} · volume ${test.volume_percent ?? '—'} %`;
      const badge = document.createElement('span');
      badge.className = 'announce-badge';
      if (ACTIVE.includes(attempt.status)) badge.classList.add('is-active');
      if (['failed', 'unknown'].includes(attempt.status)) badge.classList.add('is-problem');
      badge.textContent = STATUS_TEXT[attempt.status] || attempt.status || '—';
      head.append(title, badge);
      if (ACTIVE.includes(attempt.status)) {
        const stop = document.createElement('button');
        stop.type = 'button';
        stop.className = 'announce-btn announce-btn-stop';
        stop.textContent = 'Stop';
        stop.setAttribute('aria-label', `Stop speaker test on ${attempt.target_name || 'speaker'}`);
        stop.addEventListener('click', () => stopTest(test.id, stop));
        head.appendChild(stop);
      }
      const text = document.createElement('p');
      text.className = 'announce-test-text';
      text.textContent = test.text || '—';
      const facts = document.createElement('dl');
      facts.className = 'announce-test-facts';
      fact(facts, 'Requested', fmtTime(test.requested_at));
      fact(facts, 'Sent to speaker', fmtTime(attempt.dispatched_at || attempt.intent_at));
      fact(facts, 'Playback observed', fmtTime(attempt.playback_observed_at));
      fact(facts, 'Ended', fmtTime(attempt.finished_at));
      fact(facts, 'Volume before → test', `${fmtVolume(attempt.volume_before)} → ${fmtVolume(attempt.volume_applied)}`);
      fact(facts, 'Volume restore', RESTORE_TEXT[attempt.volume_restore] || '—');
      fact(facts, 'Stop', stopText(attempt));
      fact(facts, 'Detail', REASON_TEXT[attempt.reason] || attempt.reason || '—');
      li.append(head, text, facts);
      list.appendChild(li);
    });
  }

  async function loadTests() {
    try {
      const { tests } = await api('/tests');
      renderTests(Array.isArray(tests) ? tests : []);
      const busy = (tests || []).some(t => t.attempt && ACTIVE.includes(t.attempt.status));
      clearTimeout(pollTimer);
      if (busy) pollTimer = setTimeout(loadTests, 2000);
    } catch (error) {
      showMessage(`Could not load speaker tests: ${error.message}`, true);
    }
  }

  function bindRange(inputId, outputId) {
    const input = byId(inputId);
    const output = byId(outputId);
    if (!(input instanceof HTMLInputElement) || !output) return;
    const update = () => {
      output.textContent = `${input.value} %`;
    };
    input.addEventListener('input', update);
    update();
  }

  function init() {
    if (!byId('announceForm')) return;
    bindRange('announceChime', 'announceChimeValue');
    bindRange('announceVolume', 'announceVolumeValue');
    const text = byId('announceText');
    const counter = byId('announceTextCount');
    if (text && counter) {
      text.addEventListener('input', () => {
        counter.textContent = `${text.value.length} / ${text.maxLength > 0 ? text.maxLength : 500}`;
      });
    }
    const preview = byId('announcementPreviewButton');
    if (preview) preview.addEventListener('click', runPreview);
    const test = byId('announcementTestButton');
    if (test) test.addEventListener('click', runTest);
    const refresh = byId('announcementStatusRefresh');
    if (refresh) refresh.addEventListener('click', loadStatus);
    loadStatus();
    loadTests();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
