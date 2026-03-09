/**
 * YNAB Categorizer UI
 */

console.log('📒 ynab_categorizer.js loaded');

const RECENT_CATEGORY_STORAGE_KEY = 'ynabCategorizerRecentCategories';
const RECENT_CATEGORY_LIMIT = 6;

let queueStateRaw = null;
let queueState = {
  categories: [],
  groups: [],
  queue_filter_mode: 'strict',
  show_reconciled_transactions: false,
  queue_limit_enabled: false,
  queue_limit_value: 30,
  queue_limit_unit: 'days',
  quick_apply_include_medium: false,
  default_category_id: '',
};

let queueFilterTerm = '';
let recentCategoryIds = [];
let approvalsStateRaw = null;
let approvalFilterTerm = '';
let currentView = 'categorize';

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

function txAccountFlowLabel(tx) {
  const accountName = tx && tx.account_name ? String(tx.account_name).trim() : '';
  if (!accountName) return '—';
  const amount = Number(tx && tx.amount_milliunits);
  if (Number.isFinite(amount)) {
    if (amount < 0) return `From account: ${accountName}`;
    if (amount > 0) return `To account: ${accountName}`;
  }
  return `Account: ${accountName}`;
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

function selectedApprovalIds() {
  const ids = [];
  const checkboxes = document.querySelectorAll('.ynab-approval-check');
  checkboxes.forEach(cb => {
    if (cb instanceof HTMLInputElement && cb.checked && cb.value) {
      ids.push(cb.value);
    }
  });
  return ids;
}

function visibleApprovalIds() {
  const ids = [];
  const checkboxes = document.querySelectorAll('#approvalList .ynab-approval-check');
  checkboxes.forEach(cb => {
    if (cb instanceof HTMLInputElement && cb.value) {
      ids.push(cb.value);
    }
  });
  return ids;
}

function updateFloatingApplyBarVisibility() {
  const floatingBar = document.getElementById('floatingApplyBar');
  if (!floatingBar) return;
  if (currentView !== 'categorize') {
    floatingBar.classList.remove('is-visible');
    return;
  }
  const selectedCount = selectedTransactionIds().length;
  const countLabel = document.getElementById('selectedTxCount');
  if (countLabel) {
    countLabel.textContent = `${selectedCount} selected`;
  }
  floatingBar.classList.toggle('is-visible', selectedCount > 0);
}

function setActiveYnabView(viewName) {
  currentView = viewName === 'approve' ? 'approve' : 'categorize';
  const categorizeBtn = document.getElementById('btnViewCategorizer');
  const approvalsBtn = document.getElementById('btnViewApprovals');
  const categorizeView = document.getElementById('categorizerView');
  const approvalsView = document.getElementById('approvalsView');

  if (categorizeBtn instanceof HTMLButtonElement) {
    categorizeBtn.classList.toggle('is-active', currentView === 'categorize');
  }
  if (approvalsBtn instanceof HTMLButtonElement) {
    approvalsBtn.classList.toggle('is-active', currentView === 'approve');
  }
  if (categorizeView instanceof HTMLElement) {
    categorizeView.classList.toggle('is-active', currentView === 'categorize');
  }
  if (approvalsView instanceof HTMLElement) {
    approvalsView.classList.toggle('is-active', currentView === 'approve');
  }
  updateFloatingApplyBarVisibility();
}

function updateLimitControlsState() {
  const enabledInput = document.getElementById('queueLimitEnabled');
  const valueInput = document.getElementById('queueLimitValue');
  const unitSelect = document.getElementById('queueLimitUnit');
  const enabled = enabledInput instanceof HTMLInputElement ? enabledInput.checked : false;
  if (valueInput instanceof HTMLInputElement) {
    valueInput.disabled = !enabled;
  }
  if (unitSelect instanceof HTMLSelectElement) {
    unitSelect.disabled = !enabled;
  }
}

function applySettingsToUi(data) {
  const modeSelect = document.getElementById('queueFilterMode');
  const showReconciled = document.getElementById('showReconciledTransactions');
  const queueLimitEnabled = document.getElementById('queueLimitEnabled');
  const queueLimitValue = document.getElementById('queueLimitValue');
  const queueLimitUnit = document.getElementById('queueLimitUnit');
  const quickApplyIncludeMedium = document.getElementById('quickApplyIncludeMedium');
  const defaultCategoryId = document.getElementById('defaultCategoryId');

  if (modeSelect instanceof HTMLSelectElement && data && data.queue_filter_mode) {
    modeSelect.value = data.queue_filter_mode;
  }
  if (showReconciled instanceof HTMLInputElement) {
    showReconciled.checked = !!(data && data.show_reconciled_transactions);
  }
  if (queueLimitEnabled instanceof HTMLInputElement) {
    queueLimitEnabled.checked = !!(data && data.queue_limit_enabled);
  }
  if (queueLimitValue instanceof HTMLInputElement && data && data.queue_limit_value !== undefined) {
    queueLimitValue.value = String(data.queue_limit_value);
  }
  if (queueLimitUnit instanceof HTMLSelectElement && data && data.queue_limit_unit) {
    queueLimitUnit.value = String(data.queue_limit_unit);
  }
  if (quickApplyIncludeMedium instanceof HTMLInputElement) {
    quickApplyIncludeMedium.checked = !!(data && data.quick_apply_include_medium);
  }
  if (defaultCategoryId instanceof HTMLSelectElement) {
    const value = data && data.default_category_id ? String(data.default_category_id) : '';
    if (value) {
      defaultCategoryId.value = value;
    } else {
      defaultCategoryId.value = '';
    }
  }
  updateLimitControlsState();
}

function readSettingsFromUi() {
  const modeSelect = document.getElementById('queueFilterMode');
  const showReconciled = document.getElementById('showReconciledTransactions');
  const queueLimitEnabled = document.getElementById('queueLimitEnabled');
  const queueLimitValue = document.getElementById('queueLimitValue');
  const queueLimitUnit = document.getElementById('queueLimitUnit');
  const quickApplyIncludeMedium = document.getElementById('quickApplyIncludeMedium');
  const defaultCategoryId = document.getElementById('defaultCategoryId');

  const mode = modeSelect instanceof HTMLSelectElement ? modeSelect.value : '';
  if (!mode) {
    throw new Error('Select queue filter mode first');
  }
  const showReconciledValue = showReconciled instanceof HTMLInputElement
    ? showReconciled.checked
    : false;
  const queueLimitEnabledValue = queueLimitEnabled instanceof HTMLInputElement
    ? queueLimitEnabled.checked
    : false;
  const queueLimitValueRaw = queueLimitValue instanceof HTMLInputElement
    ? Number(queueLimitValue.value)
    : 30;
  let queueLimitValueParsed = Number.isInteger(queueLimitValueRaw) ? queueLimitValueRaw : NaN;
  if (queueLimitEnabledValue && (!Number.isFinite(queueLimitValueParsed) || queueLimitValueParsed < 1)) {
    throw new Error('Queue limit value must be a positive integer');
  }
  if (!Number.isFinite(queueLimitValueParsed) || queueLimitValueParsed < 1) {
    queueLimitValueParsed = 30;
  }
  const queueLimitUnitValue = queueLimitUnit instanceof HTMLSelectElement
    ? queueLimitUnit.value
    : 'days';
  if (queueLimitEnabledValue && !['days', 'months', 'years'].includes(queueLimitUnitValue)) {
    throw new Error('Queue limit unit must be days, months, or years');
  }
  let defaultCategoryValue = null;
  if (defaultCategoryId instanceof HTMLSelectElement) {
    const selected = String(defaultCategoryId.value || '').trim();
    if (selected) {
      defaultCategoryValue = selected;
    } else if (
      defaultCategoryId.options.length <= 1 &&
      queueState &&
      queueState.default_category_id
    ) {
      defaultCategoryValue = String(queueState.default_category_id);
    }
  }

  return {
    queue_filter_mode: mode,
    show_reconciled_transactions: showReconciledValue,
    queue_limit_enabled: queueLimitEnabledValue,
    queue_limit_value: queueLimitValueParsed,
    queue_limit_unit: queueLimitUnitValue,
    quick_apply_include_medium: quickApplyIncludeMedium instanceof HTMLInputElement
      ? quickApplyIncludeMedium.checked
      : false,
    default_category_id: defaultCategoryValue,
  };
}

function loadRecentCategoryIds() {
  try {
    const raw = window.localStorage.getItem(RECENT_CATEGORY_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(item => String(item || '').trim())
      .filter(Boolean)
      .slice(0, RECENT_CATEGORY_LIMIT);
  } catch {
    return [];
  }
}

function saveRecentCategoryIds(ids) {
  try {
    window.localStorage.setItem(RECENT_CATEGORY_STORAGE_KEY, JSON.stringify(ids));
  } catch {
    // Ignore storage errors.
  }
}

function addRecentCategory(categoryId) {
  const id = String(categoryId || '').trim();
  if (!id) return;
  const next = [id, ...recentCategoryIds.filter(existing => existing !== id)]
    .slice(0, RECENT_CATEGORY_LIMIT);
  recentCategoryIds = next;
  saveRecentCategoryIds(next);
  renderRecentCategoryChips();
}

function renderRecentCategoryChips() {
  const chipsEl = document.getElementById('recentCategoryChips');
  if (!chipsEl) return;
  const byId = {};
  (queueState.categories || []).forEach(cat => {
    if (cat && cat.id) {
      byId[String(cat.id)] = String(cat.name || cat.id);
    }
  });

  const ids = recentCategoryIds.filter(id => Object.prototype.hasOwnProperty.call(byId, id));
  if (!ids.length) {
    chipsEl.innerHTML = '';
    return;
  }

  chipsEl.innerHTML = ids
    .map(id => `<button type="button" class="ynab-recent-chip" data-category-id="${id}">${byId[id]}</button>`)
    .join('');

  const chipButtons = chipsEl.querySelectorAll('.ynab-recent-chip');
  chipButtons.forEach(btn => {
    btn.addEventListener('click', event => {
      const target = event.currentTarget;
      if (!(target instanceof HTMLElement)) return;
      const categoryId = String(target.getAttribute('data-category-id') || '').trim();
      if (!categoryId) return;
      const searchInput = document.getElementById('globalCategorySearch');
      if (searchInput instanceof HTMLInputElement) {
        searchInput.value = '';
      }
      renderGlobalCategorySelect();
      const select = document.getElementById('globalCategorySelect');
      if (select instanceof HTMLSelectElement) {
        select.value = categoryId;
      }
    });
  });
}

function globalCategorySearchTerm() {
  const searchInput = document.getElementById('globalCategorySearch');
  if (!(searchInput instanceof HTMLInputElement)) return '';
  return String(searchInput.value || '').trim().toLowerCase();
}

function renderGlobalCategorySelect() {
  const select = document.getElementById('globalCategorySelect');
  if (!(select instanceof HTMLSelectElement)) return;

  const currentValue = select.value;
  const term = globalCategorySearchTerm();
  const allCategories = Array.isArray(queueState.categories) ? queueState.categories : [];
  const categories = term
    ? allCategories.filter(cat => {
      const name = cat && cat.name ? String(cat.name) : '';
      return name.toLowerCase().includes(term);
    })
    : allCategories;

  let selected = currentValue || '';
  if (!categories.some(cat => cat && String(cat.id || '') === selected)) {
    selected = categories.length && categories[0] && categories[0].id
      ? String(categories[0].id)
      : '';
  }

  select.innerHTML = makeCategoryOptions(categories, selected);
  if (selected) {
    select.value = selected;
  }
  renderRecentCategoryChips();
}

function renderDefaultCategorySelect() {
  const select = document.getElementById('defaultCategoryId');
  if (!(select instanceof HTMLSelectElement)) return;

  const allCategories = Array.isArray(queueState.categories) ? queueState.categories : [];
  const selected = queueState && queueState.default_category_id
    ? String(queueState.default_category_id)
    : '';
  const options = ['<option value="">None</option>'];
  options.push(makeCategoryOptions(allCategories, selected));
  select.innerHTML = options.join('');
  if (selected) {
    select.value = selected;
  } else {
    select.value = '';
  }
}

function txMatchesFilter(tx, term) {
  if (!term) return true;
  const haystack = [
    tx && tx.id ? String(tx.id) : '',
    tx && tx.memo ? String(tx.memo) : '',
    tx && tx.date ? String(tx.date) : '',
    tx && tx.payee_name ? String(tx.payee_name) : '',
    tx && tx.account_name ? String(tx.account_name) : '',
    fmtEurFromMilliunits(tx ? tx.amount_milliunits : null),
  ].join(' ').toLowerCase();
  return haystack.includes(term);
}

function approvalMatchesFilter(tx, term) {
  if (!term) return true;
  const haystack = [
    tx && tx.payee_name ? String(tx.payee_name) : '',
    tx && tx.account_name ? String(tx.account_name) : '',
    tx && tx.memo ? String(tx.memo) : '',
    tx && tx.date ? String(tx.date) : '',
    tx && tx.category_name ? String(tx.category_name) : '',
    tx && tx.category_id ? String(tx.category_id) : '',
    fmtEurFromMilliunits(tx ? tx.amount_milliunits : null),
  ].join(' ').toLowerCase();
  return haystack.includes(term);
}

function visibleSuggestedGroups(includeMedium) {
  const allowed = includeMedium ? new Set(['high', 'medium']) : new Set(['high']);
  const groups = [];
  const nodes = document.querySelectorAll('#ynabGroups .ynab-group');

  nodes.forEach(node => {
    if (!(node instanceof HTMLElement)) return;
    const confidence = String(node.getAttribute('data-confidence-label') || '').toLowerCase();
    if (!allowed.has(confidence)) return;
    const categoryId = String(node.getAttribute('data-suggested-category-id') || '').trim();
    if (!categoryId) return;

    const txIds = [];
    const checkboxes = node.querySelectorAll('.ynab-tx-check');
    checkboxes.forEach(cb => {
      if (cb instanceof HTMLInputElement && cb.value) {
        txIds.push(cb.value);
      }
    });

    if (!txIds.length) return;
    groups.push({
      payee: String(node.getAttribute('data-payee') || 'UNKNOWN'),
      categoryId,
      txIds,
    });
  });

  return groups;
}

function setVisibleSelection(checked) {
  const checkboxes = document.querySelectorAll('#ynabGroups .ynab-tx-check');
  checkboxes.forEach(cb => {
    if (cb instanceof HTMLInputElement) {
      cb.checked = checked;
    }
  });
  updateFloatingApplyBarVisibility();
}

function setVisibleApprovalSelection(checked) {
  const checkboxes = document.querySelectorAll('#approvalList .ynab-approval-check');
  checkboxes.forEach(cb => {
    if (cb instanceof HTMLInputElement) {
      cb.checked = checked;
    }
  });
}

function renderQueue(payload) {
  if (payload) {
    queueStateRaw = payload;
  }
  if (!queueStateRaw) return;

  queueState = queueStateRaw;
  const groupsEl = document.getElementById('ynabGroups');
  const summaryEl = document.getElementById('queueSummary');
  const filterInput = document.getElementById('queueFilterInput');
  queueFilterTerm = filterInput instanceof HTMLInputElement
    ? String(filterInput.value || '').trim().toLowerCase()
    : '';

  applySettingsToUi(queueState);
  renderGlobalCategorySelect();
  renderDefaultCategorySelect();

  if (!groupsEl) return;

  const groups = Array.isArray(queueState.groups) ? queueState.groups.slice() : [];
  groups.sort((a, b) => {
    const ranks = { High: 0, Medium: 1, Low: 2 };
    const rankA = Object.prototype.hasOwnProperty.call(ranks, a.confidence_label) ? ranks[a.confidence_label] : 3;
    const rankB = Object.prototype.hasOwnProperty.call(ranks, b.confidence_label) ? ranks[b.confidence_label] : 3;
    if (rankA !== rankB) return rankA - rankB;
    return serializeDateForSort(b.latest_date) - serializeDateForSort(a.latest_date);
  });

  const visibleGroups = [];
  groups.forEach(group => {
    const txAll = Array.isArray(group.transactions) ? group.transactions : [];
    const payeeText = `${group.payee_display || ''} ${group.payee_normalized || ''}`.toLowerCase();
    const payeeMatches = !queueFilterTerm || payeeText.includes(queueFilterTerm);
    const txVisible = payeeMatches
      ? txAll
      : txAll.filter(tx => txMatchesFilter(tx, queueFilterTerm));

    if (!txVisible.length) {
      return;
    }

    visibleGroups.push({
      ...group,
      _visibleTransactions: txVisible,
      _totalTransactions: txAll.length,
    });
  });

  if (summaryEl) {
    const visibleTxCount = visibleGroups.reduce((sum, group) => sum + group._visibleTransactions.length, 0);
    if (queueFilterTerm) {
      summaryEl.textContent = (
        `Groups: ${visibleGroups.length}/${queueState.group_count || 0} | ` +
        `Transactions: ${visibleTxCount}/${queueState.transaction_count || 0}`
      );
    } else {
      summaryEl.textContent = `Groups: ${queueState.group_count || 0} | Transactions: ${queueState.transaction_count || 0}`;
    }
  }

  const html = visibleGroups.map(group => {
    const suggestion = group.suggestion || null;
    const suggestedCategory = suggestion && suggestion.category_id ? String(suggestion.category_id) : '';
    const suggestionText = suggestion
      ? (suggestion.source === 'default'
        ? `${suggestion.category_name || suggestion.category_id} (default)`
        : `${suggestion.category_name || suggestion.category_id} (${Math.round((suggestion.confidence || 0) * 100)}%)`)
      : 'No suggestion';
    const label = group.confidence_label || 'Low';
    const visibleCount = group._visibleTransactions.length;
    const totalCount = group._totalTransactions;
    const countText = queueFilterTerm && visibleCount !== totalCount
      ? `${visibleCount}/${totalCount} transaction(s)`
      : `${totalCount} transaction(s)`;

    const txRows = group._visibleTransactions.map(tx => {
      const txId = tx && tx.id ? String(tx.id) : '';
      const memo = tx && tx.memo ? String(tx.memo) : '—';
      const date = fmtDateDDMMYYYY(tx && tx.date ? String(tx.date) : '');
      const amount = fmtEurFromMilliunits(tx ? tx.amount_milliunits : null);
      const accountFlow = txAccountFlowLabel(tx);
      return `
        <li class="ynab-tx-item">
          <input class="ynab-tx-check" type="checkbox" value="${txId}">
          <div class="ynab-tx-main">
            <div class="ynab-tx-account">${accountFlow}</div>
            <div class="ynab-tx-memo">${memo || '—'}</div>
            <div class="ynab-tx-date">${date}</div>
          </div>
          <div class="ynab-tx-amount">${amount}</div>
        </li>`;
    }).join('');

    return `
      <article
        class="ynab-group"
        data-payee="${group.payee_normalized}"
        data-confidence-label="${String(label).toLowerCase()}"
        data-suggested-category-id="${suggestedCategory}"
      >
        <div class="ynab-group-header">
          <div>
            <div class="ynab-group-title">${group.payee_display || group.payee_normalized}</div>
            <div>${countText}</div>
          </div>
          <span class="ynab-badge ${badgeClass(label)}">${label}</span>
        </div>
        <div class="ynab-group-controls">
          <span>Suggestion: ${suggestionText}</span>
          <select class="ynab-group-category">${makeCategoryOptions(queueState.categories || [], suggestedCategory)}</select>
          <button type="button" class="btnUseSuggestion" ${suggestedCategory ? '' : 'disabled'}>Use suggestion</button>
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
      updateFloatingApplyBarVisibility();
      const catSelect = group.querySelector('.ynab-group-category');
      const categoryId = catSelect instanceof HTMLSelectElement ? catSelect.value : '';
      if (!txIds.length || !categoryId) {
        showMessage('Group apply requires transactions and category', 'error');
        return;
      }
      await applyTransactions(txIds, categoryId);
    });
  });

  const suggestionButtons = document.querySelectorAll('.btnUseSuggestion');
  suggestionButtons.forEach(btn => {
    btn.addEventListener('click', async event => {
      const target = event.currentTarget;
      if (!(target instanceof HTMLElement)) return;
      const group = target.closest('.ynab-group');
      if (!group) return;

      const suggestedCategoryId = String(group.getAttribute('data-suggested-category-id') || '').trim();
      if (!suggestedCategoryId) {
        showMessage('No suggestion available for this group', 'error');
        return;
      }

      const txIds = [];
      const checkboxes = group.querySelectorAll('.ynab-tx-check');
      checkboxes.forEach(cb => {
        if (cb instanceof HTMLInputElement && cb.value) {
          txIds.push(cb.value);
          cb.checked = true;
        }
      });
      updateFloatingApplyBarVisibility();
      if (!txIds.length) {
        showMessage('No transactions available in this group', 'error');
        return;
      }

      const catSelect = group.querySelector('.ynab-group-category');
      if (catSelect instanceof HTMLSelectElement) {
        catSelect.value = suggestedCategoryId;
      }
      await applyTransactions(txIds, suggestedCategoryId);
    });
  });

  const transactionChecks = document.querySelectorAll('.ynab-tx-check');
  transactionChecks.forEach(cb => {
    cb.addEventListener('change', updateFloatingApplyBarVisibility);
  });

  const transactionRows = document.querySelectorAll('#ynabGroups .ynab-tx-item');
  transactionRows.forEach(row => {
    row.addEventListener('click', event => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.classList.contains('ynab-tx-check')) {
        return;
      }
      if (target instanceof HTMLElement && target.closest('button,select,input,label,a')) {
        return;
      }
      const checkbox = row.querySelector('.ynab-tx-check');
      if (!(checkbox instanceof HTMLInputElement)) return;
      checkbox.checked = !checkbox.checked;
      updateFloatingApplyBarVisibility();
    });
  });

  updateFloatingApplyBarVisibility();
}

function renderApprovalQueue(payload) {
  if (payload) {
    approvalsStateRaw = payload;
  }
  if (!approvalsStateRaw) return;

  const listEl = document.getElementById('approvalList');
  const summaryEl = document.getElementById('approvalSummary');
  const filterInput = document.getElementById('approvalFilterInput');
  approvalFilterTerm = filterInput instanceof HTMLInputElement
    ? String(filterInput.value || '').trim().toLowerCase()
    : '';

  if (!(listEl instanceof HTMLElement)) return;

  const allTransactions = Array.isArray(approvalsStateRaw.transactions)
    ? approvalsStateRaw.transactions
    : [];
  const visibleTransactions = approvalFilterTerm
    ? allTransactions.filter(tx => approvalMatchesFilter(tx, approvalFilterTerm))
    : allTransactions;

  if (summaryEl instanceof HTMLElement) {
    if (approvalFilterTerm) {
      summaryEl.textContent = (
        `Transactions: ${visibleTransactions.length}/${approvalsStateRaw.transaction_count || 0}`
      );
    } else {
      summaryEl.textContent = `Transactions: ${approvalsStateRaw.transaction_count || 0}`;
    }
  }

  const html = visibleTransactions.map(tx => {
    const txId = tx && tx.id ? String(tx.id) : '';
    const payee = tx && tx.payee_name ? String(tx.payee_name) : 'Unknown';
    const accountFlow = txAccountFlowLabel(tx);
    const memo = tx && tx.memo ? String(tx.memo) : '—';
    const date = fmtDateDDMMYYYY(tx && tx.date ? String(tx.date) : '');
    const amount = fmtEurFromMilliunits(tx ? tx.amount_milliunits : null);
    const categoryText = tx && tx.category_name
      ? String(tx.category_name)
      : (tx && tx.category_id ? String(tx.category_id) : 'Uncategorized');
    return `
      <li class="ynab-tx-item ynab-approval-item">
        <input class="ynab-approval-check" type="checkbox" value="${txId}">
        <div class="ynab-tx-main">
          <div class="ynab-tx-account">${payee}</div>
          <div class="ynab-tx-memo">${accountFlow}</div>
          <div class="ynab-tx-category">${categoryText}</div>
          <div class="ynab-tx-date">${memo} • ${date}</div>
        </div>
        <div class="ynab-tx-amount">${amount}</div>
      </li>`;
  }).join('');

  listEl.innerHTML = html || '<li>No unapproved transactions.</li>';

  const rows = listEl.querySelectorAll('.ynab-approval-item');
  rows.forEach(row => {
    row.addEventListener('click', event => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.classList.contains('ynab-approval-check')) {
        return;
      }
      if (target instanceof HTMLElement && target.closest('button,select,input,label,a')) {
        return;
      }
      const checkbox = row.querySelector('.ynab-approval-check');
      if (!(checkbox instanceof HTMLInputElement)) return;
      checkbox.checked = !checkbox.checked;
    });
  });
}

async function loadQueue() {
  showMessage('Loading queue...');
  try {
    const data = await apiRequest('/api/ynab-categorizer/queue');
    queueStateRaw = data;
    renderQueue(queueStateRaw);
    showMessage('Queue loaded', 'ok');
  } catch (error) {
    console.error('Queue load failed:', error);
    showMessage(`Queue load failed: ${error.message}`, 'error');
  }
}

async function loadApprovalQueue(options = {}) {
  const opts = Object.assign({
    showStatus: false,
  }, options || {});
  if (opts.showStatus) {
    showMessage('Loading approval queue...');
  }
  try {
    const data = await apiRequest('/api/ynab-categorizer/approvals-queue');
    approvalsStateRaw = data;
    renderApprovalQueue(approvalsStateRaw);
    if (opts.showStatus) {
      showMessage('Approval queue loaded', 'ok');
    }
  } catch (error) {
    console.error('Approval queue load failed:', error);
    showMessage(`Approval queue load failed: ${error.message}`, 'error');
  }
}

async function loadConfig() {
  try {
    const data = await apiRequest('/api/ynab-categorizer/config');
    applySettingsToUi(data || {});
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

async function applyTransactions(txIds, categoryId, options = {}) {
  const opts = Object.assign({
    reloadQueue: true,
    showStatus: true,
  }, options || {});

  if (opts.showStatus) {
    showMessage(`Applying category to ${txIds.length} transaction(s)...`);
  }

  try {
    const data = await apiRequest('/api/ynab-categorizer/apply', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ transaction_ids: txIds, category_id: categoryId }),
    });

    addRecentCategory(categoryId);

    if (opts.showStatus) {
      showMessage(`Applied category to ${txIds.length} transaction(s)`, 'ok');
    }
    if (opts.reloadQueue) {
      await loadQueue();
    }

    return data;
  } catch (error) {
    console.error('Apply failed:', error);
    if (opts.showStatus) {
      showMessage(`Apply failed: ${error.message}`, 'error');
    }
    throw error;
  }
}

async function saveConfigFromUi(options = {}) {
  const opts = Object.assign({
    showSuccess: true,
    reloadQueueAfter: true,
  }, options || {});

  const payload = readSettingsFromUi();
  await apiRequest('/api/ynab-categorizer/config', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (opts.showSuccess) {
    showMessage('Settings saved', 'ok');
  }
  if (opts.reloadQueueAfter) {
    await loadQueue();
    await loadApprovalQueue();
  }
}

async function applySuggestedFromVisibleGroups() {
  const includeMediumInput = document.getElementById('quickApplyIncludeMedium');
  const includeMedium = includeMediumInput instanceof HTMLInputElement
    ? includeMediumInput.checked
    : false;
  const groups = visibleSuggestedGroups(includeMedium);

  if (!groups.length) {
    showMessage('No visible suggested groups to apply', 'error');
    return;
  }

  let appliedGroups = 0;
  let appliedTransactions = 0;
  let failedGroups = 0;

  for (let i = 0; i < groups.length; i += 1) {
    const group = groups[i];
    showMessage(`Applying suggestions ${i + 1}/${groups.length}...`);
    try {
      await applyTransactions(group.txIds, group.categoryId, {
        reloadQueue: false,
        showStatus: false,
      });
      appliedGroups += 1;
      appliedTransactions += group.txIds.length;
    } catch (error) {
      console.error('Suggested apply failed for payee:', group.payee, error);
      failedGroups += 1;
    }
  }

  await loadQueue();
  const summary = (
    `Suggestion apply complete: ${appliedGroups} group(s), ` +
    `${appliedTransactions} transaction(s), ${failedGroups} failed`
  );
  showMessage(summary, failedGroups > 0 ? 'error' : 'ok');
}

async function approveTransactions(txIds, options = {}) {
  const opts = Object.assign({
    reloadQueue: true,
    showStatus: true,
  }, options || {});

  if (opts.showStatus) {
    showMessage(`Approving ${txIds.length} transaction(s)...`);
  }

  try {
    const data = await apiRequest('/api/ynab-categorizer/approve', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ transaction_ids: txIds }),
    });

    if (opts.showStatus) {
      showMessage(`Approved ${txIds.length} transaction(s)`, 'ok');
    }
    if (opts.reloadQueue) {
      await loadApprovalQueue();
    }
    return data;
  } catch (error) {
    console.error('Approve failed:', error);
    if (opts.showStatus) {
      showMessage(`Approve failed: ${error.message}`, 'error');
    }
    throw error;
  }
}

function isTypingTarget(target) {
  if (!(target instanceof HTMLElement)) return false;
  const tag = String(target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
}

function setupKeyboardShortcuts() {
  document.addEventListener('keydown', event => {
    if (!YNAB_CONFIGURED) return;

    const queueFilterInput = document.getElementById('queueFilterInput');
    const approvalFilterInput = document.getElementById('approvalFilterInput');
    const categorySearch = document.getElementById('globalCategorySearch');
    const applySelectedBtn = document.getElementById('btnApplySelected');
    const floatingBar = document.getElementById('floatingApplyBar');
    const floatingVisible = (
      currentView === 'categorize'
      && floatingBar instanceof HTMLElement
      && floatingBar.classList.contains('is-visible')
    );
    const typing = isTypingTarget(event.target);

    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !typing) {
      const activeFilter = currentView === 'approve' ? approvalFilterInput : queueFilterInput;
      if (activeFilter instanceof HTMLInputElement) {
        event.preventDefault();
        activeFilter.focus();
        activeFilter.select();
      }
      return;
    }

    if (event.key === 'Escape') {
      if (currentView === 'categorize' && queueFilterInput instanceof HTMLInputElement && queueFilterInput.value) {
        event.preventDefault();
        queueFilterInput.value = '';
        queueFilterTerm = '';
        renderQueue(queueStateRaw);
      }
      if (currentView === 'approve' && approvalFilterInput instanceof HTMLInputElement && approvalFilterInput.value) {
        event.preventDefault();
        approvalFilterInput.value = '';
        approvalFilterTerm = '';
        renderApprovalQueue(approvalsStateRaw);
      }
      return;
    }

    if (typing) {
      return;
    }

    if ((event.key === 'c' || event.key === 'C') && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (currentView === 'categorize' && floatingVisible && categorySearch instanceof HTMLInputElement) {
        event.preventDefault();
        categorySearch.focus();
        categorySearch.select();
      }
      return;
    }

    if (event.key === 'Enter' && event.ctrlKey) {
      if (currentView === 'categorize' && floatingVisible && applySelectedBtn instanceof HTMLButtonElement) {
        event.preventDefault();
        applySelectedBtn.click();
      }
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  if (!YNAB_CONFIGURED) {
    return;
  }

  recentCategoryIds = loadRecentCategoryIds();

  const refreshBtn = document.getElementById('btnRefreshQueue');
  const applySelectedBtn = document.getElementById('btnApplySelected');
  const bootstrapBtn = document.getElementById('btnBootstrap');
  const saveModeBtn = document.getElementById('btnSaveMode');
  const globalCategory = document.getElementById('globalCategorySelect');
  const globalCategorySearch = document.getElementById('globalCategorySearch');
  const queueLimitEnabled = document.getElementById('queueLimitEnabled');
  const quickApplyIncludeMedium = document.getElementById('quickApplyIncludeMedium');
  const applySuggestedBtn = document.getElementById('btnApplySuggested');
  const selectVisibleBtn = document.getElementById('btnSelectVisible');
  const clearSelectionBtn = document.getElementById('btnClearSelection');
  const queueFilterInput = document.getElementById('queueFilterInput');
  const btnViewCategorizer = document.getElementById('btnViewCategorizer');
  const btnViewApprovals = document.getElementById('btnViewApprovals');
  const approvalFilterInput = document.getElementById('approvalFilterInput');
  const approveSelectedBtn = document.getElementById('btnApproveSelected');
  const approveVisibleBtn = document.getElementById('btnApproveVisible');
  const clearApprovalSelectionBtn = document.getElementById('btnClearApprovalSelection');

  setActiveYnabView('categorize');

  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      await loadQueue();
      await loadApprovalQueue();
    });
  }

  if (btnViewCategorizer instanceof HTMLButtonElement) {
    btnViewCategorizer.addEventListener('click', () => setActiveYnabView('categorize'));
  }

  if (btnViewApprovals instanceof HTMLButtonElement) {
    btnViewApprovals.addEventListener('click', () => setActiveYnabView('approve'));
  }

  if (queueLimitEnabled instanceof HTMLInputElement) {
    queueLimitEnabled.addEventListener('change', updateLimitControlsState);
    updateLimitControlsState();
  }

  if (quickApplyIncludeMedium instanceof HTMLInputElement) {
    quickApplyIncludeMedium.addEventListener('change', async () => {
      try {
        await saveConfigFromUi({ showSuccess: false, reloadQueueAfter: false });
      } catch (error) {
        console.error('Quick apply preference save failed:', error);
        showMessage(`Failed to save preference: ${error.message}`, 'error');
      }
    });
  }

  if (saveModeBtn) {
    saveModeBtn.addEventListener('click', async () => {
      try {
        await saveConfigFromUi();
      } catch (error) {
        console.error('Settings save failed:', error);
        showMessage(`Failed to save settings: ${error.message}`, 'error');
      }
    });
  }

  if (queueFilterInput instanceof HTMLInputElement) {
    queueFilterInput.addEventListener('input', () => {
      queueFilterTerm = String(queueFilterInput.value || '').trim().toLowerCase();
      renderQueue(queueStateRaw);
    });
  }

  if (approvalFilterInput instanceof HTMLInputElement) {
    approvalFilterInput.addEventListener('input', () => {
      approvalFilterTerm = String(approvalFilterInput.value || '').trim().toLowerCase();
      renderApprovalQueue(approvalsStateRaw);
    });
  }

  if (selectVisibleBtn) {
    selectVisibleBtn.addEventListener('click', () => setVisibleSelection(true));
  }

  if (clearSelectionBtn) {
    clearSelectionBtn.addEventListener('click', () => setVisibleSelection(false));
  }

  if (applySuggestedBtn) {
    applySuggestedBtn.addEventListener('click', async () => {
      await applySuggestedFromVisibleGroups();
    });
  }

  if (approveVisibleBtn instanceof HTMLButtonElement) {
    approveVisibleBtn.addEventListener('click', async () => {
      const txIds = visibleApprovalIds();
      if (!txIds.length) {
        showMessage('No visible approval transactions to approve', 'error');
        return;
      }
      await approveTransactions(txIds);
    });
  }

  if (clearApprovalSelectionBtn instanceof HTMLButtonElement) {
    clearApprovalSelectionBtn.addEventListener('click', () => setVisibleApprovalSelection(false));
  }

  if (approveSelectedBtn instanceof HTMLButtonElement) {
    approveSelectedBtn.addEventListener('click', async () => {
      const txIds = selectedApprovalIds();
      if (!txIds.length) {
        showMessage('Select at least one approval transaction', 'error');
        return;
      }
      await approveTransactions(txIds);
    });
  }

  if (globalCategorySearch instanceof HTMLInputElement) {
    globalCategorySearch.addEventListener('input', () => {
      renderGlobalCategorySelect();
    });
    globalCategorySearch.addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      const select = document.getElementById('globalCategorySelect');
      if (!(select instanceof HTMLSelectElement)) return;
      if (select.options.length > 0) {
        select.selectedIndex = 0;
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
        await loadApprovalQueue();
      } catch (error) {
        console.error('Bootstrap failed:', error);
        showMessage(`Bootstrap failed: ${error.message}`, 'error');
      }
    });
  }

  if (YNAB_INITIAL_CONFIG) {
    applySettingsToUi(YNAB_INITIAL_CONFIG);
  }

  if (YNAB_BOOTSTRAP_STATUS && YNAB_BOOTSTRAP_STATUS.state) {
    const statusEl = document.getElementById('bootstrapStatus');
    if (statusEl) {
      statusEl.textContent = `Bootstrap status: ${fmtDateDDMMYYYY(YNAB_BOOTSTRAP_STATUS.state.bootstrapped_at)}`;
    }
  }

  setupKeyboardShortcuts();
  loadConfig();
  loadBootstrapStatus();
  loadQueue();
  loadApprovalQueue();
});
