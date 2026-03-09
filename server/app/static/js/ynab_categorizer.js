/**
 * YNAB Categorizer UI
 */

console.log('📒 ynab_categorizer.js loaded');

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  } catch {
    return '—';
  }
}

function fmtDateDDMMYYYY(dateStr) {
  if (!dateStr) return '—';
  const raw = String(dateStr).trim();
  const isoMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (isoMatch) {
    return `${isoMatch[3]}-${isoMatch[2]}-${isoMatch[1]}`;
  }
  try {
    const d = new Date(raw);
    if (!Number.isFinite(d.getTime())) return '—';
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const yyyy = String(d.getFullYear());
    return `${dd}-${mm}-${yyyy}`;
  } catch {
    return '—';
  }
}

function fmtEurFromMilliunits(value) {
  if (value === null || value === undefined || value === '') return '—';
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '—';
  const euros = amount / 1000;
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: 'EUR',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(euros);
  } catch {
    return `${euros.toFixed(2)} €`;
  }
}

function showMessage(text, kind = 'ok') {
  const message = document.getElementById('ynabMessage');
  if (!message) return;
  message.classList.remove('is-ok', 'is-error');
  if (!text) {
    message.style.display = 'none';
    message.textContent = '';
    return;
  }
  message.style.display = 'block';
  message.textContent = text;
  message.classList.add(kind === 'error' ? 'is-error' : 'is-ok');
}

function getCsrfToken() {
  const tokenInput = document.getElementById('csrfToken');
  return tokenInput ? tokenInput.value : '';
}

async function apiRequest(url, options = {}) {
  const opts = Object.assign({ method: 'GET', credentials: 'same-origin' }, options || {});
  const headers = Object.assign({}, opts.headers || {});
  if (opts.method !== 'GET') {
    const csrf = getCsrfToken();
    if (csrf) {
      headers['X-CSRFToken'] = csrf;
      headers['X-CSRF-Token'] = csrf;
    }
  }
  opts.headers = headers;

  const response = await fetch(url, opts);
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok || (data && data.ok === false)) {
    const message = data && data.message ? data.message : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return data;
}

function makeCategoryOptions(categories, selectedId) {
  const options = [];
  categories.forEach(cat => {
    const id = cat && cat.id ? String(cat.id) : '';
    const name = cat && cat.name ? String(cat.name) : id;
    if (!id) return;
    const selected = selectedId && selectedId === id ? ' selected' : '';
    options.push(`<option value="${id}"${selected}>${name}</option>`);
  });
  return options.join('');
}

function badgeClass(label) {
  if (label === 'High') return 'high';
  if (label === 'Medium') return 'medium';
  return 'low';
}

function serializeDateForSort(dateStr) {
  if (!dateStr) return 0;
  const digits = String(dateStr).replace(/[^0-9]/g, '');
  const n = Number(digits.slice(0, 8));
  return Number.isFinite(n) ? n : 0;
}

let queueState = {
  categories: [],
  groups: [],
  queue_filter_mode: 'strict',
};

function selectedTransactionIds() {
  const ids = [];
  const checkboxes = document.querySelectorAll('.ynab-tx-check');
  checkboxes.forEach(cb => {
    if (cb instanceof HTMLInputElement && cb.checked && cb.value) {
      ids.push(cb.value);
    }
  });
  return ids;
}

function renderGlobalCategorySelect() {
  const select = document.getElementById('globalCategorySelect');
  if (!select) return;
  const currentValue = select.value;
  select.innerHTML = makeCategoryOptions(queueState.categories, currentValue || '');
}

