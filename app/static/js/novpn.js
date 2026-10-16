// No JS required — form submission handles CSRF and saving.
document.addEventListener('DOMContentLoaded', () => {
  const list = document.querySelector('.nv-device-list');
  if (!list) return;

  function loadFavs() {
    try { return JSON.parse(localStorage.getItem('novpnFavorites') || '{}'); } catch { return {}; }
  }
  function saveFavs(f) { try { localStorage.setItem('novpnFavorites', JSON.stringify(f)); } catch {} }

  function isFav(mac) {
    const favs = loadFavs();
    return !!favs[mac.toLowerCase()];
  }
  function setFav(mac, val) {
    const favs = loadFavs();
    if (val) favs[mac.toLowerCase()] = true; else delete favs[mac.toLowerCase()];
    saveFavs(favs);
  }

  function updateStarEl(star, fav) {
    if (!star) return;
    if (fav) star.classList.add('fav'); else star.classList.remove('fav');
  }

  function rowKey(row) {
    const name = (row.getAttribute('data-name') || '').toLowerCase();
    const mac = (row.getAttribute('data-mac') || '').toLowerCase();
    return { name, mac };
  }

  function sortRows() {
    const favs = loadFavs();
    const rows = Array.from(list.querySelectorAll('.nv-device-row'));
    rows.sort((a, b) => {
      const ma = (a.getAttribute('data-mac') || '').toLowerCase();
      const mb = (b.getAttribute('data-mac') || '').toLowerCase();
      const fa = !!favs[ma];
      const fb = !!favs[mb];
      if (fa !== fb) return fb ? 1 : -1; // favs first
      const ka = rowKey(a);
      const kb = rowKey(b);
      if (ka.name && kb.name) return ka.name.localeCompare(kb.name);
      return ka.mac.localeCompare(kb.mac);
    });
    rows.forEach(r => list.appendChild(r));
  }

  // Initialize stars and handlers
  const stars = list.querySelectorAll('.nv-fav-star');
  stars.forEach(star => {
    const mac = star.getAttribute('data-mac') || '';
    updateStarEl(star, isFav(mac));
    star.addEventListener('click', (e) => {
      e.preventDefault();
      const current = isFav(mac);
      setFav(mac, !current);
      updateStarEl(star, !current);
      sortRows();
    });
    star.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        star.click();
      }
    });
  });

  // Initial alphabetical sort with favorites on top
  sortRows();

  // --- Modal logic for add/edit ---
  const modal = document.getElementById('nv-modal');
  const modalForm = document.getElementById('nv-modal-form');
  const modalTitle = document.getElementById('nv-modal-title');
  const actionInput = document.getElementById('modal_action');
  const origMacInput = document.getElementById('edit_original_mac');
  const nameInput = document.getElementById('modal_name');
  const macInput = document.getElementById('modal_mac');
  const btnCancel = document.getElementById('nv-cancel');
  const btnOpenAdd = document.getElementById('open-add-modal');
  const btnDelete = document.getElementById('nv-delete');

  function openModalAdd() {
    modalTitle.textContent = 'Add Device';
    actionInput.setAttribute('name', 'add_device');
    origMacInput.value = '';
    nameInput.value = '';
    macInput.value = '';
    if (btnDelete) btnDelete.style.display = 'none';
    modal.classList.add('open');
    setTimeout(() => nameInput.focus(), 0);
  }

  function openModalEdit(name, mac, isExisting) {
    modalTitle.textContent = 'Edit Device';
    if (isExisting) {
      actionInput.setAttribute('name', 'edit_device');
      origMacInput.value = mac;
      if (btnDelete) btnDelete.style.display = '';
    } else {
      actionInput.setAttribute('name', 'add_device');
      origMacInput.value = '';
      if (btnDelete) btnDelete.style.display = 'none';
    }
    nameInput.value = name || '';
    macInput.value = mac || '';
    modal.classList.add('open');
    setTimeout(() => nameInput.focus(), 0);
  }

  function closeModal() {
    modal.classList.remove('open');
  }

  if (btnOpenAdd) {
    btnOpenAdd.addEventListener('click', () => openModalAdd());
  }
  if (btnCancel) {
    btnCancel.addEventListener('click', () => closeModal());
  }
  if (btnDelete) {
    btnDelete.addEventListener('click', () => {
      const mac = (origMacInput.value || macInput.value || '').trim();
      if (!mac) return closeModal();
      if (!confirm('Delete this device?')) return;
      actionInput.setAttribute('name', 'delete_device');
      modalForm.submit();
    });
  }
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
    });
  }

  // Click on device name to edit
  list.querySelectorAll('.nv-device-row .nv-device-name').forEach(el => {
    el.addEventListener('click', (e) => {
      const row = el.closest('.nv-device-row');
      const name = row?.getAttribute('data-name') || '';
      const mac = row?.getAttribute('data-mac') || '';
      const isExisting = (row?.getAttribute('data-existing') || '1') === '1';
      openModalEdit(name, mac, isExisting);
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        el.click();
      }
    });
  });

  // --- Inline toggle save (Ohita VPN/DNS) ---
  async function updateNovpn(mac, novpn, nodns, name) {
    try {
      const res = await fetch('/api/novpn/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ mac, novpn, nodns, name }),
        credentials: 'same-origin'
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data.ok) throw new Error(data.message || 'Update failed');
      return true;
    } catch (e) {
      console.error('novpn update failed:', e);
      return false;
    }
  }

  list.querySelectorAll('.nv-device-row').forEach(row => {
    const mac = (row.getAttribute('data-mac') || '').toLowerCase();
    const name = row.getAttribute('data-name') || '';
    const novpnCb = row.querySelector('input[name="novpn_' + mac + '"]');
    const nodnsCb = row.querySelector('input[name="nodns_' + mac + '"]');
    if (!novpnCb || !nodnsCb) return;

    function setDisabled(disabled) {
      novpnCb.disabled = !!disabled; nodnsCb.disabled = !!disabled;
    }

    novpnCb.addEventListener('change', async () => {
      const prev = !novpnCb.checked;
      setDisabled(true);
      const ok = await updateNovpn(mac, novpnCb.checked, nodnsCb.checked, name);
      if (!ok) { novpnCb.checked = prev; }
      setDisabled(false);
    });

    nodnsCb.addEventListener('change', async () => {
      const prev = !nodnsCb.checked;
      setDisabled(true);
      const ok = await updateNovpn(mac, novpnCb.checked, nodnsCb.checked, name);
      if (!ok) { nodnsCb.checked = prev; }
      setDisabled(false);
    });

    // 15-minute temporary VPN bypass
    const btn15 = row.querySelector('.nv-15min');
    if (btn15) {
      btn15.addEventListener('click', async () => {
        try {
          btn15.disabled = true; setDisabled(true);
          const res = await fetch('/api/novpn/temp_bypass', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ mac, minutes: 1, name }),
            credentials: 'same-origin'
          });
          if (!res.ok) throw new Error('HTTP ' + res.status);
          const data = await res.json();
          if (!data.ok) throw new Error(data.message || 'Failed to start temporary bypass');
          // Reflect immediate change in UI without waiting for reload
          novpnCb.checked = true;
          const isExisting = (row.getAttribute('data-existing') || '1') === '1';
          if (!isExisting) {
            // Ephemeral add uses nodns=false — reflect in UI
            nodnsCb.checked = false;
            row.setAttribute('data-existing', '1');
          }
          if (typeof showFlash === 'function') showFlash('success', 'VPN bypass enabled for 15 minutes.');
        } catch (e) {
          console.error(e);
          if (typeof showFlash === 'function') showFlash('error', 'Failed to enable temporary VPN bypass');
        } finally {
          setDisabled(false); btn15.disabled = false;
        }
      });
    }
  });
});
