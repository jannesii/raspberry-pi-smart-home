# Bounded relay commissioning observations, 14–15 October 2026

## Result and scope

The owner observed timed shutdown, cancellation, outage recovery, restart
behavior, duplicate handling and expired-command rejection with the installed
Shelly and ESP32. The final duplicate and expired-ON checks passed after the
script corrections described below. This records the bounded-relay portion of
[issue #7](https://github.com/jannesii/raspberry-pi-smart-home/issues/7) and supplies
evidence for [commissioning #12](https://github.com/jannesii/raspberry-pi-smart-home/issues/12).
It does not complete the full collection setup in #12.

Evidence consists of owner-reported physical observations, pasted session
milestones and local RPC responses. The agent did not independently observe the
relay or load. Times below are Europe/Helsinki (UTC+03:00). Physical times marked
“about” are approximate; a later telemetry confirmation is not the physical
switch-off time.

## Installation details captured

| Item | Evidence |
| --- | --- |
| Shelly model | `S3PL-20112EU` |
| Generation | `3` |
| Shelly firmware | `1.2.3-matter22` |
| Firmware build | `20241128-123512/1.2.3-outdoorplugsg3prod0-g8658900` |
| Local script | ID `1`, protocol `bounded-v1`; persistent key `car_heater_bound_v1` retained |
| Latest script source used for successful retests | `fb121a7cc41def5a110f292ca51e3e568c68e41d` |
| Test load | Laptop charger; measured draws included 5.4–38.3 W. Charger unplugged for memory diagnosis and final duplicate/expiry checks |
| Running ESP32 firmware | `3.1.1`, independently read from a live `esp32:status` frame on 15 October at 17:35:15 Helsinki time |
| Deployed database revision | PostgreSQL `alembic_version` reported `20261014_0013` in a read-only metadata transaction after the final manual check |
| Server deployment | Owner reported setup steps 1–4 complete. Exact running server source revision was not independently established |
| Conditions and placement | Owner's work desk, about 24.5°C, as confirmed by the owner after reviewing this record. Final vehicle installation layout not recorded |

Source revision identifies the correction the owner was instructed to install;
no independent hash of the script retrieved from the device was collected.
ESP32 version evidence came from telemetry rather than the source checkout.

## Timer and network loss, 14 October

| Scenario and session UUID | Original finish | Physical observation and later evidence |
| --- | --- | --- |
| Normal timed run — `710f5c0a-4404-47ce-85b7-4c8c9bed4c18` | 22:28:59 | Owner observed OFF, without supplying the precise time. Draw 13.2 W; expiry OFF requested 22:28:59 and confirmed 22:29:02 |
| Router power disconnected — `d0ec0a8e-b262-4f8d-9a4f-a1c839225b4d` | 22:34:38 | Relay OFF about 22:34:36. Draw 5.4 W; confirmation arrived after reconnection at 22:35:45 |
| Router disconnected and server stopped — `79d54768-9bbe-4194-96ea-c6cf6e68af32` | 22:40:43 | Relay OFF about 22:40:41 and stayed OFF on recovery. Draw 38.3 W. Server-restart OFF request was later, at 22:41:05; confirmation 22:41:37 |

The combined outage observation demonstrates physical shutdown before the server
recovered and requested OFF. The router-loss observation also places physical
OFF before connectivity returned. Neither conclusion depends on treating a
queued command as successful execution.

## Script and device restarts, 14 October

| Scenario and session UUID | Original finish | Physical observation and later evidence |
| --- | --- | --- |
| Script stopped — `f13e189e-6a7f-4361-8ad8-49daf09be8c8` | 22:44:41 | Relay remained ON after script stop, then OFF about 22:44:38. Draw 29.7 W. Remote OFF returned 404 at roughly five-second intervals until script recovery and confirmation at 22:45:07 |
| Script restarted — `34551a85-797c-45eb-8d4c-8c652ddecbee` | 22:48:57 | Starting script 1 turned the relay OFF; it stayed OFF through finish. Exact early-OFF time was not supplied; milestones confirmed OFF only at expiry |
| Shelly power cycle — `56781775-00c6-4502-8fda-fda5ff02a556` | 22:51:13 | Shelly started OFF and remained OFF through finish. Milestones confirmed OFF at expiry, without recording the earlier power-cycle shutdown |
| ESP32 reset — `ecff1245-3293-4a8c-85d1-d9d90d1ce9c8` | 22:54:57 | Relay remained ON under its timer, then OFF about 22:54:55; confirmed 22:54:56. Draw 16 W |
| Server restart — `14443f59-7732-4010-908e-5eb364673bdc` | 22:58:17 | Server-restart OFF requested 22:57:25; confirmed 22:57:26; physical OFF reported 22:57:27. Finish did not move later and relay stayed OFF |

The script-stop run demonstrates the native relay timer continuing with the
script endpoint absent. Restart and power-cycle observations establish the
reported physical behavior, but do not supply exact early-OFF timestamps where
the session history only recorded confirmation at expiry.

## Cancellation and reconnection

| Date, scenario and session UUID | Original finish | Observation |
| --- | --- | --- |
| 14 October, Stop while router disconnected — `247d5540-860d-459d-8b2d-81d2af79625a` | 23:01:36 | Cancel requested 22:59:49. App showed “Stop pending; waiting for relay confirmation · relay unknown”. After reconnection, physical OFF and confirmation at 23:00:40; stayed OFF through finish. Draw 11.5 W |
| 15 October, repeated Stop — `22760af5-e4eb-4ce7-b023-60df4077235e` | 16:20:47 | Stop button remained enabled. Three cancellation requests at 16:19:52–53; OFF confirmed 16:19:53 and stayed OFF through finish. Draw 7.2 W |
| 15 October, ESP32 power cable disconnected/reconnected — `a9897e43-d7fb-476d-8ede-be29dba350b3` | 16:23:46 | Physical OFF and confirmation at 16:23:08; stayed OFF through finish. Draw 11.2 W. Early OFF is allowed; the `cancelled` reason alone does not identify the recovery action |

The ESP32 cable check did not test the common external supply cable powering both
the ESP32 and router through the Biltema adapter. That broader #12 check remains
unrecorded here.

## Failures found and corrected

### Saved state not restored from keyed KVS responses

The installed Gen 3 firmware returned `KVS.GetMany.items` as an object keyed by
`car_heater_bound_v1`. The original loader and simulation assumed an array, so a
restart could ignore the saved session and its high-water mark.

Correction `41894a3d7c9d982c9ea7ba5468bb13a92b2de512` accepts keyed and array
responses and rejects malformed saved records. After updating script 1, the owner
queried status and confirmed that session
`a9897e43-d7fb-476d-8ede-be29dba350b3` and its finish `1792070626` survived restart,
with `ready=true`, `phase=stopped`, `reason=shelly_restart`, `relay_on=false`.

### Duplicate POST exhausted the script heap

After the KVS correction, ordinary stopped/heating GETs and a normal timed start
passed. Script memory was stable at about 2,730–2,744 bytes between requests, with
a 6,846-byte peak in the reported 7,000-byte pool.

A labeled replay isolated the failure to duplicate `POST /script/1/bounded`:
HTTP 500 `out_of_memory`, followed by script `running=false`. The recorded timer
started at `1792073303.23` with duration 116 seconds. The original finish was
15 October 17:10:22; the owner reported physical OFF a little before that time
despite the stopped script. Subsequent remote OFF attempts returned 404.

Correction `fb121a7cc41def5a110f292ca51e3e568c68e41d` shortens full component-status
object lifetimes by using scalar clock/relay helpers. The physical retests below
passed after the owner updated script 1. The measured peak is evidence for those
runs, not a guarantee of headroom under all future concurrent requests.

## Final corrected-script retests, 15 October

### Duplicate ON retained its native timer

Session `e6dfd461-26b8-4b2d-92a0-c86a6a5ee43a`, original finish
17:23:45 (`1792074225`):

- Before: relay ON, timer start `1792074105.63`, duration 117 seconds.
- Duplicate POST: HTTP 200, `duplicate; original timer retained`.
- After: relay ON, same timer start and same 117-second duration.
- Script stayed running: used 2,870, peak 6,622, free 4,130 bytes.
- Owner observed physical OFF by the intended finish and script remained running;
  exact physical OFF time was not supplied.

This physically checks a reconstructed duplicate at the Shelly endpoint. It does
not inject the same command simultaneously through the server's HTTP and
WebSocket transports. Shared firmware execution is covered by isolated tests.

### Expired ON rejected without restarting or extending heating

Session `a7c0b26a-7770-4b88-bd7d-7a982bced853`, original finish
17:29:23 (`1792074563`):

- Before: relay ON, timer start `1792074444.28`, duration 116 seconds.
- The captured command retained its original issue, expiry and finish; the test
  waited 35 seconds before sending it.
- At 17:28:05 (`1792074485`), POST returned HTTP 409 with
  `command lifetime expired or invalid`.
- The immediate response still sampled `relay_on=true`, before asynchronous OFF
  completed. The owner observed OFF after the delay; telemetry recorded relay OFF
  at 17:28:05 and confirmed shutdown at 17:28:10.
- Script stayed running: used 2,870, peak 6,622, free 4,130 bytes.
- Relay stayed OFF through the original finish.

## Remaining commissioning work

These observations support the bounded relay behavior exercised above. Earlier
outage/restart checks used earlier script revisions; they were not all repeated
after both script corrections. No complete commissioning sign-off is made here.

### Version and execution evidence still to capture

- Deployed server source revision and a retrieved hash of the installed script.
- Exact installed heater/sensor/electronics placement for the final vehicle setup.
- Common external-cable disconnect/reconnect with ESP32 and router together.
- Physical end-to-end duplicate delivery across both transports, and explicit
  checks that previously enabled automatic modes cannot resume after recovery.
- Owner reviewed and approved this record and authorized publication, evidence-based
  checklist updates and closure of #7 if its criteria are met. Full #12 completion
  remains deferred until its blocking issues are complete.

### Wider #12 requirements not established by the charger tests

- Actual heater-load operation and installation checks; charger measurements are
  not validation at the heater's full load or in cold conditions.
- Both identified TMP117 readings and SHT45 behavior across wiring, transports,
  persistence, browser views, individual sensor faults and recovery.
- Deployed retention inspection, ongoing database/photo backups and isolated
  restore evidence.
- iPhone framing/capture/upload, outcome labeling, complete interface acceptance
  and finding all evidence under the same session after restart.

## Automated checks associated with the final script correction

At source revision `fb121a7`:

- `TZ=UTC .venv/bin/pytest -q`: 329 passed, including firmware-executor/script
  integration.
- `node --test tests/device/bounded_relay.test.cjs`: 22 passed, including duplicate
  bursts, unchanged timer/relay calls, persistence and missing-versus-zero values.
- `python3 scripts/check_agent_instructions.py` and diff checks passed.
- Standards and specification reviews reported no remaining confirmed findings.

The isolated Node suite does not model the Shelly heap; actual duplicate/expiry
responses and memory readings above are separate owner-supplied device evidence.
