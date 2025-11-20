let wsStatus = null;

async function handleStatusData(data) {
  document.getElementById("currentTemp").textContent = data.temp.toFixed(1);
  let heater_on = data.is_on;
  document.getElementById("heaterState").textContent = heater_on ? "ON" : "OFF";
  
  let dzEl = document.getElementById("inDeadzone");
  let dzEnabled = data.dz_enabled;
  let in_deadzone = data.in_deadzone;
  if (dzEnabled === true) {
    dzEl.textContent = in_deadzone ? "Yes" : "No";
  } else {
    dzEl.textContent = "Disabled";
  }

  let htEnabled = data.heater_task_enabled;
  document.getElementById("heaterTaskState").textContent =
    htEnabled ? "Enabled" : "Disabled";
  document.getElementById("currentTime").textContent = data.current_time || "";

  // Heater button
  const btn = document.getElementById("heaterBtn");
  btn.textContent = heater_on ? "Heater OFF" : "Heater ON";
  btn.className   = heater_on ? "off" : "on";

  // Deadzone button
  const dzBtn = document.getElementById("dzBtn");
  dzBtn.textContent = dzEnabled ? "Disable Deadzone" : "Enable Deadzone";
  dzBtn.className   = dzEnabled ? "off" : "on";

  // HeaterTask button
  const htBtn = document.getElementById("heaterTaskBtn");
  htBtn.textContent = htEnabled ? "Disable Heater Task" : "Enable Heater Task";
  htBtn.className   = htEnabled ? "off" : "on";

  if (!data.time_synced) {
    syncTimeFromDevice();
  }
}

async function loadStatus() {
  try {
    const resp = await fetch("/api/status");
    if (!resp.ok) {
      throw new Error("HTTP " + resp.status);
    }
    const data = await resp.json();
    // Text fields
    document.getElementById("wifiSsid").textContent    = data.wifi_ssid || "";

    await handleStatusData(data);

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

    // Status after syncing time
    sendWebSocketMessage("request_status");
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
          handleStatusData(data);
        } else if (data.type === "time_sync") {
            console.log("[WS] Time sync update", data);
            if (!data.time_synced) {
              syncTimeFromDevice();
            }
        }
      } catch (e) {
        console.warn("[WS] Status invalid message:", event.data);
      }
    });
  }

  connect();
}

function sendWebSocketMessage(message) {
  if (wsStatus && wsStatus.readyState === WebSocket.OPEN) {
    console.log("[WS] Sending message:", message);
    wsStatus.send(message);
  } else {
    console.warn("[WS] Cannot send message, WebSocket not open");
  }
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
  const dzBtn = document.getElementById("dzBtn");
  if (dzBtn) {
    dzBtn.addEventListener("click", () => {
      sendWebSocketMessage("toggle_deadzone");
    });
  }
  const htBtn = document.getElementById("heaterTaskBtn");
  if (htBtn) {
    htBtn.addEventListener("click", () => {
      sendWebSocketMessage("toggle_heater_task");
    });
  }
  const heaterBtn = document.getElementById("heaterBtn");
  if (heaterBtn) {
    heaterBtn.addEventListener("click", () => {
      sendWebSocketMessage("toggle_heater");
    });
  }
  const rebootLink = document.getElementById("reboot");
  if (rebootLink) {
    rebootLink.addEventListener("click", (event) => {
      event.preventDefault();
      fetch("/api/reboot", { method: "POST" })
        .then(() => {
          console.log("Reboot command sent");
        })
        .catch((err) => {
          console.error("Failed to send reboot command:", err);
        });
    });
  }
});