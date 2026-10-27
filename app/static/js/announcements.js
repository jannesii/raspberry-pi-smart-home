/**
 * Spoken announcements: browser preview, explicit speaker tests, one-time
 * reminder schedules and their occurrence history.
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
    quiet_hours: 'Held for quiet hours.',
    music_playing: 'Waiting for music on the speaker to stop.',
    media_unknown: 'Waiting: the speaker’s media state is unclear.',
    speaker_busy: 'Waiting for another announcement on this speaker.',
    expired_before_dispatch: 'Expired before it could be sent (for example while the server was down).',
    schedule_paused: 'The schedule was paused.',
    schedule_deleted: 'The schedule was deleted.',
  };
  const WAITING_TEXT = {
    quiet_hours: 'quiet hours',
    music_playing: 'music playing',
    media_unknown: 'speaker state unclear',
    speaker_busy: 'another announcement',
    speaker_unavailable: 'speaker not responding',
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
  const FALLBACK_TEXT = {
    failed: 'Eligible – spoken delivery failed',
    partial: 'Eligible – partial delivery',
    unknown: 'Eligible – delivery uncertain',
    expired_undelivered: 'Eligible – expired without spoken delivery',
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
  let reminderTimer = null;
  let previewToken = 0;
  let editing = null; // {id, revision} while editing a schedule
  let scheduleRequestId = null;

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
        ? 'Available'
        : 'Unavailable – music an urgent reminder interrupts stays stopped';
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

  function setScheduleError(field, message) {
    const due = byId('announceDueError');
    const expiry = byId('announceExpiryError');
    if (due) due.textContent = '';
    if (expiry) expiry.textContent = '';
    ['announceDate', 'announceTime', 'announceExpiry'].forEach(id => {
      const input = byId(id);
      if (input) input.setAttribute('aria-invalid', 'false');
    });
    if (!message) return;
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
    return {
      text: base.text,
      language: base.language,
      target: base.target,
      volume_percent: base.volume_percent,
      chime_percent: base.chime_percent,
      due_local: dueDate && dueTime ? `${dueDate}T${dueTime.slice(0, 5)}` : '',
      fold: foldGroup && !foldGroup.hidden && fold ? Number(fold.value) : null,
      expiry_minutes: expiry && expiry.value !== '' ? Number(expiry.value) : null,
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
    if (legend) legend.textContent = 'One-time reminder';
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
    if (!values.due_local) {
      setScheduleError('due_local', 'Choose a date and a time.');
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
      showMessage(`${verb}: ${fmtDue(due.due_at || result.schedule.due_at)} (Helsinki) on ${result.schedule.target_name}.`);
      resetScheduleForm();
      await loadReminders();
    } catch (error) {
      if (['due_local', 'fold', 'expiry_minutes'].includes(error.field)) {
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
    const [date, time] = (schedule.due_local || 'T').split('T');
    setValue('announceDate', date);
    setValue('announceTime', time);
    setValue('announceExpiry', schedule.expiry_minutes);
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
    showMessage('Editing the reminder. Changes apply to its future announcement; history stays as it was.');
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
      empty.textContent = 'No reminders yet. Write the text, choose a speaker, date and time, then save.';
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
      title.textContent = next ? fmtDue(next.due_at) : fmtDue(schedule.due_at);
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
      fact(facts, 'Expires after', schedule.expiry_minutes ? `${schedule.expiry_minutes} min` : '—');
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

  function occurrenceState(occurrence) {
    if (occurrence.status === 'waiting') {
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
      return `${status} (${sent})`;
    }).join('; ');
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
      li.dataset.occurrenceId = occurrence.id || '';
      const head = document.createElement('div');
      head.className = 'announce-test-head';
      const title = document.createElement('strong');
      title.textContent = `${fmtDue(occurrence.due_at)} · ${occurrence.target_name || '—'}`;
      const badge = document.createElement('span');
      badge.className = 'announce-badge';
      if (['waiting', 'announcing'].includes(occurrence.status)) badge.classList.add('is-active');
      if (['failed', 'unknown'].includes(occurrence.delivery) || occurrence.disposition === 'missed') {
        badge.classList.add('is-problem');
      }
      badge.textContent = occurrenceState(occurrence);
      head.append(title, badge);
      const text = document.createElement('p');
      text.className = 'announce-test-text';
      text.textContent = occurrence.text || '—';
      const facts = document.createElement('dl');
      facts.className = 'announce-test-facts';
      fact(facts, 'Due', fmtTime(occurrence.due_at));
      fact(facts, 'Expires', fmtTime(occurrence.expires_at));
      const waited = (occurrence.waiting_history || []).map(h => REASON_TEXT[h.reason] || h.reason);
      fact(facts, 'Waited for', waited.length ? waited.join(' ') : '—');
      fact(facts, 'Attempts', attemptSummary(attempts));
      fact(facts, 'Playback observed', fmtTime(last.playback_observed_at));
      fact(facts, 'Delivery', DELIVERY_TEXT[occurrence.delivery] || '—');
      fact(facts, 'Detail', REASON_TEXT[occurrence.delivery_reason || occurrence.disposition_reason]
        || occurrence.delivery_reason || occurrence.disposition_reason || '—');
      if (last.interrupted_media) {
        fact(facts, 'Music', 'Interrupted – restoration unavailable');
      }
      fact(facts, 'Volume restore', RESTORE_TEXT[last.volume_restore] || '—');
      fact(facts, 'Fallback', FALLBACK_TEXT[occurrence.fallback_reason] || 'Not needed');
      fact(facts, 'Acknowledged', item.acknowledgement ? fmtTime(item.acknowledgement.at) : 'Not recorded');
      li.append(head, text, facts);
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
