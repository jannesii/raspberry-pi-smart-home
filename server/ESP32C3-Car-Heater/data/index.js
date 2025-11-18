let wsStatus = null;

async function loadStatus() {
  try {
    const resp = await fetch("/api/status");
    if (!resp.ok) {
      throw new Error("HTTP " + resp.status);
    }
    const data = await resp.json();

    // Text fields
    document.getElementById("wifiSsid").textContent    = data.wifi_ssid || "";
    document.getElementById("currentTemp").textContent = data.temp.toFixed(1);
    document.getElementById("heaterState").textContent = data.heater_state || "";
    document.getElementById("currentTime").textContent = data.current_time || "";

    // Heater button
    const btn = document.getElementById("heaterBtn");
    btn.textContent = data.heater_btn_label || "Toggle";
    btn.className   = data.heater_btn_class || "";

    // Config inputs
    document.getElementById("target").value    = data.target_temp.toFixed(1);
    document.getElementById("hyst").value      = data.hyst.toFixed(1);
    document.getElementById("taskdelay").value = data.task_delay.toFixed(1);
    document.getElementById("dzs").value       = data.dz_start || "";
    document.getElementById("dze").value       = data.dz_end || "";

  } catch (err) {
    console.error("Failed to load status:", err);
    // optional: show some error text in the UI
  }
}

async function syncTimeFromDevice() {
  try {
    const now = new Date();
    const epochSeconds = Math.floor(now.getTime() / 1000);
    const tzOffsetMin  = -now.getTimezoneOffset(); // east-positive

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

    if (!resp.ok && resp.status !== 302) {
      // redirect will be followed by browser if this were a form;
      // via fetch we just ignore it
      console.warn("Time sync returned HTTP", resp.status);
    }

    // Reload status after syncing time
    await loadStatus();
  } catch (e) {
    console.error("Failed to sync time:", e);
  }
}

function setupStatusWebSocket() {
  const protocol = (location.protocol === "https:") ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${location.host}/ws`;

  function connect() {
    wsStatus = new WebSocket(wsUrl);

    wsStatus.addEventListener("open", () => {
      console.log("[WS] Status connected");
    });

    wsStatus.addEventListener("close", () => {
      console.log("[WS] Status disconnected, retrying in 3s...");
      setTimeout(connect, 3000);
    });

    wsStatus.addEventListener("error", (err) => {
      console.error("[WS] Status error:", err);
      wsStatus.close();
    });

    wsStatus.addEventListener("message", (event) => {
      // existing temp_update handling here...
      try {
        const data = JSON.parse(event.data);

        if (data.type === "temp_update") {
          // update temp, state, button, time...
          if (typeof data.temp === "number") {
            document.getElementById("currentTemp").textContent =
              data.temp.toFixed(1);
          }
          if (typeof data.is_on === "boolean") {
            const stateEl = document.getElementById("heaterState");
            if (data.in_deadzone) {
              stateEl.textContent = data.is_on ? "ON (deadzone?)" : "OFF (deadzone)";
            } else {
              stateEl.textContent = data.is_on ? "ON" : "OFF";
            }

            const btn = document.getElementById("heaterBtn");
            btn.textContent = data.is_on ? "Turn OFF" : "Turn ON";
            btn.className   = data.is_on ? "off" : "on";
          }
          if (typeof data.current_time === "string") {
            document.getElementById("currentTime").textContent =
              data.current_time;
          }
        }
      } catch (e) {
        console.warn("[WS] Status invalid message:", event.data);
      }
    });
  }

  connect();
}

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();
  setupStatusWebSocket();

  const syncBtn = document.getElementById("syncTimeBtn");
  if (syncBtn) {
    syncBtn.addEventListener("click", () => {
      syncTimeFromDevice();
    });
  }

  const toggleForm = document.getElementById("toggleForm");
  if (toggleForm) {
    toggleForm.addEventListener("submit", (ev) => {
      // If WS is available, use it and prevent full page reload
      if (wsStatus && wsStatus.readyState === WebSocket.OPEN) {
        ev.preventDefault();
        console.log("[UI] Sending toggle_heater over WebSocket");
        wsStatus.send("toggle_heater");
        loadStatus();
      } else {
        // Fallback: let the form submit normally to /toggle
        console.log("[UI] WS not ready, using HTTP /toggle");
      }
    });
  }
});