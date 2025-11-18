async function loadLogs() {
  const container = document.getElementById("logs");
  if (!container) return;

  container.textContent = "Loading…";

  try {
    const resp = await fetch("/api/logs");
    if (!resp.ok) {
      throw new Error("HTTP " + resp.status);
    }

    const text = await resp.text();
    const lines = text.split("\n").filter(line => line.trim().length > 0);

    // Clear previous content
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

document.addEventListener("DOMContentLoaded", () => {
  loadLogs();
});
