/* ─────────────────────────────── flash helpers ────────────────────────── */
function ensureFixedFlashContainer (container) {
  if (!container) return;
  const computed = window.getComputedStyle(container);
  if (computed.position === 'fixed') return;

  // Fallback inline styles when page-specific CSS overrides or misses flash.css
  container.style.position = 'fixed';
  container.style.top = '12px';
  container.style.left = '50%';
  container.style.transform = 'translateX(-50%)';
  container.style.width = 'min(800px, calc(100% - 32px))';
  container.style.zIndex = '2000';
  container.style.margin = '0';
  container.style.pointerEvents = 'none';
}

function getOrCreateFlashContainer () {
  // Always look for container as direct child of body for proper fixed positioning
  let container = document.body.querySelector(':scope > .flash-container');
  if (container) {
    ensureFixedFlashContainer(container);
    return container;
  }

  // Check if there's a container elsewhere (e.g., server-rendered inside content)
  // and move it to body if found
  const existingContainer = document.querySelector('.flash-container');
  if (existingContainer) {
    document.body.appendChild(existingContainer);
    ensureFixedFlashContainer(existingContainer);
    return existingContainer;
  }

  // Create new container and append directly to body
  container = document.createElement('div');
  container.className = 'flash-container';
  document.body.appendChild(container);
  ensureFixedFlashContainer(container);
  return container;
}
function clearFlash (flash, container) {
  flash.remove();
  if (container.children.length === 0) {
    container.remove();
  }
}


function showFlash (category = 'info', message = '') {
  const container = getOrCreateFlashContainer();

  const flash = document.createElement('div');
  flash.className = `flash ${category}`;    // e.g. "flash success"
  flash.textContent = message;
  container.appendChild(flash);

  // auto‑dismiss after 3 s (same as server‑rendered flashes)
  setTimeout(() => clearFlash(flash, container), 3000);
}

/* ───────────────────── Socket.IO listener ───────────────────────────────
   Backend should emit something like:
   socketio.emit('flash', {'category': 'success', 'message': 'Print paused'})
*/
window.socket = window.socket || io('/', { transports: ['websocket'], auth: { role: 'view' } });  // make one if it doesn’t exist
window.addEventListener('beforeunload', () => window.socket && window.socket.disconnect());

window.socket.on('flash', ({ category, message }) => {
  console.log('💬 flash:', category, message);
  showFlash(category, message);
});

// Normalize any server-rendered flash container after the DOM is ready.
const initialFlashContainer = document.querySelector('.flash-container');
if (initialFlashContainer) {
  if (initialFlashContainer.parentElement !== document.body) {
    document.body.appendChild(initialFlashContainer);
  }
  ensureFixedFlashContainer(initialFlashContainer);
}
