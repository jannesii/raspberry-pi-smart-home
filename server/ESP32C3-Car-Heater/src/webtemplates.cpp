#include "webtemplates.h"
#include "timekeeper.h"   // if you need it somewhere else

// Main page HTML template (Status + Config)
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Car Heater</title>
    <style>
      :root { --maxw: 520px; }
      body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, sans-serif; background: #111; color: #eee; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }

      .nav {
        background: #181818;
        border-bottom: 1px solid #333;
      }
      .nav-inner {
        max-width: var(--maxw);
        margin: 0 auto;
        padding: 0.75rem 1rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
      }
      .nav-title {
        font-weight: 600;
        font-size: 1rem;
      }
      .nav-links {
        display: flex;
        gap: 0.5rem;
      }
      .nav-link {
        font-size: 0.9rem;
        color: #ccc;
        text-decoration: none;
        padding: 0.25rem 0.6rem;
        border-radius: 999px;
        border: 1px solid transparent;
      }
      .nav-link:hover {
        color: #fff;
        border-color: #444;
        background: #222;
      }

      .card {
        max-width: var(--maxw);
        margin: 1.25rem auto;
        padding: 1rem;
        border-radius: 0.75rem;
        background: #222;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
      }
      .card + .card {
        margin-top: 0.75rem;
      }
      h1, h2 {
        margin: 0 0 0.75rem 0;
      }
      .muted { color: #aaa; font-size: 0.9rem; }
      button {
        padding: 0.8rem 1rem;
        border-radius: 0.5rem;
        border: none;
        cursor: pointer;
        font: inherit;
        min-height: 44px; /* mobile-friendly target */
        touch-action: manipulation;
      }
      .on { background: #2e7d32; color: white; }
      .off { background: #c62828; color: white; }
      .unknown { background: #b59f00; color: #111; }
      .secondary { background: #444; color: #eee; margin-top: 1rem; }
      .btn-primary { background: #1976d2; color: #fff; }
      .btn-primary:hover { filter: brightness(1.1); }
      hr { margin: 1rem 0; border: none; border-top: 1px solid #333; }

      /* Config section layout */
      .config-header {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 0.5rem;
        margin-bottom: 0.5rem;
      }
      .config-header small {
        color: #888;
        font-size: 0.8rem;
      }

      .config-form {
        margin-top: 0.5rem;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 0.75rem 1rem;
      }

      .config-field {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
        font-size: 0.9rem;
      }

      .config-field label {
        color: #ccc;
      }

      .config-field input[type="number"],
      .config-field input[type="time"] {
        width: 100%;
        padding: 0.6rem 0.6rem;
        border-radius: 0.5rem;
        border: 1px solid #555;
        background: #111;
        color: #eee;
        box-sizing: border-box;
        font: inherit;
        min-height: 44px;
        font-size: 1rem;
      }

      .config-actions {
        margin-top: 0.75rem;
        display: flex;
        justify-content: flex-end;
      }

      /* Make primary actions full-width on small screens */
      form[action="/toggle"] button,
      #syncTimeBtn,
      .config-actions .btn-primary {
        width: 100%;
      }

      @media (max-width: 400px) {
        .nav-title { font-size: 1.05rem; }
        .nav-inner { padding: 0.6rem 0.8rem; }
        .card { margin: 1rem auto; padding: 0.9rem; }
      }
    </style>
  </head>
  <body data-time-synced="%TIME_SYNCED%">
    <nav class="nav">
      <div class="nav-inner">
        <span class="nav-title">Car Heater</span>
        <div class="nav-links">
          <a href="/" class="nav-link">Status</a>
          <a href="/logs" class="nav-link">Logs</a>
        </div>
      </div>
    </nav>

    <div class="card">
      <h1>Status</h1>
      <p class="muted">Wi-Fi SSID: %WIFI_SSID%</p>

      <p>Current temperature: <strong>%TEMP%</strong> °C</p>
      <p>Heater state: <strong>%HEATER_STATE%</strong></p>
      <form action="/toggle" method="POST">
        <button type="submit" class="%HEATER_BTN_CLASS%">%HEATER_BTN_LABEL%</button>
      </form>

      <hr>

      <p>Current time: <strong>%CURRENT_TIME%</strong></p>
      <button type="button" id="syncTimeBtn" class="secondary">
        Sync time from this device
      </button>
    </div>

    <div class="card">
      <div class="config-header">
        <h2>Config</h2>
        <small>Adjust control logic</small>
      </div>

      <form action="/set-config" method="POST">
        <div class="config-form">
          <div class="config-field">
            <label for="target">Target (°C)</label>
            <input type="number" inputmode="decimal" step="0.1" id="target" name="target" value="%TARGET_TEMP%">
          </div>

          <div class="config-field">
            <label for="hyst">Hysteresis (°C)</label>
            <input type="number" inputmode="decimal" step="0.1" id="hyst" name="hyst" value="%HYST%">
          </div>

          <div class="config-field">
            <label for="taskdelay">Heater Task Delay (s)</label>
            <input type="number" inputmode="decimal" step="0.1" id="taskdelay" name="taskdelay" value="%TASK_DELAY%">
          </div>
        </div>
        <hr>
        <div class="config-form">
          <div class="config-field">
            <label for="dzs">Deadzone Start</label>
            <input type="time" id="dzs" name="dzstart" value="%DZ_START%">
          </div>
          <div class="config-field">
            <label for="dze">Deadzone End</label>
            <input type="time" id="dze" name="dzend" value="%DZ_END%">
          </div>
        </div>

        <div class="config-actions">
          <button type="submit" class="btn-primary">Save</button>
        </div>
      </form>
    </div>

    <script>
      async function syncTime() {
        try {
          const now = new Date();
          const epochSeconds = Math.floor(now.getTime() / 1000);
          const tzOffsetMin = -now.getTimezoneOffset(); // east-positive

          const params = new URLSearchParams();
          params.set("epoch", epochSeconds.toString());
          params.set("tz", tzOffsetMin.toString());

          const resp = await fetch("/sync-time", {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded"
            },
            body: params.toString()
          });

          if (!resp.ok) {
            alert("Failed to sync time (" + resp.status + ")");
            return;
          }

          location.reload();
        } catch (e) {
          console.error(e);
          alert("Failed to sync time");
        }
      }

      document.addEventListener("DOMContentLoaded", function () {
        const btn = document.getElementById("syncTimeBtn");
        if (btn) {
          // Manual sync (with alerts)
          btn.addEventListener("click", () => syncTime(true));
        }

        // Read server-side flag from data attribute
        const flag = document.body.dataset.timeSynced; // "0" or "1"
        const timeSynced = (flag === "1");

        // Auto-sync only if server says time is not valid yet
        if (!timeSynced) {
          // No alerts on auto-fail
          syncTime(false);
        }
      });
    </script>
  </body>
</html>
)rawliteral";


// Logs page HTML template
const char LOGS_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Car Heater – Logs</title>
    <style>
      :root { --maxw: 520px; }
      body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, sans-serif; background: #111; color: #eee; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }

      .nav {
        background: #181818;
        border-bottom: 1px solid #333;
      }
      .nav-inner {
        max-width: var(--maxw);
        margin: 0 auto;
        padding: 0.75rem 1rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
      }
      .nav-title {
        font-weight: 600;
        font-size: 1rem;
      }
      .nav-links {
        display: flex;
        gap: 0.5rem;
      }
      .nav-link {
        font-size: 0.9rem;
        color: #ccc;
        text-decoration: none;
        padding: 0.25rem 0.6rem;
        border-radius: 999px;
        border: 1px solid transparent;
      }
      .nav-link:hover {
        color: #fff;
        border-color: #444;
        background: #222;
      }

      .card {
        max-width: var(--maxw);
        margin: 1.25rem auto;
        padding: 1rem;
        border-radius: 0.75rem;
        background: #222;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
      }

      h1 {
        margin: 0 0 0.75rem 0;
      }
      .muted { color: #aaa; font-size: 0.9rem; }

      .logs {
        margin-top: 0.75rem;
        padding: 0.75rem;
        background: #111;
        border-radius: 0.5rem;
        font-family: "Fira Code", monospace;
        font-size: 0.9rem;
        max-height: 60vh;
        overflow-y: auto;
        border: 1px solid #333;
        white-space: pre;  /* preserve line breaks, no wrapping */
      }

      .logs-actions {
        margin-top: 0.75rem;
        display: flex;
        justify-content: flex-end;
      }

      .btn-danger {
        padding: 0.8rem 1rem;
        border-radius: 0.5rem;
        border: none;
        cursor: pointer;
        font: inherit;
        background: #c62828;
        color: #fff;
        min-height: 44px;
        touch-action: manipulation;
        width: 100%;
      }
      .btn-danger:hover {
        filter: brightness(1.1);
      }
    </style>
  </head>
  <body>
    <nav class="nav">
      <div class="nav-inner">
        <span class="nav-title">Car Heater</span>
        <div class="nav-links">
          <a href="/" class="nav-link">Status</a>
          <a href="/logs" class="nav-link">Logs</a>
        </div>
      </div>
    </nav>

    <div class="card">
      <h1>Logs</h1>
      <p class="muted">Newest entries first. Timestamp is local device time.</p>

      <div class="logs">%LOG_LINES%</div>

      <form class="logs-actions" action="/logs/clear" method="POST">
        <button type="submit" class="btn-danger">Clear logs</button>
      </form>
    </div>
  </body>
</html>
)rawliteral";
