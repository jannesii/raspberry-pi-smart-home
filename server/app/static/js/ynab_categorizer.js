/**
 * YNAB Categorizer workspace UI
 */

console.log('ynab_categorizer.js loaded');

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

let pendingCategoryByTxId = {};
let initialCategoryByTxId = {};
let groupExpansionState = {};
let resizeTimer = null;
let customRulesState = [];
let customRuleCounter = 0;
let reviewCommitInFlight = false;

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
  if (!(message instanceof HTMLElement)) return;
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
  return tokenInput instanceof HTMLInputElement ? tokenInput.value : '';
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

function setWorkspaceSummary(text) {
  const summaryEl = document.getElementById('workspaceSummary');
  if (summaryEl instanceof HTMLElement) {
    summaryEl.textContent = text;
  }
}

function updateWorkspaceModeUi(source = null) {
  const modeSource = source && typeof source === 'object' ? source : queueState;
  const testModeEnabled = !!(modeSource && modeSource.test_mode_enabled);
  const badgeEl = document.getElementById('workspaceModeBadge');
  const commitBtn = document.getElementById('btnReviewCommit');

  if (badgeEl instanceof HTMLElement) {
    badgeEl.textContent = testModeEnabled ? 'Test mode: writes disabled' : 'Live mode';
    badgeEl.classList.toggle('is-test', testModeEnabled);
  }
  if (commitBtn instanceof HTMLButtonElement) {
    commitBtn.textContent = reviewCommitInFlight
      ? (testModeEnabled ? 'Simulating...' : 'Approving...')
      : (testModeEnabled ? 'Simulate visible' : 'Approve visible');
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

  if (needsCategoryCountEl instanceof HTMLElement) {
    needsCategoryCountEl.textContent = queueStateRaw
      ? String(queueStateRaw.needs_category_count || 0)
      : '—';
  }
  if (needsCategoryMetaEl instanceof HTMLElement) {
    needsCategoryMetaEl.textContent = queueStateRaw
      ? `${queueStateRaw.group_count || 0} group(s) in review`
      : 'Loading queue…';
  }
  if (needsApprovalCountEl instanceof HTMLElement) {
    needsApprovalCountEl.textContent = queueStateRaw
      ? String(queueStateRaw.needs_approval_count || 0)
      : '—';
  }
  if (needsApprovalMetaEl instanceof HTMLElement) {
    needsApprovalMetaEl.textContent = queueStateRaw
      ? `${queueStateRaw.transaction_count || 0} transaction(s) shown`
      : 'Loading queue…';
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

function currentFilterTerm() {
  const filterInput = document.getElementById('workspaceFilterInput');
  return filterInput instanceof HTMLInputElement
    ? String(filterInput.value || '').trim().toLowerCase()
    : '';
}

function buildVisibleGroups(filterTerm) {
  const groups = Array.isArray(queueState.groups) ? queueState.groups.slice() : [];
  groups.sort((a, b) => {
    const rankA = String(a && a.confidence_label ? a.confidence_label : 'None');
    const rankB = String(b && b.confidence_label ? b.confidence_label : 'None');
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

  return visibleGroups;
}

function visibleTransactionsFromGroups(groups) {
  return groups.flatMap(group => Array.isArray(group._visibleTransactions) ? group._visibleTransactions : []);
}

function seededCategoryId(tx) {
  if (tx && tx.current_category_id) return String(tx.current_category_id);
  if (tx && tx.resolved_category_id) return String(tx.resolved_category_id);
  return '';
}

function syncPendingCategories(payload) {
  const nextPending = {};
  const nextInitial = {};
  const groups = Array.isArray(payload && payload.groups) ? payload.groups : [];
  groups.forEach(group => {
    const transactions = Array.isArray(group && group.transactions) ? group.transactions : [];
    transactions.forEach(tx => {
      const txId = tx && tx.id ? String(tx.id) : '';
      if (!txId) return;
      const seeded = seededCategoryId(tx);
      nextInitial[txId] = seeded;
      nextPending[txId] = seeded;
    });
  });
  pendingCategoryByTxId = nextPending;
  initialCategoryByTxId = nextInitial;
}

function categoryLabelById(categoryId) {
  const id = String(categoryId || '').trim();
  if (!id) return 'Uncategorized';
  const match = (queueState.categories || []).find(cat => cat && String(cat.id || '') === id);
  return match && match.name ? String(match.name) : id;
}

function rowState(tx) {
  const txId = String(tx && tx.id ? tx.id : '');
  const pendingCategoryId = String(pendingCategoryByTxId[txId] || '');
  const initialCategoryId = String(initialCategoryByTxId[txId] || seededCategoryId(tx));
  const missing = !!(tx && tx.needs_category && !pendingCategoryId);
  const dirty = pendingCategoryId !== initialCategoryId;
  const ready = !missing;
  const label = missing ? 'Missing category' : (dirty ? 'Edited' : 'Ready');
  const statusClass = missing ? 'missing' : (dirty ? 'dirty' : 'ready');
  return {
    pendingCategoryId,
    initialCategoryId,
    missing,
    dirty,
    ready,
    label,
    statusClass,
  };
}

function rowCategorySummary(tx) {
  const currentCategory = tx && tx.current_category_name ? String(tx.current_category_name) : 'Uncategorized';
  const resolvedCategory = tx && tx.resolved_category_name ? String(tx.resolved_category_name) : '';
  if (!resolvedCategory || resolvedCategory === currentCategory) {
    return `Current: ${currentCategory}`;
  }
  return `Current: ${currentCategory} • Suggested: ${resolvedCategory}`;
}

function humanizeSuggestionSource(source) {
  const normalized = String(source || '').trim().toLowerCase();
  if (!normalized || normalized === 'none') return 'No shared suggestion';
  if (normalized === 'current_category') return 'Current category';
  return normalized.replace(/_/g, ' ');
}

function updateReviewRail(visibleTransactions) {
  const countEl = document.getElementById('reviewVisibleCount');
  const statsEl = document.getElementById('reviewCommitStats');
  const hintEl = document.getElementById('reviewCommitHint');
  const commitBtn = document.getElementById('btnReviewCommit');
  const resetBtn = document.getElementById('btnResetVisibleCategories');

  const total = visibleTransactions.length;
  let readyCount = 0;
  let missingCount = 0;
  let dirtyCount = 0;
  visibleTransactions.forEach(tx => {
    const state = rowState(tx);
    if (state.ready) readyCount += 1;
    if (state.missing) missingCount += 1;
    if (state.dirty) dirtyCount += 1;
  });

  if (countEl instanceof HTMLElement) {
    countEl.textContent = `${total} visible`;
  }
  if (statsEl instanceof HTMLElement) {
    statsEl.textContent = `Ready ${readyCount} • Missing ${missingCount} • Changed ${dirtyCount}`;
  }
  if (hintEl instanceof HTMLElement) {
    if (!total) {
      hintEl.textContent = 'Adjust the filter or refresh the queue to review transactions.';
    } else if (missingCount > 0) {
      hintEl.textContent = 'Every visible uncategorized row needs a category before batch approval.';
    } else {
      hintEl.textContent = 'Visible rows will be submitted together with each row’s selected category.';
    }
  }
  if (commitBtn instanceof HTMLButtonElement) {
    commitBtn.disabled = reviewCommitInFlight || total === 0 || missingCount > 0;
  }
  if (resetBtn instanceof HTMLButtonElement) {
    resetBtn.disabled = reviewCommitInFlight || total === 0 || dirtyCount === 0;
  }
  updateWorkspaceModeUi(queueState);
}

function renderQueue(payload) {
  if (payload) {
    queueStateRaw = payload;
  }
  if (!queueStateRaw) return;

  queueState = queueStateRaw;
  updateWorkspaceModeUi(queueState);
  renderDefaultCategorySelect();
  renderCustomRulesEditor();
  updateHeaderStats();

  const groupsEl = document.getElementById('ynabGroups');
  if (!(groupsEl instanceof HTMLElement)) return;

  const filterTerm = currentFilterTerm();
  const visibleGroups = buildVisibleGroups(filterTerm);
  const visibleTransactions = visibleTransactionsFromGroups(visibleGroups);

  if (filterTerm) {
    setWorkspaceSummary(
      `Showing ${visibleGroups.length}/${queueState.group_count || 0} groups • ` +
      `${visibleTransactions.length}/${queueState.transaction_count || 0} transactions`
    );
  } else {
    setWorkspaceSummary(
      `${queueState.group_count || 0} groups • ${queueState.transaction_count || 0} transactions • ` +
      `${queueState.needs_category_count || 0} need category • ${queueState.needs_approval_count || 0} need approval`
    );
  }

  if (!visibleGroups.length) {
    groupsEl.innerHTML = '<div class="ynab-empty-placeholder">No review transactions match the current filter.</div>';
    updateReviewRail([]);
    return;
  }

  const categories = Array.isArray(queueState.categories) ? queueState.categories : [];
  const html = visibleGroups.map(group => {
    const label = String(group && group.confidence_label ? group.confidence_label : 'None');
    const visibleCount = group._visibleTransactions.length;
    const totalCount = group._totalTransactions;
    const countText = filterTerm && visibleCount !== totalCount
      ? `${visibleCount}/${totalCount} transaction(s)`
      : `${totalCount} transaction(s)`;
    const expanded = isGroupExpanded(group);
    const groupKey = String(group.group_key || group.payee_normalized || '');
    const lastActivity = group.latest_date ? fmtDateDDMMYYYY(group.latest_date) : '—';
    const needsSummary = `${group.needs_category_count || 0} need category • ${group.needs_approval_count || 0} need approval`;

    const txRows = group._visibleTransactions.map(tx => {
      const txId = tx && tx.id ? String(tx.id) : '';
      const memoText = tx && tx.memo ? String(tx.memo) : 'No memo';
      const dateText = fmtDateDDMMYYYY(tx && tx.date ? String(tx.date) : '');
      const amountText = fmtEurFromMilliunits(tx ? tx.amount_milliunits : null);
      const accountFlow = txAccountFlowLabel(tx);
      const categorySummary = rowCategorySummary(tx);
      const status = rowState(tx);
      const selectedCategoryName = categoryLabelById(status.pendingCategoryId);
      const stateCopy = status.missing
        ? 'Pick a category to include this row in the batch.'
        : (status.dirty ? `Will submit as ${selectedCategoryName}.` : `Ready with ${selectedCategoryName}.`);

      return (
        `<li class="ynab-tx-item is-${escapeHtml(status.statusClass)}" data-tx-id="${escapeHtml(txId)}">` +
        '<div class="ynab-tx-main">' +
        `<div class="ynab-tx-primary">${escapeHtml(accountFlow)}</div>` +
        `<div class="ynab-tx-secondary">${escapeHtml(categorySummary)}</div>` +
        `<div class="ynab-tx-tertiary">${escapeHtml(memoText)} • ${escapeHtml(dateText)}</div>` +
        '</div>' +
        '<div class="ynab-tx-controls">' +
        `<label class="ynab-tx-category-field" for="txCategory-${escapeHtml(txId)}">` +
        '<span>Category</span>' +
        `<select id="txCategory-${escapeHtml(txId)}" class="ynab-tx-category-select" data-tx-id="${escapeHtml(txId)}">` +
        `${makeCategoryOptions(categories, status.pendingCategoryId, {
          includeEmptyOption: !(tx && tx.current_category_id),
          emptyLabel: 'Choose category',
          allowMissingSelected: true,
        })}` +
        '</select>' +
        '</label>' +
        `<div class="ynab-tx-state-meta">` +
        `<span class="ynab-tx-state ${escapeHtml(status.statusClass)}">${escapeHtml(status.label)}</span>` +
        `<span class="ynab-tx-state-copy">${escapeHtml(stateCopy)}</span>` +
        '</div>' +
        '</div>' +
        `<div class="ynab-tx-amount">${escapeHtml(amountText)}</div>` +
        '</li>'
      );
    }).join('');

    const toggleLabel = expanded ? 'Collapse' : 'Expand';
    const suggestionSummary = group && group.suggestion && group.suggestion.category_name
      ? `${group.suggestion.category_name} • ${humanizeSuggestionSource(group.suggestion.source)}`
      : 'No shared suggestion';

    return (
      `<article class="ynab-group" data-group-key="${escapeHtml(groupKey)}">` +
      '<div class="ynab-group-shell">' +
      '<div class="ynab-group-top">' +
      `<button type="button" class="ynab-group-expand" data-group-key="${escapeHtml(groupKey)}">` +
      '<div class="ynab-group-heading">' +
      `<div class="ynab-group-title">${escapeHtml(group.payee_display || group.payee_normalized)}</div>` +
      `<div class="ynab-group-count">${escapeHtml(countText)}</div>` +
      '</div>' +
      '<div class="ynab-group-meta">' +
      `<span class="ynab-badge ${badgeClass(label)}">${escapeHtml(label)}</span>` +
      `<span class="ynab-group-meta-item">${escapeHtml(suggestionSummary)}</span>` +
      `<span class="ynab-group-meta-item">${escapeHtml(needsSummary)}</span>` +
      `<span class="ynab-group-meta-item">Last activity ${escapeHtml(lastActivity)}</span>` +
      '</div>' +
      '</button>' +
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
  updateReviewRail(visibleTransactions);
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
    syncPendingCategories(queueStateRaw);
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
  if (!(statusEl instanceof HTMLElement)) return;
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

function visibleTransactions() {
  return visibleTransactionsFromGroups(buildVisibleGroups(currentFilterTerm()));
}

function resetVisibleCategories() {
  const transactions = visibleTransactions();
  transactions.forEach(tx => {
    const txId = String(tx && tx.id ? tx.id : '');
    if (!txId) return;
    pendingCategoryByTxId[txId] = String(initialCategoryByTxId[txId] || '');
  });
  renderQueue(queueStateRaw);
}

async function reviewCommitVisible() {
  const transactions = visibleTransactions();
  if (!transactions.length) {
    showMessage('No visible transactions to submit', 'error');
    return;
  }

  const missing = transactions.filter(tx => {
    const txId = String(tx && tx.id ? tx.id : '');
    return !String(pendingCategoryByTxId[txId] || '');
  });
  if (missing.length) {
    showMessage(`Choose categories for ${missing.length} visible transaction(s) first`, 'error');
    renderQueue(queueStateRaw);
    return;
  }

  const payload = transactions.map(tx => ({
    id: String(tx.id),
    category_id: String(pendingCategoryByTxId[String(tx.id)] || ''),
  }));

  reviewCommitInFlight = true;
  renderQueue(queueStateRaw);
  showMessage(`Submitting ${payload.length} visible transaction(s)...`);
  console.debug('Submitting YNAB review commit', {
    transactionCount: payload.length,
  });

  try {
    const data = await apiRequest('/api/ynab-categorizer/review-commit', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ transactions: payload }),
    });
    const statusText = data && data.simulated
      ? `Test mode: simulated ${data.transaction_count} review row(s)`
      : `Submitted ${data.approved_count} row(s): ${data.categorized_count} categorized, ${data.approval_only_count} approve-only`;
    showMessage(statusText, 'ok');
    await loadQueue({ showStatus: false });
  } catch (error) {
    console.error('Review commit failed:', error);
    showMessage(`Submit failed: ${error.message}`, 'error');
  } finally {
    reviewCommitInFlight = false;
    renderQueue(queueStateRaw);
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

    const filterInput = document.getElementById('workspaceFilterInput');
    const settingsShell = document.getElementById('ynabSettingsShell');
    const settingsOpen = settingsShell instanceof HTMLElement && settingsShell.classList.contains('is-open');
    const typing = isTypingTarget(event.target);

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

    if (event.key === 'Enter' && event.ctrlKey) {
      const commitBtn = document.getElementById('btnReviewCommit');
      if (commitBtn instanceof HTMLButtonElement && !commitBtn.disabled) {
        event.preventDefault();
        commitBtn.click();
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
  }
}

function handleQueueListChange(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  if (target instanceof HTMLSelectElement && target.classList.contains('ynab-tx-category-select')) {
    const txId = String(target.getAttribute('data-tx-id') || '').trim();
    if (!txId) return;
    pendingCategoryByTxId[txId] = String(target.value || '').trim();
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

  const refreshBtn = document.getElementById('btnRefreshQueue');
  const reviewCommitBtn = document.getElementById('btnReviewCommit');
  const resetVisibleBtn = document.getElementById('btnResetVisibleCategories');
  const bootstrapBtn = document.getElementById('btnBootstrap');
  const saveModeBtn = document.getElementById('btnSaveMode');
  const addCustomRuleBtn = document.getElementById('btnAddCustomRule');
  const queueLimitEnabled = document.getElementById('queueLimitEnabled');
  const filterInput = document.getElementById('workspaceFilterInput');
  const openSettingsBtn = document.getElementById('btnOpenSettings');
  const closeSettingsBtn = document.getElementById('btnCloseSettings');
  const closeSettingsBackdropBtn = document.getElementById('btnCloseSettingsBackdrop');
  const queueContainer = document.getElementById('ynabGroups');
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

  if (reviewCommitBtn instanceof HTMLButtonElement) {
    reviewCommitBtn.addEventListener('click', async () => {
      await reviewCommitVisible();
    });
  }

  if (resetVisibleBtn instanceof HTMLButtonElement) {
    resetVisibleBtn.addEventListener('click', resetVisibleCategories);
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
    if (statusEl instanceof HTMLElement) {
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
