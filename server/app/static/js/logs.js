/**
 * Logs Page - iOS-Style Interactive Log Viewer
 * Features: Real-time updates, search, filtering, pagination
 */

(function() {
  'use strict';

  // State
  let socket = null;
  let isLive = true;
  let currentFilter = 'all';
  let currentSearch = '';
  let logsData = [];
  let hasMore = true;
  let isLoading = false;
  let newLogsWhilePaused = 0;
  let lastSeenId = null;

  // DOM Elements
  const logsList = document.getElementById('logsList');
  const liveIndicator = document.getElementById('liveIndicator');
  const liveText = document.getElementById('liveText');
  const searchInput = document.getElementById('searchInput');
  const clearSearchBtn = document.getElementById('clearSearch');
  const filterBtns = document.querySelectorAll('.filter-btn');
  const loadMoreBtn = document.getElementById('loadMoreBtn');
  const loadMoreContainer = document.getElementById('loadMoreContainer');
  const scrollTopBtn = document.getElementById('scrollTopBtn');
  const newLogsBanner = document.getElementById('newLogsBanner');
  const totalCountEl = document.getElementById('totalCount');

  // Initialize
  document.addEventListener('DOMContentLoaded', init);

  function init() {
    // Parse initial logs from template
    const initialData = document.getElementById('initialLogsData');
    if (initialData) {
      try {
        const data = JSON.parse(initialData.textContent);
        logsData = data.logs || [];
        hasMore = data.hasMore || false;
        lastSeenId = logsData.length > 0 ? logsData[0].id : null;
      } catch (e) {
        console.error('Failed to parse initial logs:', e);
      }
    }

    renderLogs();
    setupEventListeners();
    initSocket();
    updateLoadMoreVisibility();
  }

  function setupEventListeners() {
    // Live toggle
    liveIndicator?.addEventListener('click', toggleLive);

    // Search
    let searchTimeout;
    searchInput?.addEventListener('input', () => {
      clearSearchBtn?.classList.toggle('visible', searchInput.value.length > 0);
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        currentSearch = searchInput.value.trim();
        refreshLogs();
      }, 300);
    });

    clearSearchBtn?.addEventListener('click', () => {
      searchInput.value = '';
      clearSearchBtn.classList.remove('visible');
      currentSearch = '';
      refreshLogs();
    });

    // Filter buttons
    filterBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        filterBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentFilter = btn.dataset.filter;
        refreshLogs();
      });
    });

    // Load more
    loadMoreBtn?.addEventListener('click', loadMore);

    // Scroll to top
    scrollTopBtn?.addEventListener('click', () => {
      logsList?.scrollTo({ top: 0, behavior: 'smooth' });
    });

    // Scroll detection for scroll-to-top button
    logsList?.addEventListener('scroll', () => {
      const showBtn = logsList.scrollTop > 300;
      scrollTopBtn?.classList.toggle('visible', showBtn);
    });

    // New logs banner click
    newLogsBanner?.addEventListener('click', () => {
      isLive = true;
      updateLiveIndicator();
      newLogsWhilePaused = 0;
      newLogsBanner.classList.remove('visible');
      logsList?.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  function initSocket() {
    if (typeof io === 'undefined') {
      console.warn('Socket.IO not available');
      return;
    }

    socket = io({
      transports: ['websocket', 'polling'],
      reconnectionAttempts: 5,
    });

    socket.on('connect', () => {
      console.log('Logs socket connected');
    });

    socket.on('db_log', (log) => {
      handleNewLog(log);
    });

    socket.on('disconnect', () => {
      console.log('Logs socket disconnected');
    });
  }

  function handleNewLog(log) {
    // Check if log matches current filters
    const matchesFilter = currentFilter === 'all' || log.type === currentFilter;
    const matchesSearch = !currentSearch || 
      log.message.toLowerCase().includes(currentSearch.toLowerCase());

    if (!matchesFilter || !matchesSearch) {
      return;
    }

    // Update total count
    if (totalCountEl) {
      const currentCount = parseInt(totalCountEl.textContent) || 0;
      totalCountEl.textContent = currentCount + 1;
    }

    if (isLive) {
      // Add to beginning of list
      logsData.unshift(log);
      if (lastSeenId === null || log.id > lastSeenId) {
        lastSeenId = log.id;
      }
      
      // Prepend to DOM with animation
      const logEl = createLogElement(log, true);
      if (logsList && logsList.firstChild) {
        logsList.insertBefore(logEl, logsList.firstChild);
      } else if (logsList) {
        logsList.appendChild(logEl);
      }

      // Remove empty state if present
      const emptyState = logsList?.querySelector('.logs-empty');
      if (emptyState) {
        emptyState.remove();
      }

      // Scroll to top if near top
      if (logsList && logsList.scrollTop < 100) {
        logsList.scrollTo({ top: 0, behavior: 'smooth' });
      }
    } else {
      // Track new logs while paused
      newLogsWhilePaused++;
      newLogsBanner.textContent = `${newLogsWhilePaused} new log${newLogsWhilePaused > 1 ? 's' : ''} - click to view`;
      newLogsBanner.classList.add('visible');
    }
  }

  function toggleLive() {
    isLive = !isLive;
    updateLiveIndicator();
    
    if (isLive) {
      // Refresh to get any missed logs
      if (newLogsWhilePaused > 0) {
        refreshLogs();
      }
      newLogsWhilePaused = 0;
      newLogsBanner?.classList.remove('visible');
    }
  }

  function updateLiveIndicator() {
    if (!liveIndicator || !liveText) return;
    
    if (isLive) {
      liveIndicator.classList.remove('paused');
      liveText.textContent = 'Live';
    } else {
      liveIndicator.classList.add('paused');
      liveText.textContent = 'Paused';
    }
  }

  function refreshLogs() {
    logsData = [];
    hasMore = true;
    lastSeenId = null;
    fetchLogs();
  }

  function fetchLogs(beforeId = null) {
    if (isLoading) return;
    isLoading = true;

    if (loadMoreBtn) {
      loadMoreBtn.disabled = true;
      loadMoreBtn.innerHTML = '<span class="loading-spinner"></span>Loading...';
    }

    const params = new URLSearchParams();
    if (currentFilter !== 'all') {
      params.set('type', currentFilter);
    }
    if (currentSearch) {
      params.set('search', currentSearch);
    }
    if (beforeId) {
      params.set('before_id', beforeId);
    }
    params.set('limit', '50');

    fetch(`/api/logs?${params}`)
      .then(res => res.json())
      .then(data => {
        if (beforeId) {
          // Append to existing
          logsData = logsData.concat(data.logs);
          appendLogs(data.logs);
        } else {
          // Replace all
          logsData = data.logs;
          if (logsData.length > 0) {
            lastSeenId = logsData[0].id;
          }
          renderLogs();
        }
        hasMore = data.has_more;
        updateLoadMoreVisibility();
      })
      .catch(err => {
        console.error('Failed to fetch logs:', err);
      })
      .finally(() => {
        isLoading = false;
        if (loadMoreBtn) {
          loadMoreBtn.disabled = false;
          loadMoreBtn.textContent = 'Load more';
        }
      });
  }

  function loadMore() {
    if (!hasMore || isLoading || logsData.length === 0) return;
    const oldestId = logsData[logsData.length - 1].id;
    fetchLogs(oldestId);
  }

  function renderLogs() {
    if (!logsList) return;

    if (logsData.length === 0) {
      logsList.innerHTML = `
        <div class="logs-empty">
          <div class="logs-empty-icon">📋</div>
          <div class="logs-empty-text">No logs found</div>
        </div>
      `;
      return;
    }

    logsList.innerHTML = logsData.map(log => createLogHTML(log)).join('');
  }

  function appendLogs(logs) {
    if (!logsList) return;
    
    // Remove empty state if present
    const emptyState = logsList.querySelector('.logs-empty');
    if (emptyState) {
      emptyState.remove();
    }

    logs.forEach(log => {
      logsList.appendChild(createLogElement(log, false));
    });
  }

  function createLogElement(log, isNew = false) {
    const div = document.createElement('div');
    div.className = `log-entry${isNew ? ' new' : ''}`;
    div.dataset.id = log.id;
    div.innerHTML = createLogInnerHTML(log);
    return div;
  }

  function createLogHTML(log) {
    return `<div class="log-entry" data-id="${log.id}">${createLogInnerHTML(log)}</div>`;
  }

  function createLogInnerHTML(log) {
    const relativeTime = formatRelativeTime(log.timestamp);
    const absoluteTime = formatAbsoluteTime(log.timestamp);
    const typeIcon = getTypeIcon(log.type);
    
    return `
      <div class="log-entry-header">
        <div class="log-entry-left">
          <span class="log-badge ${log.type}">${typeIcon} ${formatType(log.type)}</span>
          <span class="log-timestamp" title="${absoluteTime}">
            <span class="log-timestamp-relative">${relativeTime}</span>
            <span class="log-timestamp-absolute">${absoluteTime}</span>
          </span>
        </div>
      </div>
      <div class="log-message">${escapeHtml(log.message)}</div>
    `;
  }

  function formatType(type) {
    const typeMap = {
      'info': 'Info',
      'warning': 'Warning',
      'error': 'Error',
      'auth': 'Auth',
      'ac': 'AC',
      'car_heater': 'Heater',
    };
    return typeMap[type] || type;
  }

  function getTypeIcon(type) {
    const iconMap = {
      'info': 'ℹ️',
      'warning': '⚠️',
      'error': '❌',
      'auth': '🔐',
      'ac': '❄️',
      'car_heater': '🔥',
    };
    return iconMap[type] || '📝';
  }

  function formatRelativeTime(isoString) {
    try {
      const date = new Date(isoString);
      const now = new Date();
      const diffMs = now - date;
      const diffSec = Math.floor(diffMs / 1000);
      const diffMin = Math.floor(diffSec / 60);
      const diffHour = Math.floor(diffMin / 60);
      const diffDay = Math.floor(diffHour / 24);

      if (diffSec < 60) return 'just now';
      if (diffMin < 60) return `${diffMin} min ago`;
      if (diffHour < 24) return `${diffHour} hr ago`;
      if (diffDay < 7) return `${diffDay} day${diffDay > 1 ? 's' : ''} ago`;
      
      return date.toLocaleDateString('fi-FI');
    } catch {
      return isoString;
    }
  }

  function formatAbsoluteTime(isoString) {
    try {
      const date = new Date(isoString);
      return date.toLocaleString('fi-FI', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch {
      return isoString;
    }
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function updateLoadMoreVisibility() {
    if (!loadMoreContainer) return;
    loadMoreContainer.style.display = hasMore ? 'block' : 'none';
  }

  // Update relative times periodically
  setInterval(() => {
    document.querySelectorAll('.log-timestamp').forEach(el => {
      const entry = el.closest('.log-entry');
      if (!entry) return;
      const id = entry.dataset.id;
      const log = logsData.find(l => String(l.id) === id);
      if (log) {
        const relSpan = el.querySelector('.log-timestamp-relative');
        if (relSpan) {
          relSpan.textContent = formatRelativeTime(log.timestamp);
        }
      }
    });
  }, 60000); // Every minute

})();
