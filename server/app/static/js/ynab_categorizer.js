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
  test_mode_enabled: false,
  show_reconciled_transactions: false,
  queue_limit_enabled: false,
  queue_limit_value: 30,
  queue_limit_unit: 'days',
  quick_apply_include_medium: false,
  default_category_id: '',
  custom_rules: [],
  needs_category_count: 0,
  needs_approval_count: 0,
};

let recentCategoryIds = [];
let selectedQueueIds = new Set();
let groupExpansionState = {};
let resizeTimer = null;
let customRulesState = [];
let customRuleCounter = 0;

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

function txLookupMap() {
  const byId = {};
  const groups = Array.isArray(queueStateRaw && queueStateRaw.groups) ? queueStateRaw.groups : [];
  groups.forEach(group => {
    const transactions = Array.isArray(group && group.transactions) ? group.transactions : [];
    transactions.forEach(tx => {
      const txId = tx && tx.id ? String(tx.id) : '';
      if (txId) {
        byId[txId] = tx;
      }
    });
  });
  return byId;
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
  const opts = Object.assign(
    {
      includeEmptyOption: false,
      emptyLabel: 'None',
      allowMissingSelected: false,
    },
    options || {}
  );

  const items = [];
  if (opts.includeEmptyOption) {
    items.push(`<option value="">${escapeHtml(opts.emptyLabel)}</option>`);
  }

  const seenIds = new Set();
  categories.forEach(cat => {
    const id = cat && cat.id ? String(cat.id) : '';
    const name = cat && cat.name ? String(cat.name) : id;
    if (!id) return;
    seenIds.add(id);
    const selected = selectedId && selectedId === id ? ' selected' : '';
    items.push(`<option value="${escapeHtml(id)}"${selected}>${escapeHtml(name)}</option>`);
  });

  if (opts.allowMissingSelected && selectedId && !seenIds.has(String(selectedId))) {
    items.push(
      `<option value="${escapeHtml(String(selectedId))}" selected>${escapeHtml(String(selectedId))}</option>`
    );
  }

  return items.join('');
}

