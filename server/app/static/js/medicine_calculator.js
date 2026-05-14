/**
 * Medicine Calculator workspace
 */

(function() {
  console.log('💊 medicine_calculator.js loaded');

  const API_URL = '/api/medicine-calculator/purchases';
  const ADD_NEW_VALUE = '__add_new__';
  const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  let state = {
    purchases: [],
    medicines: [],
    summaries: [],
  };
  let editingId = null;

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function getCsrfToken() {
    const tokenInput = byId('csrfToken');
    return tokenInput instanceof HTMLInputElement ? tokenInput.value : '';
  }

  function fmtDate(dateStr) {
    if (!dateStr) return '—';
    const parts = String(dateStr).split('-');
    if (parts.length !== 3) return '—';
    return `${parts[2]}.${parts[1]}.${parts[0]}`;
  }

  function fmtTreatmentDays(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return Number.isInteger(number) ? String(number) : number.toFixed(1);
  }

  function fmtWeekdays(values) {
    if (!Array.isArray(values) || values.length === 0) return '—';
    return values
      .map(value => WEEKDAY_LABELS[Number(value)] || null)
      .filter(Boolean)
      .join(', ') || '—';
  }

  function showMessage(text, kind = 'ok') {
    const message = byId('medicineMessage');
    if (!(message instanceof HTMLElement)) return;
    message.classList.remove('is-error');
    if (!text) {
      message.style.display = 'none';
      message.textContent = '';
      return;
    }
    message.textContent = text;
    message.style.display = 'block';
    if (kind === 'error') {
      message.classList.add('is-error');
    }
  }

  async function apiRequest(url, options = {}) {
    const opts = Object.assign({ method: 'GET', credentials: 'same-origin' }, options || {});
    const headers = Object.assign({}, opts.headers || {});
    if (opts.method !== 'GET') {
      headers['Content-Type'] = 'application/json';
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

  function setStateFromPayload(payload) {
    state = {
      purchases: Array.isArray(payload.purchases) ? payload.purchases : [],
      medicines: Array.isArray(payload.medicines) ? payload.medicines : [],
      summaries: Array.isArray(payload.summaries) ? payload.summaries : [],
    };
    console.debug('Medicine state updated:', state);
  }

  function latestPurchaseForKey(medicineKey) {
    return state.purchases.find(purchase => purchase.medicine_key === medicineKey) || null;
  }

  function renderMedicineSelect(selectedKey = ADD_NEW_VALUE) {
    const select = byId('medicineSelect');
    if (!(select instanceof HTMLSelectElement)) return;

    const options = [
      `<option value="${ADD_NEW_VALUE}">Add new medicine</option>`,
      ...state.medicines.map(item => {
        const key = String(item.medicine_key || '');
        const name = String(item.medicine_name || key);
        const selected = key === selectedKey ? ' selected' : '';
        return `<option value="${escapeHtml(key)}"${selected}>${escapeHtml(name)}</option>`;
      }),
    ];
    select.innerHTML = options.join('');
    if (!state.medicines.some(item => item.medicine_key === selectedKey)) {
      select.value = ADD_NEW_VALUE;
    }
    updateMedicineNameMode();
  }

  function updateMedicineNameMode() {
    const select = byId('medicineSelect');
    const field = byId('newMedicineField');
    const input = byId('newMedicineName');
    if (!(select instanceof HTMLSelectElement) || !(field instanceof HTMLElement)) return;
    const addingNew = select.value === ADD_NEW_VALUE;
    field.style.display = addingNew ? 'grid' : 'none';
    if (input instanceof HTMLInputElement) {
      input.required = addingNew;
      if (!addingNew) input.value = '';
    }
  }

  function applyDefaultsForSelectedMedicine() {
    if (editingId !== null) return;
    const select = byId('medicineSelect');
    if (!(select instanceof HTMLSelectElement) || select.value === ADD_NEW_VALUE) return;
    const latest = latestPurchaseForKey(select.value);
    if (!latest) return;

    const doseInput = byId('doseInput');
    if (doseInput instanceof HTMLInputElement) {
      doseInput.value = String(latest.dose_per_dosing_day || 1);
    }
    setWeekdays(Array.isArray(latest.dosing_weekdays) ? latest.dosing_weekdays : []);
  }

  function selectedWeekdays() {
    const grid = byId('weekdayGrid');
    if (!(grid instanceof HTMLElement)) return [];
    const checked = grid.querySelectorAll('input[name="dosingWeekday"]:checked');
    return Array.from(checked).map(input => Number(input.value));
  }

  function setWeekdays(values) {
    const selected = new Set((Array.isArray(values) ? values : []).map(value => Number(value)));
    const grid = byId('weekdayGrid');
    if (!(grid instanceof HTMLElement)) return;
    grid.querySelectorAll('input[name="dosingWeekday"]').forEach(input => {
      if (input instanceof HTMLInputElement) {
        input.checked = selected.has(Number(input.value));
      }
    });
  }

  function renderSummaries() {
    const list = byId('medicineSummaryList');
    const count = byId('medicineSummaryCount');
    if (!(list instanceof HTMLElement)) return;
    if (count instanceof HTMLElement) {
      count.textContent = `${state.summaries.length} medicines`;
    }

    if (state.summaries.length === 0) {
      list.innerHTML = '<p class="medicine-empty">No purchases yet.</p>';
      return;
    }

    list.innerHTML = state.summaries.map(summary => {
      const calc = summary.calculation || {};
      const latest = summary.latest_purchase || {};
      return `
        <article class="medicine-summary-row">
          <div class="medicine-summary-name">${escapeHtml(summary.medicine_name || '—')}</div>
          <div class="medicine-kv"><span>Next date</span><strong>${fmtDate(calc.next_purchase_date)}</strong></div>
          <div class="medicine-kv"><span>Runs out</span><strong>${fmtDate(calc.run_out_date)}</strong></div>
          <div class="medicine-kv"><span>Latest purchase</span><strong>${fmtDate(latest.purchase_date)}</strong></div>
          <div class="medicine-kv"><span>Flex</span><strong>${escapeHtml(calc.flex_days ?? '—')} days</strong></div>
        </article>
      `;
    }).join('');
  }

  function renderHistory() {
    const body = byId('medicineHistoryBody');
    const count = byId('medicineHistoryCount');
    if (!(body instanceof HTMLElement)) return;
    if (count instanceof HTMLElement) {
      count.textContent = `${state.purchases.length} rows`;
    }

    if (state.purchases.length === 0) {
      body.innerHTML = `
        <tr>
          <td colspan="7" class="medicine-empty">No purchase history.</td>
        </tr>
      `;
      return;
    }

    body.innerHTML = state.purchases.map(purchase => {
      const calc = purchase.calculation || {};
      return `
        <tr>
          <td class="medicine-row-name" data-label="Medicine">${escapeHtml(purchase.medicine_name || '—')}</td>
          <td data-label="Purchase">${fmtDate(purchase.purchase_date)}</td>
          <td data-label="Pieces">${escapeHtml(purchase.pieces_bought ?? '—')}</td>
          <td data-label="Dose">${escapeHtml(purchase.dose_per_dosing_day ?? '—')}</td>
          <td data-label="Days">${escapeHtml(fmtWeekdays(purchase.dosing_weekdays))}</td>
          <td data-label="Next date">
            ${fmtDate(calc.next_purchase_date)}
            <span class="medicine-muted">(${fmtTreatmentDays(calc.treatment_days)} days)</span>
          </td>
          <td data-label="Actions">
            <div class="medicine-row-actions">
              <button class="medicine-btn medicine-btn-secondary" type="button" data-action="edit" data-id="${escapeHtml(purchase.id)}">Edit</button>
              <button class="medicine-btn medicine-danger" type="button" data-action="delete" data-id="${escapeHtml(purchase.id)}">Delete</button>
            </div>
          </td>
        </tr>
      `;
    }).join('');
  }

  function renderAll(selectedKey = ADD_NEW_VALUE) {
    renderMedicineSelect(selectedKey);
    renderSummaries();
    renderHistory();
  }

  function getSelectedMedicineName() {
    const select = byId('medicineSelect');
    const newInput = byId('newMedicineName');
    if (!(select instanceof HTMLSelectElement)) return '';
    if (select.value === ADD_NEW_VALUE) {
      return newInput instanceof HTMLInputElement ? newInput.value.trim() : '';
    }
    const match = state.medicines.find(item => item.medicine_key === select.value);
    return match ? String(match.medicine_name || '').trim() : '';
  }

  function payloadFromForm() {
    const purchaseDateInput = byId('purchaseDateInput');
    const piecesInput = byId('piecesBoughtInput');
    const doseInput = byId('doseInput');
    const weekdays = selectedWeekdays();
    if (weekdays.length === 0) {
      throw new Error('Select at least one dosing day.');
    }
    return {
      medicine_name: getSelectedMedicineName(),
      purchase_date: purchaseDateInput instanceof HTMLInputElement ? purchaseDateInput.value : '',
      pieces_bought: piecesInput instanceof HTMLInputElement ? Number(piecesInput.value) : 0,
      dose_per_dosing_day: doseInput instanceof HTMLInputElement ? Number(doseInput.value) : 0,
      dosing_weekdays: weekdays,
    };
  }

  function resetForm() {
    editingId = null;
    const form = byId('medicineForm');
    const title = byId('medicineFormTitle');
    const mode = byId('medicineFormMode');
    const cancel = byId('btnCancelEdit');
    if (form instanceof HTMLFormElement) form.reset();
    if (title instanceof HTMLElement) title.textContent = 'Add purchase';
    if (mode instanceof HTMLElement) mode.textContent = 'New snapshot';
    if (cancel instanceof HTMLButtonElement) cancel.style.display = 'none';
    setDefaultPurchaseDate();
    setWeekdays([0, 1, 2, 3, 4]);
    renderMedicineSelect(ADD_NEW_VALUE);
  }

  function startEdit(purchaseId) {
    const purchase = state.purchases.find(item => Number(item.id) === Number(purchaseId));
    if (!purchase) return;
    editingId = Number(purchase.id);

    const title = byId('medicineFormTitle');
    const mode = byId('medicineFormMode');
    const cancel = byId('btnCancelEdit');
    const purchaseDateInput = byId('purchaseDateInput');
    const piecesInput = byId('piecesBoughtInput');
    const doseInput = byId('doseInput');
    if (title instanceof HTMLElement) title.textContent = 'Edit purchase';
    if (mode instanceof HTMLElement) mode.textContent = `Editing #${purchase.id}`;
    if (cancel instanceof HTMLButtonElement) cancel.style.display = 'inline-flex';

    renderMedicineSelect(purchase.medicine_key || ADD_NEW_VALUE);
    if (purchaseDateInput instanceof HTMLInputElement) purchaseDateInput.value = purchase.purchase_date || '';
    if (piecesInput instanceof HTMLInputElement) piecesInput.value = String(purchase.pieces_bought || '');
    if (doseInput instanceof HTMLInputElement) doseInput.value = String(purchase.dose_per_dosing_day || '');
    setWeekdays(purchase.dosing_weekdays || []);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  async function deletePurchase(purchaseId) {
    if (!window.confirm('Delete this medicine purchase?')) return;
    const payload = await apiRequest(`${API_URL}/${purchaseId}`, { method: 'DELETE' });
    setStateFromPayload(payload);
    renderAll();
    showMessage('Purchase deleted.');
  }

  async function saveForm(event) {
    event.preventDefault();
    try {
      const payload = payloadFromForm();
      const wasEditing = editingId !== null;
      const url = editingId === null ? API_URL : `${API_URL}/${editingId}`;
      const method = editingId === null ? 'POST' : 'PATCH';
      const result = await apiRequest(url, {
        method,
        body: JSON.stringify(payload),
      });
      setStateFromPayload(result);
      resetForm();
      showMessage(wasEditing ? 'Purchase updated.' : 'Purchase saved.');
      renderAll();
    } catch (error) {
      console.error('Medicine purchase save failed:', error);
      showMessage(error.message || 'Failed to save purchase.', 'error');
    }
  }

  async function loadPurchases() {
    try {
      showMessage('Loading purchases...');
      const payload = await apiRequest(API_URL);
      setStateFromPayload(payload);
      renderAll();
      showMessage('');
    } catch (error) {
      console.error('Medicine purchases load failed:', error);
      showMessage(error.message || 'Failed to load purchases.', 'error');
    }
  }

  function setDefaultPurchaseDate() {
    const input = byId('purchaseDateInput');
    if (!(input instanceof HTMLInputElement) || input.value) return;
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    input.value = `${yyyy}-${mm}-${dd}`;
  }

  function bindEvents() {
    const form = byId('medicineForm');
    const cancel = byId('btnCancelEdit');
    const select = byId('medicineSelect');
    const body = byId('medicineHistoryBody');

    if (form instanceof HTMLFormElement) {
      form.addEventListener('submit', saveForm);
    }
    if (cancel instanceof HTMLButtonElement) {
      cancel.addEventListener('click', () => {
        resetForm();
        showMessage('');
      });
    }
    if (select instanceof HTMLSelectElement) {
      select.addEventListener('change', () => {
        updateMedicineNameMode();
        applyDefaultsForSelectedMedicine();
      });
    }
    if (body instanceof HTMLElement) {
      body.addEventListener('click', event => {
        const target = event.target;
        if (!(target instanceof HTMLButtonElement)) return;
        const action = target.dataset.action;
        const id = Number(target.dataset.id);
        if (!Number.isFinite(id)) return;
        if (action === 'edit') startEdit(id);
        if (action === 'delete') {
          deletePurchase(id).catch(error => {
            console.error('Medicine purchase delete failed:', error);
            showMessage(error.message || 'Failed to delete purchase.', 'error');
          });
        }
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    resetForm();
    loadPurchases();
  });
})();
