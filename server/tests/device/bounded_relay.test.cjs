const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');

function rig() {
  let epoch = 1800000000, uptime = 0, on = false, timer = null;
  let persistent = {}, endpoint, config = {initial_state: 'off', auto_on: false, auto_off: false};
  let network = true, calls = [], failKvs = false, tick, deferKvs = false, callbacks = [], clockOffset = 0, espClockOffset = 0;
  function boot() {
    on = false; timer = null; uptime = 0;
    const context = {
      JSON, Math,
      Shelly: {
        getComponentConfig: () => config,
        getComponentStatus: name => name === 'sys' ? {unixtime: Math.floor(epoch + clockOffset), uptime} : {output: on, apower: on ? 1400 : 0},
        call(method, params, cb, context) {
          calls.push([method, params]);
          if (method === 'KVS.GetMany') return cb({items: persistent[params.match] ? [{value: persistent[params.match]}] : []}, 0, null, context);
          if (method === 'KVS.Set') {
            if (failKvs) return cb(null, 1, "storage failed", context);
            if (deferKvs) { callbacks.push(() => {persistent[params.key] = params.value; cb({}, 0, null, context);}); return; }
            persistent[params.key] = params.value; return cb({}, 0, null, context);
          }
          if (method === 'Switch.Set') {
            on = params.on; timer = on && params.toggle_after ? uptime + params.toggle_after : null;
            if (cb) cb({was_on: false}, 0, null, context);
          }
        }
      },
      HTTPServer: {registerEndpoint: (name, fn) => endpoint = fn},
      Timer: {set: (ms, repeat, fn) => {tick = fn; return 1;}},
      print() {}
    };
    vm.runInNewContext(fs.readFileSync('ESP32C3-Car-Heater/shelly/bounded_heating.js', 'utf8'), context);
  }
  boot();
  return {
    command(body) {
      if (body && body.boot_id === undefined) body = {...body, boot_id: this.status().boot_id};
      if (!network) throw Error('Wi-Fi and Shelly RPC unavailable');
      if (process.env.RELAY_EXECUTOR_DRIVER) {
        const result = spawnSync(process.env.RELAY_EXECUTOR_DRIVER,
          [JSON.stringify({...body, device_boot_id: body.device_boot_id || 'esp-one'}), String(Math.floor(epoch + espClockOffset)), 'esp-one'], {encoding: 'utf8'});
        if (result.status !== 0) { this.command({action: 'turn_off'}); return {code: 409, body: {note: 'firmware rejected ON'}}; }
        body = JSON.parse(result.stdout);
      }
      let result;
      endpoint({method: 'POST', body: JSON.stringify(body)}, {
        send() {result = {code: this.code, body: JSON.parse(this.body)};}
      });
      return result;
    },
    status() {
      let result;
      endpoint({method: 'GET'}, {send() {result = JSON.parse(this.body);}});
      return result;
    },
    advance(seconds) {epoch += seconds; uptime += seconds; if (timer !== null && uptime >= timer) {on = false; timer = null;} if (tick) tick();},
    stopScript() {tick = null;},
    routerFailure() {network = false;}, restoreRouter() {network = true;}, boot,
    set clockOffset(value) {clockOffset = value;},
    set espClockOffset(value) {espClockOffset = value;},
    set deferKvs(value) {deferKvs = value;},
    flushKvs() {deferKvs = false; const pending = callbacks; callbacks = []; pending.forEach(fn => fn());},
    get on() {return on;}, get timer() {return timer;}, get calls() {return calls;},
    set failKvs(v) {failKvs = v;}, set config(v) {config = v;}
  };
}
const command = (extra = {}) => ({action: 'turn_on', session_id: 'session-1', issued_at: 1800000000,
  expires_at: 1800000030, finish_at: 1800003600, ...extra});

test('relay timer finishes during router failure without any remote OFF', () => {
  const r = rig();
  assert.equal(r.command(command()).code, 200);
  assert.equal(r.on, true);
  r.routerFailure();
  const calls = r.calls.filter(c => c[0] === "Switch.Set").length;
  assert.throws(() => r.command(command({action: 'turn_off'})), /unavailable/);
  r.advance(3600);
  assert.equal(r.on, false);
  assert.equal(r.calls.filter(c => c[0] === "Switch.Set").length, calls);
});

