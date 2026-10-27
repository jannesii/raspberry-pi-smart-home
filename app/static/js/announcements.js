/**
 * Spoken announcements: browser preview, explicit speaker tests, one-time and
 * recurring reminder schedules and their occurrence history with Done, Skip
 * and Stop speaking. Recurring times are sent as Helsinki wall-clock "HH:MM"
 * text, never converted in the browser.
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
    group_unavailable: 'The speaker group did not respond. Nothing was sent to the speakers separately.',
    group_not_configured: 'The speaker group is not configured (ANNOUNCEMENT_TARGETS needs its host, port and Cast id).',
    member_unavailable: 'The group played, but a speaker in it did not take part.',
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
    quiet_hours: 'Held for quiet hours.',
    music_playing: 'Waiting for music on the speaker to stop.',
    media_unknown: 'Waiting: the speaker’s media state is unclear.',
    speaker_busy: 'Waiting for another announcement on this speaker.',
    expired_before_dispatch: 'Expired before it could be sent (for example while the server was down).',
    schedule_paused: 'The schedule was paused.',
    schedule_deleted: 'The schedule was deleted.',
    dst_gap: 'This time did not exist: Helsinki clocks moved forward. Recorded as missed, not spoken.',
    acknowledged: 'Marked by you.',
    retry_not_started: 'A retry after a failed attempt could not start before the reminder expired.',
  };
  const ACK_TEXT = { done: 'Done', skipped: 'Skipped' };
  const PURPOSE_TEXT = { announcement: 'first', repeat: 'repeat', retry: 'automatic retry' };
  const STOP_OUTCOME_TEXT = {
    stopped: 'speaker confirmed it stopped',
    failed: 'speaker did not confirm the stop',
    unknown: 'stop outcome unknown',
    not_dispatched: 'cancelled before it was sent',
  };
  const OCCURRENCE_ACTIONS = {
    done: { label: 'Done', path: 'done', message: 'Marked done. No more announcements for this reminder.' },
    skip: { label: 'Skip this occurrence', path: 'skip',
      message: 'Skipped this time. The reminder schedule itself continues.' },
    snooze: { label: 'Snooze', path: 'snooze', message: 'Reminder postponed. Quiet hours still apply.' },
    stop: { label: 'Stop speaking', path: 'stop',
      message: 'Stop requested; repeats are cancelled. Done and Skip are still available.' },
  };
  const snoozeRequests = new Map();
  const MAX_ANNOUNCEMENTS = 5;
  const WEEKDAY_TEXT = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const MAX_TIMES = 12;
  const WAITING_TEXT = {
    quiet_hours: 'quiet hours',
    music_playing: 'music playing',
    media_unknown: 'speaker state unclear',
    speaker_busy: 'another announcement',
    speaker_unavailable: 'speaker not responding',
    group_unavailable: 'speaker group not responding',
  };
  const DELIVERY_TEXT = {
    none: 'Not delivered',
    delivered: 'Speaker reported playback',
    partial: 'Partly delivered',
    failed: 'Delivery failed',
    unknown: 'Delivery unknown',
  };
  const DISPOSITION_TEXT = {
    missed: 'Missed (expired)',
    cancelled: 'Cancelled',
    done: 'Done',
    skipped: 'Skipped',
    snoozed: 'Snoozed',
    superseded: 'Superseded',
    stopped: 'Stopped',
  };
  const FALLBACK_REASON_TEXT = {
    failed: 'spoken delivery failed',
    partial: 'partial delivery',
    unknown: 'delivery uncertain',
    expired_undelivered: 'expired without spoken delivery',
  };
  const FALLBACK_STATUS_TEXT = {
    pending: 'Waiting to send to Discord',
    sending: 'Sending to Discord',
    accepted: 'Accepted by Discord',
    unknown: 'Unknown – may have reached Discord; not resent',
    failed: 'Not delivered to Discord',
    unavailable: 'Not sent – Discord webhook (ALERT_WEBHOOK_URL) not configured',
    cancelled: 'Not sent – cancelled',
  };
  const FALLBACK_DETAIL_TEXT = {
    attempts_exhausted: 'all attempts failed',
    rejected: 'Discord rejected the message',
    rate_limited: 'Discord asked to wait',
    failed: 'the request could not be sent',
    acceptance_unknown: 'no answer from Discord',
    interrupted_by_restart: 'the server restarted while sending',
    message_too_long: 'the message is too long for Discord',
    acknowledged: 'you marked the reminder Done or Skipped before it was sent',
  };
  const RESTORE_TEXT = {
    restored: 'Restored',
    skipped_newer_intent: 'Left as changed by you',
    not_needed: 'Not needed',
    failed: 'Restore failed',
    unknown: 'Unknown',
    pending: 'Waiting for the announcement to end',
    unavailable: 'Unavailable – interrupted music may remain stopped',
    disabled: 'Disabled – interrupted music may remain stopped',
    unmapped_target: 'This speaker has no matched Spotify device',
  };

  const AVAILABILITY_TEXT = {
    available: 'Responded',
    unavailable: 'Not responding',
    not_checked: 'Not checked yet',
    unconfigured: 'Not configured',
    unknown: 'Unknown',
  };
  const MEMBER_AVAILABILITY_TEXT = {
    available: 'responded',
    unavailable: 'not responding',
    unknown: 'not checked',
  };
  const DISCOVERY_REASON_TEXT = {
    cast_unavailable: 'Cast support is not installed or not available on the server.',
    discovery_failed: 'Looking up Cast devices failed.',
    discovery_error: 'Looking up Cast devices failed unexpectedly.',
  };

  let targets = [];
  let discoveryTimer = null;
  let pollTimer = null;
  let reminderTimer = null;
  let previewToken = 0;
  let editing = null; // {id, revision} while editing a schedule
  let scheduleRequestId = null;
  let linkedShown = false;

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
      error.code = data && data.error;
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
    const quiet = byId('announceStatusQuiet');
    const restore = byId('announceStatusRestore');
    const quietHours = status.schedules && status.schedules.quiet_hours;
    if (quiet) quiet.textContent = quietHours ? quietHours.replace('-', '–') : 'Off';
    const quietText = byId('announceQuietText');
    if (quietText) quietText.textContent = quietHours ? quietHours.replace('-', '–') : 'off';
    if (restore) {
      restore.textContent = status.music_restoration && status.music_restoration.available
        ? (status.music_restoration.enabled ? 'Enabled – restoration requires unchanged playback ownership' : 'Connected – restoration off')
        : 'Unavailable – music an urgent reminder interrupts may stay stopped';
    }
    renderSpotify(status.music_restoration, status.targets || []);
    renderFallbackStatus(status.fallback);
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
      const configured = target.configured !== false;
      if (!configured) {
        input.disabled = true;
        input.checked = false;
        label.classList.add('is-disabled');
      }
      if (target.kind === 'group') label.classList.add('is-group');
      const text = document.createElement('span');
      text.textContent = target.kind === 'group' ? `${target.name} – speaker group` : target.name;
      const host = document.createElement('small');
      host.textContent = target.host || '—';
      const availability = document.createElement('small');
      availability.textContent = targetAvailabilityText(target);
      if (['unavailable', 'unconfigured'].includes(target.availability)) availability.classList.add('is-problem');
      text.append(host, availability);
      if (target.kind === 'group' && Array.isArray(target.member_status)) {
        const members = document.createElement('small');
        members.textContent = target.member_status
          .map(m => `${m.name || m.key}: ${AVAILABILITY_TEXT[m.availability] || m.availability || '—'}`)
          .join(' · ') || '—';
        text.appendChild(members);
      }
      label.append(input, text);
      container.appendChild(label);
    });
    if (!container.querySelector('input:checked')) {
      const first = container.querySelector('input:not(:disabled)');
      if (first) first.checked = true;
    }
    renderDiscovery(status.discovery);
    const defaults = status.defaults || {};
    const textArea = byId('announceText');
    if (textArea && defaults.max_text_length) textArea.maxLength = defaults.max_text_length;
  }

  function targetAvailabilityText(target) {
    const text = AVAILABILITY_TEXT[target.availability] || target.availability || '—';
    const observed = target.last_probe && target.last_probe.observed_at;
    if (target.availability === 'unconfigured') {
      return target.kind === 'group'
        ? 'Not configured – use “Check speakers and find Cast group”'
        : 'Not configured';
    }
    return observed ? `${text} (${fmtTime(observed)})` : text;
  }

  function renderDiscovery(discovery) {
    const state = byId('announceDiscoveryState');
    const list = byId('announceDiscoveryList');
    const config = byId('announceDiscoveryConfig');
    const json = byId('announceDiscoveryJson');
    const button = byId('announcementDiscoveryButton');
    if (!discovery) return;
    const pending = discovery.status === 'requested';
    if (button) button.disabled = pending;
    if (state) {
      if (pending) state.textContent = 'Looking for Cast devices…';
      else if (discovery.status === 'failed') {
        state.textContent = DISCOVERY_REASON_TEXT[discovery.reason] || discovery.reason || 'Lookup failed.';
      } else if (discovery.status === 'finished') {
        const devices = discovery.devices || [];
        state.textContent = devices.length
          ? `Found ${devices.length} Cast ${devices.length === 1 ? 'receiver' : 'receivers'} (${fmtTime(discovery.finished_at)}).`
          : 'No Cast receivers answered at the configured addresses.';
      } else {
        state.textContent = '';
      }
    }
    const devices = Array.isArray(discovery.devices) ? discovery.devices : [];
    if (list) {
      list.replaceChildren();
      devices.forEach(device => {
        const li = document.createElement('li');
        const kind = device.cast_type === 'group' ? 'speaker group' : 'speaker';
        li.textContent = `${device.name || '—'} (${kind}) · ${device.host || '—'}:${device.port ?? '—'} · Cast id ${device.device_id || '—'}`;
        list.appendChild(li);
      });
      list.hidden = !devices.length;
    }
    const suggested = Array.isArray(discovery.suggested_targets) ? discovery.suggested_targets : [];
    if (config && json) {
      json.textContent = suggested.length ? JSON.stringify(suggested, null, 2) : '';
      config.hidden = !(discovery.status === 'finished' && suggested.length);
    }
    clearTimeout(discoveryTimer);
    if (pending) discoveryTimer = setTimeout(loadStatus, 1500);
  }

  async function runDiscovery() {
    const button = byId('announcementDiscoveryButton');
    if (button) button.disabled = true;
    try {
      const { discovery } = await api('/discovery', { method: 'POST', body: '{}' });
      renderDiscovery(discovery);
      setTimeout(loadStatus, 1500);
    } catch (error) {
      if (button) button.disabled = false;
      showMessage(`Could not check speakers: ${error.message}`, true);
    }
  }

  function memberEvidenceText(attempt) {
    const members = Array.isArray(attempt.member_evidence) ? attempt.member_evidence : [];
    if (!members.length) return '—';
    return members.map(m => {
      const parts = [MEMBER_AVAILABILITY_TEXT[m.availability] || m.availability || '—'];
      if (m.in_group === true) parts.push('listed in the group');
      else if (m.in_group === false) parts.push('not listed in the group');
      if (m.left_group_during_playback === true) parts.push('left the group during playback');
      return `${m.name || m.key}: ${parts.join(', ')}`;
    }).join(' · ');
  }

  function groupFacts(facts, attempt) {
    if (!attempt || attempt.evidence_scope !== 'group') return;
    fact(facts, 'Evidence', 'Group playback report only – speakers do not report what was audible');
    fact(facts, 'Speakers in group', memberEvidenceText(attempt));
  }

  function renderFallbackStatus(fallback) {
    const channel = byId('announceStatusFallback');
    const link = byId('announceStatusLink');
    if (channel) {
      if (!fallback) {
        channel.textContent = '—';
      } else if (!fallback.available) {
        channel.textContent = 'Not configured – set ALERT_WEBHOOK_URL; failed or missed reminders stay only here';
      } else {
        let text = `Available (Discord, up to ${fallback.max_attempts || '—'} attempts)`;
        const last = fallback.last;
        if (last) {
          text += `. Last: ${FALLBACK_STATUS_TEXT[last.status] || last.status} ${fmtTime(last.updated_at)}`;
        }
        channel.textContent = text;
      }
    }
    if (link) {
      link.textContent = fallback && fallback.link_origin
        ? fallback.link_origin
        : 'Not configured – set ANNOUNCEMENT_PUBLIC_ORIGIN to include a link';
    }
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
      if (attempt.interrupted_media) fact(facts, 'Music restore', RESTORE_TEXT[attempt.media_restore] || '—');
      fact(facts, 'Stop', stopText(attempt));
      fact(facts, 'Detail', REASON_TEXT[attempt.reason] || attempt.reason || '—');
      groupFacts(facts, attempt);
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

  // Reminders -------------------------------------------------------------------
  function helsinkiToday() {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Helsinki', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date());
    const get = type => (parts.find(p => p.type === type) || {}).value;
    return `${get('year')}-${get('month')}-${get('day')}`;
  }

  function fmtDue(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat('fi-FI', {
      timeZone: 'Europe/Helsinki',
      weekday: 'short',
      day: 'numeric',
      month: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(date);
  }

  function fmtLocal(local) {
    // "YYYY-MM-DDTHH:MM" Helsinki wall time, shown as-is (no timezone maths).
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})$/.exec(local || '');
    if (!match) return '—';
    return `${Number(match[3])}.${Number(match[2])}.${match[1]} ${match[4]}`;
  }

  function repeatMode() {
    const checked = document.querySelector('input[name="announceRepeat"]:checked');
    return checked ? checked.value : 'once';
  }

  function applyRepeatMode() {
    const mode = repeatMode();
    const once = byId('announceOnceFields');
    const recurring = byId('announceRecurringFields');
    const weekdays = byId('announceWeekdayGroup');
    if (once) once.hidden = mode !== 'once';
    if (recurring) recurring.hidden = mode === 'once';
    if (weekdays) weekdays.hidden = mode !== 'weekdays';
    const list = byId('announceTimesList');
    if (mode !== 'once' && list && !list.children.length) addTimeRow('08:00', false);
  }

  function setRepeatMode(mode) {
    document.querySelectorAll('input[name="announceRepeat"]').forEach(input => {
      input.checked = input.value === mode;
    });
    applyRepeatMode();
  }

  function renumberTimes() {
    const list = byId('announceTimesList');
    if (!list) return;
    const rows = Array.from(list.children);
    rows.forEach((row, index) => {
      const input = row.querySelector('input');
      const label = row.querySelector('label');
      const remove = row.querySelector('button');
      const id = `announceRecurringTime${index + 1}`;
      if (input) input.id = id;
      if (label) {
        label.htmlFor = id;
        label.textContent = `Time ${index + 1}`;
      }
      if (remove) {
        remove.hidden = rows.length === 1;
        remove.setAttribute('aria-label', `Remove time ${index + 1}${input && input.value ? ` (${input.value})` : ''}`);
      }
    });
    const add = byId('announceAddTime');
    if (add) add.disabled = rows.length >= MAX_TIMES;
  }

  function addTimeRow(value, focus) {
    const list = byId('announceTimesList');
    if (!list || list.children.length >= MAX_TIMES) return;
    const row = document.createElement('li');
    row.className = 'announce-time-row';
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'time';
    input.step = 60;
    input.required = true;
    input.value = value || '';
    input.setAttribute('aria-describedby', 'announceRecurrenceError');
    input.addEventListener('change', () => {
      scheduleRequestId = null;
      renumberTimes();
    });
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'announce-btn announce-btn-quiet';
    remove.textContent = 'Remove';
    remove.addEventListener('click', () => {
      const next = row.previousElementSibling || row.nextElementSibling;
      row.remove();
      scheduleRequestId = null;
      renumberTimes();
      const target = next && next.querySelector('input');
      if (target) target.focus();
    });
    row.append(label, input, remove);
    list.appendChild(row);
    renumberTimes();
    if (focus) input.focus();
  }

  function setTimes(times) {
    const list = byId('announceTimesList');
    if (!list) return;
    list.replaceChildren();
    (times && times.length ? times : ['08:00']).forEach(time => addTimeRow(time, false));
  }

  function recurrenceValues() {
    const times = Array.from(document.querySelectorAll('#announceTimesList input'))
      .map(input => input.value.slice(0, 5))
      .filter(Boolean);
    const weekdays = repeatMode() === 'daily'
      ? [1, 2, 3, 4, 5, 6, 7]
      : Array.from(document.querySelectorAll('input[name="announceWeekday"]:checked')).map(input => Number(input.value));
    return { weekdays, times };
  }

  function recurrenceText(recurrence) {
    if (!recurrence || !Array.isArray(recurrence.weekdays) || !Array.isArray(recurrence.times)) return '—';
    const days = recurrence.weekdays.length === 7
      ? 'Every day'
      : recurrence.weekdays.map(day => WEEKDAY_TEXT[day] || '?').join(', ');
    return `${days} at ${recurrence.times.join(', ')}`;
  }

  // Mirrors the server: announcement k starts k * interval minutes after the
  // due time and must start before expiry. Delays only make fewer fit.
  function repeatFit(count, interval, expiry) {
    const offsets = [0];
    for (let k = 1; k < count; k += 1) {
      if (!interval || k * interval >= expiry) break;
      offsets.push(k * interval);
    }
    return offsets;
  }

  function ordinal(n) {
    return ['first', 'second', 'third', 'fourth', 'fifth'][n - 1] || `#${n}`;
  }

  function repeatFitText(count, interval, expiry) {
    if (!count || count <= 1) return 'One announcement. Done, Skip and Stop speaking stay available.';
    if (!interval) return 'Choose the minutes between announcements.';
    if (!expiry) return 'Set the expiry to see how many announcements fit.';
    const offsets = repeatFit(count, interval, expiry);
    const at = offsets.map(m => `${m}`).join(', ');
    let text = `${offsets.length} of ${count} announcements fit before the reminder expires (${expiry} min): `
      + `${at} min after the due time.`;
    if (offsets.length < count) {
      text += ` The ${ordinal(offsets.length + 1)} would come after expiry and is never spoken.`;
    }
    return `${text} Repeats stop at Done, Skip or Stop speaking; waiting for music or quiet hours makes fewer fit.`;
  }

  function updateRepeatFit() {
    const count = byId('announceMaxAnnouncements');
    const interval = byId('announceRepeatInterval');
    const expiry = byId('announceExpiry');
    const fit = byId('announceRepeatFit');
    if (!count || !interval || !fit) return;
    const n = Number(count.value) || 1;
    interval.disabled = n <= 1;
    const minutes = interval.value === '' ? null : Number(interval.value);
    fit.textContent = repeatFitText(n, minutes, expiry ? Number(expiry.value) : null);
  }

  function upcomingText(item) {
    if (item.missed === 'dst_gap') return `${fmtLocal(item.local)} – does not exist (clocks move forward), recorded as missed`;
    return fmtDue(item.due_at);
  }

  function setScheduleError(field, message) {
    const due = byId('announceDueError');
    const expiry = byId('announceExpiryError');
    const recurrence = byId('announceRecurrenceError');
    const repeats = byId('announceRepeatError');
    if (due) due.textContent = '';
    if (expiry) expiry.textContent = '';
    if (recurrence) recurrence.textContent = '';
    if (repeats) repeats.textContent = '';
    ['announceDate', 'announceTime', 'announceExpiry', 'announceMaxAnnouncements', 'announceRepeatInterval']
      .forEach(id => {
      const input = byId(id);
      if (input) input.setAttribute('aria-invalid', 'false');
    });
    document.querySelectorAll('#announceTimesList input, input[name="announceWeekday"]').forEach(input => {
      input.setAttribute('aria-invalid', 'false');
    });
    if (!message) return;
    if (['recurrence', 'weekdays', 'times'].includes(field)) {
      if (recurrence) recurrence.textContent = message;
      const selector = field === 'weekdays' ? 'input[name="announceWeekday"]' : '#announceTimesList input';
      const inputs = Array.from(document.querySelectorAll(selector));
      inputs.forEach(input => input.setAttribute('aria-invalid', 'true'));
      const invalid = inputs.find(input => !input.value) || inputs[0];
      if (invalid) invalid.focus();
      return;
    }
    if (field === 'max_announcements' || field === 'repeat_interval_minutes') {
      const details = byId('announceAdvanced');
      if (details) details.open = true;
      if (repeats) repeats.textContent = message;
      const input = byId(field === 'max_announcements' ? 'announceMaxAnnouncements' : 'announceRepeatInterval');
      if (input) {
        input.setAttribute('aria-invalid', 'true');
        input.disabled = false;
        input.focus();
      }
      return;
    }
    if (field === 'expiry_minutes') {
      const details = byId('announceAdvanced');
      if (details) details.open = true;
      if (expiry) expiry.textContent = message;
      const input = byId('announceExpiry');
      if (input) {
        input.setAttribute('aria-invalid', 'true');
        input.focus();
      }
      return;
    }
    if (due) due.textContent = message;
    if (field === 'fold') {
      const group = byId('announceFoldGroup');
      if (group) group.hidden = false;
      const first = document.querySelector('input[name="announceFold"]');
      if (first) first.focus();
      return;
    }
    const date = byId('announceDate');
    const time = byId('announceTime');
    [date, time].forEach(input => input && input.setAttribute('aria-invalid', 'true'));
    if (time) time.focus();
  }

  function scheduleValues() {
    const base = formValues();
    const date = byId('announceDate');
    const time = byId('announceTime');
    const expiry = byId('announceExpiry');
    const urgent = byId('announceUrgent');
    const quiet = byId('announceQuietOverride');
    const foldGroup = byId('announceFoldGroup');
    const fold = document.querySelector('input[name="announceFold"]:checked');
    const dueDate = date ? date.value : '';
    const dueTime = time ? time.value : '';
    const recurring = repeatMode() !== 'once';
    const countInput = byId('announceMaxAnnouncements');
    const intervalInput = byId('announceRepeatInterval');
    const count = countInput && countInput.value !== '' ? Number(countInput.value) : 1;
    const interval = intervalInput && intervalInput.value !== '' ? Number(intervalInput.value) : null;
    return {
      kind: recurring ? 'recurring' : 'one_time',
      recurrence: recurring ? recurrenceValues() : null,
      text: base.text,
      language: base.language,
      target: base.target,
      volume_percent: base.volume_percent,
      chime_percent: base.chime_percent,
      due_local: !recurring && dueDate && dueTime ? `${dueDate}T${dueTime.slice(0, 5)}` : null,
      fold: !recurring && foldGroup && !foldGroup.hidden && fold ? Number(fold.value) : null,
      expiry_minutes: expiry && expiry.value !== '' ? Number(expiry.value) : null,
      max_announcements: count,
      repeat_interval_minutes: count > 1 ? interval : null,
      urgent: Boolean(urgent && urgent.checked),
      quiet_hours_override: Boolean(quiet && quiet.checked),
    };
  }

  function resetScheduleForm() {
    editing = null;
    scheduleRequestId = null;
    const legend = byId('announceScheduleLegend');
    const button = byId('announcementScheduleButton');
    const cancel = byId('announcementScheduleCancel');
    if (legend) legend.textContent = 'Reminder';
    if (button) button.textContent = 'Save reminder';
    if (cancel) cancel.hidden = true;
    const group = byId('announceFoldGroup');
    if (group) group.hidden = true;
    document.querySelectorAll('input[name="announceFold"]').forEach(input => {
      input.checked = false;
    });
    setScheduleError(null, '');
  }

  async function saveSchedule() {
    const button = byId('announcementScheduleButton');
    const values = scheduleValues();
    if (!validText(values)) return;
    if (!values.target) {
      showMessage('Choose a speaker for the reminder.', true);
      return;
    }
    if (values.kind === 'one_time' && !values.due_local) {
      setScheduleError('due_local', 'Choose a date and a time.');
      return;
    }
    if (values.kind === 'recurring') {
      if (!values.recurrence.weekdays.length) {
        setScheduleError('weekdays', 'Choose at least one weekday.');
        return;
      }
      const rows = document.querySelectorAll('#announceTimesList input');
      if (!values.recurrence.times.length || values.recurrence.times.length !== rows.length) {
        setScheduleError('times', 'Fill in every time of day, or remove the empty one.');
        return;
      }
    }
    if (values.max_announcements > MAX_ANNOUNCEMENTS || values.max_announcements < 1) {
      setScheduleError('max_announcements', `Choose 1–${MAX_ANNOUNCEMENTS} announcements.`);
      return;
    }
    if (values.max_announcements > 1 && !values.repeat_interval_minutes) {
      setScheduleError('repeat_interval_minutes', 'Choose the minutes between announcements.');
      return;
    }
    setScheduleError(null, '');
    if (button) {
      button.disabled = true;
      button.textContent = 'Saving…';
    }
    showMessage('');
    try {
      let result;
      if (editing) {
        result = await api(`/schedules/${encodeURIComponent(editing.id)}`, {
          method: 'PUT',
          body: JSON.stringify(Object.assign({ revision: editing.revision }, values)),
        });
      } else {
        if (!scheduleRequestId) scheduleRequestId = requestId();
        result = await api('/schedules', {
          method: 'POST',
          body: JSON.stringify(Object.assign({ request_id: scheduleRequestId }, values)),
        });
      }
      const due = (result.upcoming && result.upcoming[0]) || {};
      const verb = editing ? 'Reminder updated' : 'Reminder saved';
      const when = result.schedule.kind === 'recurring'
        ? `${recurrenceText(result.schedule.recurrence)}; next ${due.due_at ? upcomingText(due) : '—'}`
        : fmtDue(due.due_at || result.schedule.due_at);
      showMessage(`${verb}: ${when} (Helsinki) on ${result.schedule.target_name}.`);
      resetScheduleForm();
      await loadReminders();
    } catch (error) {
      if (['due_local', 'fold', 'expiry_minutes', 'recurrence', 'weekdays', 'times', 'max_announcements',
        'repeat_interval_minutes'].includes(error.field)) {
        setScheduleError(error.field, error.message);
      } else if (error.field === 'text') {
        setFieldError(error.message);
      }
      showMessage(`Reminder not saved: ${error.message}`, true);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = editing ? 'Save changes' : 'Save reminder';
      }
    }
  }

  function setChecked(id, value) {
    const input = byId(id);
    if (input instanceof HTMLInputElement) input.checked = Boolean(value);
  }

  function setValue(id, value) {
    const input = byId(id);
    if (input && value !== null && value !== undefined) {
      input.value = value;
      input.dispatchEvent(new Event('input'));
    }
  }

  function startEdit(schedule) {
    editing = { id: schedule.id, revision: schedule.revision };
    setValue('announceText', schedule.text);
    setValue('announceLanguage', schedule.language);
    setValue('announceChime', schedule.chime_percent);
    setValue('announceVolume', schedule.volume_percent);
    const target = document.querySelector(`input[name="announceTarget"][value="${CSS.escape(schedule.target_key)}"]`);
    if (target) target.checked = true;
    const recurrence = schedule.recurrence;
    if (schedule.kind === 'recurring' && recurrence) {
      setRepeatMode(recurrence.weekdays && recurrence.weekdays.length === 7 ? 'daily' : 'weekdays');
      document.querySelectorAll('input[name="announceWeekday"]').forEach(input => {
        input.checked = (recurrence.weekdays || []).includes(Number(input.value));
      });
      setTimes(recurrence.times);
    } else {
      setRepeatMode('once');
      const [date, time] = (schedule.due_local || 'T').split('T');
      setValue('announceDate', date);
      setValue('announceTime', time);
    }
    setValue('announceExpiry', schedule.expiry_minutes);
    setValue('announceMaxAnnouncements', schedule.max_announcements || 1);
    const interval = byId('announceRepeatInterval');
    if (interval) interval.value = schedule.repeat_interval_minutes || '';
    updateRepeatFit();
    setChecked('announceUrgent', schedule.urgent);
    setChecked('announceQuietOverride', schedule.quiet_hours_override);
    const group = byId('announceFoldGroup');
    if (group) group.hidden = schedule.due_fold === null || schedule.due_fold === undefined;
    document.querySelectorAll('input[name="announceFold"]').forEach(input => {
      input.checked = String(schedule.due_fold) === input.value;
    });
    const legend = byId('announceScheduleLegend');
    const button = byId('announcementScheduleButton');
    const cancel = byId('announcementScheduleCancel');
    if (legend) legend.textContent = 'Edit reminder';
    if (button) button.textContent = 'Save changes';
    if (cancel) cancel.hidden = false;
    const text = byId('announceText');
    if (text) text.focus();
    showMessage('Editing the reminder. Changes apply to future announcements; history stays as it was.');
  }

  async function scheduleAction(schedule, action, button) {
    if (button) button.disabled = true;
    try {
      const path = `/schedules/${encodeURIComponent(schedule.id)}`;
      if (action === 'delete') {
        await api(path, { method: 'DELETE' });
        if (editing && editing.id === schedule.id) resetScheduleForm();
        showMessage('Reminder deleted. Its history is kept below.');
      } else {
        await api(`${path}/${action}`, { method: 'POST', body: '{}' });
        showMessage(action === 'pause' ? 'Reminder paused; pending speech was cancelled.' : 'Reminder resumed.');
      }
      await loadReminders();
    } catch (error) {
      showMessage(`Could not ${action} the reminder: ${error.message}`, true);
      if (button) button.disabled = false;
    }
  }

  function actionButton(label, aria, className, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `announce-btn ${className}`;
    button.textContent = label;
    button.setAttribute('aria-label', aria);
    button.addEventListener('click', () => handler(button));
    return button;
  }

  function scheduleState(item) {
    const schedule = item.schedule || {};
    if (schedule.status === 'paused') return 'Paused';
    if (item.upcoming && item.upcoming.length) return 'Scheduled';
    if (schedule.due_passed) return 'Due time passed';
    return '—';
  }

  function renderSchedules(items) {
    const list = byId('announceSchedulesList');
    const count = byId('announceSchedulesCount');
    if (count) count.textContent = `${items.length} ${items.length === 1 ? 'reminder' : 'reminders'}`;
    if (!list) return;
    list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement('li');
      empty.className = 'announce-empty';
      empty.textContent = 'No reminders yet. Write the text, choose a speaker and when to repeat, then save.';
      list.appendChild(empty);
      return;
    }
    items.forEach(item => {
      const schedule = item.schedule || {};
      const li = document.createElement('li');
      li.className = 'announce-test announce-reminder';
      li.dataset.scheduleId = schedule.id || '';
      const head = document.createElement('div');
      head.className = 'announce-test-head';
      const title = document.createElement('strong');
      const next = item.upcoming && item.upcoming[0];
      if (schedule.kind === 'recurring') {
        title.textContent = recurrenceText(schedule.recurrence);
      } else {
        title.textContent = next ? fmtDue(next.due_at) : fmtDue(schedule.due_at);
      }
      const badge = document.createElement('span');
      badge.className = 'announce-badge';
      if (schedule.status === 'active' && next) badge.classList.add('is-active');
      badge.textContent = scheduleState(item);
      head.append(title, badge);
      const text = document.createElement('p');
      text.className = 'announce-test-text';
      text.textContent = schedule.text || '—';
      const facts = document.createElement('dl');
      facts.className = 'announce-test-facts';
      fact(facts, 'Speaker', `${schedule.target_name || '—'} · ${(schedule.language || '—').toUpperCase()}`);
      if (schedule.kind === 'recurring') {
        const coming = (item.upcoming || []).slice(0, 3).map(upcomingText);
        fact(facts, 'Next', coming.length ? coming.join('; ') : '—');
      }
      fact(facts, 'Expires after', schedule.expiry_minutes ? `${schedule.expiry_minutes} min` : '—');
      fact(facts, 'Announcements', repeatsText(schedule.repeats));
      fact(facts, 'Urgent', schedule.urgent ? 'Yes – may interrupt music' : 'No – waits for music');
      fact(facts, 'Quiet hours', schedule.quiet_hours_override ? 'May speak' : 'Held');
      const actions = document.createElement('div');
      actions.className = 'announce-item-actions';
      const name = schedule.text ? schedule.text.slice(0, 40) : 'reminder';
      actions.appendChild(actionButton('Edit', `Edit reminder: ${name}`, 'announce-btn-quiet', () => startEdit(schedule)));
      if (schedule.status === 'paused') {
        actions.appendChild(actionButton('Resume', `Resume reminder: ${name}`, 'announce-btn-quiet',
          button => scheduleAction(schedule, 'resume', button)));
      } else {
        actions.appendChild(actionButton('Pause', `Pause reminder: ${name}`, 'announce-btn-quiet',
          button => scheduleAction(schedule, 'pause', button)));
      }
      const remove = actionButton('Delete', `Delete reminder: ${name}`, 'announce-btn-stop', () => {
        remove.hidden = true;
        const confirm = actionButton('Confirm delete', `Confirm deleting reminder: ${name}`, 'announce-btn-stop',
          button => scheduleAction(schedule, 'delete', button));
        const keep = actionButton('Keep', `Keep reminder: ${name}`, 'announce-btn-quiet', () => {
          confirm.remove();
          keep.remove();
          remove.hidden = false;
          remove.focus();
        });
        actions.append(confirm, keep);
        confirm.focus();
      });
      actions.appendChild(remove);
      li.append(head, text, facts, actions);
      list.appendChild(li);
    });
  }

  function repeatsText(repeats) {
    if (!repeats || !repeats.max_announcements) return '—';
    if (repeats.max_announcements <= 1) return 'Once';
    const fit = repeats.fit < repeats.max_announcements
      ? `; only ${repeats.fit} fit before expiry` : '';
    return `Up to ${repeats.max_announcements}, every ${repeats.repeat_interval_minutes} min${fit}`;
  }

  function acknowledgementText(ack) {
    if (!ack) return 'Not acknowledged';
    return `${ACK_TEXT[ack.action] || ack.action} by ${ack.acknowledged_by || '—'} at ${fmtTime(ack.acknowledged_at)}`;
  }

  function stopEvidence(attempts) {
    const stopped = attempts.filter(a => a.stop_requested_at);
    if (!stopped.length) return null;
    const last = stopped[stopped.length - 1];
    const outcome = last.stop_outcome ? STOP_OUTCOME_TEXT[last.stop_outcome] || last.stop_outcome : 'waiting for the speaker';
    return `Requested ${fmtTime(last.stop_requested_at)} – ${outcome}`;
  }

  function countsText(occurrence) {
    const started = Number(occurrence.announcements_started) || 0;
    const max = Number(occurrence.max_announcements) || 1;
    const retries = Number(occurrence.retries_started) || 0;
    const retryText = retries ? ` · ${retries} automatic ${retries === 1 ? 'retry' : 'retries'}` : '';
    return `${started > max ? `${started} started (includes snooze)` : `${started} of ${max}`}${retryText}`;
  }

  async function occurrenceAction(occurrence, action, button, snoozeMinutes) {
    const spec = OCCURRENCE_ACTIONS[action];
    if (!spec) return;
    if (button) {
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
    }
    let postponement;
    if (action === 'snooze') {
      postponement = snoozeRequests.get(occurrence.id);
      if (!postponement || postponement.minutes !== snoozeMinutes) {
        postponement = { minutes: snoozeMinutes, request_id: requestId() };
        snoozeRequests.set(occurrence.id, postponement);
      }
    }
    try {
      await api(`/occurrences/${encodeURIComponent(occurrence.id)}/${spec.path}`, { method: 'POST', body: JSON.stringify(action === 'snooze'
          ? postponement
          : {}) });
      if (action === 'snooze') snoozeRequests.delete(occurrence.id);
      showMessage(spec.message);
      await loadReminders();
    } catch (error) {
      showMessage(`${spec.label} did not apply: ${error.message}`, true);
      if (button) {
        button.disabled = false;
        button.removeAttribute('aria-busy');
      }
      await loadReminders();
    }
  }

  function occurrenceState(occurrence) {
    if (occurrence.status === 'waiting') {
      if (occurrence.next_attempt_purpose === 'snooze') return `Snoozed until ${fmtTime(occurrence.next_attempt_at)}`;
      const reason = WAITING_TEXT[occurrence.waiting_reason];
      return reason ? `Waiting – ${reason}` : 'Waiting';
    }
    if (occurrence.status === 'announcing') return 'Speaking';
    const disposition = DISPOSITION_TEXT[occurrence.disposition];
    const delivery = DELIVERY_TEXT[occurrence.delivery] || occurrence.delivery || '—';
    if (disposition && occurrence.delivery === 'none') return disposition;
    return disposition ? `${delivery} · ${disposition}` : delivery;
  }

  function attemptSummary(attempts) {
    if (!attempts.length) return 'None';
    return attempts.map(attempt => {
      const status = STATUS_TEXT[attempt.status] || attempt.status;
      const sent = attempt.intent_at ? fmtTime(attempt.dispatched_at || attempt.intent_at) : 'not sent';
      const purpose = PURPOSE_TEXT[attempt.purpose];
      return purpose ? `${status} (${purpose}, ${sent})` : `${status} (${sent})`;
    }).join('; ');
  }

  function fallbackText(occurrence, fallback) {
    const reason = FALLBACK_REASON_TEXT[occurrence.fallback_reason] || occurrence.fallback_reason;
    if (!fallback) return reason ? `Due – ${reason}` : 'Not needed';
    let text = FALLBACK_STATUS_TEXT[fallback.status] || fallback.status || '—';
    if (fallback.status === 'accepted') text += ` ${fmtTime(fallback.accepted_at)}`;
    const detail = FALLBACK_DETAIL_TEXT[fallback.status_reason];
    if (detail && fallback.status !== 'unavailable') text += ` – ${detail}`;
    if (fallback.status === 'pending' && fallback.attempts_made > 0) {
      text += `; next try ${fmtTime(fallback.next_attempt_at)}`;
    }
    const attempts = Number(fallback.attempts_made) || 0;
    const most = Number(fallback.max_attempts) || 0;
    if (attempts) text += ` (attempt ${attempts} of ${most})`;
    return reason ? `${text}. Reason: ${reason}.` : text;
  }

  function linkedOccurrenceId() {
    const value = new URLSearchParams(window.location.search).get('occurrence') || '';
    return /^[0-9a-f-]{36}$/i.test(value) ? value : null;
  }

  function showLinkedOccurrence() {
    if (linkedShown) return;
    const wanted = linkedOccurrenceId();
    const list = byId('announceHistoryList');
    if (!wanted || !list) return;
    const match = Array.from(list.children).find(li => li.dataset && li.dataset.occurrenceId === wanted);
    if (!match) return;
    linkedShown = true;
    match.classList.add('is-linked');
    match.tabIndex = -1;
    match.scrollIntoView({ block: 'center' });
    match.focus({ preventScroll: true });
  }

  function renderHistory(items) {
    const list = byId('announceHistoryList');
    const count = byId('announceHistoryCount');
    if (count) count.textContent = `${items.length} ${items.length === 1 ? 'occurrence' : 'occurrences'}`;
    if (!list) return;
    list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement('li');
      empty.className = 'announce-empty';
      empty.textContent = 'No reminder has been due yet.';
      list.appendChild(empty);
      return;
    }
    items.forEach(item => {
      const occurrence = item.occurrence || {};
      const attempts = Array.isArray(item.attempts) ? item.attempts : [];
      const last = attempts[attempts.length - 1] || {};
      const li = document.createElement('li');
      li.className = 'announce-test announce-occurrence';
      if (linkedShown && occurrence.id === linkedOccurrenceId()) li.classList.add('is-linked');
      li.dataset.occurrenceId = occurrence.id || '';
      const head = document.createElement('div');
      head.className = 'announce-test-head';
      const title = document.createElement('strong');
      const gap = occurrence.disposition_reason === 'dst_gap';
      const due = gap ? fmtLocal(occurrence.instance_local) : fmtDue(occurrence.due_at);
      title.textContent = `${due} · ${occurrence.target_name || '—'}`;
      const badge = document.createElement('span');
      badge.className = 'announce-badge';
      if (['waiting', 'announcing'].includes(occurrence.status)) badge.classList.add('is-active');
      const fallbackProblem = item.fallback && ['failed', 'unavailable', 'unknown'].includes(item.fallback.status);
      if (['failed', 'partial', 'unknown'].includes(occurrence.delivery) || occurrence.disposition === 'missed'
        || fallbackProblem) {
        badge.classList.add('is-problem');
      }
      badge.textContent = gap ? 'Missed (clock change)' : occurrenceState(occurrence);
      head.append(title, badge);
      const text = document.createElement('p');
      text.className = 'announce-test-text';
      text.textContent = occurrence.text || '—';
      const facts = document.createElement('dl');
      facts.className = 'announce-test-facts';
      fact(facts, 'Due', gap ? `${fmtLocal(occurrence.instance_local)} (did not exist)` : fmtTime(occurrence.due_at));
      fact(facts, 'Expires', fmtTime(occurrence.expires_at));
      if (item.snoozes && item.snoozes.length) fact(facts, 'Snoozed', item.snoozes.map(s => `${s.minutes} min at ${fmtTime(s.snoozed_at)}`).join('; '));
      const waited = (occurrence.waiting_history || []).map(h => REASON_TEXT[h.reason] || h.reason);
      fact(facts, 'Waited for', waited.length ? waited.join(' ') : '—');
      fact(facts, 'Announcements', countsText(occurrence));
      fact(facts, 'Attempts', attemptSummary(attempts));
      fact(facts, 'Playback observed', fmtTime(last.playback_observed_at));
      fact(facts, 'Delivery', DELIVERY_TEXT[occurrence.delivery] || '—');
      fact(facts, 'Detail', REASON_TEXT[occurrence.delivery_reason || occurrence.disposition_reason]
        || occurrence.delivery_reason || occurrence.disposition_reason || '—');
      if (last.interrupted_media) {
        fact(facts, 'Music', 'Interrupted – restoration unavailable');
      }
      fact(facts, 'Volume restore', RESTORE_TEXT[last.volume_restore] || '—');
      if (last.interrupted_media) fact(facts, 'Music restore', RESTORE_TEXT[last.media_restore] || '—');
      groupFacts(facts, last.id ? last : null);
      fact(facts, 'Discord fallback', fallbackText(occurrence, item.fallback));
      const stop = stopEvidence(attempts);
      if (stop) fact(facts, 'Stop speaking', stop);
      fact(facts, 'Acknowledged', acknowledgementText(item.acknowledgement));
      li.append(head, text, facts);
      const available = Array.isArray(item.actions) ? item.actions : [];
      if (available.length) {
        const actions = document.createElement('div');
        actions.className = 'announce-item-actions';
        const name = occurrence.text ? occurrence.text.slice(0, 40) : 'reminder';
        available.forEach(action => {
          const spec = OCCURRENCE_ACTIONS[action];
          if (!spec) return;
          if (action === 'snooze') {
            const label = document.createElement('label');
            label.className = 'announce-snooze';
            label.textContent = 'Snooze minutes: ';
            const choices = document.createElement('select');
            choices.setAttribute('aria-label', `Snooze minutes: ${name}`);
            [5, 10, 15, 30].forEach(minutes => {
              const option = document.createElement('option');
              option.value = String(minutes);
              option.textContent = `${minutes} minutes`;
              option.selected = minutes === 10;
              choices.appendChild(option);
            });
            label.appendChild(choices);
            actions.append(label, actionButton('Snooze', `Snooze: ${name}`, 'announce-btn-quiet',
              button => occurrenceAction(occurrence, action, button, Number(choices.value))));
            return;
          }
          const style = action === 'stop' ? 'announce-btn-stop' : (action === 'done' ? 'announce-btn-primary' : 'announce-btn-quiet');
          actions.appendChild(actionButton(spec.label, `${spec.label}: ${name}`, style,
            button => occurrenceAction(occurrence, action, button)));
        });
        li.appendChild(actions);
      }
      list.appendChild(li);
    });
  }

  async function loadReminders() {
    try {
      const [schedules, history] = await Promise.all([api('/schedules'), api('/occurrences')]);
      const scheduleItems = Array.isArray(schedules.schedules) ? schedules.schedules : [];
      const historyItems = Array.isArray(history.occurrences) ? history.occurrences : [];
      renderSchedules(scheduleItems);
      renderHistory(historyItems);
      showLinkedOccurrence();
      const active = historyItems.some(i => i.occurrence && ['waiting', 'announcing'].includes(i.occurrence.status));
      clearTimeout(reminderTimer);
      reminderTimer = setTimeout(loadReminders, active ? 3000 : 20000);
    } catch (error) {
      showMessage(`Could not load reminders: ${error.message}`, true);
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

  let spotifyDevices = [];
  let spotifyStatus = {};
  const SPOTIFY_REASON = {
    not_configured: 'Ask the server owner to configure Spotify access before connecting.',
    disconnected: 'No account connected. Basic announcements remain available.',
    reconnect_required: 'Spotify access expired or was revoked. Connect again.',
    access_denied: 'Spotify refused playback control. Check Premium and app access, then reconnect.',
    spotify_unavailable: 'Spotify is unavailable. Interrupted music may remain stopped.',
    premium_required: 'Spotify Premium is required.',
    scopes_required: 'Playback permissions were not granted. Connect again.',
  };

  function renderSpotify(status, speakerTargets) {
    spotifyStatus = status || {};
    const note = byId('announceSpotifyState');
    if (note) note.textContent = SPOTIFY_REASON[spotifyStatus.reason] ||
      (spotifyStatus.enabled ? 'Connected. Restoration enabled for matched speakers.' : 'Connected. Restoration is off.');
    if (note && spotifyStatus.connected && spotifyStatus.premium_status === 'required_not_reported') {
      note.textContent += ' Spotify does not report the subscription type; Premium and playback-control access remain required.';
    }
    if (note && spotifyStatus.credentials_status === 'expired') note.textContent += ' Access token expired; it will be refreshed on the next Spotify request.';
    const connected = !!spotifyStatus.connected;
    ['announceSpotifyDisconnect', 'announceSpotifyFind', 'announceSpotifySave', 'announceSpotifyEnabled'].forEach(id => {
      const control = byId(id);
      if (control) control.disabled = !connected;
    });
    const connect = byId('announceSpotifyConnect');
    if (connect) connect.disabled = !spotifyStatus.configured;
    const enabled = byId('announceSpotifyEnabled');
    if (enabled) enabled.checked = !!spotifyStatus.enabled;
    const container = byId('announceSpotifyTargets');
    if (!container) return;
    container.replaceChildren();
    speakerTargets.filter(t => t.configured).forEach(target => {
      const label = document.createElement('label');
      label.className = 'announce-field';
      const text = document.createElement('span');
      text.textContent = `${target.name}: Spotify device`;
      const select = document.createElement('select');
      select.dataset.target = target.key;
      select.disabled = !connected;
      const off = document.createElement('option');
      off.value = '';
      off.textContent = 'No match – restoration unavailable here';
      select.append(off);
      const selected = (spotifyStatus.devices || {})[target.key];
      const devices = spotifyDevices.slice();
      if (selected && !devices.some(d => d.id === selected)) devices.push({id: selected, name: 'Saved device (not checked yet)'});
      devices.forEach(device => {
        const option = document.createElement('option');
        option.value = device.id;
        option.textContent = device.name + (device.restricted ? ' (playback restricted)' : '');
        option.disabled = !!device.restricted;
        option.selected = device.id === selected;
        select.append(option);
      });
      label.append(text, select);
      container.append(label);
    });
  }

  async function spotifyAction(action, button, body) {
    button.disabled = true;
    showMessage('Updating Spotify connection…');
    try {
      const result = await api(`/spotify/${action}`, {method: 'POST', body: JSON.stringify(body || {})});
      if (result.authorization_url) {
        window.location.assign(result.authorization_url);
        return;
      }
      showMessage(action === 'disconnect' ? 'Spotify disconnected. Basic announcements remain available.' : 'Music restoration settings saved.');
      await loadStatus();
    } catch (error) {
      showMessage(error.message || 'Spotify request failed.', true);
    } finally {
      button.disabled = false;
    }
  }

  function init() {
    if (!byId('announceForm')) return;
    const spotifyConnect = byId('announceSpotifyConnect');
    if (spotifyConnect) spotifyConnect.addEventListener('click', () => spotifyAction('connect', spotifyConnect));
    const spotifyDisconnect = byId('announceSpotifyDisconnect');
    if (spotifyDisconnect) spotifyDisconnect.addEventListener('click', () => spotifyAction('disconnect', spotifyDisconnect));
    const spotifyFind = byId('announceSpotifyFind');
    if (spotifyFind) spotifyFind.addEventListener('click', async () => {
      spotifyFind.disabled = true;
      showMessage('Finding Spotify devices…');
      try {
        const data = await api(`/spotify/devices`);
        spotifyDevices = Array.isArray(data.devices) ? data.devices : [];
        renderSpotify(spotifyStatus, targets);
        showMessage(spotifyDevices.length ? 'Choose the Spotify device for each speaker, then save.' : 'No Spotify devices found. Open Spotify on the intended speaker and try again.');
      } catch (error) {
        showMessage(error.message || 'Spotify devices could not be read.', true);
      } finally {
        spotifyFind.disabled = false;
      }
    });
    const spotifyForm = byId('announceSpotifyForm');
    if (spotifyForm) spotifyForm.addEventListener('submit', event => {
      event.preventDefault();
      const devices = {};
      spotifyForm.querySelectorAll('select[data-target]').forEach(select => {
        if (select.value) devices[select.dataset.target] = select.value;
      });
      spotifyAction('enable', byId('announceSpotifySave'), {enabled: byId('announceSpotifyEnabled').checked, devices});
    });
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
    const discover = byId('announcementDiscoveryButton');
    if (discover) discover.addEventListener('click', runDiscovery);
    const save = byId('announcementScheduleButton');
    if (save) save.addEventListener('click', saveSchedule);
    const cancel = byId('announcementScheduleCancel');
    if (cancel) {
      cancel.addEventListener('click', () => {
        resetScheduleForm();
        showMessage('Edit cancelled.');
      });
    }
    const date = byId('announceDate');
    if (date instanceof HTMLInputElement && !date.value) date.value = helsinkiToday();
    document.querySelectorAll('input[name="announceRepeat"]').forEach(input => {
      input.addEventListener('change', () => {
        scheduleRequestId = null;
        setScheduleError(null, '');
        applyRepeatMode();
      });
    });
    document.querySelectorAll('input[name="announceWeekday"]').forEach(input => {
      input.addEventListener('change', () => {
        scheduleRequestId = null;
      });
    });
    ['announceMaxAnnouncements', 'announceRepeatInterval', 'announceExpiry'].forEach(id => {
      const input = byId(id);
      if (input) input.addEventListener('input', updateRepeatFit);
    });
    updateRepeatFit();
    const addTime = byId('announceAddTime');
    if (addTime) addTime.addEventListener('click', () => addTimeRow('', true));
    applyRepeatMode();
    ['announceDate', 'announceTime'].forEach(id => {
      const input = byId(id);
      if (input) {
        input.addEventListener('change', () => {
          scheduleRequestId = null;
          const group = byId('announceFoldGroup');
          if (group && !editing) group.hidden = true;
        });
      }
    });
    loadStatus().then(loadReminders);
    loadTests();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
