/**
 * Car Heater Logs Modal - Displays ESP32 logs fetched via get_logs command
 */

console.log('📜 car_heater_logs.js loaded');

const LOGS_REQUEST_TIMEOUT_MS = 12000;
const LOGS_DEDUPE_WINDOW_MS = 3000;

let logsRequestTimeout = null;
let pendingLogsRequestTs = 0;
let lastLogsSignature = '';
let lastLogsSignatureTs = 0;

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '—';
  }
}

function normalizeLogs(raw) {
  if (raw === null || raw === undefined) return '';
  if (typeof raw === 'string') return raw.trim();
  try {
    return String(raw).trim();
  } catch {
    return '';
  }
}

function hashText(text) {
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash) ^ text.charCodeAt(i);
  }
  return (hash >>> 0).toString(16);
}

function getSignatureTsBucket(ts) {
  if (!ts) return Math.floor(Date.now() / LOGS_DEDUPE_WINDOW_MS);
  const parsedMs = Date.parse(ts);
  if (!Number.isFinite(parsedMs)) {
    return Math.floor(Date.now() / LOGS_DEDUPE_WINDOW_MS);
  }
  return Math.floor(parsedMs / LOGS_DEDUPE_WINDOW_MS);
}

function getLogsSignature(payload) {
  const logs = normalizeLogs(payload && payload.logs);
  const tsBucket = getSignatureTsBucket(payload && payload.received_ts);
  return `${hashText(logs)}:${tsBucket}`;
}

function clearLogsRequestTimeout() {
  if (logsRequestTimeout) {
    clearTimeout(logsRequestTimeout);
    logsRequestTimeout = null;
  }
}

function setLogsState(state, text) {
  const logsState = document.getElementById('carHeaterLogsState');
  if (!logsState) return;
  logsState.classList.remove('is-waiting', 'is-ready', 'is-error');
  if (state === 'waiting') logsState.classList.add('is-waiting');
  if (state === 'ready') logsState.classList.add('is-ready');
  if (state === 'error') logsState.classList.add('is-error');
  logsState.textContent = text || '—';
}

function setLogsMeta(source, receivedTs) {
  const logsSource = document.getElementById('carHeaterLogsSource');
  const logsReceived = document.getElementById('carHeaterLogsReceived');
  if (logsSource) logsSource.textContent = `Source: ${source || '—'}`;
  if (logsReceived) logsReceived.textContent = `Received: ${fmtTs(receivedTs)}`;
}

function setLogsContent(text) {
  const logsContent = document.getElementById('carHeaterLogsContent');
  if (!logsContent) return;
  logsContent.textContent = text || '—';
}

function isLogsModalOpen() {
  const modal = document.getElementById('carHeaterLogsModal');
  return !!(modal && modal.classList.contains('is-open'));
}

function openLogsModal() {
  const modal = document.getElementById('carHeaterLogsModal');
  if (!modal) return;
  modal.classList.add('is-open');
  modal.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
}

function closeLogsModal() {
  const modal = document.getElementById('carHeaterLogsModal');
  if (!modal) return;
  modal.classList.remove('is-open');
  modal.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}

function applyLogsPayload(payload, sourceHint = '') {
  const logs = normalizeLogs(payload && payload.logs);
  const receivedTs = (payload && payload.received_ts) || new Date().toISOString();
  const source = (payload && payload.source) || sourceHint || '—';
  const deviceId = payload && payload.device_id ? String(payload.device_id) : '';
  const note = payload && payload.note ? String(payload.note) : '';

  const signature = getLogsSignature({
    logs,
    received_ts: receivedTs,
  });
  const now = Date.now();
  if (signature === lastLogsSignature && (now - lastLogsSignatureTs) < LOGS_DEDUPE_WINDOW_MS) {
    console.log('📜 duplicate car_heater_logs payload skipped');
    if (pendingLogsRequestTs) {
      pendingLogsRequestTs = 0;
      clearLogsRequestTimeout();
      let sourceLabel = source;
      if (deviceId) sourceLabel = `${source} (${deviceId})`;
      setLogsMeta(sourceLabel, receivedTs);
      if (!logs) {
        setLogsContent('—');
        setLogsState('ready', 'No logs available');
      } else {
        setLogsContent(logs);
        setLogsState('ready', 'Latest snapshot unchanged');
      }
      openLogsModal();
    }
    return;
  }
  lastLogsSignature = signature;
  lastLogsSignatureTs = now;

  pendingLogsRequestTs = 0;
  clearLogsRequestTimeout();

  let sourceLabel = source;
  if (deviceId) sourceLabel = `${source} (${deviceId})`;
  setLogsMeta(sourceLabel, receivedTs);

  if (!logs) {
    setLogsContent('—');
    setLogsState('ready', 'No logs available');
  } else {
    setLogsContent(logs);
    setLogsState('ready', note ? `Latest snapshot loaded (${note})` : 'Latest snapshot loaded');
  }

  openLogsModal();
}