test('HTTP/WS duplicate delivery never rearms the relay timer', () => {
  const r = rig(); const c = command();
  r.command(c); r.advance(10);
  assert.equal(r.command(c).code, 200);
  assert.equal(r.timer, 3597);
  r.advance(3590);
  assert.equal(r.on, false);
});
test('changed finish, competing modes and delayed ON cannot extend allowance', () => {
  const r = rig(); r.command(command());
  assert.equal(r.command(command({finish_at: 1800007200})).code, 409);
  r.advance(1);
  assert.equal(r.command(command({session_id: 'other', issued_at: 1800000001, expires_at: 1800000031})).code, 409);
  assert.ok(r.timer === null || r.timer <= 3600);
  r.advance(31);
  assert.equal(r.command(command()).code, 409);
  assert.equal(r.on, false);
});
test('maximum 180 minutes applies to the original issue time', () => {
  const r = rig();
  assert.equal(r.command(command({finish_at: 1800010801})).code, 409);
  assert.equal(r.on, false);
  assert.equal(r.command(command({finish_at: 1800010800})).code, 200);
  r.advance(10800);
  assert.equal(r.on, false);
});
test('cancel before ON persists a tombstone; repeated OFF stays retryable', () => {
  const r = rig();
  r.command(command({action: 'turn_off'}));
  assert.equal(r.command(command()).code, 409);
  r.command(command({action: 'turn_off'}));
  r.boot();
  assert.equal(r.command(command()).code, 409);
  assert.equal(r.on, false);
});
test('Shelly restart cannot resume a session or rearm its timer', () => {
  const r = rig(); const c = {...command(), boot_id: r.status().boot_id};
  r.command(c); r.advance(5); r.boot();
  assert.equal(r.on, false);
  assert.equal(r.command(c).code, 409);
  assert.equal(r.command(command()).code, 409);
  assert.equal(r.timer, null);
});
test('storage failure and unsafe relay startup configuration block ON', () => {
  const r = rig(); r.failKvs = true;
  assert.equal(r.command(command()).code, 503);
  assert.equal(r.on, false);
  const bad = rig(); bad.config = {initial_state: 'restore_last', auto_on: false, auto_off: false};
  assert.equal(bad.command(command()).code, 409);
  assert.equal(bad.on, false);
});
test('stopping the script leaves the native relay timer enforced', () => {
  const r = rig(); r.command(command()); r.stopScript(); r.routerFailure();
  r.advance(3600);
  assert.equal(r.on, false);
});
test('ordinary comfort remains usable after confirmed OFF but old ON is rejected', () => {
  const r = rig(); r.command(command());
  r.command(command({action: 'turn_off'})); r.advance(2);
  assert.equal(r.command(command({session_id: null, issued_at: 1800000002, expires_at: 1800000032})).code, 200);
  r.command({action: 'turn_off', issued_at: 1800000003});
  assert.equal(r.command(command()).code, 409);
  assert.equal(r.on, false);
});


test('asynchronous storage cannot authorize ON past its delivery lifetime', () => {
  const r = rig(); r.deferKvs = true;
  r.command(command());
  assert.equal(r.on, false);
  r.advance(31); r.flushKvs();
  assert.equal(r.on, false);
  assert.equal(r.status().phase, 'stopped');
});
test('delayed storage uses remaining original allowance without extending finish', () => {
  const r = rig(); r.deferKvs = true;
  r.command(command()); r.advance(20); r.flushKvs();
  assert.equal(r.on, true);
  assert.equal(r.timer, 3597);
  r.routerFailure(); r.advance(3580);
  assert.equal(r.on, false);
});


test('whole-second Shelly clock cannot push timer finish past the original deadline', () => {
  const r = rig(); r.advance(0.9);
  assert.equal(r.command(command()).code, 200);
  assert.ok(r.timer <= 3600);
  r.routerFailure(); r.advance(3599.1);
  assert.equal(r.on, false);
});

test('fresh timed ON tolerates one-second device clock lag without extending finish', () => {
  const r = rig(); r.clockOffset = -1; r.espClockOffset = -1;
  assert.equal(r.command(command()).code, 200);
  assert.equal(r.on, true);
  assert.ok(r.timer <= 3600);
  r.routerFailure(); r.advance(3600);
  assert.equal(r.on, false);
});
test('Shelly callbacks use named top-level references to avoid device nesting crashes', () => {
  const script = fs.readFileSync('ESP32C3-Car-Heater/shelly/bounded_heating.js', 'utf8');
  assert.doesNotMatch(script, /\bfunction\s*\(/);
});

test('excessive relay clock lag is rejected with a usable reason', () => {
  const r = rig(); r.clockOffset = -3;
  const result = r.command(command());
  assert.equal(result.code, 409);
  assert.match(result.body.note, /clock.*behind/);
  assert.equal(r.on, false);
});
test('cancellation during asynchronous authorization prevents relay ON', () => {
  const r = rig(); r.deferKvs = true;
  r.command(command());
  r.command(command({action: 'turn_off'}));
  r.flushKvs();
  assert.equal(r.on, false);
  assert.equal(r.command(command()).code, 409);
});
