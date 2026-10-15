// bounded-v1: install as an enabled-on-boot Shelly script (see README).
// Use named, top-level callbacks: Shelly's interpreter limits nested functions.
// The relay's own flip-back timer enforces shutdown even if this script stops.
let bootId = "" + Math.random();
let KEY = "car_heater_bound_v1";
let state = null;
let ready = false;
let busy = false;
let cancelled = false;
let CLOCK_SKEW_S = 2;

// Component status objects are large on a 7 KB scripting heap. Return scalars
// so control handlers do not retain them while reply() allocates its response.
function clockNow() {
  let sys = Shelly.getComponentStatus("sys");
  return sys ? sys.unixtime : null;
}
function relayOn() {
  let sw = Shelly.getComponentStatus("switch:0");
  return sw ? sw.output : null;
}
function reply(response, code, note) {
  let now = clockNow();
  let sw = Shelly.getComponentStatus("switch:0");
  response.code = code;
  response.headers = [["Content-Type", "application/json"]];
  response.body = JSON.stringify({protocol: 1, ready: ready && !!now,
    boot_id: bootId, note: note, clock_epoch: now, session_id: state ? state.session_id : null,
    finish_at: state ? state.finish_at : null,
    phase: state ? state.phase : "stopped", reason: state ? state.reason : null,
    relay_on: sw ? sw.output : null, power_w: sw ? sw.apower : null,
    energy_wh: sw && sw.aenergy ? sw.aenergy.total : null});
  response.send();
}
function off() { Shelly.call("Switch.Set", {id: 0, on: false}); }
function persist(callback, context) {
  Shelly.call("KVS.Set", {key: KEY, value: JSON.stringify(state)}, callback, context);
}
function safeConfig() {
  let c = Shelly.getComponentConfig("switch:0");
  return c && c.initial_state === "off" && c.auto_on === false && c.auto_off === false
    && c.autorecover_voltage_errors !== true;
}
function restored(result, error) { ready = !error && safeConfig(); }
function validStoredState(saved) {
  if (!saved || typeof saved.session_id !== "string" || !saved.session_id.length ||
      saved.session_id.length > 36 || typeof saved.issued_at !== "number" ||
      saved.issued_at <= 0 || typeof saved.finish_at !== "number" ||
      (saved.phase !== "heating" && saved.phase !== "stopped")) return false;
  // Arithmetic rejects non-finite values without relying on optional built-ins.
  if (saved.issued_at - saved.issued_at !== 0 || saved.finish_at - saved.finish_at !== 0) return false;
  // A standalone OFF may leave a zero-finish tombstone, but never a heating run.
  if (saved.finish_at === 0) return saved.phase === "stopped";
  return saved.finish_at > saved.issued_at && saved.finish_at <= saved.issued_at + 10800;
}
function loaded(result, error) {
  // GetMany distinguishes an empty store from a storage failure.
  if (error || !result || !result.items || typeof result.items !== "object") return;
  // Installed Gen 3 firmware returns a keyed object. Some API examples use
  // an array: accept both without mistaking an existing key for empty storage.
  let item = result.items[KEY];
  if (typeof result.items.length === "number") {
    if (result.items.length > 1) return;
    item = result.items.length === 1 ? result.items[0] : undefined;
  }
  if (item !== undefined) {
    if (!item || typeof item.value !== "string") return;
    try { state = JSON.parse(item.value); } catch (e) { return; }
    if (!validStoredState(state)) return;
    state.phase = "stopped";
    state.reason = "shelly_restart";
    persist(restored, null);
  } else { ready = safeConfig(); }
}
function persistedReply(result, error, message, context) {
  if (error) ready = false;
  reply(context.response, error ? 503 : context.code, error ? "storage_failed" : context.note);
}
function rejectPending(context, code, reason, detail) {
  busy = false;
  off();
  state.phase = "stopped";
  state.reason = reason;
  persist(persistedReply, {response: context.response, code: code,
    note: reason + (detail ? ": " + detail : "")});
}
function switched(result, error, message, context) {
  busy = false;
  if (error || cancelled) {
    rejectPending(context, 503, error ? "relay_failed" : "cancelled", message);
    return;
  }
  reply(context.response, 200, "relay-local timer armed");
}
function authorized(result, error, message, context) {
  if (error || cancelled) {
    if (error) ready = false;
    rejectPending(context, 503, error ? "storage_failed" : "cancelled", message);
    return;
  }
  let current = clockNow();
  // Account for bounded clock lag and whole-second reports. Neither can
  // grant a timer later than the server's original finish.
  let remaining = Math.floor(context.finish - current) - CLOCK_SKEW_S - 1;
  if (!current || remaining <= 0 || current < context.now ||
      current + CLOCK_SKEW_S >= context.command.expires_at || !safeConfig()) {
    rejectPending(context, 409, "expired before ON");
    return;
  }
  Shelly.call("Switch.Set", {id: 0, on: true, toggle_after: remaining}, switched, context);
}
function requestBounded(request, response) {
  if (request.method === "GET") { reply(response, 200, "status"); return; }
  if (request.method !== "POST") { reply(response, 405, "POST required"); return; }
  let c;
  try { c = JSON.parse(request.body); } catch (e) { reply(response, 400, "invalid JSON"); return; }
  if (!c || (c.action !== "turn_on" && c.action !== "turn_off")) {
    reply(response, 400, "invalid action"); return;
  }
  if (c.action === "turn_off") {
    cancelled = true;
    off();
    // OFF arriving before its ON must leave a durable tombstone.
    if (typeof c.issued_at === "number" && (!state || c.issued_at > state.issued_at)) {
      state = {session_id: c.session_id || "off-" + c.issued_at,
        issued_at: c.issued_at, finish_at: c.finish_at || 0};
    }
    if (state) {
      state.phase = "stopped";
      state.reason = (typeof c.reason === "string" ? c.reason : "cancelled").slice(0, 32);
      persist(persistedReply, {response: response, code: 200, note: "OFF requested"});
    } else { reply(response, 200, "OFF requested"); }
    return;
  }
  let now = clockNow();
  let bounded = typeof c.session_id === "string" && c.session_id.length > 0 && c.session_id.length <= 36;
  if (!c.session_id && c.session_id !== undefined && c.session_id !== null) {
    reply(response, 400, "invalid identity"); return;
  }
  if (c.session_id && !bounded) { reply(response, 400, "invalid identity"); return; }
  if (c.boot_id !== bootId) {
    off(); reply(response, 409, "obsolete Shelly boot token"); return;
  }
  if (!ready || !safeConfig()) {
    off(); reply(response, 409, "relay capability or configuration unsafe"); return;
  }
  if (!now) { off(); reply(response, 409, "Shelly clock unavailable"); return; }
  if (busy) { off(); reply(response, 409, "relay operation pending"); return; }
  if (typeof c.issued_at !== "number" || typeof c.expires_at !== "number") {
    off(); reply(response, 409, "command lifetime missing"); return;
  }
  if (c.issued_at > now + CLOCK_SKEW_S) {
    off(); reply(response, 409, "Shelly clock behind command issue time"); return;
  }
  if (c.expires_at <= now + CLOCK_SKEW_S || c.expires_at > c.issued_at + 30) {
    off(); reply(response, 409, "command lifetime expired or invalid"); return;
  }
  // Persistent high-water mark rejects old commands across power cycles.
  if (state && c.issued_at <= state.issued_at) {
    let same = bounded && c.session_id === state.session_id &&
      c.issued_at === state.issued_at && c.finish_at === state.finish_at;
    if (same && state.phase === "heating") {
      reply(response, 200, "duplicate; original timer retained");
    } else { reply(response, 409, "obsolete ON"); }
    return;
  }
  if (relayOn() !== false || (state && state.phase === "heating")) {
    reply(response, 409, "confirmed OFF required"); return;
  }
  let finish = bounded ? c.finish_at : c.issued_at + 10800;
  if (typeof finish !== "number" || finish <= now || finish > c.issued_at + 10800) {
    off(); reply(response, 409, "invalid finish"); return;
  }
  busy = true;
  cancelled = false;
  state = {session_id: bounded ? c.session_id : "comfort-" + c.issued_at,
    issued_at: c.issued_at, finish_at: finish, phase: "heating",
    reason: (typeof c.reason === "string" ? c.reason : "started").slice(0, 32)};
  // Write once before ON: ambiguous execution/reboot can never replay ON.
  persist(authorized, {response: response, command: c, finish: finish, now: now});
}
function recordedStop(result, error) { if (error) ready = false; }
function noteLocalExpiry() {
  if (!state || state.phase !== "heating" || busy) return;
  if (relayOn() === false) {
    state.phase = "stopped";
    state.reason = "relay_off_or_local_expiry";
    persist(recordedStop, null);
  }
}
// Startup is fail-closed. Never restore ON after Shelly/script restart.
off();
Shelly.call("KVS.GetMany", {match: KEY}, loaded);
HTTPServer.registerEndpoint("bounded", requestBounded);
Timer.set(1000, true, noteLocalExpiry);
