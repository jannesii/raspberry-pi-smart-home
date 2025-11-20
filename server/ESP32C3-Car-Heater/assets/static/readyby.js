// readyby.js

function pad2(n) {
  return n.toString().padStart(2, "0");
}

function formatDuration(seconds) {
  if (!isFinite(seconds) || seconds <= 0) return "–";
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h === 0) return `${mins} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function formatDate(d) {
  const yyyy = d.getFullYear();
  const mm   = pad2(d.getMonth() + 1);
  const dd   = pad2(d.getDate());
  return `${yyyy}-${mm}-${dd}`;
}

function formatTimeHM(d) {
  const hh = pad2(d.getHours());
  const mm = pad2(d.getMinutes());
  return `${hh}:${mm}`;
}

function setDefaultDateAndTime() {
  const dateInput  = document.getElementById("rb_date");
  const timeInput  = document.getElementById("rb_time");

  if (!dateInput || !timeInput) {
    console.warn("[ReadyBy] Missing date/time inputs");
    return;
  }

  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);

  dateInput.value = formatDate(tomorrow);
  timeInput.value = "07:30";

  updateTargetDisplay();
}

function updateTargetDisplay() {
  const dateInput = document.getElementById("rb_date");
  const timeInput = document.getElementById("rb_time");
  const targetEl  = document.getElementById("rb_target_display");

  if (!dateInput || !timeInput || !targetEl) return;

  const dateVal = dateInput.value;
  const timeVal = timeInput.value;

  if (!dateVal || !timeVal) {
    targetEl.textContent = "–";
    return;
  }

  targetEl.textContent = `${dateVal} ${timeVal}`;
}

async function handleStatusData(data) {
  const statusEl  = document.getElementById("rb_status");
  const warmupEl  = document.getElementById("rb_warmup");
  const startEl   = document.getElementById("rb_start");
  const targetEl  = document.getElementById("rb_target_display");
  const dateInput = document.getElementById("rb_date");
  const timeInput = document.getElementById("rb_time");
  const targetInput = document.getElementById("rb_target");
  const configForm = document.querySelector(".readyby-form .config-form");
  const btnSet     = document.getElementById("rb_set");
  const btnClear   = document.getElementById("rb_clear");
  const currentTempEl = document.getElementById("currentTemp");

  // Update current temperature display
  if (currentTempEl && typeof data.current_temp === "number") {
    currentTempEl.textContent = data.current_temp.toFixed(1);
  }

  if (!data.scheduled) {
    // Not scheduled
    btnClear.style.display = "none";
    return;
  }
  // Scheduled: update status
  if (statusEl) {
    statusEl.textContent = "Scheduled";
    statusEl.classList.add("rb-scheduled");
  }

  // Warmup info
  if (warmupEl && typeof data.warmup_seconds === "number") {
    warmupEl.textContent = formatDuration(data.warmup_seconds);
  }

  // Times from device (UTC → browser local)
  if (typeof data.start_epoch_utc === "number" &&
      typeof data.target_epoch_utc === "number") {

    const startDate  = new Date(data.start_epoch_utc * 1000);
    const targetDate = new Date(data.target_epoch_utc * 1000);

    if (startEl) {
      startEl.textContent = `${formatDate(startDate)} ${formatTimeHM(startDate)}`;
    }

    if (targetEl) {
      targetEl.textContent = `${formatDate(targetDate)} ${formatTimeHM(targetDate)}`;
    }

    // Keep the hidden inputs in sync with actual schedule
    if (dateInput) dateInput.value = formatDate(targetDate);
    if (timeInput) timeInput.value = formatTimeHM(targetDate);
  }

  if (targetInput && typeof data.target_temp_c === "number") {
    targetInput.value = data.target_temp_c.toFixed(1);
  }

  // Hide inputs + "Schedule" button when a schedule is active
  if (configForm) {
    configForm.style.display = "none";
  }
  if (btnSet) {
    btnSet.style.display = "none";
  }
  if (!data.time_synced) {
    syncTimeFromDevice();
  }
}

async function loadReadyByStatus() {
  try {
    console.log("[ReadyBy] Loading status from device");
    const resp = await fetch("/api/ready-by");
    if (!resp.ok) {
      console.warn("[ReadyBy] /api/ready-by returned", resp.status);
      return;
    }
    const data = await resp.json();
    console.log("[ReadyBy] /api/ready-by response", data);

    await handleStatusData(data);

  } catch (err) {
    console.error("[ReadyBy] Failed to load status", err);
  }
}

async function scheduleReadyBy() {
  const dateInput   = document.getElementById("rb_date");
  const timeInput   = document.getElementById("rb_time");
  const targetInput = document.getElementById("rb_target");

  if (!dateInput || !timeInput || !targetInput) return;

  const dateVal = dateInput.value;
  const timeVal = timeInput.value;
  const targetTemp = parseFloat(targetInput.value);

  if (!dateVal || !timeVal || isNaN(targetTemp)) {
    alert("Please fill date, time and target temperature");
    return;
  }

  // Build a local Date from date + time, then convert to epoch seconds (UTC)
  const [year, month, day] = dateVal.split("-").map(Number);
  const [hour, minute] = timeVal.split(":").map(Number);

  const dt = new Date(year, month - 1, day, hour, minute, 0, 0);
  const epochSeconds = Math.floor(dt.getTime() / 1000);

  const params = new URLSearchParams();
  params.set("target_epoch_utc", epochSeconds.toString());
  params.set("target_temp_c", targetTemp.toString());

  try {
    const resp = await fetch("/api/ready-by", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: params.toString()
    });

    if (!resp.ok) {
      console.warn("[ReadyBy] schedule returned", resp.status);
      alert("Failed to schedule Ready By");
      return;
    }

    const data = await resp.json();
    console.log("[ReadyBy] schedule response:", data);

  } catch (err) {
    console.error("[ReadyBy] schedule error", err);
    alert("Error talking to device");
  }
}

async function clearReadyBy() {
  const statusEl   = document.getElementById("rb_status");
  const warmupEl   = document.getElementById("rb_warmup");
  const startEl    = document.getElementById("rb_start");
  const targetEl   = document.getElementById("rb_target_display");
  const configForm = document.querySelector(".readyby-form .config-form");
  const btnSet     = document.getElementById("rb_set");

  try {
    const resp = await fetch("/api/ready-by/clear", {
        method: "POST",
        headers: {
            "Content-Type": "application/x-www-form-urlencoded"
        },
        body: ""
    });

    if (!resp.ok) {
      console.warn("[ReadyBy] clear returned", resp.status, await resp.text());
      alert("Failed to clear Ready By schedule");
      return;
    }

    // Reset UI to "not scheduled" state
    if (statusEl) {
      statusEl.textContent = "Not scheduled";
      statusEl.classList.remove("rb-scheduled");
    }
    if (warmupEl) warmupEl.textContent = "–";
    if (startEl)  startEl.textContent  = "–";
    if (targetEl) targetEl.textContent = "–";

    if (configForm) {
      configForm.style.display = "";
    }
    if (btnSet) {
      btnSet.style.display = "";
    }

    // Re-apply defaults
    setDefaultDateAndTime();

  } catch (err) {
    console.error("[ReadyBy] clear error", err);
    alert("Error talking to device");
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

        if (data.type === "ready_by_update") {
          console.log("[WS] Ready By update", data);
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

document.addEventListener("DOMContentLoaded", () => {
  setDefaultDateAndTime();

  const dateInput = document.getElementById("rb_date");
  const timeInput = document.getElementById("rb_time");

  if (dateInput) {
    dateInput.addEventListener("change", updateTargetDisplay);
  }
  if (timeInput) {
    timeInput.addEventListener("change", updateTargetDisplay);
  }

  const btnSet   = document.getElementById("rb_set");
  const btnClear = document.getElementById("rb_clear");

  if (btnSet) {
    btnSet.addEventListener("click", scheduleReadyBy);
  }

  if (btnClear) {
    btnClear.addEventListener("click", clearReadyBy);
  }

  // 🔥 Load schedule from device on page load
  loadReadyByStatus();
  setupStatusWebSocket();
});
