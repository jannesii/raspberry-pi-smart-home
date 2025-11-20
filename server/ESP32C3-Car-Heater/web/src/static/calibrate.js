(() => {
  const els = {
    currentK: document.getElementById('currentKVal'),
    idealSec: document.getElementById('idealSecVal'),
    resultBox: document.getElementById('resultBox'),
    resultText: document.getElementById('resultText'),
    statusText: document.getElementById('statusText'),
    applyInput: document.getElementById('applyK'),
    form: document.getElementById('kfactor-form'),
    ambient: document.getElementById('ambient'),
    target: document.getElementById('target'),
    warmupMin: document.getElementById('warmupMin'),
    applyBtn: document.getElementById('apply-btn')
  };

  function setResult(text, ok = true) {
    els.resultText.textContent = text;
    els.resultBox.classList.toggle('error', !ok);
  }

  function setStatus(text) {
    els.statusText.textContent = text || '';
  }

  async function fetchStatus() {
    try {
      const res = await fetch('/api/kfactor/status');
      const data = await res.json();
      els.currentK.textContent = data.current_k?.toFixed(2) ?? '–';
      els.idealSec.textContent = data.ideal_seconds_per_deg?.toFixed(2) ?? '–';
    } catch (err) {
      setResult('Failed to load current kFactor', false);
    }
  }

  async function suggest(event) {
    event.preventDefault();
    const ambient = parseFloat(els.ambient.value);
    const target = parseFloat(els.target.value);
    const warmupMin = parseFloat(els.warmupMin.value);

    if (Number.isNaN(ambient) || Number.isNaN(target) || Number.isNaN(warmupMin)) {
      setResult('Please fill all numbers', false);
      return;
    }

    const body = new URLSearchParams();
    body.set('ambient', ambient);
    body.set('target', target);
    body.set('warmup_min', warmupMin);

    try {
      const res = await fetch('/api/kfactor/suggest', {
        method: 'POST',
        body
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'Suggestion failed');
      }
      setResult(
        `Suggested kFactor: ${data.suggested_k.toFixed(2)} (observed ${data.warmup_seconds.toFixed(0)}s over Δ${data.delta_t_c.toFixed(1)}°C)`,
        true
      );
      els.applyInput.value = data.suggested_k.toFixed(2);
    } catch (err) {
      console.error(err);
      setResult(err.message, false);
    }
  }

  async function applyK() {
    const k = parseFloat(els.applyInput.value);
    if (Number.isNaN(k) || k <= 0) {
      setStatus('Enter a valid kFactor first.');
      return;
    }
    setStatus('Saving...');
    try {
      const res = await fetch('/api/kfactor/apply', {
        method: 'POST',
        body: new URLSearchParams({ k })
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'Save failed');
      }
      setStatus(`Saved kFactor = ${k.toFixed(2)}`);
      els.currentK.textContent = k.toFixed(2);
    } catch (err) {
      console.error(err);
      setStatus(err.message);
    }
  }

  els.form.addEventListener('submit', suggest);
  els.applyBtn.addEventListener('click', applyK);
  fetchStatus();
})();
