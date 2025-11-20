function appendLogLine(line) {
  const container = document.getElementById("logs");
  if (!container) return;

  let list = container.querySelector(".logs-list");
  if (!list) {
    list = document.createElement("div");
    list.className = "logs-list";
    container.appendChild(list);
  }

  const item = document.createElement("pre");
  item.className = "log-line";
  item.textContent = line;

  // Newest first: insert at top
  if (list.firstChild) {
    list.insertBefore(item, list.firstChild);
  } else {
    list.appendChild(item);
  }
}

async function loadLogs() {
  const container = document.getElementById("logs");
  if (!container) return;

  container.textContent = "Loading…";

  try {
    const resp = await fetch("/api/logs");
    if (!resp.ok) throw new Error("HTTP " + resp.status);

    const data = await resp.json();
    const lines = data.logs.split("\n").filter(line => line.trim().length > 0);

    container.innerHTML = "";

    if (lines.length === 0) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "No log entries yet.";
      container.appendChild(p);
      return;
    }

    const list = document.createElement("div");
    list.className = "logs-list";

    for (const line of lines) {
      const item = document.createElement("pre");
      item.className = "log-line";
      item.textContent = line;
      list.appendChild(item);
    }

    container.appendChild(list);
    if (!data.time_synced) {
      syncTimeFromDevice();
    }

  } catch (err) {
    console.error("Failed to load logs:", err);
    container.innerHTML = "";
    const p = document.createElement("p");
    p.className = "error";
    p.textContent = "Failed to load logs.";
    container.appendChild(p);
  }
}

function setupLogWebSocket() {
  const protocol = (location.protocol === "https:") ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${location.host}/ws`;

  let ws;

  function connect() {
    ws = new WebSocket(wsUrl);

    ws.addEventListener("open", () => {
      console.log("[WS] Connected");
    });

    ws.addEventListener("close", () => {
      console.log("[WS] Disconnected, retrying in 3s...");
      setTimeout(connect, 3000);
    });

    ws.addEventListener("error", (err) => {
      console.error("[WS] Error:", err);
      ws.close();
    });

    ws.addEventListener("message", (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "log_append" && data.line) {
          appendLogLine(data.line);
        } else if (data.type === "time_sync") {
            console.log("[WS] Time sync update", data);
            if (!data.time_synced) {
              syncTimeFromDevice();
            }
        }
      } catch (e) {
        console.warn("[WS] Non-JSON or invalid message:", event.data);
      }
    });
  }

  connect();
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

document.addEventListener("DOMContentLoaded", () => {
  loadLogs();
  setupLogWebSocket();
});