function handleGetLogsRequested() {
  pendingLogsRequestTs = Date.now();
  clearLogsRequestTimeout();

  setLogsMeta('pending', new Date().toISOString());
  setLogsState('waiting', 'Waiting for logs...');

  logsRequestTimeout = setTimeout(() => {
    if (!pendingLogsRequestTs) return;
    pendingLogsRequestTs = 0;
    setLogsContent('—');
    setLogsMeta('timeout', new Date().toISOString());
    setLogsState('error', 'No logs received yet');
    openLogsModal();
  }, LOGS_REQUEST_TIMEOUT_MS);
}

function resolveActionResultOk(data) {
  if (!data || typeof data !== 'object') return true;
  if (typeof data.ok === 'boolean') return data.ok;
  if (typeof data.success === 'boolean') return data.success;
  return true;
}

document.addEventListener('DOMContentLoaded', () => {
  const modal = document.getElementById('carHeaterLogsModal');
  const closeBtn = document.getElementById('carHeaterLogsClose');
  const copyBtn = document.getElementById('carHeaterLogsCopy');
  const clearBtn = document.getElementById('carHeaterLogsClear');

  if (closeBtn) {
    closeBtn.addEventListener('click', closeLogsModal);
  }
  if (modal) {
    modal.addEventListener('click', evt => {
      if (evt.target === modal) closeLogsModal();
    });
  }
  document.addEventListener('keydown', evt => {
    if (evt.key === 'Escape' && isLogsModalOpen()) {
      closeLogsModal();
    }
  });

  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const logsContent = document.getElementById('carHeaterLogsContent');
      const text = logsContent ? String(logsContent.textContent || '') : '';
      if (!text || text === '—') {
        setLogsState('error', 'Nothing to copy');
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        setLogsState('ready', 'Copied logs to clipboard');
      } catch (error) {
        console.warn('Failed to copy logs:', error);
        setLogsState('error', 'Copy failed');
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      setLogsContent('—');
      setLogsMeta('—', null);
      setLogsState('ready', 'Logs cleared');
      pendingLogsRequestTs = 0;
      clearLogsRequestTimeout();
    });
  }

  window.addEventListener('car_heater_get_logs_requested', () => {
    console.log('📜 get_logs requested');
    handleGetLogsRequested();
  });

  if (!window.socket) return;

  window.socket.on('car_heater_logs', payload => {
    console.log('📜 car_heater_logs:', payload);
    applyLogsPayload(payload, 'car_heater_logs');
  });

  // Compatibility fallback when logs are nested under status payloads.
  window.socket.on('car_heater_status', payload => {
    const status = payload && payload.status ? payload.status : payload;
    if (!status || !Object.prototype.hasOwnProperty.call(status, 'logs')) return;
    applyLogsPayload(
      {
        logs: status.logs,
        received_ts: status.timestamp || (payload && payload.received_ts) || new Date().toISOString(),
        source: 'http_status',
        device_id: status.device_id || (payload && payload.device_id) || null,
      },
      'car_heater_status',
    );
  });

  window.socket.on('car_heater_action_result', data => {
    if (!data || data.action !== 'get_logs') return;
    const ok = resolveActionResultOk(data);
    if (ok) return;

    pendingLogsRequestTs = 0;
    clearLogsRequestTimeout();
    setLogsState('error', 'Get Logs failed');
    openLogsModal();
  });
});
