/**
 * YNAB Categorizer workspace UI
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

let approvalsStateRaw = null;
let currentView = 'categorize';
let queueFilterTerm = '';
let approvalFilterTerm = '';
let recentCategoryIds = [];
let selectedQueueIds = new Set();
let selectedApprovalTxIds = new Set();
let groupExpansionState = {};
let resizeTimer = null;

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleString('fi-FI', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
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

function makeCategoryOptions(categories, selectedId, options = {}) {
  const opts = Object.assign({
    includeEmptyOption: false,
    emptyLabel: 'None',
  }, options || {});

  const items = [];
  if (opts.includeEmptyOption) {
    items.push(`<option value="">${escapeHtml(opts.emptyLabel)}</option>`);
  }

  categories.forEach(cat => {
    const id = cat && cat.id ? String(cat.id) : '';
    const name = cat && cat.name ? String(cat.name) : id;
    if (!id) return;
    const selected = selectedId && selectedId === id ? ' selected' : '';
    items.push(
      `<option value="${escapeHtml(id)}"${selected}>${escapeHtml(name)}</option>`
    );
  });

  return items.join('');
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

function availableQueueTransactionIds() {
  const ids = new Set();
  const groups = Array.isArray(queueStateRaw && queueStateRaw.groups) ? queueStateRaw.groups : [];
  groups.forEach(group => {
    const transactions = Array.isArray(group && group.transactions) ? group.transactions : [];
    transactions.forEach(tx => {
      const txId = tx && tx.id ? String(tx.id) : '';
      if (txId) ids.add(txId);
    });
  });
  return ids;
}

function availableApprovalIds() {
  const ids = new Set();
  const transactions = Array.isArray(approvalsStateRaw && approvalsStateRaw.transactions)
    ? approvalsStateRaw.transactions
    : [];
  transactions.forEach(tx => {
    const txId = tx && tx.id ? String(tx.id) : '';
    if (txId) ids.add(txId);
  });
  return ids;
}

function pruneSelectionState() {
  const queueIds = availableQueueTransactionIds();
  selectedQueueIds = new Set([...selectedQueueIds].filter(id => queueIds.has(id)));

  const approvalIds = availableApprovalIds();
  selectedApprovalTxIds = new Set([...selectedApprovalTxIds].filter(id => approvalIds.has(id)));
}

function currentSelectionSet() {
  return currentView === 'approve' ? selectedApprovalTxIds : selectedQueueIds;
}

function currentSelectionIds() {
  return [...currentSelectionSet()];
}

function currentFilterValue() {
  return currentView === 'approve' ? approvalFilterTerm : queueFilterTerm;
}

function setWorkspaceSummary(text) {
  const summaryEl = document.getElementById('workspaceSummary');
  if (summaryEl) {
    summaryEl.textContent = text;
  }
}

function defaultGroupExpanded(group) {
  const txCount = Number(group && group.transaction_count) || 0;
  return !(window.innerWidth >= 980 && txCount > 4);
}

function isGroupExpanded(group) {
  const key = String(group && group.payee_normalized ? group.payee_normalized : '');
  if (!key) return true;
  if (Object.prototype.hasOwnProperty.call(groupExpansionState, key)) {
    return !!groupExpansionState[key];
  }
  return defaultGroupExpanded(group);
}

function setGroupExpanded(groupKey, expanded) {
  if (!groupKey) return;
  groupExpansionState[groupKey] = !!expanded;
}

function updateHeaderStats() {
  const categorizeCountEl = document.getElementById('headerCategorizeCount');
  const categorizeMetaEl = document.getElementById('headerCategorizeMeta');
  const approveCountEl = document.getElementById('headerApproveCount');
  const approveMetaEl = document.getElementById('headerApproveMeta');

  if (categorizeCountEl) {
    categorizeCountEl.textContent = queueStateRaw ? String(queueStateRaw.transaction_count || 0) : '—';
  }
  if (categorizeMetaEl) {
    if (queueStateRaw) {
      categorizeMetaEl.textContent = `${queueStateRaw.group_count || 0} group(s) ready`;
    } else {
      categorizeMetaEl.textContent = 'Loading queue…';
    }
  }
  if (approveCountEl) {
    approveCountEl.textContent = approvalsStateRaw ? String(approvalsStateRaw.transaction_count || 0) : '—';
  }
  if (approveMetaEl) {
    if (approvalsStateRaw) {
      approveMetaEl.textContent = `${approvalsStateRaw.transaction_count || 0} item(s) pending`;
    } else {
      approveMetaEl.textContent = 'Loading approvals…';
    }
  }
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
    defaultCategoryId.value = value || '';
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
    show_reconciled_transactions: showReconciled instanceof HTMLInputElement
      ? showReconciled.checked
      : false,
    queue_limit_enabled: queueLimitEnabledValue,
    queue_limit_value: queueLimitValueParsed,
    queue_limit_unit: queueLimitUnitValue,
    quick_apply_include_medium: quickApplyIncludeMedium instanceof HTMLInputElement
      ? quickApplyIncludeMedium.checked
      : false,
    default_category_id: defaultCategoryValue,
  };
}

function globalCategorySearchTerm() {
  const searchInput = document.getElementById('globalCategorySearch');
  if (!(searchInput instanceof HTMLInputElement)) return '';
  return String(searchInput.value || '').trim().toLowerCase();
}

function renderRecentCategoryChips() {
  const chipsEl = document.getElementById('recentCategoryChips');
  if (!chipsEl) return;

  if (currentView !== 'categorize') {
    chipsEl.innerHTML = '';
    return;
  }

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
    .map(id => (
      `<button type="button" class="ynab-recent-chip" data-category-id="${escapeHtml(id)}">` +
      `${escapeHtml(byId[id])}</button>`
    ))
    .join('');
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

  if (!categories.length) {
    select.innerHTML = '<option value="">No matching categories</option>';
    select.value = '';
    renderRecentCategoryChips();
    return;
  }

  let selected = currentValue || '';
  if (!categories.some(cat => cat && String(cat.id || '') === selected)) {
    selected = categories[0] && categories[0].id ? String(categories[0].id) : '';
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
  select.innerHTML = makeCategoryOptions(allCategories, selected, {
    includeEmptyOption: true,
    emptyLabel: 'None',
  });
  select.value = selected || '';
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

function visibleQueueTransactionIds() {
  return Array.from(document.querySelectorAll('#ynabGroups .ynab-tx-check'))
    .filter(cb => cb instanceof HTMLInputElement && cb.value)
    .map(cb => cb.value);
}

function visibleApprovalIds() {
  return Array.from(document.querySelectorAll('#approvalList .ynab-approval-check'))
    .filter(cb => cb instanceof HTMLInputElement && cb.value)
    .map(cb => cb.value);
}

function setVisibleSelection(checked) {
  const ids = currentView === 'approve' ? visibleApprovalIds() : visibleQueueTransactionIds();
  const selection = currentSelectionSet();
  ids.forEach(id => {
    if (checked) {
      selection.add(id);
    } else {
      selection.delete(id);
    }
  });
  if (currentView === 'approve') {
    renderApprovalQueue(approvalsStateRaw);
  } else {
    renderQueue(queueStateRaw);
  }
}

function clearCurrentSelection() {
  if (currentView === 'approve') {
    selectedApprovalTxIds.clear();
    renderApprovalQueue(approvalsStateRaw);
  } else {
    selectedQueueIds.clear();
    renderQueue(queueStateRaw);
  }
}

function updateToolbarForCurrentView() {
  const searchInput = document.getElementById('workspaceFilterInput');
  const primaryActionBtn = document.getElementById('btnToolbarPrimaryAction');
  const quickApplyWrap = document.getElementById('quickApplyIncludeMediumWrap');

  if (searchInput instanceof HTMLInputElement) {
    searchInput.value = currentFilterValue();
    searchInput.placeholder = currentView === 'approve'
      ? 'Filter approvals by payee, account, memo, date, amount, category'
      : 'Filter queue by payee, account, memo, date, amount';
  }

  if (primaryActionBtn instanceof HTMLButtonElement) {
    primaryActionBtn.textContent = currentView === 'approve'
      ? 'Approve visible'
      : 'Apply visible suggestions';
  }

  if (quickApplyWrap instanceof HTMLElement) {
    quickApplyWrap.style.display = currentView === 'approve' ? 'none' : 'inline-flex';
  }
}

function updateFloatingApplyBarVisibility() {
  const floatingBar = document.getElementById('floatingApplyBar');
  const countLabel = document.getElementById('selectedTxCount');
  const hintLabel = document.getElementById('floatingApplyHint');
  const categorizeTray = document.getElementById('categorizeTray');
  const approveTray = document.getElementById('approveTray');

  if (!(floatingBar instanceof HTMLElement)) return;

  const selectedCount = currentSelectionIds().length;
  if (countLabel) {
    countLabel.textContent = `${selectedCount} selected`;
  }
  if (hintLabel) {
    hintLabel.textContent = currentView === 'approve'
      ? 'Confirm the current approval selection.'
      : 'Choose a category, then apply.';
  }
  if (categorizeTray instanceof HTMLElement) {
    categorizeTray.classList.toggle('is-active', currentView === 'categorize');
  }
  if (approveTray instanceof HTMLElement) {
    approveTray.classList.toggle('is-active', currentView === 'approve');
  }

  floatingBar.classList.toggle('is-visible', selectedCount > 0);
  renderRecentCategoryChips();
}

function setActiveYnabView(viewName) {
  currentView = viewName === 'approve' ? 'approve' : 'categorize';
  const categorizeBtn = document.getElementById('btnViewCategorizer');
  const approvalsBtn = document.getElementById('btnViewApprovals');
  const categorizeView = document.getElementById('categorizerView');
  const approvalsView = document.getElementById('approvalsView');

  if (categorizeBtn instanceof HTMLButtonElement) {
    categorizeBtn.classList.toggle('is-active', currentView === 'categorize');
    categorizeBtn.setAttribute('aria-selected', currentView === 'categorize' ? 'true' : 'false');
  }
  if (approvalsBtn instanceof HTMLButtonElement) {
    approvalsBtn.classList.toggle('is-active', currentView === 'approve');
    approvalsBtn.setAttribute('aria-selected', currentView === 'approve' ? 'true' : 'false');
  }
  if (categorizeView instanceof HTMLElement) {
    categorizeView.classList.toggle('is-active', currentView === 'categorize');
  }
  if (approvalsView instanceof HTMLElement) {
    approvalsView.classList.toggle('is-active', currentView === 'approve');
  }

  updateToolbarForCurrentView();
  updateFloatingApplyBarVisibility();

  if (currentView === 'approve') {
    renderApprovalQueue(approvalsStateRaw);
  } else {
    renderQueue(queueStateRaw);
  }
}

function openSettingsSheet() {
  const shell = document.getElementById('ynabSettingsShell');
  if (!(shell instanceof HTMLElement)) return;
  shell.classList.add('is-open');
  shell.setAttribute('aria-hidden', 'false');
  console.debug('YNAB settings sheet opened');
}

function closeSettingsSheet() {
  const shell = document.getElementById('ynabSettingsShell');
  if (!(shell instanceof HTMLElement)) return;
  shell.classList.remove('is-open');
  shell.setAttribute('aria-hidden', 'true');
  console.debug('YNAB settings sheet closed');
}

function updateGroupCheckboxStates() {
  const groups = document.querySelectorAll('#ynabGroups .ynab-group');
  groups.forEach(groupNode => {
    if (!(groupNode instanceof HTMLElement)) return;
    const groupCheckbox = groupNode.querySelector('.ynab-group-checkbox');
    if (!(groupCheckbox instanceof HTMLInputElement)) return;

    const txIds = Array.from(groupNode.querySelectorAll('.ynab-tx-check'))
      .filter(cb => cb instanceof HTMLInputElement && cb.value)
      .map(cb => cb.value);
    const selectedCount = txIds.filter(id => selectedQueueIds.has(id)).length;
    groupCheckbox.checked = txIds.length > 0 && selectedCount === txIds.length;
    groupCheckbox.indeterminate = selectedCount > 0 && selectedCount < txIds.length;
  });
}

function renderQueue(payload) {
  if (payload) {
    queueStateRaw = payload;
  }
  if (!queueStateRaw) return;

  pruneSelectionState();
  queueState = queueStateRaw;
  applySettingsToUi(queueState);
  renderGlobalCategorySelect();
  renderDefaultCategorySelect();
  updateHeaderStats();

  const groupsEl = document.getElementById('ynabGroups');
  if (!(groupsEl instanceof HTMLElement)) return;

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

    if (!txVisible.length) return;

    visibleGroups.push({
      ...group,
      _visibleTransactions: txVisible,
      _totalTransactions: txAll.length,
    });
  });

  const visibleTxCount = visibleGroups.reduce((sum, group) => sum + group._visibleTransactions.length, 0);
  if (currentView === 'categorize') {
    if (queueFilterTerm) {
      setWorkspaceSummary(
        `Showing ${visibleGroups.length}/${queueState.group_count || 0} groups • ` +
        `${visibleTxCount}/${queueState.transaction_count || 0} transactions`
      );
    } else {
      setWorkspaceSummary(
        `${queueState.group_count || 0} groups • ${queueState.transaction_count || 0} transactions`
      );
    }
  }

  if (!visibleGroups.length) {
    groupsEl.innerHTML = (
      '<div class="ynab-empty-placeholder">No uncategorized transactions match the current filter.</div>'
    );
    updateFloatingApplyBarVisibility();
    return;
  }

  const html = visibleGroups.map(group => {
    const suggestion = group.suggestion || null;
    const suggestedCategory = suggestion && suggestion.category_id ? String(suggestion.category_id) : '';
    const suggestionText = suggestion
      ? (suggestion.source === 'default'
        ? `${suggestion.category_name || suggestion.category_id} default`
        : `${suggestion.category_name || suggestion.category_id} ${Math.round((suggestion.confidence || 0) * 100)}%`)
      : 'Choose in tray';
    const label = group.confidence_label || 'Low';
    const visibleCount = group._visibleTransactions.length;
    const totalCount = group._totalTransactions;
    const countText = queueFilterTerm && visibleCount !== totalCount
      ? `${visibleCount}/${totalCount} transaction(s)`
      : `${totalCount} transaction(s)`;
    const expanded = isGroupExpanded(group);
    const payeeKey = String(group.payee_normalized || '');

    const txRows = group._visibleTransactions.map(tx => {
      const txId = tx && tx.id ? String(tx.id) : '';
      const memoText = tx && tx.memo ? String(tx.memo) : 'No memo';
      const dateText = fmtDateDDMMYYYY(tx && tx.date ? String(tx.date) : '');
      const amountText = fmtEurFromMilliunits(tx ? tx.amount_milliunits : null);
      const accountFlow = txAccountFlowLabel(tx);
      const isSelected = selectedQueueIds.has(txId);
      return (
        `<li class="ynab-tx-item${isSelected ? ' is-selected' : ''}" data-tx-id="${escapeHtml(txId)}">` +
        `<input class="ynab-tx-check" type="checkbox" value="${escapeHtml(txId)}"${isSelected ? ' checked' : ''}>` +
        '<div class="ynab-tx-main">' +
        `<div class="ynab-tx-primary">${escapeHtml(accountFlow)}</div>` +
        `<div class="ynab-tx-secondary">${escapeHtml(memoText)}</div>` +
        `<div class="ynab-tx-tertiary">${escapeHtml(dateText)}</div>` +
        '</div>' +
        `<div class="ynab-tx-amount">${escapeHtml(amountText)}</div>` +
        '</li>'
      );
    }).join('');

    const suggestionButton = suggestedCategory
      ? `<button type="button" class="ynab-btn ynab-btn-primary ynab-group-inline-action btnUseSuggestion">Apply suggestion</button>`
      : '';

    const toggleLabel = expanded ? 'Collapse' : 'Expand';
    const lastActivity = group.latest_date ? fmtDateDDMMYYYY(group.latest_date) : '—';

    return (
      `<article class="ynab-group" data-payee="${escapeHtml(payeeKey)}" ` +
      `data-confidence-label="${escapeHtml(String(label).toLowerCase())}" ` +
      `data-suggested-category-id="${escapeHtml(suggestedCategory)}">` +
      '<div class="ynab-group-shell">' +
      '<div class="ynab-group-top">' +
      '<label class="ynab-group-checkbox-wrap" aria-label="Select group">' +
      `<input class="ynab-group-checkbox" type="checkbox" data-payee="${escapeHtml(payeeKey)}">` +
      '</label>' +
      `<button type="button" class="ynab-group-expand" data-payee="${escapeHtml(payeeKey)}">` +
      '<div class="ynab-group-heading">' +
      `<div class="ynab-group-title">${escapeHtml(group.payee_display || group.payee_normalized)}</div>` +
      `<div class="ynab-group-count">${escapeHtml(countText)}</div>` +
      '</div>' +
      '<div class="ynab-group-meta">' +
      `<span class="ynab-badge ${badgeClass(label)}">${escapeHtml(label)}</span>` +
      `<span class="ynab-group-meta-item">${escapeHtml(suggestionText)}</span>` +
      `<span class="ynab-group-meta-item">Last activity ${escapeHtml(lastActivity)}</span>` +
      '</div>' +
      '</button>' +
      `<div class="ynab-group-actions">${suggestionButton}</div>` +
      '</div>' +
      `<div class="ynab-group-body${expanded ? '' : ' is-collapsed'}">` +
      `<ul class="ynab-tx-list">${txRows}</ul>` +
      `<button type="button" class="ynab-group-toggle" data-payee="${escapeHtml(payeeKey)}">${escapeHtml(toggleLabel)}</button>` +
      '</div>' +
      '</div>' +
      '</article>'
    );
  }).join('');

  groupsEl.innerHTML = html;
  updateGroupCheckboxStates();
  updateFloatingApplyBarVisibility();
}

function renderApprovalQueue(payload) {
  if (payload) {
    approvalsStateRaw = payload;
  }
  if (!approvalsStateRaw) return;

  pruneSelectionState();
  updateHeaderStats();

  const listEl = document.getElementById('approvalList');
  if (!(listEl instanceof HTMLElement)) return;

  const allTransactions = Array.isArray(approvalsStateRaw.transactions)
    ? approvalsStateRaw.transactions
    : [];
  const visibleTransactions = approvalFilterTerm
    ? allTransactions.filter(tx => approvalMatchesFilter(tx, approvalFilterTerm))
    : allTransactions;

  if (currentView === 'approve') {
    if (approvalFilterTerm) {
      setWorkspaceSummary(
        `Showing ${visibleTransactions.length}/${approvalsStateRaw.transaction_count || 0} approval items`
      );
    } else {
      setWorkspaceSummary(`${approvalsStateRaw.transaction_count || 0} approval items`);
    }
  }

  if (!visibleTransactions.length) {
    listEl.innerHTML = (
      '<li class="ynab-empty-placeholder">No approval transactions match the current filter.</li>'
    );
    updateFloatingApplyBarVisibility();
    return;
  }

  const html = visibleTransactions.map(tx => {
    const txId = tx && tx.id ? String(tx.id) : '';
    const payee = tx && tx.payee_name ? String(tx.payee_name) : 'Unknown';
    const accountFlow = txAccountFlowLabel(tx);
    const memo = tx && tx.memo ? String(tx.memo) : 'No memo';
    const date = fmtDateDDMMYYYY(tx && tx.date ? String(tx.date) : '');
    const amount = fmtEurFromMilliunits(tx ? tx.amount_milliunits : null);
    const categoryText = tx && tx.category_name
      ? String(tx.category_name)
      : (tx && tx.category_id ? String(tx.category_id) : 'Uncategorized');
    const isSelected = selectedApprovalTxIds.has(txId);

    return (
      `<li class="ynab-approval-item${isSelected ? ' is-selected' : ''}" data-tx-id="${escapeHtml(txId)}">` +
      `<input class="ynab-approval-check" type="checkbox" value="${escapeHtml(txId)}"${isSelected ? ' checked' : ''}>` +
      '<div class="ynab-tx-main">' +
      `<div class="ynab-tx-primary">${escapeHtml(payee)}</div>` +
      `<div class="ynab-tx-secondary">${escapeHtml(categoryText)} • ${escapeHtml(accountFlow)}</div>` +
      `<div class="ynab-tx-tertiary">${escapeHtml(memo)} • ${escapeHtml(date)}</div>` +
      '</div>' +
      `<div class="ynab-tx-amount">${escapeHtml(amount)}</div>` +
      '</li>'
    );
  }).join('');

  listEl.innerHTML = html;
  updateFloatingApplyBarVisibility();
}

async function loadQueue(options = {}) {
  const opts = Object.assign({ showStatus: true }, options || {});
  if (opts.showStatus) {
    showMessage('Loading queue...');
  }
  console.debug('Loading YNAB categorize queue');
  try {
    const data = await apiRequest('/api/ynab-categorizer/queue');
    queueStateRaw = data;
    renderQueue(queueStateRaw);
    if (opts.showStatus) {
      showMessage('Queue loaded', 'ok');
    }
  } catch (error) {
    console.error('Queue load failed:', error);
    showMessage(`Queue load failed: ${error.message}`, 'error');
  }
}

async function loadApprovalQueue(options = {}) {
  const opts = Object.assign({ showStatus: false }, options || {});
  if (opts.showStatus) {
    showMessage('Loading approval queue...');
  }
  console.debug('Loading YNAB approval queue');
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
    statusEl.textContent = `Bootstrap status: ${fmtTs(data.state.bootstrapped_at)}`;
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

  console.debug('Applying YNAB category', {
    txCount: txIds.length,
    categoryId,
  });

  try {
    const data = await apiRequest('/api/ynab-categorizer/apply', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ transaction_ids: txIds, category_id: categoryId }),
    });

    addRecentCategory(categoryId);
    txIds.forEach(id => selectedQueueIds.delete(id));

    if (opts.showStatus) {
      showMessage(`Applied category to ${txIds.length} transaction(s)`, 'ok');
    }
    if (opts.reloadQueue) {
      await loadQueue({ showStatus: false });
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

async function approveTransactions(txIds, options = {}) {
  const opts = Object.assign({
    reloadQueue: true,
    showStatus: true,
  }, options || {});

  if (opts.showStatus) {
    showMessage(`Approving ${txIds.length} transaction(s)...`);
  }

  console.debug('Approving YNAB transactions', {
    txCount: txIds.length,
  });

  try {
    const data = await apiRequest('/api/ynab-categorizer/approve', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ transaction_ids: txIds }),
    });

    txIds.forEach(id => selectedApprovalTxIds.delete(id));

    if (opts.showStatus) {
      showMessage(`Approved ${txIds.length} transaction(s)`, 'ok');
    }
    if (opts.reloadQueue) {
      await loadApprovalQueue({ showStatus: false });
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

async function saveConfigFromUi(options = {}) {
  const opts = Object.assign({
    showSuccess: true,
    reloadQueueAfter: true,
  }, options || {});

  const payload = readSettingsFromUi();
  console.debug('Saving YNAB workspace config', payload);
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
    await loadQueue({ showStatus: false });
    await loadApprovalQueue({ showStatus: false });
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

  await loadQueue({ showStatus: false });
  const summary = (
    `Suggestion apply complete: ${appliedGroups} group(s), ` +
    `${appliedTransactions} transaction(s), ${failedGroups} failed`
  );
  showMessage(summary, failedGroups > 0 ? 'error' : 'ok');
}

function isTypingTarget(target) {
  if (!(target instanceof HTMLElement)) return false;
  const tag = String(target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
}

function setupKeyboardShortcuts() {
  document.addEventListener('keydown', event => {
    if (!YNAB_CONFIGURED) return;

    const filterInput = document.getElementById('workspaceFilterInput');
    const categorySearch = document.getElementById('globalCategorySearch');
    const applySelectedBtn = document.getElementById('btnApplySelected');
    const approveSelectedBtn = document.getElementById('btnApproveSelected');
    const typing = isTypingTarget(event.target);
    const settingsShell = document.getElementById('ynabSettingsShell');
    const settingsOpen = settingsShell instanceof HTMLElement && settingsShell.classList.contains('is-open');

    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !typing) {
      if (filterInput instanceof HTMLInputElement) {
        event.preventDefault();
        filterInput.focus();
        filterInput.select();
      }
      return;
    }

    if (event.key === 'Escape') {
      if (settingsOpen) {
        event.preventDefault();
        closeSettingsSheet();
        return;
      }

      if (filterInput instanceof HTMLInputElement && filterInput.value) {
        event.preventDefault();
        filterInput.value = '';
        if (currentView === 'approve') {
          approvalFilterTerm = '';
          renderApprovalQueue(approvalsStateRaw);
        } else {
          queueFilterTerm = '';
          renderQueue(queueStateRaw);
        }
      }
      return;
    }

    if (typing) {
      return;
    }

    if ((event.key === 'c' || event.key === 'C') && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (currentView === 'categorize' && categorySearch instanceof HTMLInputElement) {
        const floatingBar = document.getElementById('floatingApplyBar');
        const floatingVisible = floatingBar instanceof HTMLElement && floatingBar.classList.contains('is-visible');
        if (floatingVisible) {
          event.preventDefault();
          categorySearch.focus();
          categorySearch.select();
        }
      }
      return;
    }

    if (event.key === 'Enter' && event.ctrlKey) {
      if (currentView === 'categorize' && applySelectedBtn instanceof HTMLButtonElement) {
        const floatingBar = document.getElementById('floatingApplyBar');
        const floatingVisible = floatingBar instanceof HTMLElement && floatingBar.classList.contains('is-visible');
        if (floatingVisible) {
          event.preventDefault();
          applySelectedBtn.click();
        }
      }
      if (currentView === 'approve' && approveSelectedBtn instanceof HTMLButtonElement) {
        const floatingBar = document.getElementById('floatingApplyBar');
        const floatingVisible = floatingBar instanceof HTMLElement && floatingBar.classList.contains('is-visible');
        if (floatingVisible) {
          event.preventDefault();
          approveSelectedBtn.click();
        }
      }
    }
  });
}

function handleQueueListClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  const suggestionBtn = target.closest('.btnUseSuggestion');
  if (suggestionBtn instanceof HTMLElement) {
    const group = suggestionBtn.closest('.ynab-group');
    if (!group) return;
    const suggestedCategoryId = String(group.getAttribute('data-suggested-category-id') || '').trim();
    if (!suggestedCategoryId) {
      showMessage('No suggestion available for this group', 'error');
      return;
    }
    const txIds = Array.from(group.querySelectorAll('.ynab-tx-check'))
      .filter(cb => cb instanceof HTMLInputElement && cb.value)
      .map(cb => cb.value);
    if (!txIds.length) {
      showMessage('No transactions available in this group', 'error');
      return;
    }
    applyTransactions(txIds, suggestedCategoryId).catch(() => {});
    return;
  }

  const toggleBtn = target.closest('.ynab-group-toggle');
  if (toggleBtn instanceof HTMLElement) {
    const payee = String(toggleBtn.getAttribute('data-payee') || '').trim();
    const groupData = (queueState.groups || []).find(group => String(group.payee_normalized || '') === payee);
    setGroupExpanded(payee, !isGroupExpanded(groupData || { payee_normalized: payee }));
    renderQueue(queueStateRaw);
    return;
  }

  const expandBtn = target.closest('.ynab-group-expand');
  if (expandBtn instanceof HTMLElement) {
    const payee = String(expandBtn.getAttribute('data-payee') || '').trim();
    const groupData = (queueState.groups || []).find(group => String(group.payee_normalized || '') === payee);
    setGroupExpanded(payee, !isGroupExpanded(groupData || { payee_normalized: payee }));
    renderQueue(queueStateRaw);
    return;
  }

  if (target.closest('button,input,label,a')) {
    return;
  }

  const row = target.closest('.ynab-tx-item');
  if (!(row instanceof HTMLElement)) return;
  const checkbox = row.querySelector('.ynab-tx-check');
  if (!(checkbox instanceof HTMLInputElement)) return;
  checkbox.checked = !checkbox.checked;
  if (checkbox.checked) {
    selectedQueueIds.add(checkbox.value);
  } else {
    selectedQueueIds.delete(checkbox.value);
  }
  renderQueue(queueStateRaw);
}

function handleQueueListChange(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  if (target instanceof HTMLInputElement && target.classList.contains('ynab-tx-check')) {
    if (target.checked) {
      selectedQueueIds.add(target.value);
    } else {
      selectedQueueIds.delete(target.value);
    }
    renderQueue(queueStateRaw);
    return;
  }

  if (target instanceof HTMLInputElement && target.classList.contains('ynab-group-checkbox')) {
    const group = target.closest('.ynab-group');
    if (!(group instanceof HTMLElement)) return;
    const txIds = Array.from(group.querySelectorAll('.ynab-tx-check'))
      .filter(cb => cb instanceof HTMLInputElement && cb.value)
      .map(cb => cb.value);
    txIds.forEach(id => {
      if (target.checked) {
        selectedQueueIds.add(id);
      } else {
        selectedQueueIds.delete(id);
      }
    });
    renderQueue(queueStateRaw);
  }
}

function handleApprovalListClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.closest('button,input,label,a')) {
    return;
  }

  const row = target.closest('.ynab-approval-item');
  if (!(row instanceof HTMLElement)) return;
  const checkbox = row.querySelector('.ynab-approval-check');
  if (!(checkbox instanceof HTMLInputElement)) return;
  checkbox.checked = !checkbox.checked;
  if (checkbox.checked) {
    selectedApprovalTxIds.add(checkbox.value);
  } else {
    selectedApprovalTxIds.delete(checkbox.value);
  }
  renderApprovalQueue(approvalsStateRaw);
}

function handleApprovalListChange(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target instanceof HTMLInputElement && target.classList.contains('ynab-approval-check')) {
    if (target.checked) {
      selectedApprovalTxIds.add(target.value);
    } else {
      selectedApprovalTxIds.delete(target.value);
    }
    renderApprovalQueue(approvalsStateRaw);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (!YNAB_CONFIGURED) {
    return;
  }

  recentCategoryIds = loadRecentCategoryIds();

  const refreshBtn = document.getElementById('btnRefreshQueue');
  const applySelectedBtn = document.getElementById('btnApplySelected');
  const approveSelectedBtn = document.getElementById('btnApproveSelected');
  const bootstrapBtn = document.getElementById('btnBootstrap');
  const saveModeBtn = document.getElementById('btnSaveMode');
  const globalCategory = document.getElementById('globalCategorySelect');
  const globalCategorySearch = document.getElementById('globalCategorySearch');
  const queueLimitEnabled = document.getElementById('queueLimitEnabled');
  const quickApplyIncludeMedium = document.getElementById('quickApplyIncludeMedium');
  const toolbarPrimaryActionBtn = document.getElementById('btnToolbarPrimaryAction');
  const toolbarSelectVisibleBtn = document.getElementById('btnToolbarSelectVisible');
  const toolbarClearSelectionBtn = document.getElementById('btnToolbarClearSelection');
  const trayClearSelectionBtn = document.getElementById('btnTrayClearSelection');
  const filterInput = document.getElementById('workspaceFilterInput');
  const btnViewCategorizer = document.getElementById('btnViewCategorizer');
  const btnViewApprovals = document.getElementById('btnViewApprovals');
  const openSettingsBtn = document.getElementById('btnOpenSettings');
  const closeSettingsBtn = document.getElementById('btnCloseSettings');
  const closeSettingsBackdropBtn = document.getElementById('btnCloseSettingsBackdrop');
  const queueContainer = document.getElementById('ynabGroups');
  const approvalContainer = document.getElementById('approvalList');
  const recentCategoryContainer = document.getElementById('recentCategoryChips');

  setActiveYnabView('categorize');

  if (refreshBtn instanceof HTMLButtonElement) {
    refreshBtn.addEventListener('click', async () => {
      await loadQueue();
      await loadApprovalQueue();
      await loadBootstrapStatus();
    });
  }

  if (btnViewCategorizer instanceof HTMLButtonElement) {
    btnViewCategorizer.addEventListener('click', () => setActiveYnabView('categorize'));
  }

  if (btnViewApprovals instanceof HTMLButtonElement) {
    btnViewApprovals.addEventListener('click', () => setActiveYnabView('approve'));
  }

  if (openSettingsBtn instanceof HTMLButtonElement) {
    openSettingsBtn.addEventListener('click', openSettingsSheet);
  }

  if (closeSettingsBtn instanceof HTMLButtonElement) {
    closeSettingsBtn.addEventListener('click', closeSettingsSheet);
  }

  if (closeSettingsBackdropBtn instanceof HTMLButtonElement) {
    closeSettingsBackdropBtn.addEventListener('click', closeSettingsSheet);
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

  if (saveModeBtn instanceof HTMLButtonElement) {
    saveModeBtn.addEventListener('click', async () => {
      try {
        await saveConfigFromUi();
        closeSettingsSheet();
      } catch (error) {
        console.error('Settings save failed:', error);
        showMessage(`Failed to save settings: ${error.message}`, 'error');
      }
    });
  }

  if (filterInput instanceof HTMLInputElement) {
    filterInput.addEventListener('input', () => {
      if (currentView === 'approve') {
        approvalFilterTerm = String(filterInput.value || '').trim().toLowerCase();
        renderApprovalQueue(approvalsStateRaw);
      } else {
        queueFilterTerm = String(filterInput.value || '').trim().toLowerCase();
        renderQueue(queueStateRaw);
      }
    });
  }

  if (toolbarSelectVisibleBtn instanceof HTMLButtonElement) {
    toolbarSelectVisibleBtn.addEventListener('click', () => setVisibleSelection(true));
  }

  if (toolbarClearSelectionBtn instanceof HTMLButtonElement) {
    toolbarClearSelectionBtn.addEventListener('click', clearCurrentSelection);
  }

  if (trayClearSelectionBtn instanceof HTMLButtonElement) {
    trayClearSelectionBtn.addEventListener('click', clearCurrentSelection);
  }

  if (toolbarPrimaryActionBtn instanceof HTMLButtonElement) {
    toolbarPrimaryActionBtn.addEventListener('click', async () => {
      if (currentView === 'approve') {
        const txIds = visibleApprovalIds();
        if (!txIds.length) {
          showMessage('No visible approval transactions to approve', 'error');
          return;
        }
        await approveTransactions(txIds);
        return;
      }
      await applySuggestedFromVisibleGroups();
    });
  }

  if (approveSelectedBtn instanceof HTMLButtonElement) {
    approveSelectedBtn.addEventListener('click', async () => {
      const txIds = [...selectedApprovalTxIds];
      if (!txIds.length) {
        showMessage('Select at least one approval transaction', 'error');
        return;
      }
      await approveTransactions(txIds);
    });
  }

  if (globalCategorySearch instanceof HTMLInputElement) {
    globalCategorySearch.addEventListener('input', renderGlobalCategorySelect);
    globalCategorySearch.addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      const select = document.getElementById('globalCategorySelect');
      if (!(select instanceof HTMLSelectElement)) return;
      if (select.options.length > 0) {
        select.selectedIndex = 0;
      }
    });
  }

  if (recentCategoryContainer instanceof HTMLElement) {
    recentCategoryContainer.addEventListener('click', event => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const chip = target.closest('.ynab-recent-chip');
      if (!(chip instanceof HTMLElement)) return;
      const categoryId = String(chip.getAttribute('data-category-id') || '').trim();
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
  }

  if (applySelectedBtn instanceof HTMLButtonElement) {
    applySelectedBtn.addEventListener('click', async () => {
      const txIds = [...selectedQueueIds];
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

  if (bootstrapBtn instanceof HTMLButtonElement) {
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
        await loadQueue({ showStatus: false });
        await loadApprovalQueue({ showStatus: false });
      } catch (error) {
        console.error('Bootstrap failed:', error);
        showMessage(`Bootstrap failed: ${error.message}`, 'error');
      }
    });
  }

  if (queueContainer instanceof HTMLElement) {
    queueContainer.addEventListener('click', handleQueueListClick);
    queueContainer.addEventListener('change', handleQueueListChange);
  }

  if (approvalContainer instanceof HTMLElement) {
    approvalContainer.addEventListener('click', handleApprovalListClick);
    approvalContainer.addEventListener('change', handleApprovalListChange);
  }

  if (YNAB_INITIAL_CONFIG) {
    applySettingsToUi(YNAB_INITIAL_CONFIG);
  }

  if (YNAB_BOOTSTRAP_STATUS && YNAB_BOOTSTRAP_STATUS.state) {
    const statusEl = document.getElementById('bootstrapStatus');
    if (statusEl) {
      statusEl.textContent = `Bootstrap status: ${fmtTs(YNAB_BOOTSTRAP_STATUS.state.bootstrapped_at)}`;
    }
  }

  window.addEventListener('resize', () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      if (currentView === 'approve') {
        renderApprovalQueue(approvalsStateRaw);
      } else {
        renderQueue(queueStateRaw);
      }
    }, 120);
  });

  setupKeyboardShortcuts();
  loadConfig();
  loadBootstrapStatus();
  loadQueue();
  loadApprovalQueue();
});
