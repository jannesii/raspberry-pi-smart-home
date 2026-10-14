// bounded-v1: install as an enabled-on-boot Shelly script (see README).
// The relay's own flip-back timer enforces shutdown even if this script stops.
let bootId = "" + Math.random();
let KEY = "car_heater_bound_v1";
let state = null;
let ready = false;
let busy = false;
let cancelled = false;

function reply(response, code, note) {
  let sw = Shelly.getComponentStatus("switch:0");
  response.code = code;
  response.headers = [["Content-Type", "application/json"]];
  let sys = Shelly.getComponentStatus("sys");
  response.body = JSON.stringify({protocol: 1, ready: ready && !!sys && !!sys.unixtime, boot_id: bootId, note: note,
    session_id: state ? state.session_id : null,
    finish_at: state ? state.finish_at : null,
    phase: state ? state.phase : "stopped", reason: state ? state.reason : null,
    relay_on: sw ? sw.output : null, power_w: sw ? sw.apower : null,
    energy_wh: sw && sw.aenergy ? sw.aenergy.total : null});
  response.send();
}
function off() { Shelly.call("Switch.Set", {id: 0, on: false}); }
function persist(cb) {
  Shelly.call("KVS.Set", {key: KEY, value: JSON.stringify(state)}, cb);
}
function safeConfig() {
  let c = Shelly.getComponentConfig("switch:0");
  return c && c.initial_state === "off" && c.auto_on === false && c.auto_off === false
    && c.autorecover_voltage_errors !== true;
}
// Startup is fail-closed. Never restore ON after Shelly/script restart.
off();
Shelly.call("KVS.GetMany", {match: KEY}, function(result, error) {
  // GetMany distinguishes an empty store from a storage failure.
  if (error || !result || !result.items) return;
  if (result.items.length > 0) {
    try { state = JSON.parse(result.items[0].value); } catch (e) { return; }
    if (!state || typeof state.issued_at !== "number" || !state.session_id) return;
    state.phase = "stopped";
    state.reason = "shelly_restart";
    persist(function(r, err) { ready = !err && safeConfig(); });
  } else { ready = safeConfig(); }
});
HTTPServer.registerEndpoint("bounded", function(request, response) {
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
    if (typeof c.issued_at === "number" &&
        (!state || c.issued_at > state.issued_at)) {
      state = {session_id: c.session_id || "off-" + c.issued_at, issued_at: c.issued_at, finish_at: c.finish_at || 0};
    }
    if (state) {
      state.phase = "stopped"; state.reason = (typeof c.reason === "string" ? c.reason : "cancelled").slice(0, 32);
      persist(function(r, err) { if (err) ready = false; reply(response, err ? 503 : 200, "OFF requested"); });
    } else { reply(response, 200, "OFF requested"); }
    return;
  }
  let sys = Shelly.getComponentStatus("sys");
  let now = sys ? sys.unixtime : null;
  let bounded = typeof c.session_id === "string" && c.session_id.length > 0 && c.session_id.length <= 36;
  if (!c.session_id && c.session_id !== undefined && c.session_id !== null) { reply(response, 400, "invalid identity"); return; }
  if (c.session_id && !bounded) { reply(response, 400, "invalid identity"); return; }
  if (c.boot_id !== bootId || !ready || !safeConfig() || !now || busy || typeof c.issued_at !== "number" ||
      typeof c.expires_at !== "number" || c.issued_at > now ||
      c.expires_at <= now || c.expires_at > c.issued_at + 30) {
    off(); reply(response, 409, "capability, clock or lifetime invalid"); return;
  }
  // Persistent high-water mark rejects old commands across power cycles.
  if (state && c.issued_at <= state.issued_at) {
    let same = bounded && c.session_id === state.session_id &&
      c.issued_at === state.issued_at && c.finish_at === state.finish_at;
    if (same && state.phase === "heating") { reply(response, 200, "duplicate; original timer retained"); }
    else { reply(response, 409, "obsolete ON"); }
    return;
  }
  let sw = Shelly.getComponentStatus("switch:0");
  if (!sw || sw.output !== false || (state && state.phase === "heating")) {
    reply(response, 409, "confirmed OFF required"); return;
  }
  let finish = bounded ? c.finish_at : c.issued_at + 10800;
  if (typeof finish !== "number" || finish <= now || finish > c.issued_at + 10800) {
    off(); reply(response, 409, "invalid finish"); return;
  }
  busy = true; cancelled = false;
  state = {session_id: bounded ? c.session_id : "comfort-" + c.issued_at,
    issued_at: c.issued_at, finish_at: finish, phase: "heating", reason: (typeof c.reason === "string" ? c.reason : "started").slice(0, 32)};
  // Write once before ON: ambiguous execution/reboot can never replay ON.
  persist(function(result, error) {
    if (error || cancelled) {
      busy = false; if (error) ready = false; off();
      state.phase = "stopped";
      state.reason = error ? "storage_failed" : "cancelled";
      persist(function(r, err) { reply(response, 503, state.reason); });
      return;
    }
    let current = Shelly.getComponentStatus("sys").unixtime;
    // Sys status may expose whole seconds. Subtract one second so the
    // native relative timer cannot round the original finish into the future.
    let remaining = Math.floor(finish - current) - 1;
    if (!current || remaining <= 0 || current < now || current >= c.expires_at || !safeConfig()) {
      busy = false; state.phase = "stopped"; state.reason = "expired"; off();
      persist(function(r, err) { reply(response, 409, "expired before ON"); }); return;
    }
    Shelly.call("Switch.Set", {id: 0, on: true, toggle_after: remaining}, function(r, err) {
      busy = false;
      if (err || cancelled) {
        off(); state.phase = "stopped"; state.reason = err ? "relay_failed" : "cancelled";
        persist(function(r2, err2) { reply(response, 503, state.reason); }); return;
      }
      reply(response, 200, "relay-local timer armed");
    });
  });
});
// Record local expiry, without a network call. The built-in timer performs OFF.
Timer.set(1000, true, function() {
  if (!state || state.phase !== "heating" || busy) return;
  let sw = Shelly.getComponentStatus("switch:0");
  if (sw && sw.output === false) {
    state.phase = "stopped"; state.reason = "relay_off_or_local_expiry";
    persist(function(r, err) { if (err) ready = false; });
  }
});
