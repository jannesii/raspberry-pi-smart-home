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

    const text = await resp.text();
    const lines = text.split("\n").filter(line => line.trim().length > 0);

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
        }
      } catch (e) {
        console.warn("[WS] Non-JSON or invalid message:", event.data);
      }
    });
  }

  connect();
}

document.addEventListener("DOMContentLoaded", () => {
  loadLogs();
  setupLogWebSocket();
});
