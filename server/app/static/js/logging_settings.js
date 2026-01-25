document.addEventListener('DOMContentLoaded', () => {
  console.log('logging_settings.js loaded');

  const input = document.getElementById('filterInput');
  const statusEl = document.getElementById('autoSaveStatus');

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text || '';
    statusEl.dataset.kind = kind || '';
  }

  const csrfInput = document.querySelector('input[name="csrf_token"]');
  const csrf = (csrfInput && csrfInput.value) ? csrfInput.value : '';
  const form = document.querySelector('form.logging-form');
  if (!form) {
    setStatus('Error: logging form not found', 'error');
    return;
  }
  if (!csrf) {
    // Page should still render, but applying will fail due to CSRF.
    setStatus('Warning: CSRF token missing; auto-apply disabled', 'error');
  } else {
    setStatus('Auto-apply enabled', '');
  }

  async function applyChange(selectEl) {
    const name = selectEl.name || '';
    let kind = '';
    let key = '';
    if (name === 'root_level') {
      kind = 'root';
    } else if (name.startsWith('logger_level|')) {
      kind = 'logger';
      key = name.split('|', 2)[1] || '';
    } else if (name.startsWith('handler_level|')) {
      kind = 'handler';
      key = name.split('|', 2)[1] || '';
    } else {
      return;
    }

    const level = (selectEl.value || '').toString();

    setStatus('Applying…', 'pending');
    const prevDisabled = selectEl.disabled;
    selectEl.disabled = true;
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
        body: JSON.stringify({ kind, name: key, level }),
      });
      if (res.redirected) {
        setStatus('Failed: redirected (not logged in?)', 'error');
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        const msg = data?.message || `HTTP ${res.status}`;
        setStatus(`Failed: ${msg}`, 'error');
        return;
      }
      setStatus('Applied', 'ok');
    } catch (e) {
      setStatus(`Failed: ${e}`, 'error');
    } finally {
      selectEl.disabled = prevDisabled;
    }
  }

  function applyFilter() {
    const q = (input.value || '').trim().toLowerCase();
    document.querySelectorAll('.filter-row').forEach(row => {
      const hay = (row.getAttribute('data-filter') || '').toLowerCase();
      row.style.display = (!q || hay.includes(q)) ? '' : 'none';
    });
  }

  if (input) {
    input.addEventListener('input', applyFilter);
  }

  // Auto-apply on change (event delegation)
  form.addEventListener('change', (e) => {
    const t = e.target;
    if (!t || t.tagName !== 'SELECT') return;
    if (!csrf) {
      setStatus('Failed: CSRF token missing', 'error');
      return;
    }
    applyChange(t);
  });
});