function badgeClass(label) {
  const normalized = String(label || '').toLowerCase();
  if (normalized === 'rule') return 'rule';
  if (normalized === 'current') return 'current';
  if (normalized === 'default') return 'default';
  if (normalized === 'high') return 'high';
  if (normalized === 'medium') return 'medium';
  if (normalized === 'low') return 'low';
  return 'neutral';
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
  const next = [id, ...recentCategoryIds.filter(existing => existing !== id)].slice(0, RECENT_CATEGORY_LIMIT);
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

function pruneSelectionState() {
  const queueIds = availableQueueTransactionIds();
  selectedQueueIds = new Set([...selectedQueueIds].filter(id => queueIds.has(id)));
}

function currentSelectionIds() {
  return [...selectedQueueIds];
}

function setWorkspaceSummary(text) {
  const summaryEl = document.getElementById('workspaceSummary');
  if (summaryEl) {
    summaryEl.textContent = text;
  }
}

function updateWorkspaceModeUi(source = null) {
  const modeSource = source && typeof source === 'object' ? source : queueState;
  const testModeEnabled = !!(modeSource && modeSource.test_mode_enabled);
  const badgeEl = document.getElementById('workspaceModeBadge');
  const toolbarPrimaryActionBtn = document.getElementById('btnToolbarPrimaryAction');
  const applySelectedBtn = document.getElementById('btnApplySelected');
  const approveSelectedBtn = document.getElementById('btnApproveSelected');

  if (badgeEl instanceof HTMLElement) {
    badgeEl.textContent = testModeEnabled ? 'Test mode: writes disabled' : 'Live mode';
    badgeEl.classList.toggle('is-test', testModeEnabled);
  }
  if (toolbarPrimaryActionBtn instanceof HTMLButtonElement) {
    toolbarPrimaryActionBtn.textContent = testModeEnabled
      ? 'Simulate visible suggestions'
      : 'Apply visible suggestions';
  }
  if (applySelectedBtn instanceof HTMLButtonElement) {
    applySelectedBtn.textContent = testModeEnabled ? 'Simulate category' : 'Apply category';
  }
  if (approveSelectedBtn instanceof HTMLButtonElement) {
    approveSelectedBtn.textContent = testModeEnabled ? 'Simulate approval' : 'Approve as-is';
  }
}

function defaultGroupExpanded(group) {
  const txCount = Number(group && group.transaction_count) || 0;
  return !(window.innerWidth >= 980 && txCount > 4);
}

function groupExpansionKey(group) {
  return String(group && group.group_key ? group.group_key : '');
}

function isGroupExpanded(group) {
  const key = groupExpansionKey(group);
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
  const needsCategoryCountEl = document.getElementById('headerNeedsCategoryCount');
  const needsCategoryMetaEl = document.getElementById('headerNeedsCategoryMeta');
  const needsApprovalCountEl = document.getElementById('headerNeedsApprovalCount');
  const needsApprovalMetaEl = document.getElementById('headerNeedsApprovalMeta');

  if (needsCategoryCountEl) {
    needsCategoryCountEl.textContent = queueStateRaw
      ? String(queueStateRaw.needs_category_count || 0)
      : '—';
  }
  if (needsCategoryMetaEl) {
    if (queueStateRaw) {
      needsCategoryMetaEl.textContent = `${queueStateRaw.group_count || 0} group(s) in review`;
    } else {
      needsCategoryMetaEl.textContent = 'Loading queue…';
    }
  }
  if (needsApprovalCountEl) {
    needsApprovalCountEl.textContent = queueStateRaw
      ? String(queueStateRaw.needs_approval_count || 0)
      : '—';
  }
  if (needsApprovalMetaEl) {
    if (queueStateRaw) {
      needsApprovalMetaEl.textContent = `${queueStateRaw.transaction_count || 0} transaction(s) shown`;
    } else {
      needsApprovalMetaEl.textContent = 'Loading queue…';
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

function createBlankRule() {
  customRuleCounter += 1;
  return {
    id: `rule-${Date.now()}-${customRuleCounter}`,
    enabled: true,
    payee_match_type: 'contains',
    payee_value: '',
    amount_operator: 'any',
    amount_value_eur: null,
    category_id: '',
  };
}

function cloneRules(rules) {
  if (!Array.isArray(rules)) return [];
  return rules.map(rule => ({
    id: String(rule && rule.id ? rule.id : createBlankRule().id),
    enabled: !!(rule && rule.enabled),
    payee_match_type: String(rule && rule.payee_match_type ? rule.payee_match_type : 'contains'),
    payee_value: String(rule && rule.payee_value ? rule.payee_value : ''),
    amount_operator: String(rule && rule.amount_operator ? rule.amount_operator : 'any'),
    amount_value_eur:
      rule && rule.amount_value_eur !== null && rule.amount_value_eur !== undefined && rule.amount_value_eur !== ''
        ? Number(rule.amount_value_eur)
        : null,
    category_id: String(rule && rule.category_id ? rule.category_id : ''),
  }));
}

function renderCustomRulesEditor() {
  const listEl = document.getElementById('customRulesList');
  if (!(listEl instanceof HTMLElement)) return;

  if (!customRulesState.length) {
    listEl.innerHTML = '<div class="ynab-empty-placeholder">No custom rules yet. Add one to override learned suggestions.</div>';
    return;
  }

  const categories = Array.isArray(queueState.categories) ? queueState.categories : [];
  listEl.innerHTML = customRulesState.map((rule, index) => {
    const amountDisabled = rule.amount_operator === 'any' ? ' disabled' : '';
    const amountValue = rule.amount_value_eur === null || Number.isNaN(Number(rule.amount_value_eur))
      ? ''
      : String(rule.amount_value_eur);
    return (
      `<article class="ynab-rule-card" data-rule-id="${escapeHtml(rule.id)}">` +
      '<div class="ynab-rule-row ynab-rule-row-top">' +
      `<div class="ynab-rule-index">Rule ${index + 1}</div>` +
      '<div class="ynab-rule-actions">' +
      '<button type="button" class="ynab-btn ynab-btn-secondary jsRuleMoveUp">Up</button>' +
      '<button type="button" class="ynab-btn ynab-btn-secondary jsRuleMoveDown">Down</button>' +
      '<button type="button" class="ynab-btn ynab-btn-secondary jsRuleDelete">Delete</button>' +
      '</div>' +
      '</div>' +
      '<div class="ynab-rule-grid">' +
      '<label class="ynab-check-row">' +
      `<input class="jsRuleEnabled" type="checkbox"${rule.enabled ? ' checked' : ''}>` +
      '<span>Enabled</span>' +
      '</label>' +
      '<label class="ynab-field">' +
      '<span>Payee match</span>' +
      '<select class="jsRuleMatchType">' +
      `<option value="contains"${rule.payee_match_type === 'contains' ? ' selected' : ''}>contains</option>` +
      `<option value="equals"${rule.payee_match_type === 'equals' ? ' selected' : ''}>equals</option>` +
      '</select>' +
      '</label>' +
      '<label class="ynab-field ynab-rule-wide">' +
      '<span>Payee text</span>' +
      `<input class="jsRulePayeeValue" type="text" value="${escapeHtml(rule.payee_value)}" placeholder="ABC or Suomen Vahinkovakuutus Oy">` +
      '</label>' +
      '<label class="ynab-field">' +
      '<span>Amount</span>' +
      '<select class="jsRuleAmountOperator">' +
      `<option value="any"${rule.amount_operator === 'any' ? ' selected' : ''}>any</option>` +
      `<option value="over"${rule.amount_operator === 'over' ? ' selected' : ''}>over</option>` +
      `<option value="under"${rule.amount_operator === 'under' ? ' selected' : ''}>under</option>` +
      '</select>' +
      '</label>' +
      '<label class="ynab-field">' +
      '<span>Amount EUR</span>' +
      `<input class="jsRuleAmountValue" type="number" min="0" step="0.01" value="${escapeHtml(amountValue)}"${amountDisabled} placeholder="50">` +
      '</label>' +
      '<label class="ynab-field ynab-rule-wide">' +
      '<span>Category</span>' +
      `<select class="jsRuleCategoryId">${makeCategoryOptions(categories, rule.category_id, { includeEmptyOption: true, emptyLabel: 'Choose category', allowMissingSelected: true })}</select>` +
      '</label>' +
      '</div>' +
      '</article>'
    );
  }).join('');
}

function applySettingsToUi(data) {
  const modeSelect = document.getElementById('queueFilterMode');
  const testModeEnabled = document.getElementById('testModeEnabled');
  const showReconciled = document.getElementById('showReconciledTransactions');
  const queueLimitEnabled = document.getElementById('queueLimitEnabled');
  const queueLimitValue = document.getElementById('queueLimitValue');
  const queueLimitUnit = document.getElementById('queueLimitUnit');
  const quickApplyIncludeMedium = document.getElementById('quickApplyIncludeMedium');
  const defaultCategoryId = document.getElementById('defaultCategoryId');

  if (modeSelect instanceof HTMLSelectElement && data && data.queue_filter_mode) {
    modeSelect.value = data.queue_filter_mode;
  }
  if (testModeEnabled instanceof HTMLInputElement) {
    testModeEnabled.checked = !!(data && data.test_mode_enabled);
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

  customRulesState = cloneRules(data && Array.isArray(data.custom_rules) ? data.custom_rules : []);
  updateLimitControlsState();
  renderCustomRulesEditor();
  updateWorkspaceModeUi(data);
}

function readSettingsFromUi() {
  const modeSelect = document.getElementById('queueFilterMode');
  const testModeEnabled = document.getElementById('testModeEnabled');
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
    defaultCategoryValue = selected || null;
  }

  const normalizedRules = customRulesState.map((rule, index) => {
    const payeeValue = String(rule.payee_value || '').trim();
    const categoryId = String(rule.category_id || '').trim();
    const amountOperator = String(rule.amount_operator || 'any').trim().toLowerCase();
    let amountValueEur = null;
    if (!payeeValue) {
      throw new Error(`Custom rule ${index + 1}: payee text is required`);
    }
    if (!categoryId) {
      throw new Error(`Custom rule ${index + 1}: category is required`);
    }
    if (!['contains', 'equals'].includes(String(rule.payee_match_type || ''))) {
      throw new Error(`Custom rule ${index + 1}: invalid payee match type`);
    }
    if (!['any', 'over', 'under'].includes(amountOperator)) {
      throw new Error(`Custom rule ${index + 1}: invalid amount operator`);
    }
    if (amountOperator !== 'any') {
      amountValueEur = Number(rule.amount_value_eur);
      if (!Number.isFinite(amountValueEur) || amountValueEur <= 0) {
        throw new Error(`Custom rule ${index + 1}: amount must be positive`);
      }
    }
    return {
      id: String(rule.id || createBlankRule().id),
      enabled: !!rule.enabled,
      payee_match_type: String(rule.payee_match_type),
      payee_value: payeeValue,
      amount_operator: amountOperator,
      amount_value_eur: amountOperator === 'any' ? null : amountValueEur,
      category_id: categoryId,
    };
  });

  return {
    queue_filter_mode: mode,
    test_mode_enabled: testModeEnabled instanceof HTMLInputElement
      ? testModeEnabled.checked
      : false,
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
    custom_rules: normalizedRules,
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
    allowMissingSelected: true,
  });
  select.value = selected || '';
}

function txMatchesFilter(tx, term) {
  if (!term) return true;
  const haystack = [
    tx && tx.memo ? String(tx.memo) : '',
    tx && tx.date ? String(tx.date) : '',
    tx && tx.payee_name ? String(tx.payee_name) : '',
    tx && tx.account_name ? String(tx.account_name) : '',
    tx && tx.current_category_name ? String(tx.current_category_name) : '',
    tx && tx.resolved_category_name ? String(tx.resolved_category_name) : '',
    tx && tx.resolved_source ? String(tx.resolved_source) : '',
    fmtEurFromMilliunits(tx ? tx.amount_milliunits : null),
  ].join(' ').toLowerCase();
  return haystack.includes(term);
}

function groupIsQuickApplicable(source, label, includeMedium) {
  const normalizedSource = String(source || '').toLowerCase();
  const normalizedLabel = String(label || '').toLowerCase();
  if (normalizedSource === 'rule' || normalizedSource === 'default' || normalizedSource === 'current_category') {
    return true;
  }
  if (normalizedSource !== 'learned') {
    return false;
  }
  if (normalizedLabel === 'high') return true;
  if (normalizedLabel === 'medium') return includeMedium;
  return false;
}

function visibleResolvedGroups(includeMedium) {
  const groups = [];
  const nodes = document.querySelectorAll('#ynabGroups .ynab-group');

  nodes.forEach(node => {
    if (!(node instanceof HTMLElement)) return;
    const categoryId = String(node.getAttribute('data-resolved-category-id') || '').trim();
    const resolvedSource = String(node.getAttribute('data-resolved-source') || '').trim();
    const confidenceLabel = String(node.getAttribute('data-confidence-label') || '').trim();
    if (!categoryId) return;
    if (!groupIsQuickApplicable(resolvedSource, confidenceLabel, includeMedium)) return;

    const txIds = [];
    const checkboxes = node.querySelectorAll('.ynab-tx-check');
    checkboxes.forEach(cb => {
      if (cb instanceof HTMLInputElement && cb.value) {
        txIds.push(cb.value);
      }
    });

    if (!txIds.length) return;
    groups.push({
      categoryId,
      resolvedSource,
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

function setVisibleSelection(checked) {
  const ids = visibleQueueTransactionIds();
  ids.forEach(id => {
    if (checked) {
      selectedQueueIds.add(id);
    } else {
      selectedQueueIds.delete(id);
    }
  });
  renderQueue(queueStateRaw);
}

function clearCurrentSelection() {
  selectedQueueIds.clear();
  renderQueue(queueStateRaw);
}

function selectedTransactions() {
  const byId = txLookupMap();
  return currentSelectionIds()
    .map(txId => byId[txId])
    .filter(Boolean);
}

function canApproveSelectedAsIs() {
  const transactions = selectedTransactions();
  if (!transactions.length) return false;
  return transactions.every(tx => (
    !!(tx && tx.current_category_id) &&
    !!(tx && tx.needs_approval) &&
    !(tx && tx.needs_category)
  ));
}

function updateFloatingApplyBarVisibility() {
  const floatingBar = document.getElementById('floatingApplyBar');
  const countLabel = document.getElementById('selectedTxCount');
  const hintLabel = document.getElementById('floatingApplyHint');
  const approveBtn = document.getElementById('btnApproveSelected');

  if (!(floatingBar instanceof HTMLElement)) return;

  const selectedCount = currentSelectionIds().length;
  if (countLabel) {
    countLabel.textContent = `${selectedCount} selected`;
  }
  if (hintLabel) {
    hintLabel.textContent = canApproveSelectedAsIs()
      ? 'Approve selected categorized transactions as-is, or pick a new category.'
      : 'Choose a category to apply. Approve as-is is only available when every selected item already has a category.';
  }
  if (approveBtn instanceof HTMLButtonElement) {
    approveBtn.disabled = !canApproveSelectedAsIs();
  }

  floatingBar.classList.toggle('is-visible', selectedCount > 0);
  renderRecentCategoryChips();
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

function suggestionSummary(group) {
  const suggestion = group && group.suggestion ? group.suggestion : null;
  if (!suggestion || !suggestion.category_id) {
    return 'Choose in tray';
  }
  const source = String(suggestion.source || '');
  const categoryName = suggestion.category_name || suggestion.category_id;
  if (source === 'rule') return `${categoryName} via custom rule`;
  if (source === 'current_category') return `${categoryName} current category`;
  if (source === 'default') return `${categoryName} default fallback`;
  const confidence = Math.round((Number(suggestion.confidence) || 0) * 100);
  return `${categoryName} ${confidence}%`;
}

function badgeLabel(group) {
  if (!group || !group.suggestion) return 'None';
  return String(group.suggestion.confidence_label || group.confidence_label || 'None');
}

function rowCategorySummary(tx) {
  const currentCategory = tx && tx.current_category_name ? String(tx.current_category_name) : 'Uncategorized';
  const resolvedCategory = tx && tx.resolved_category_name ? String(tx.resolved_category_name) : '';
  const resolvedSource = tx && tx.resolved_source ? String(tx.resolved_source) : 'none';

  if (!resolvedCategory || resolvedSource === 'none') {
    return `Current: ${currentCategory}`;
  }
  if (resolvedSource === 'current_category') {
    return `Current: ${currentCategory}`;
  }
  return `Current: ${currentCategory} • Review: ${resolvedCategory}`;
}

function renderQueue(payload) {
  if (payload) {
    queueStateRaw = payload;
  }
  if (!queueStateRaw) return;

  pruneSelectionState();
  queueState = queueStateRaw;
  updateWorkspaceModeUi(queueState);
  renderGlobalCategorySelect();
  renderDefaultCategorySelect();
  renderCustomRulesEditor();
  updateHeaderStats();

  const groupsEl = document.getElementById('ynabGroups');
  const filterInput = document.getElementById('workspaceFilterInput');
  if (!(groupsEl instanceof HTMLElement)) return;
  const filterTerm = filterInput instanceof HTMLInputElement
    ? String(filterInput.value || '').trim().toLowerCase()
    : '';

  const groups = Array.isArray(queueState.groups) ? queueState.groups.slice() : [];
  groups.sort((a, b) => {
    const rankA = badgeLabel(a);
    const rankB = badgeLabel(b);
    if (rankA !== rankB) {
      const order = { Rule: 0, High: 1, Current: 2, Medium: 3, Default: 4, Low: 5, None: 6 };
      return (order[rankA] ?? 99) - (order[rankB] ?? 99);
    }
    return serializeDateForSort(b.latest_date) - serializeDateForSort(a.latest_date);
  });

  const visibleGroups = [];
  groups.forEach(group => {
    const txAll = Array.isArray(group.transactions) ? group.transactions : [];
    const payeeText = `${group.payee_display || ''} ${group.payee_normalized || ''}`.toLowerCase();
    const payeeMatches = !filterTerm || payeeText.includes(filterTerm);
    const txVisible = payeeMatches ? txAll : txAll.filter(tx => txMatchesFilter(tx, filterTerm));
    if (!txVisible.length) return;
    visibleGroups.push({
      ...group,
      _visibleTransactions: txVisible,
      _totalTransactions: txAll.length,
    });
  });

  const visibleTxCount = visibleGroups.reduce((sum, group) => sum + group._visibleTransactions.length, 0);
  if (filterTerm) {
    setWorkspaceSummary(
      `Showing ${visibleGroups.length}/${queueState.group_count || 0} groups • ` +
      `${visibleTxCount}/${queueState.transaction_count || 0} transactions`
    );
  } else {
    setWorkspaceSummary(
      `${queueState.group_count || 0} groups • ${queueState.transaction_count || 0} transactions • ` +
      `${queueState.needs_category_count || 0} need category • ${queueState.needs_approval_count || 0} need approval`
    );
  }

  if (!visibleGroups.length) {
    groupsEl.innerHTML = '<div class="ynab-empty-placeholder">No review transactions match the current filter.</div>';
    updateFloatingApplyBarVisibility();
    return;
  }

  const html = visibleGroups.map(group => {
    const suggestion = group.suggestion || null;
    const resolvedCategoryId = suggestion && suggestion.category_id ? String(suggestion.category_id) : '';
    const label = badgeLabel(group);
    const visibleCount = group._visibleTransactions.length;
    const totalCount = group._totalTransactions;
    const countText = filterTerm && visibleCount !== totalCount
      ? `${visibleCount}/${totalCount} transaction(s)`
      : `${totalCount} transaction(s)`;
    const expanded = isGroupExpanded(group);
    const groupKey = String(group.group_key || group.payee_normalized || '');
    const lastActivity = group.latest_date ? fmtDateDDMMYYYY(group.latest_date) : '—';
    const actionLabel = suggestion && suggestion.source === 'current_category'
      ? 'Approve group'
      : 'Apply suggestion';
    const needsSummary = `${group.needs_category_count || 0} need category • ${group.needs_approval_count || 0} need approval`;

    const txRows = group._visibleTransactions.map(tx => {
      const txId = tx && tx.id ? String(tx.id) : '';
      const memoText = tx && tx.memo ? String(tx.memo) : 'No memo';
      const dateText = fmtDateDDMMYYYY(tx && tx.date ? String(tx.date) : '');
      const amountText = fmtEurFromMilliunits(tx ? tx.amount_milliunits : null);
      const accountFlow = txAccountFlowLabel(tx);
      const categoryText = rowCategorySummary(tx);
      const isSelected = selectedQueueIds.has(txId);
      return (
        `<li class="ynab-tx-item${isSelected ? ' is-selected' : ''}" data-tx-id="${escapeHtml(txId)}">` +
        `<input class="ynab-tx-check" type="checkbox" value="${escapeHtml(txId)}"${isSelected ? ' checked' : ''}>` +
        '<div class="ynab-tx-main">' +
        `<div class="ynab-tx-primary">${escapeHtml(accountFlow)}</div>` +
        `<div class="ynab-tx-secondary">${escapeHtml(categoryText)}</div>` +
        `<div class="ynab-tx-tertiary">${escapeHtml(memoText)} • ${escapeHtml(dateText)}</div>` +
        '</div>' +
        `<div class="ynab-tx-amount">${escapeHtml(amountText)}</div>` +
        '</li>'
      );
    }).join('');

    const suggestionButton = resolvedCategoryId
      ? `<button type="button" class="ynab-btn ynab-btn-primary ynab-group-inline-action btnUseSuggestion">${escapeHtml(actionLabel)}</button>`
      : '';

    const toggleLabel = expanded ? 'Collapse' : 'Expand';

    return (
      `<article class="ynab-group" data-group-key="${escapeHtml(groupKey)}" ` +
      `data-resolved-source="${escapeHtml(String(group.resolved_source || 'none'))}" ` +
      `data-confidence-label="${escapeHtml(String(label).toLowerCase())}" ` +
      `data-resolved-category-id="${escapeHtml(resolvedCategoryId)}">` +
      '<div class="ynab-group-shell">' +
      '<div class="ynab-group-top">' +
      '<label class="ynab-group-checkbox-wrap" aria-label="Select group">' +
      `<input class="ynab-group-checkbox" type="checkbox" data-group-key="${escapeHtml(groupKey)}">` +
      '</label>' +
      `<button type="button" class="ynab-group-expand" data-group-key="${escapeHtml(groupKey)}">` +
      '<div class="ynab-group-heading">' +
      `<div class="ynab-group-title">${escapeHtml(group.payee_display || group.payee_normalized)}</div>` +
      `<div class="ynab-group-count">${escapeHtml(countText)}</div>` +
      '</div>' +
      '<div class="ynab-group-meta">' +
      `<span class="ynab-badge ${badgeClass(label)}">${escapeHtml(label)}</span>` +
      `<span class="ynab-group-meta-item">${escapeHtml(suggestionSummary(group))}</span>` +
      `<span class="ynab-group-meta-item">${escapeHtml(needsSummary)}</span>` +
      `<span class="ynab-group-meta-item">Last activity ${escapeHtml(lastActivity)}</span>` +
      '</div>' +
      '</button>' +
      `<div class="ynab-group-actions">${suggestionButton}</div>` +
      '</div>' +
      `<div class="ynab-group-body${expanded ? '' : ' is-collapsed'}">` +
      `<ul class="ynab-tx-list">${txRows}</ul>` +
      `<button type="button" class="ynab-group-toggle" data-group-key="${escapeHtml(groupKey)}">${escapeHtml(toggleLabel)}</button>` +
      '</div>' +
      '</div>' +
      '</article>'
    );
  }).join('');

  groupsEl.innerHTML = html;
  updateGroupCheckboxStates();
  updateFloatingApplyBarVisibility();
}

async function loadQueue(options = {}) {
  const opts = Object.assign({ showStatus: true }, options || {});
  if (opts.showStatus) {
    showMessage('Loading review queue...');
  }
  console.debug('Loading YNAB review queue');
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
  const opts = Object.assign(
    {
      reloadQueue: true,
      showStatus: true,
    },
    options || {}
  );

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
      const statusText = data && data.simulated
        ? `Test mode: simulated category apply for ${txIds.length} transaction(s)`
        : `Applied category and approved ${txIds.length} transaction(s)`;
      showMessage(statusText, 'ok');
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
  const opts = Object.assign(
    {
      reloadQueue: true,
      showStatus: true,
    },
    options || {}
  );

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

    txIds.forEach(id => selectedQueueIds.delete(id));
    if (opts.showStatus) {
      const statusText = data && data.simulated
        ? `Test mode: simulated approval for ${txIds.length} transaction(s)`
        : `Approved ${txIds.length} transaction(s)`;
      showMessage(statusText, 'ok');
    }
    if (opts.reloadQueue) {
      await loadQueue({ showStatus: false });
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
  const opts = Object.assign(
    {
      showSuccess: true,
      reloadQueueAfter: true,
    },
    options || {}
  );

  const payload = readSettingsFromUi();
  console.debug('Saving YNAB workspace config', payload);
  const data = await apiRequest('/api/ynab-categorizer/config', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  applySettingsToUi(data || payload);
  if (opts.showSuccess) {
    showMessage('Settings saved', 'ok');
  }
  if (opts.reloadQueueAfter) {
    await loadQueue({ showStatus: false });
  }
}

async function applyResolvedFromVisibleGroups() {
  const includeMediumInput = document.getElementById('quickApplyIncludeMedium');
  const includeMedium = includeMediumInput instanceof HTMLInputElement
    ? includeMediumInput.checked
    : false;
  const groups = visibleResolvedGroups(includeMedium);

  if (!groups.length) {
    showMessage('No visible resolvable groups to apply', 'error');
    return;
  }

  let appliedGroups = 0;
  let appliedTransactions = 0;
  let failedGroups = 0;

  for (let i = 0; i < groups.length; i += 1) {
    const group = groups[i];
    showMessage(`Applying visible groups ${i + 1}/${groups.length}...`);
    try {
      await applyTransactions(group.txIds, group.categoryId, {
        reloadQueue: false,
        showStatus: false,
      });
      appliedGroups += 1;
      appliedTransactions += group.txIds.length;
    } catch (error) {
      console.error('Visible group apply failed:', group, error);
      failedGroups += 1;
    }
  }

  await loadQueue({ showStatus: false });
  const actionLabel = isTestModeEnabled() ? 'Visible simulation complete' : 'Visible apply complete';
  const summary = (
    `${actionLabel}: ${appliedGroups} group(s), ` +
    `${appliedTransactions} transaction(s), ${failedGroups} failed`
  );
  showMessage(summary, failedGroups > 0 ? 'error' : 'ok');
}

function isTestModeEnabled() {
  return !!(queueState && queueState.test_mode_enabled);
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
        renderQueue(queueStateRaw);
      }
      return;
    }

    if (typing) {
      return;
    }

    if ((event.key === 'c' || event.key === 'C') && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (categorySearch instanceof HTMLInputElement) {
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
      if (applySelectedBtn instanceof HTMLButtonElement) {
        const floatingBar = document.getElementById('floatingApplyBar');
        const floatingVisible = floatingBar instanceof HTMLElement && floatingBar.classList.contains('is-visible');
        if (floatingVisible) {
          event.preventDefault();
          applySelectedBtn.click();
        }
      }
    }
  });
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

function handleQueueListClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  const suggestionBtn = target.closest('.btnUseSuggestion');
  if (suggestionBtn instanceof HTMLElement) {
    const group = suggestionBtn.closest('.ynab-group');
    if (!group) return;
    const resolvedCategoryId = String(group.getAttribute('data-resolved-category-id') || '').trim();
    if (!resolvedCategoryId) {
      showMessage('No resolved category available for this group', 'error');
      return;
    }
    const txIds = Array.from(group.querySelectorAll('.ynab-tx-check'))
      .filter(cb => cb instanceof HTMLInputElement && cb.value)
      .map(cb => cb.value);
    if (!txIds.length) {
      showMessage('No transactions available in this group', 'error');
      return;
    }
    applyTransactions(txIds, resolvedCategoryId).catch(() => {});
    return;
  }

  const toggleBtn = target.closest('.ynab-group-toggle');
  if (toggleBtn instanceof HTMLElement) {
    const groupKey = String(toggleBtn.getAttribute('data-group-key') || '').trim();
    const groupData = (queueState.groups || []).find(group => String(group.group_key || '') === groupKey);
    setGroupExpanded(groupKey, !isGroupExpanded(groupData || { group_key: groupKey }));
    renderQueue(queueStateRaw);
    return;
  }

  const expandBtn = target.closest('.ynab-group-expand');
  if (expandBtn instanceof HTMLElement) {
    const groupKey = String(expandBtn.getAttribute('data-group-key') || '').trim();
    const groupData = (queueState.groups || []).find(group => String(group.group_key || '') === groupKey);
    setGroupExpanded(groupKey, !isGroupExpanded(groupData || { group_key: groupKey }));
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

function handleRuleListClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const card = target.closest('.ynab-rule-card');
  if (!(card instanceof HTMLElement)) return;
  const ruleId = String(card.getAttribute('data-rule-id') || '').trim();
  if (!ruleId) return;

  if (target.closest('.jsRuleDelete')) {
    customRulesState = customRulesState.filter(rule => rule.id !== ruleId);
    renderCustomRulesEditor();
    return;
  }
  if (target.closest('.jsRuleMoveUp')) {
    const index = customRulesState.findIndex(rule => rule.id === ruleId);
    if (index > 0) {
      const next = customRulesState.slice();
      [next[index - 1], next[index]] = [next[index], next[index - 1]];
      customRulesState = next;
      renderCustomRulesEditor();
    }
    return;
  }
  if (target.closest('.jsRuleMoveDown')) {
    const index = customRulesState.findIndex(rule => rule.id === ruleId);
    if (index >= 0 && index < customRulesState.length - 1) {
      const next = customRulesState.slice();
      [next[index], next[index + 1]] = [next[index + 1], next[index]];
      customRulesState = next;
      renderCustomRulesEditor();
    }
  }
}

function handleRuleListInput(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const card = target.closest('.ynab-rule-card');
  if (!(card instanceof HTMLElement)) return;
  const ruleId = String(card.getAttribute('data-rule-id') || '').trim();
  if (!ruleId) return;

  if (target.classList.contains('jsRuleEnabled') && target instanceof HTMLInputElement) {
    customRulesState = customRulesState.map(rule => (
      rule.id === ruleId ? { ...rule, enabled: target.checked } : rule
    ));
    return;
  }
  if (target.classList.contains('jsRuleMatchType') && target instanceof HTMLSelectElement) {
    customRulesState = customRulesState.map(rule => (
      rule.id === ruleId ? { ...rule, payee_match_type: target.value } : rule
    ));
    return;
  }
  if (target.classList.contains('jsRulePayeeValue') && target instanceof HTMLInputElement) {
    customRulesState = customRulesState.map(rule => (
      rule.id === ruleId ? { ...rule, payee_value: target.value } : rule
    ));
    return;
  }
  if (target.classList.contains('jsRuleAmountOperator') && target instanceof HTMLSelectElement) {
    customRulesState = customRulesState.map(rule => (
      rule.id === ruleId
        ? {
          ...rule,
          amount_operator: target.value,
          amount_value_eur: target.value === 'any' ? null : rule.amount_value_eur,
        }
        : rule
    ));
    renderCustomRulesEditor();
    return;
  }
  if (target.classList.contains('jsRuleAmountValue') && target instanceof HTMLInputElement) {
    customRulesState = customRulesState.map(rule => (
      rule.id === ruleId
        ? {
          ...rule,
          amount_value_eur: target.value === '' ? null : Number(target.value),
        }
        : rule
    ));
    return;
  }
  if (target.classList.contains('jsRuleCategoryId') && target instanceof HTMLSelectElement) {
    customRulesState = customRulesState.map(rule => (
      rule.id === ruleId ? { ...rule, category_id: target.value } : rule
    ));
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
  const addCustomRuleBtn = document.getElementById('btnAddCustomRule');
  const globalCategory = document.getElementById('globalCategorySelect');
  const globalCategorySearch = document.getElementById('globalCategorySearch');
  const queueLimitEnabled = document.getElementById('queueLimitEnabled');
  const quickApplyIncludeMedium = document.getElementById('quickApplyIncludeMedium');
  const toolbarPrimaryActionBtn = document.getElementById('btnToolbarPrimaryAction');
  const toolbarSelectVisibleBtn = document.getElementById('btnToolbarSelectVisible');
  const toolbarClearSelectionBtn = document.getElementById('btnToolbarClearSelection');
  const trayClearSelectionBtn = document.getElementById('btnTrayClearSelection');
  const filterInput = document.getElementById('workspaceFilterInput');
  const openSettingsBtn = document.getElementById('btnOpenSettings');
  const closeSettingsBtn = document.getElementById('btnCloseSettings');
  const closeSettingsBackdropBtn = document.getElementById('btnCloseSettingsBackdrop');
  const queueContainer = document.getElementById('ynabGroups');
  const recentCategoryContainer = document.getElementById('recentCategoryChips');
  const customRulesList = document.getElementById('customRulesList');

  if (refreshBtn instanceof HTMLButtonElement) {
    refreshBtn.addEventListener('click', async () => {
      await loadQueue();
      await loadBootstrapStatus();
    });
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

  if (addCustomRuleBtn instanceof HTMLButtonElement) {
    addCustomRuleBtn.addEventListener('click', () => {
      customRulesState = [...customRulesState, createBlankRule()];
      renderCustomRulesEditor();
    });
  }

  if (filterInput instanceof HTMLInputElement) {
    filterInput.addEventListener('input', () => {
      renderQueue(queueStateRaw);
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
      await applyResolvedFromVisibleGroups();
    });
  }

  if (approveSelectedBtn instanceof HTMLButtonElement) {
    approveSelectedBtn.addEventListener('click', async () => {
      const txIds = currentSelectionIds();
      if (!txIds.length) {
        showMessage('Select at least one transaction', 'error');
        return;
      }
      if (!canApproveSelectedAsIs()) {
        showMessage('Approve as-is only works when every selected transaction already has a category', 'error');
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
      const txIds = currentSelectionIds();
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

  if (customRulesList instanceof HTMLElement) {
    customRulesList.addEventListener('click', handleRuleListClick);
    customRulesList.addEventListener('input', handleRuleListInput);
    customRulesList.addEventListener('change', handleRuleListInput);
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
      renderQueue(queueStateRaw);
    }, 120);
  });

  setupKeyboardShortcuts();
  loadConfig();
  loadBootstrapStatus();
  loadQueue();
});