function renderQueue(payload) {
  queueState = payload;

  const groupsEl = document.getElementById('ynabGroups');
  const summaryEl = document.getElementById('queueSummary');
  const modeSelect = document.getElementById('queueFilterMode');

  if (modeSelect && payload.queue_filter_mode) {
    modeSelect.value = payload.queue_filter_mode;
  }

  renderGlobalCategorySelect();

  if (summaryEl) {
    summaryEl.textContent = `Groups: ${payload.group_count || 0} | Transactions: ${payload.transaction_count || 0}`;
  }

  if (!groupsEl) return;

  const groups = Array.isArray(payload.groups) ? payload.groups.slice() : [];
  groups.sort((a, b) => {
    const ranks = { High: 0, Medium: 1, Low: 2 };
    const rankA = Object.prototype.hasOwnProperty.call(ranks, a.confidence_label) ? ranks[a.confidence_label] : 3;
    const rankB = Object.prototype.hasOwnProperty.call(ranks, b.confidence_label) ? ranks[b.confidence_label] : 3;
    if (rankA !== rankB) return rankA - rankB;
    return serializeDateForSort(b.latest_date) - serializeDateForSort(a.latest_date);
  });

  const html = groups.map(group => {
    const suggestion = group.suggestion || null;
    const suggestedCategory = suggestion && suggestion.category_id ? suggestion.category_id : '';
    const suggestionText = suggestion
      ? `${suggestion.category_name || suggestion.category_id} (${Math.round((suggestion.confidence || 0) * 100)}%)`
      : 'No suggestion';
    const label = group.confidence_label || 'Low';

    const txRows = (group.transactions || []).map(tx => {
      const txId = tx && tx.id ? String(tx.id) : '';
      const memo = tx && tx.memo ? String(tx.memo) : '—';
      const date = fmtDateDDMMYYYY(tx && tx.date ? String(tx.date) : '');
      const amount = fmtEurFromMilliunits(tx ? tx.amount_milliunits : null);
      return `
        <li class="ynab-tx-item">
          <input class="ynab-tx-check" type="checkbox" value="${txId}">
          <div class="ynab-tx-main">
            <div>${txId}</div>
            <div class="ynab-tx-memo">${memo || '—'}</div>
            <div class="ynab-tx-date">${date}</div>
          </div>
          <div class="ynab-tx-amount">${amount}</div>
        </li>`;
    }).join('');

    return `
      <article class="ynab-group" data-payee="${group.payee_normalized}">
        <div class="ynab-group-header">
          <div>
            <div class="ynab-group-title">${group.payee_display || group.payee_normalized}</div>
            <div>${group.transaction_count || 0} transaction(s)</div>
          </div>
          <span class="ynab-badge ${badgeClass(label)}">${label}</span>
        </div>
        <div class="ynab-group-controls">
          <span>Suggestion: ${suggestionText}</span>
          <select class="ynab-group-category">${makeCategoryOptions(queueState.categories || [], suggestedCategory)}</select>
          <button type="button" class="btnApplyGroup">Apply group</button>
        </div>
        <ul class="ynab-tx-list">${txRows}</ul>
      </article>`;
  }).join('');

  groupsEl.innerHTML = html || '<p>No uncategorized transactions in queue.</p>';

  const groupButtons = document.querySelectorAll('.btnApplyGroup');
  groupButtons.forEach(btn => {
    btn.addEventListener('click', async event => {
      const target = event.currentTarget;
      if (!(target instanceof HTMLElement)) return;
      const group = target.closest('.ynab-group');
      if (!group) return;
      const txIds = [];
      const checkboxes = group.querySelectorAll('.ynab-tx-check');
      checkboxes.forEach(cb => {
        if (cb instanceof HTMLInputElement && cb.value) {
          txIds.push(cb.value);
          cb.checked = true;
        }
      });
      const catSelect = group.querySelector('.ynab-group-category');
      const categoryId = catSelect instanceof HTMLSelectElement ? catSelect.value : '';
      if (!txIds.length || !categoryId) {
        showMessage('Group apply requires transactions and category', 'error');
        return;
      }
      await applyTransactions(txIds, categoryId);
    });
  });

  const transactionRows = document.querySelectorAll('.ynab-tx-item');
  transactionRows.forEach(row => {
    row.addEventListener('click', event => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.classList.contains('ynab-tx-check')) {
        return;
      }
      const checkbox = row.querySelector('.ynab-tx-check');
      if (!(checkbox instanceof HTMLInputElement)) return;
      checkbox.checked = !checkbox.checked;
    });
  });
}

async function loadQueue() {
  showMessage('Loading queue...');
  try {
    const data = await apiRequest('/api/ynab-categorizer/queue');
    renderQueue(data);
    showMessage('Queue loaded', 'ok');
  } catch (error) {
    console.error('Queue load failed:', error);
    showMessage(`Queue load failed: ${error.message}`, 'error');
  }
}

async function loadConfig() {
  const modeSelect = document.getElementById('queueFilterMode');
  if (!modeSelect) return;
  try {
    const data = await apiRequest('/api/ynab-categorizer/config');
    if (data && data.queue_filter_mode) {
      modeSelect.value = data.queue_filter_mode;
    }
  } catch (error) {
    console.error('Config load failed:', error);
  }
}

