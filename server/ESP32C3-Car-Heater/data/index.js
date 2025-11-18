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

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();

  const syncBtn = document.getElementById("syncTimeBtn");
  if (syncBtn) {
    syncBtn.addEventListener("click", () => {
      syncTimeFromDevice();
    });
  }
});
