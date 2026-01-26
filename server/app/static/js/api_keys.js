function copyCreatedKey() {
  try {
    const el = document.getElementById('createdKeyValue');
    if (!el) return;
    el.select();
    el.setSelectionRange(0, 99999);
    document.execCommand('copy');
    // Fallback to modern API if available
    if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(el.value).catch(() => {});
    }
    // Lightweight inline feedback via alert; project uses flash messages elsewhere
    alert('API-avain kopioitu leikepöydälle.');
  } catch (e) {
    console.error('Copy failed', e);
  }
}