async function loadBootstrapStatus() {
  const statusEl = document.getElementById('bootstrapStatus');
  if (!statusEl) return;
  try {
    const data = await apiRequest('/api/ynab-categorizer/bootstrap-status');
    if (!data || !data.bootstrapped || !data.state) {
      statusEl.textContent = 'Bootstrap status: not bootstrapped';
      return;
    }
    statusEl.textContent = `Bootstrap status: ${fmtDateDDMMYYYY(data.state.bootstrapped_at)}`;
  } catch (error) {
    console.error('Bootstrap status failed:', error);
    statusEl.textContent = 'Bootstrap status: failed to load';
  }
}

async function applyTransactions(txIds, categoryId) {
  showMessage(`Applying category to ${txIds.length} transaction(s)...`);
  try {
    await apiRequest('/api/ynab-categorizer/apply', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ transaction_ids: txIds, category_id: categoryId }),
    });
    showMessage(`Applied category to ${txIds.length} transaction(s)`, 'ok');
    await loadQueue();
  } catch (error) {
    console.error('Apply failed:', error);
    showMessage(`Apply failed: ${error.message}`, 'error');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (!YNAB_CONFIGURED) {
    return;
  }

  const refreshBtn = document.getElementById('btnRefreshQueue');
  const applySelectedBtn = document.getElementById('btnApplySelected');
  const bootstrapBtn = document.getElementById('btnBootstrap');
  const saveModeBtn = document.getElementById('btnSaveMode');
  const globalCategory = document.getElementById('globalCategorySelect');

  if (refreshBtn) {
    refreshBtn.addEventListener('click', loadQueue);
  }

  if (saveModeBtn) {
    saveModeBtn.addEventListener('click', async () => {
      const modeSelect = document.getElementById('queueFilterMode');
      const mode = modeSelect instanceof HTMLSelectElement ? modeSelect.value : '';
      if (!mode) {
        showMessage('Select queue filter mode first', 'error');
        return;
      }
      try {
        await apiRequest('/api/ynab-categorizer/config', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ queue_filter_mode: mode }),
        });
        showMessage('Queue filter mode saved', 'ok');
        await loadQueue();
      } catch (error) {
        console.error('Mode save failed:', error);
        showMessage(`Failed to save queue mode: ${error.message}`, 'error');
      }
    });
  }

  if (applySelectedBtn) {
    applySelectedBtn.addEventListener('click', async () => {
      const txIds = selectedTransactionIds();
      const categoryId = globalCategory instanceof HTMLSelectElement ? globalCategory.value : '';
      if (!txIds.length) {
        showMessage('Select at least one transaction', 'error');
        return;
      }
      if (!categoryId) {
        showMessage('Select a category first', 'error');
        return;
      }
      await applyTransactions(txIds, categoryId);
    });
  }

  if (bootstrapBtn) {
    bootstrapBtn.addEventListener('click', async () => {
      if (!window.confirm('Run bootstrap from last 24 months categorized history?')) {
        return;
      }
      try {
        showMessage('Bootstrapping...');
        await apiRequest('/api/ynab-categorizer/bootstrap', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ force: false }),
        });
        showMessage('Bootstrap completed', 'ok');
        await loadBootstrapStatus();
        await loadQueue();
      } catch (error) {
        console.error('Bootstrap failed:', error);
        showMessage(`Bootstrap failed: ${error.message}`, 'error');
      }
    });
  }

  if (YNAB_INITIAL_CONFIG && YNAB_INITIAL_CONFIG.queue_filter_mode) {
    const modeSelect = document.getElementById('queueFilterMode');
    if (modeSelect instanceof HTMLSelectElement) {
      modeSelect.value = YNAB_INITIAL_CONFIG.queue_filter_mode;
    }
  }

  if (YNAB_BOOTSTRAP_STATUS && YNAB_BOOTSTRAP_STATUS.state) {
    const statusEl = document.getElementById('bootstrapStatus');
    if (statusEl) {
      statusEl.textContent = `Bootstrap status: ${fmtDateDDMMYYYY(YNAB_BOOTSTRAP_STATUS.state.bootstrapped_at)}`;
    }
  }

  loadConfig();
  loadBootstrapStatus();
  loadQueue();
});
