/**
 * Car Heater Settings - Base Module
 * Shared utilities and modal management
 */

console.log('⚙️ car_heater_settings_base.js loaded');

// ============================================
// Utility Functions
// ============================================

function settingsFmtTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleString('fi-FI', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  } catch {
    return '—';
  }
}

function settingsFmtTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('fi-FI', {
      hour: '2-digit', minute: '2-digit'
    });
  } catch {
    return '—';
  }
}

function settingsFmtNum(v, decimals = 1) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(decimals);
}

// ============================================
// Toast Notifications
// ============================================

function showToast(message, type = 'success') {
  // Use existing flash system if available
  if (window.showFlash && typeof window.showFlash === 'function') {
    window.showFlash(message, type);
    return;
  }

  // Simple fallback toast
  const toast = document.createElement('div');
  toast.className = `ready-by-toast toast-${type}`;
  toast.textContent = message;
  toast.style.cssText = `
    position: fixed;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%);
    background: ${type === 'success' ? '#30d158' : type === 'error' ? '#ff453a' : '#0a84ff'};
    color: white;
    padding: 12px 24px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 500;
    z-index: 10001;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    animation: toastIn 0.3s ease-out;
  `;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.animation = 'toastOut 0.3s ease-in forwards';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// Add toast animations to head
(function initToastStyles() {
  const toastStyles = document.createElement('style');
  toastStyles.textContent = `
    @keyframes toastIn { from { opacity: 0; transform: translateX(-50%) translateY(20px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
    @keyframes toastOut { from { opacity: 1; transform: translateX(-50%) translateY(0); } to { opacity: 0; transform: translateX(-50%) translateY(20px); } }
  `;
  document.head.appendChild(toastStyles);
})();

// ============================================
// Settings Modal Management
// ============================================

function openSettingsModal() {
  const modal = document.getElementById('carHeaterSettingsModal');
  if (modal) {
    modal.classList.add('is-open');
    document.body.style.overflow = 'hidden';

    // Request latest status via socket
    if (window.socket) {
      window.socket.emit('kfactor_control', { action: 'status' });
    }
  }
}

function closeSettingsModal() {
  const modal = document.getElementById('carHeaterSettingsModal');
  if (modal) {
    modal.classList.remove('is-open');
    document.body.style.overflow = '';
  }
}

// ============================================
// Modal Event Listeners Initialization
// ============================================

document.addEventListener('DOMContentLoaded', () => {
  // Settings modal buttons
  const openSettingsBtn = document.getElementById('btnOpenReadyBySettings');
  const closeSettingsBtn = document.getElementById('carHeaterSettingsClose');
  const settingsModal = document.getElementById('carHeaterSettingsModal');

  if (openSettingsBtn) {
    openSettingsBtn.addEventListener('click', openSettingsModal);
  }

  if (closeSettingsBtn) {
    closeSettingsBtn.addEventListener('click', closeSettingsModal);
  }

  // Close modal on backdrop click
  if (settingsModal) {
    settingsModal.addEventListener('click', (e) => {
      if (e.target === settingsModal) {
        closeSettingsModal();
      }
    });
  }

  // Close modal on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeSettingsModal();
    }
  });
});

// Export utilities for other modules
window.CarHeaterSettings = window.CarHeaterSettings || {};
Object.assign(window.CarHeaterSettings, {
  fmtTs: settingsFmtTs,
  fmtTime: settingsFmtTime,
  fmtNum: settingsFmtNum,
  showToast,
  openSettingsModal,
  closeSettingsModal,
});
