/**
 * Logging Settings - Auto-apply logic with clean UI updates
 */
document.addEventListener('DOMContentLoaded', () => {
  const statusEl = document.getElementById('autoSaveStatus');
  const filterInput = document.getElementById('filterInput');
  const resetBtn = document.getElementById('resetAllBtn');
  const overridesPanel = document.getElementById('overridesPanel');
  const overridesList = document.getElementById('overridesList');
  const overrideCount = document.getElementById('overrideCount');
  const csrfInput = document.getElementById('csrfToken');
  const csrf = csrfInput?.value || '';

  // Track overrides in memory
  const overrides = {
    root: null,
    loggers: {},
    handlers: {}
  };

  // Initialize from DOM
  document.querySelectorAll('.override-item').forEach(item => {
    const type = item.dataset.type;
    const name = item.dataset.name;
    const level = item.querySelector('.override-level')?.textContent;
    if (type === 'root') {
      overrides.root = level;
    } else if (type === 'logger') {
      overrides.loggers[name] = level;
    } else if (type === 'handler') {
      overrides.handlers[name] = level;
    }
  });

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text || '';
    statusEl.dataset.kind = kind || '';
    if (kind === 'ok') {
      setTimeout(() => {
        if (statusEl.dataset.kind === 'ok') {
          statusEl.textContent = '';
          statusEl.dataset.kind = '';
        }
      }, 2000);
    }
  }

  function updateOverridesPanel() {
    const total = (overrides.root ? 1 : 0) +
                  Object.keys(overrides.loggers).length +
                  Object.keys(overrides.handlers).length;

    if (overrideCount) {
      overrideCount.textContent = total;
    }

    if (overridesPanel) {
      overridesPanel.style.display = total > 0 ? '' : 'none';
    }

    // Rebuild the overrides list
    if (overridesList) {
      overridesList.innerHTML = '';
      
      if (overrides.root) {
        overridesList.appendChild(createOverrideItem('root', 'root', overrides.root));
      }
      
      Object.entries(overrides.loggers).forEach(([name, level]) => {
        overridesList.appendChild(createOverrideItem('logger', name, level));
      });
      
      Object.entries(overrides.handlers).forEach(([name, level]) => {
        overridesList.appendChild(createOverrideItem('handler', name, level));
      });
    }
  }

  function createOverrideItem(type, name, level) {
    const div = document.createElement('div');
    div.className = 'override-item';
    div.dataset.type = type;
    if (name !== 'root') div.dataset.name = name;
    div.innerHTML = `
      <span class="override-name">${name}</span>
      <span class="override-arrow">→</span>
      <span class="override-level">${level}</span>
    `;
    return div;
  }

  function updateRowOverrideState(select, hasOverride) {
    const row = select.closest('.logger-row, .handler-row');
    if (!row) return;

    const control = select.closest('.logger-control, .handler-control');
    let dot = control?.querySelector('.override-dot');

    if (hasOverride) {
      row.classList.add('has-override');
      if (!dot && control) {
        dot = document.createElement('span');
        dot.className = 'override-dot';
        dot.title = 'Has override';
        dot.textContent = '●';
        control.insertBefore(dot, select);
      }
    } else {
      row.classList.remove('has-override');
      if (dot) dot.remove();
    }
  }

  async function applyChange(select) {
    const kind = select.dataset.kind;
    const name = select.dataset.name || '';
    const effectiveLevel = select.dataset.effective || '';
    const newLevel = select.value;

    // Determine if this creates or removes an override
    // For root: any change is an override (we'll track it)
    // For loggers/handlers: if newLevel === effectiveLevel, remove override
    let levelToSend = newLevel;
    let isOverride = true;

    if (kind !== 'root') {
      // If setting back to effective level, remove the override
      if (newLevel === effectiveLevel) {
        levelToSend = ''; // Empty = remove override
        isOverride = false;
      }
    }

    setStatus('Applying…', 'pending');
    select.disabled = true;

    try {
      const res = await fetch('/settings/logging/apply', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf,
          'X-CSRF-Token': csrf,
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
        },
        body: JSON.stringify({ kind, name, level: levelToSend }),
      });

      if (res.redirected) {
        setStatus('Session expired - please refresh', 'error');
        return;
      }

      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        const msg = data?.message || `HTTP ${res.status}`;
        setStatus(`Failed: ${msg}`, 'error');
        return;
      }

      // Update local state
      if (kind === 'root') {
        overrides.root = newLevel;
      } else if (kind === 'logger') {
        if (isOverride) {
          overrides.loggers[name] = newLevel;
        } else {
          delete overrides.loggers[name];
        }
        select.dataset.override = isOverride ? newLevel : '';
        updateRowOverrideState(select, isOverride);
      } else if (kind === 'handler') {
        if (isOverride) {
          overrides.handlers[name] = newLevel;
        } else {
          delete overrides.handlers[name];
        }
        select.dataset.override = isOverride ? newLevel : '';
        updateRowOverrideState(select, isOverride);
      }

      updateOverridesPanel();
      setStatus('Applied', 'ok');
    } catch (e) {
      setStatus(`Failed: ${e}`, 'error');
    } finally {
      select.disabled = false;
    }
  }

  async function resetAllOverrides() {
    setStatus('Resetting…', 'pending');

    try {
      const formData = new FormData();
      formData.append('csrf_token', csrf);
      formData.append('reset', '1');

      const res = await fetch('/settings/logging', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': csrf,
          'X-CSRF-Token': csrf,
        },
        body: formData,
      });

      if (res.ok || res.redirected) {
        // Reload page to show clean state
        window.location.reload();
      } else {
        setStatus('Failed to reset', 'error');
      }
    } catch (e) {
      setStatus(`Failed: ${e}`, 'error');
    }
  }

  // Filter functionality
  function applyFilter() {
    const query = (filterInput?.value || '').trim().toLowerCase();
    document.querySelectorAll('.logger-row').forEach(row => {
      const filterText = row.dataset.filter || '';
      const matches = !query || filterText.includes(query);
      row.classList.toggle('hidden', !matches);
    });
  }

  // Event listeners
  if (filterInput) {
    filterInput.addEventListener('input', applyFilter);
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', (e) => {
      e.preventDefault();
      if (confirm('Clear all logging overrides?')) {
        resetAllOverrides();
      }
    });
  }

  // Listen for select changes (event delegation)
  document.addEventListener('change', (e) => {
    const select = e.target;
    if (select.tagName !== 'SELECT' || !select.dataset.kind) return;
    if (!csrf) {
      setStatus('CSRF token missing - please refresh', 'error');
      return;
    }
    applyChange(select);
  });

  // Initial status
  if (!csrf) {
    setStatus('Warning: CSRF token missing', 'error');
  }

  // ============================================
  // Live Log Console
  // ============================================
  const logConsole = document.getElementById('logConsole');
  const logToggleBtn = document.getElementById('logToggleBtn');
  const logClearBtn = document.getElementById('logClearBtn');
  const logAutoScroll = document.getElementById('logAutoScroll');

  let socket = null;
  let isStreaming = false;
  const MAX_LINES = 1000;

  function detectLogLevel(line) {
    const upper = line.toUpperCase();
    if (upper.includes(' CRITICAL ') || upper.includes(' CRITICAL:')) return 'critical';
    if (upper.includes(' ERROR ') || upper.includes(' ERROR:')) return 'error';
    if (upper.includes(' WARNING ') || upper.includes(' WARNING:') || upper.includes(' WARN ')) return 'warning';
    if (upper.includes(' DEBUG ') || upper.includes(' DEBUG:')) return 'debug';
    if (upper.includes(' INFO ') || upper.includes(' INFO:')) return 'info';
    return 'info';
  }

  function formatLogLine(raw) {
    const level = detectLogLevel(raw);
    const div = document.createElement('div');
    div.className = `log-line level-${level}`;
    div.textContent = raw;
    return div;
  }

  function appendLogLine(line) {
    if (!logConsole) return;
    
    // Remove placeholder if present
    const placeholder = logConsole.querySelector('.console-placeholder');
    if (placeholder) placeholder.remove();

    const lineEl = formatLogLine(line);
    logConsole.appendChild(lineEl);

    // Trim old lines
    while (logConsole.children.length > MAX_LINES) {
      logConsole.removeChild(logConsole.firstChild);
    }

    // Auto-scroll
    if (logAutoScroll?.checked) {
      logConsole.scrollTop = logConsole.scrollHeight;
    }
  }

  function clearConsole() {
    if (!logConsole) return;
    logConsole.innerHTML = '<div class="console-placeholder">Console cleared</div>';
  }

  function startLogStream() {
    if (socket) return;

    try {
      socket = io({
        transports: ['websocket', 'polling'],
      });

      socket.on('connect', () => {
        console.log('Log stream socket connected');
        socket.emit('log_stream', { action: 'subscribe', lines: 100 });
      });

      socket.on('log_line', (data) => {
        if (data?.line) {
          appendLogLine(data.line);
        }
      });

      socket.on('error', (data) => {
        console.error('Log stream error:', data);
        appendLogLine(`[ERROR] ${data?.message || 'Connection error'}`);
      });

      socket.on('disconnect', () => {
        console.log('Log stream socket disconnected');
        if (isStreaming) {
          appendLogLine('[DISCONNECTED] Reconnecting...');
        }
      });

      isStreaming = true;
      updateToggleButton();
      
      // Remove placeholder
      const placeholder = logConsole?.querySelector('.console-placeholder');
      if (placeholder) {
        placeholder.textContent = 'Connecting...';
      }
    } catch (e) {
      console.error('Failed to start log stream:', e);
      setStatus('Failed to connect to log stream', 'error');
    }
  }

  function stopLogStream() {
    if (!socket) return;

    try {
      socket.emit('log_stream', { action: 'unsubscribe' });
      socket.disconnect();
    } catch (e) {
      console.error('Error stopping log stream:', e);
    }
    
    socket = null;
    isStreaming = false;
    updateToggleButton();
    appendLogLine('[STOPPED] Log stream stopped');
  }

  function updateToggleButton() {
    if (!logToggleBtn) return;
    logToggleBtn.dataset.streaming = isStreaming ? 'true' : 'false';
    logToggleBtn.textContent = isStreaming ? 'Stop' : 'Start';
  }

  // Log console event listeners
  if (logToggleBtn) {
    logToggleBtn.addEventListener('click', () => {
      if (isStreaming) {
        stopLogStream();
      } else {
        startLogStream();
      }
    });
  }

  if (logClearBtn) {
    logClearBtn.addEventListener('click', clearConsole);
  }

  // Clean up on page unload
  window.addEventListener('beforeunload', () => {
    if (socket) {
      try {
        socket.emit('log_stream', { action: 'unsubscribe' });
        socket.disconnect();
      } catch (e) {}
    }
  });
});
