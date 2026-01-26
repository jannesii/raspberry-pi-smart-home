// users.js - Format expiry dates in user grid
function formatDateTime(isoString) {
  if (!isoString) return '-';
  // Remove trailing Z if present
  isoString = isoString.replace('Z', '');
  // Replace T with space
  isoString = isoString.replace('T', ' ');
  const dt = new Date(isoString);
  if (isNaN(dt.getTime())) return isoString;
  const pad = n => n < 10 ? '0' + n : n;
  return `${pad(dt.getDate())}.${pad(dt.getMonth() + 1)}.${dt.getFullYear()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
}

document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.expiry-date').forEach(function(el) {
    const raw = el.dataset.raw;
    el.textContent = formatDateTime(raw);
    if (raw) {
      const dt = new Date(raw.replace('Z', '').replace('T', ' '));
      if (!isNaN(dt.getTime()) && dt < new Date()) {
        el.classList.add('expired');
      }
    }
  });

  document.querySelectorAll('.expiry-date').forEach(function(dateElem) {
    dateElem.addEventListener('click', function() {
      const userId = dateElem.getAttribute('data-user-id');
      const form = document.querySelector('.extend-expiry-form[data-user-id="' + userId + '"]');
      if (form) {
        form.style.display = (form.style.display === 'none' || !form.style.display) ? 'inline-block' : 'none';
      }
    });
  });

  // Add confirmation to delete buttons
  document.querySelectorAll('.delete-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      if (!confirm('Haluatko varmasti poistaa tämän käyttäjän?')) {
        e.preventDefault();
      }
    });
  });
});
