# Spoken announcement commissioning — 29 October 2026

Guided owner session in Europe/Helsinki. Record machine checks separately from
owner browser, listening and account observations. An unperformed check is not
a pass. Do not include passwords, webhook URLs, OAuth tokens, encryption keys
or media capability URLs in this record.

## Candidate and environment

- Selected worktree: `/home/jannesi/Code/server-worktrees/spec40-announcement-verification`.
- Branch: `codex/spec40-announcement-verification`.
- Running candidate reported by `dev status`: `e8d2bd3`; after the authorized
  restart, the banner also reports uncommitted commissioning documentation.
- Original integrated announcement implementation `02fb7f3` is an ancestor of
  this combined candidate. A restoration correction was subsequently made and
  verified in this session; its uncommitted runtime source is fingerprinted below.
- Local browser endpoint: `http://127.0.0.1:5557`.
- Owner browser endpoint: `https://dev.jannenkoti.com`.
- Current acceptance selection: **jbl-right (192.168.1.119:8009)** and
  **jbl-left (192.168.1.148:8009)**. Right was authorized in Step 2; Left was
  separately approved later for individual-speaker checks. Discord, Spotify
  and synchronized groups remain disabled.
- Development-only dependencies and protected configuration were prepared and
  development was stopped/started with authorization. The owner completed the
  first JBL Right playback check. No production release/restart or account
  notification was performed.

## How the installed commands work

The fish functions invoke the production checkout's Python tools using its
interpreter: `scripts/dev.py` and `scripts/release.py`. The dev tool selects
the worktree to run; it does not run whichever directory the shell happens to
be in. It uses protected development configuration, a dedicated environment,
PostgreSQL database, Redis process and writable state. Setup selects/prepares
a worktree; start/stop manage only the recorded development processes. Reset
is a separate destructive operation and was not used.

The release tool prepares an exact committed revision with its own dependencies
and checks, records feature/device acceptance, and gates deployment on matching
evidence and database readiness. Migration releases require disposable rehearsal
and a backup. Deploy, recover, restart and explicit database restoration are
separate operations; this session authorizes none of them.

See [development operations](development-environment.md) and
[release operations](releases.md) for the complete contracts.

## Preflight results

| Check | Result | Evidence and limitation |
| --- | --- | --- |
| Selected worktree and running instance | PASS — machine check | `dev status` reports the intended worktree, branch, commit and running state |
| Local login endpoint | PASS — machine check | HTTP 200; response contains the development indicator, expected branch/commit and acceptance-off text |
| Public browser route | PASS — owner observation | Owner confirmed access to the requested public development page; the earlier agent-only HTTP 403 remains a separate probe result |
| Dedicated login and visible development banner | PASS — owner observation | Owner confirmed the expected worktree/commit banner and acceptance-off state; browser/device type was not reported |
| Selected external effects | RIGHT SPEAKER ONLY | Initially off; after owner authorization, status and running login banner identify only jbl-right at 192.168.1.119:8009 |
| Cast dependency | PASS — setup check | PyChromecast 14.0.10 installed in the dedicated development environment; dependency check passed |
| Speech dependency/configuration | PASS — local synthesis check | Separate Piper 1.8.0 environment with Finnish Harri medium and English Alba medium; both generated valid WAV files without playback |
| Browser automation | NOT INSTALLED | Playwright is absent from the dedicated development environment; owner browser checks are separate |
| Speaker-reported network identity | CONFIRMED — physical identity pending | Approved address reports JBL Right, JBL Link Portable, HARMAN International Industries; Cast ID e4fcb950-f1c9-d7fc-cd42-27e6e4a4ee65 pinned |
| LAN media endpoint restrictions | PASS — machine checks | Invalid capability and management path both return HTTP 404 at 192.168.1.50:5558; actual speaker fetch remains unverified |
| Production service continuity | CONFIRMED RUNNING | Active/running, with active-enter timestamp 29 October 2026 02:00:01 EET; no production stop/start command issued |

## Current acceptance boundaries

Ordinary development intentionally leaves the announcement service disabled.
The adapter can enable explicitly named individual speakers and an explicitly
selected Discord channel, using a separate LAN-only media listener. Configuration
changes require development stop/start and confirmation of the running banner.

The current adapter refuses synchronized Cast groups and Spotify account
acceptance. Those checks remain unverified and need a separately scoped path;
individual-speaker acceptance cannot establish either result. No such controls
were bypassed or expanded during this inspection.

## Guided checks and owner observations

| Step | Check | Status | Owner observation |
| --- | --- | --- | --- |
| 1 | Open the public dev page, sign in with the development login, confirm candidate and acceptance-off banner | PASS — owner observation | Owner: "Yes, the banner and acceptance state match"; recorded 29 October 2026, approximately 18:11 EET |
| 2 | Select and authorize the first individual JBL; prepare dependencies and restricted acceptance settings | PASS — authorized setup | Owner selected Jbl-Right at 192.168.1.119; setup, local synthesis and running restricted-banner machine checks passed; refreshed owner banner confirmation pending |
| 3 | Browser preview: Finnish/English speech, chime, gap and startup latency | PASS — listening only | Owner confirmed clear chime, pause and speech for both languages at chime 40%; perceived startup latency remains unmeasured; Finnish female voice requested as an enhancement |
| 4 | First JBL identity, LAN media fetch and comfortable volume | PASS — owner listening | Owner confirmed only the physical right JBL played clearly and comfortably at the requested 15%; app recorded observed playback and completion |
| 5 | Prior-volume restoration, Stop and subsequent Done/Skip/Snooze | PASS for tested individual-speaker actions | Normal restoration, speaker-test Stop, reminder Stop speaking, Done, Skip and Snooze have owner observations and saved evidence; Stop-to-Snooze later PASSED (see "Stop speaking followed by Snooze") |
| 5a | Preserve a deliberate volume change during speech | PASS — actual chosen level 40.7% | Owner clarified the test used 40.7%, not the suggested 20%; history intentionally skipped restoration and fresh device read matched 40.7% |
| 6 | Second JBL identity and overlapping individual reminders | PASS for both identities and overlapping individual playback | Owner confirmed Left-only result and both speakers overlapping; both saved attempts finished and restored volume |
| 7 | Selected Discord fallback, wording, authenticated link and mentions | SKIPPED AT OWNER REQUEST — unverified | Owner explicitly skipped Discord acceptance at handoff; no real fallback sent, Discord remains disabled |
| 8 | Phone/desktop scheduling, quiet hours, expiry, repeats, history and Helsinki times | UNVERIFIED | No owner browser result |
| 8a | One-time scheduled delivery in Helsinki time | PASS — owner and saved evidence | Intended 19:17 reminder played once, with one retained attempt and restored volume; acknowledgement remains separate |
| 8b | Done acknowledgement persists without rewriting delivery | PASS | Owner confirmed Done persists after refresh; saved action/disposition are done, delivery remains delivered and count remains one |
| 8c | Skip cancels a pending repeat without rewriting delivery | PASS | Owner heard no second clip; saved acknowledgement closed the waiting occurrence after one of two allowed announcements |
| 8d | Explicit five-minute Snooze replays once, then Done persists | PASS | Owner confirmed quiet delay and one replay; saved snooze shifted due/expiry, with one original and one snooze attempt |
| 8e | Automatic one-minute repeat and two-announcement limit | PASS for two deliveries and saved limit closure | Owner confirmed two clips; saved first/repeat playback reports are 60.59 seconds apart and occurrence closed at its limit before Done |
| 8f | Stop speaking cancels a scheduled repeat without acknowledgement | PASS | Owner confirmed prompt stop, restored volume, no repeat and Done/Skip still available; saved stopped occurrence has one attempt and no acknowledgement |
| 8g | Pause before due and Resume after due without catch-up | PASS for observed state and silence | Owner confirmed persisted Paused and silence; resumed schedule has no next due, occurrence or attempt |
| 8h | Phone portrait layout | PASS after two fixes | iPhone Chrome owner confirmed both date/time controls fit and both pickers work; original overlap and first retest failures retained below |
| 8i | Daily, selected weekdays and other checks spanning multiple days | SKIPPED AT OWNER REQUEST — unverified | Owner: "skip this and all multiple day checks"; do not count recurrence or future-day acceptance as passed |
| 8j | Same-day edit replaces pending time and text | PASS | Owner confirmed silence at original time and one edited clip; saved revision 2 has one matching occurrence/attempt and Done |
| 8k | Weekdays group hidden for Once and Every day | PASS — owner observation after software fix | Isolated browser flows found the checkboxes visible despite `hidden`; after CSS fix `20261029-3` owner confirmed hidden for Once/Every day and shown for Selected weekdays |
| 9 | Group synchronization and membership/partial-delivery behavior | BLOCKED BY CURRENT ADAPTER | No group acceptance supported |
| 10 | Spotify wait, interruption, restoration and newer owner intent | 10a PASS; 10b FAIL then PASS after fix; account restoration BLOCKED | Ordinary reminder waited for owner-started Spotify music; urgent load went to the Spotify receiver (failed `interrupted`, volume left at 30%); fixed by launching the media receiver first, owner retest passed; account restoration unsupported by adapter |
| 11 | Retained history, explicit deletion and recovery in the configured environment | PARTIAL — retention, explicit deletion and active-history guard PASS | Schedule deletion retained history; Cancel kept it; confirmed history deletion removed only the selected occurrence; deletion refused while a repeat was pending (owner-observed, refusal not logged); dev restart between clips: no replay, one repeat; restart mid-speech and backup restore unverified |

For each completed check, append the action, approximate local time, actual
observation, PASS/FAIL/UNVERIFIED and limitations. Preserve earlier observations
when a retest changes the result. Keep all GitHub issues open pending the owner's
acceptance decision; this session has not published new issue comments.

## Observation log

### Step 1 — public development page and isolation indicator

- Recorded: 29 October 2026, approximately 18:11 EET.
- Requested action: open the announcement page at the public development host,
  sign in with the development login and inspect the development banner.
- Expected: `codex/spec40-announcement-verification` at `e8d2bd3`, with
  `Acceptance: off (safe development)`.
- Owner response: "Yes, the banner and acceptance state match".
- Result: **PASS** for public access and the reported candidate/acceptance
  indicator. Browser and device type were not specified; this does not establish
  phone layout, audio playback, synthesis, speaker reachability or account effects.

### Step 2 — authorized individual-speaker preparation

- Completed: 29 October 2026, approximately 18:21 EET.
- Owner authorized Jbl-Right at 192.168.1.119, including missing development-only
  packages/local voice files, restricted settings and development restart.
- Before enabling: Controller-backed development reads found zero active
  announcement schedules, pending occurrences, pending attempts and pending
  fallbacks. No development reset or data deletion was needed.
- Speaker metadata was queried read-only at the approved address. The reported
  name is `JBL Right` (a spacing variant of the planned `Jbl-Right` label), model
  `JBL Link Portable`, manufacturer `HARMAN International Industries`, Cast UUID
  `e4fcb950-f1c9-d7fc-cd42-27e6e4a4ee65`. Physical location and firmware remain
  unverified until owner observation.
- PyChromecast 14.0.10 was installed into the existing development environment,
  constrained by its app requirements. Piper 1.8.0 was installed separately under
  `$DEV_STATE_DIR/instance/announcement-speech/venv`; voice files, model cards,
  attribution and SHA-256 manifest are beside it. Both environments passed
  `pip check`. No operating-system packages were installed.
- Local synthesis only: `fi_FI-harri-medium` produced 1.71 seconds of mono,
  16-bit, 22,050 Hz audio in 3.43 seconds; `en_GB-alba-medium` produced 1.80
  seconds of the same format in 3.70 seconds. These elapsed times include CLI
  startup/model loading. Neither file was played, and chime/gap perception is
  still an owner check.
- Model-card provenance: [Harri](https://huggingface.co/rhasspy/piper-voices/blob/main/fi/fi_FI/harri/medium/MODEL_CARD)
  identifies a CC0 dataset; [Alba](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_GB/alba/medium/MODEL_CARD)
  identifies the University of Edinburgh dataset under CC BY 4.0. Both cards
  retain their Lessac fine-tuning provenance. This records source statements,
  not a general assertion about redistribution rights.
- Protected development configuration was backed up with mode 0600 before
  changes. Unrelated values were preserved. Only Piper settings, the one-speaker
  acceptance selection and LAN media origin were changed.
- Development was restarted with media on `http://192.168.1.50:5558`. The local
  login response was HTTP 200 with the right-only acceptance selection. An
  invented capability returned 404, as did a management path on the media port.
  This does not prove that the JBL can fetch authorized media.
- Discord and Spotify were not enabled. The other speaker and production were
  not controlled. No browser preview or speaker test has yet been requested.
- Result: **PASS for setup**, with browser preview and physical acceptance next.

### Step 3a — Finnish browser preview

- Recorded: 29 October 2026, approximately 18:24 EET.
- Requested action: refresh the announcement page, enter
  `Tämä on äänitesti. Yksi, kaksi, kolme.`, keep Suomi (Finnish) and chime 40%,
  then use **Preview in browser**.
- Owner response: "Yes — chime, pause and Finnish speech sound clear".
- Result: **PASS** for owner-observed Finnish browser playback, chime, audible
  pause and clarity with the requested settings. The precise gap duration,
  perceived startup latency, browser/device type and comfortable physical JBL
  volume were not reported or measured. English preview and JBL playback remain
  unverified. Earlier setup-only observations above describe their original time.

### Step 3b — English browser preview

- Recorded: 29 October 2026, approximately 18:26 EET.
- Requested action: select English, enter
  `This is an audio test. One, two, three.`, keep chime 40% and use
  **Preview in browser**.
- Owner response: "Option one all good with this. I would love if there was a
  woman audio option for finnish too".
- Result: **PASS** for the requested English browser listening check: clear
  chime, brief pause and English speech. No precise startup timing or device
  type was reported. Physical JBL playback remains unverified.

### Step 4 preparation — read-only volume baseline

- A direct read-only Cast connection to the selected UUID reported volume 30%,
  unmuted, with the receiver idle. The media controller's initial player state
  was UNKNOWN; the receiver's idle indication is recorded separately.
- Saved machine evidence: `$DEV_STATE_DIR/instance/announcement-speech/right-before-first-test.json`.
- No volume change or playback command was sent. The planned first owner test
  uses 15% announcement volume and chime 40%; actual comfort and restoration
  from 15% to the captured prior volume remain unverified.

## Requested voice enhancement

The owner requested a Finnish female voice option during Step 3b. The current
[Piper voice index](https://huggingface.co/rhasspy/piper-voices/blob/main/voices.json),
checked on 29 October 2026, lists only `fi_FI-harri-low` and
`fi_FI-harri-medium` for Finnish, both single-speaker Harri models. A Finnish
female option was not found in that standard catalog. Alternative local models
need separate suitability/license/runtime evaluation; this is not a claim that
none exist elsewhere. The request is recorded as an outstanding enhancement.
No new model, cloud service or voice-selection implementation was introduced.

## Step 4 result and restoration investigation

- Recorded: 29 October 2026, approximately 18:31 EET.
- Requested action: Finnish test text from Step 3a, JBL Right, test volume 15%,
  chime 40%, **Test on speaker**, allowing the clip to finish before changing
  volume.
- Owner response: "Yes — only JBL Right played, clear and comfortable".
- Listening result: **PASS** for physical right-speaker identity, actual playback
  through the configured LAN media path, clarity and comfort at these settings.
  This does not establish another volume setting or group playback.
- Saved attempt: `9f8943a1-76c3-4bb4-80c6-beb574bd4607`, speaker test
  `bdc9e31a-6a22-43d7-bd6c-5f73dcaaf820`, target UUID matching the pinned right
  speaker. Playback observed at 18:30:57 EET, finished at 18:31:02 EET, with
  status `finished` and no failure reason.
- Restoration evidence: `volume_before` approximately 30%, `volume_applied` 15%,
  `volume_restore=skipped_newer_intent`, and no restoration timestamp. A direct
  read-only post-test Cast connection found volume 15%, unmuted.
- Restoration result: **OPEN / not passed**. The owner was asked whether they
  changed volume or other playback during or after the test. A skip reason alone
  does not establish that owner intervention actually occurred. No automatic
  corrective volume change or additional playback was sent.
- A read-only connection captured the default media receiver app `CC1AD845`,
  with receiver-idle false and initial media state UNKNOWN/no content identity
  at 0.0, 0.1, 0.6 and 1.6 seconds after receiver readiness. No media URL or
  credential was captured. This is diagnostic evidence, not a proven cause.
- Private machine evidence: `first-speaker-test-evidence.json`,
  `right-after-first-test.json` and `right-readonly-status-timing.json` under
  `$DEV_STATE_DIR/instance/announcement-speech/`.
- The sampled retained history also contains two earlier `speaker_state_unknown`
  failures at 18:20:49 and 18:20:55 EET. Those records were preserved and are not
  reclassified by the later successful listening result.
- Stop/actions commissioning is held while this discrepancy is investigated.

### Owner clarification and isolated diagnosis

- Owner clarification: "I did only one test with 40 15 audio settings, didn't
  notice there were other one". This reports one requested listening test and no
  separate volume/playback change. The earlier failed history requests are not
  evidence of another audible clip or a second requested volume setting.
- The 30% baseline was the speaker's existing level before the test, not another
  announcement-volume setting requested from the owner. Historical records were
  preserved.
- Feedback loop: the public authenticated speaker-test API and processing
  interface, real isolated Controller/SQLite state, and the real Cast adapter
  with an external SDK/receiver double. No real speaker is used by these tests.
  `tests/test_announcement_cast_ownership.py::test_finished_speaker_test_restores_volume_after_fresh_connection`
  reproduced `skipped_newer_intent` instead of `restored` with the 30% → 15%
  scenario, while the retained adapter still confirmed playback ownership.
- Read-only hardware evidence: a media-status query was acknowledged with
  `type=MEDIA_STATUS` and an empty `status` list. PyChromecast 14.0.10 leaves a
  fresh media snapshot UNKNOWN for that response. Merely waiting or requesting
  status without interpreting the reply did not establish idle in the old probe.
- Fix in the commissioning worktree: request bounded media status for an active
  Default Media Receiver with an unknown fresh snapshot; treat its explicit
  empty-session reply as idle. Missing/unusable replies remain unknown. Unknown
  restoration evidence is recorded as unknown rather than newer owner intent.
- Ownership is rechecked after the status query, so an observed volume change
  away and back during that wait wins over restoration. Loss of volume ownership
  also prevents optional music restoration.
- All three isolated cases passed: explicit empty reply restores 30%; unanswered
  request preserves 15% with unknown outcome; owner change away/back preserves
  15% with skipped-newer-intent outcome. The related speaker/Spotify suite passed
  71 tests. Lint/format/whitespace checks passed; full-suite verification is in
  progress. No deployment or commit was performed.
- A separate invocation of the patched public Cast probe read JBL Right as idle
  at approximately 15%, without a playback or volume command. Machine evidence:
  `right-media-status-response.json` and `right-patched-probe.json` in the private
  commissioning evidence directory. This validates the read-side correction,
  not an actual post-fix volume restoration.
- Development was stopped for validation. Production remains running and
  unchanged. An owner-controlled restoration retest is required before marking
  this commissioning check passed.

### Restoration correction validation and development restart

- Completed: 29 October 2026, approximately 19:00 EET.
- Full suite in the dedicated development environment: **883 passed, 51 skipped,
  0 failed**, 32 warnings, 492.15 seconds, exit 0. Skips: 40 browser/Playwright,
  8 legacy PostgreSQL 15 fixtures, 1 firmware-toolchain check, 1 existing query-plan
  check and 1 intentional PyChromecast-absence case because the package is now
  installed. Private PostgreSQL 17 checks ran. This suite used isolated test data
  and external-boundary doubles, not the real speaker or production data.
- Focused speaker/Spotify suite: **71 passed**. Ruff lint and format checks on
  the two changed runtime modules and regression module passed; whitespace passed.
- Full log: `$DEV_STATE_DIR/instance/announcement-speech/volume-restoration-full-suite.log`.
  Runtime SHA-256 fingerprints: `volume-fix-source.json` in the same directory;
  they were rechecked before development restart and match the tested files.
- Development restarted successfully with only jbl-right selected. Production
  remained active with its unchanged 02:00:01 EET start timestamp. No release,
  production restart, database migration, automatic replay or corrective volume
  command was issued.
- Status: **software validation passed; physical restoration retest pending**.
  The original skipped-restoration attempt remains unchanged in history. The
  next owner test must deliberately establish a new normal-volume baseline;
  the failed clip must not be replayed automatically or relabelled as restored.

### Restoration hardware retest — PASS

- Recorded: 29 October 2026, approximately 19:04 EET.
- Requested action: deliberately set normal JBL Right volume to 30% in Google
  Home, then repeat the Finnish speaker test at 15%, chime 40%, and let it finish
  without intervening volume changes.
- Owner response: "Yes — played normally, Restored, and back at 30%".
- Saved test `bd74f8aa-feb0-4ad9-bb35-5b374e2badcf`, attempt
  `28e05e91-fe93-4041-9ca9-2d22fab60d80`: playback observed at 19:03:44 EET,
  finished at 19:03:49 EET, status `finished`, no failure reason, restoration
  recorded `restored` at 19:03:49 EET.
- Actual stored prior volume was 0.29866114258766174 (displayed as 30% by Google
  Home), test volume 0.15. A fresh read-only device connection after the test
  reported approximately 29.9%, unmuted, consistent with that prior level.
- Result: **PASS** for this normal-completion restoration case, supported by
  owner observation, retained attempt evidence and a separate device read.
  The original failed restoration remains retained. This does not establish
  Stop behavior or the real owner-intervention cases, which still need checks.
- Evidence files: `restoration-retest-evidence.json` and
  `right-after-restoration-retest.json` in the private commissioning directory.

### Speaker-test Stop — PASS

- Recorded: 29 October 2026, approximately 19:08 EET.
- Requested action: play the longer neutral Finnish clip on JBL Right at 15%
  test volume, chime 40%, and press **Stop** on the newest speaker-test entry
  while speech was audible.
- Owner response: "Yes — speech stopped and volume restored".
- Saved test `37b3cdaa-ec88-40cf-8949-da2ec886dbab`, attempt
  `9d182d4c-f3c3-4097-b816-78654fde4dc7`: status `stopped`, reason
  `stop_requested`, `stop_outcome=stopped`. Playback was observed at
  19:07:19 EET; Stop was requested and observed at 19:07:22 EET.
- Stored prior volume approximately 29.9%, applied volume 15%, restoration
  `restored` at 19:07:22 EET. A fresh device read reported approximately 29.9%,
  unmuted, matching the prior level.
- Result: **PASS** for owner-observed mid-speech stopping and restoration after
  this standalone speaker test. Request/observation timestamps are software
  evidence, not a precise measurement of physical acoustic stopping latency.
  Reminder acknowledgement, repeat cancellation and Stop→Snooze remain separate
  checks; this speaker test does not acknowledge a reminder.
- Evidence: `stop-test-evidence.json` and `right-after-stop-test.json` in the
  private commissioning directory.

### Owner volume change during playback — PASS

- Recorded: 29 October 2026, approximately 19:13 EET.
- Requested action: test at 15%, chime 40%, adjust volume during speech in Google
  Home, then allow playback to finish. The suggested new level was 20%.
- Initial owner response selected the successful outcome. A fresh device read
  instead showed 40.7%, so the actual chosen level was clarified before recording
  a numeric pass. Owner clarification: "I used that 40.7 during the test".
- Saved test `c9f735ba-1006-4622-bede-13fb31e3116f`, attempt
  `04acef84-5456-47cc-8184-54f9b066ae53`: observed playback at 19:10:09 EET,
  finished at 19:10:17 EET, status `finished`, no failure reason, prior volume
  approximately 29.9%, test volume 15%, `volume_restore=skipped_newer_intent`,
  no restoration timestamp. The owner saw **Left as changed by you**.
- A read-only device connection at approximately 19:11 EET reported 40.7%,
  unmuted, consistent with the owner's clarified choice. Machine evidence:
  `owner-volume-change-evidence.json` and `right-after-owner-volume-change.json`
  in the private commissioning directory.
- Result: **PASS** for preserving the actual owner-selected volume during this
  clip. This is not evidence that a 20% setting was tested. No corrective volume
  command or replay was sent in response to the clarification.
- The next check is a one-time scheduled occurrence; repeat and reminder action
  acceptance remain unverified.

### One-time scheduled reminder — PASS

- Recorded: 29 October 2026, approximately 19:17 EET.
- Requested action: save a one-time Finnish reminder for today, 2–3 minutes
  ahead in Helsinki time, JBL Right, volume 15%, chime 40%, one announcement;
  text `Tämä on ajastetun muistutuksen testi.`.
- Owner response: "Yes — correct upcoming time and one scheduled announcement".
- Schedule `717bc912-844b-4dca-9ac9-165d19b2de59`, occurrence
  `c962d6f2-1d25-4399-ac36-b5bbc697d7e6`: due 19:17:00 EET, expires
  19:32:00 EET, one announcement allowed and started, delivery `delivered`,
  no disposition/acknowledgement action recorded at inspection.
- Attempt `ac980d9c-c40f-4378-9471-a1a307f063fa`: intent at 19:17:00.904 EET,
  dispatched 19:17:01.315, playback observed 19:17:03.351, finished 19:17:11.555.
  The observed playback report is approximately 3.35 seconds after due time,
  not a microphone measurement of when sound began.
- Prior volume approximately 40.7%, applied 15%, restoration recorded `restored`
  at 19:17:12.101 EET. No failure reason. Occurrence closed after its one allowed
  announcement; successful delivery did not mark it Done.
- Result: **PASS** for upcoming-time presentation and this one-time scheduled
  delivery, supported by owner observation and saved occurrence/attempt state.
  Other browser timezones, recurring schedules and cancellation/repeat actions
  remain separate checks.
- Evidence: `one-time-reminder-evidence.json` in the private commissioning
  directory. Next action is owner acknowledgement of this same occurrence.

### Done acknowledgement — saved state confirmed

- Recorded: 29 October 2026, approximately 19:19 EET.
- Requested action: press **Done** on the 19:17 occurrence, refresh, and inspect
  Acknowledged and Delivery separately.
- The owner accidentally selected the failure response, then immediately wrote
  "oops misclick". That selection is withdrawn, not recorded as an application
  failure. The owner then confirmed: "Yes — that is what I meant", verifying
  Done persists after refresh and Delivery is still Delivered.
- A fresh Controller read of occurrence `c962d6f2-1d25-4399-ac36-b5bbc697d7e6`
  found disposition `done`, delivery still `delivered`, status `closed` and one
  announcement started. The acknowledgement action is `done`, recorded at
  19:19:22.669 EET, retaining prior delivery `delivered` and prior disposition null.
- Result: **PASS for acknowledgement/delivery separation**, supported by the
  owner's corrected browser-refresh observation and saved backend evidence.
- Evidence: `one-time-done-evidence.json` in the private commissioning directory.

### Skip cancels the waiting repeat

- Recorded: 29 October 2026, approximately 19:28 EET.
- Owner created a Once reminder due 19:26 EET, Finnish text
  `Tämä on ohitettavan muistutuksen testi.`, JBL Right, speaker volume 15%,
  chime 40%, two announcements allowed and a one-minute repeat interval.
- Owner confirmed: "Yes — Skipped persists, Delivered remains, no second
  announcement", after pressing Skip following the first clip, waiting
  90 seconds and refreshing.
- Schedule `860491f2-0d2b-4e69-b23b-93344c706078`, occurrence
  `6bb9f65b-4499-4cdb-bf27-888e9d79aa02`: status `closed`, disposition
  `skipped`, delivery `delivered`, one of two announcements started and no
  next attempt queued. Expiry was 19:41 EET.
- Skip acknowledgement at 19:26:16.592 EET retained prior status `waiting`
  and prior delivery `delivered`, confirming cancellation during the wait.
- Sole attempt `3a5ca117-4a0e-42d6-91eb-c240609c58d8`: playback observed
  19:26:07.421, finished 19:26:11.531, volume restored 19:26:12.056 EET
  from applied 15% to the prior approximately 40.7%. No failure reason.
- Result: **PASS** for this occurrence's pending-repeat cancellation,
  persisted Skip and preservation of completed delivery evidence.
- Evidence: `skip-repeat-evidence.json` in the private commissioning directory.
- Evidence correction for the earlier 19:17 one-time reminder: the initial
  instructions suggested `Tämä on ajastetun muistutuksen testi.`, but a fresh
  saved-state read shows the owner used a longer neutral Finnish sentence
  beginning `Teksti on kirjallinen tai suullinen esitys`. The scheduling,
  playback and Done results are unchanged; the suggested text above is not
  an exact transcript of that earlier occurrence.

### Five-minute Snooze and subsequent Done

- Recorded: 29 October 2026, approximately 19:35 EET.
- Owner used a Once reminder due 19:29 EET, Finnish text
  `Tämä on torkutetun muistutuksen testi.`, JBL Right, speaker volume 15%,
  chime 40% and one automatic announcement. After its first clip the owner
  selected five minutes, pressed Snooze, refreshed, waited and marked Done
  after the replay.
- Owner confirmed: "Yes — snooze persisted, silent until due, one replay,
  then Done persisted".
- Schedule `55a77042-67a4-47e6-af4e-f8ea2993d862`, occurrence
  `1bb59eb8-6f85-4339-bc7d-0be52bfcaa04`: original due 19:29:00 EET.
  Snooze recorded 19:29:21.370 shifted due to 19:34:21.370 and expiry from
  19:44:00 to 19:49:21.370, retaining the fifteen-minute delivery window.
- Original attempt `b5720fbe-f848-4d0d-9d3c-fc3ce892df69` was observed playing
  19:29:05.158 and finished 19:29:09.275 EET. The sole additional attempt,
  `17f0bafc-892c-4633-b28d-90d03e86ae18`, has purpose `snooze`, was observed
  playing 19:34:25.053 and finished 19:34:29.168 EET. Both restored the
  prior approximately 40.7% volume after playing at 15%, with no failure reason.
- Two announcements started despite an automatic limit of one: the second
  was explicitly requested by Snooze, not an automatic repeat.
- Done saved at 19:34:36.588 EET, retaining delivery `delivered`; occurrence
  is closed with disposition `done` and no next attempt queued.
- Result: **PASS** for this five-minute Snooze, silence during its delay,
  one deliberate replay, volume restoration and subsequent persisted Done.
- Evidence: `snooze-replay-evidence.json` in the private commissioning directory.

### Automatic repeat and two-announcement limit

- Recorded: 29 October 2026, approximately 19:40 EET.
- Owner confirmed: "Yes — exactly two clips, no third, Done persists and
  Delivered remains". Requested settings: Once, JBL Right, Finnish, speaker
  volume 15%, chime 40%, two announcements, interval one minute, expiry 15 minutes.
- Saved text remained `Tämä on torkutetun muistutuksen testi.` rather than the
  newly suggested repeat-test wording; this does not affect the repeat check.
- Schedule `39db85ea-5a61-41a4-bfb3-abfc83d3215b`, occurrence
  `a171ae43-36cb-4369-af96-4531712d5a76`: due 19:37 EET, two allowed and
  two started, interval one minute. It closed at 19:38:07.617, before Done,
  with no next attempt queued. Delivery remains `delivered`, disposition `done`.
- First attempt `77d8ccb9-2233-4ed1-919d-7e2c1746133b`: playback observed
  19:37:02.357, finished 19:37:06.461. Repeat attempt
  `385bee2c-8096-4419-a163-c0f39ceeccac`: playback observed 19:38:02.950,
  finished 19:38:07.068 EET. Playback reports are 60.59 seconds apart.
- Both attempts restored the prior approximately 40.7% volume after playing
  at 15%, with no failure reason. Done saved at 19:38:11.051 EET.
- Result: **PASS** for two automatic deliveries, approximate one-minute
  separation, closure at the configured limit and subsequent persisted Done.
- Limitation: Done was saved approximately four seconds after the second
  clip finished, earlier than the requested additional 90-second wait. Thus
  absence of a third clip is owner-confirmed after Done; independent saved
  evidence establishes limit closure before Done, rather than a full live
  no-action observation window beyond the second clip.
- Evidence: `automatic-repeat-evidence.json` in the private commissioning directory.

### Scheduled Stop speaking without acknowledgement

- Recorded: 29 October 2026, approximately 19:44 EET.
- Once reminder due 19:43 EET, JBL Right, Finnish, longer neutral test text,
  speaker volume 15%, chime 40%, two announcements one minute apart and expiry
  15 minutes. Owner pressed Stop speaking during the first clip and left
  Done/Skip/Snooze untouched through the requested 90-second observation.
- Owner confirmed: "Yes — stopped promptly, volume restored, no repeat,
  Done/Skip remain".
- Schedule `7ea9f0ae-533a-4ee3-af8d-665634b9e969`, occurrence
  `67001fc1-54cf-4b55-b72c-82bfb58f5638`: closed, disposition `stopped`,
  reason `stop_requested`, delivery `delivered`, one of two announcements
  started, no next attempt queued and no acknowledgement record.
- Sole attempt `e7a4c831-9f25-438d-a898-d574be0056da`: playback observed
  19:43:11.130, stop requested 19:43:19.264, stop observed 19:43:19.328 EET,
  outcome `stopped`. Approximately 64 milliseconds between saved request and
  observed stop is software evidence, not an acoustic latency measurement.
- Prior approximately 40.7% volume restored at 19:43:19.838 after applying 15%.
- Result: **PASS** for stopping this scheduled clip, cancelling its repeat,
  restoration and keeping acknowledgement separate. The availability of
  subsequent Done/Skip is owner-confirmed; Stop followed by Snooze has not
  been exercised as a combined sequence.
- Evidence: `scheduled-stop-evidence.json` in the private commissioning directory.

### Pause before due and Resume after due

- Recorded: 29 October 2026, approximately 19:48 EET.
- Owner confirmed: "Yes — Paused persisted, silent while paused and after
  late Resume". The requested check paused a new Once schedule before due,
  refreshed, waited across due, then resumed and checked for catch-up speech.
- Schedule `e1b5273e-9b02-4a90-b9d7-c461e7a80d17`: created 19:46:08.904,
  due 19:47:00, last updated on Resume 19:47:20.653 EET. Final status `active`,
  `paused_at` cleared, `next_due_at` null. No occurrence or attempt exists for
  this schedule. The saved text reused the preceding longer Stop-test wording.
- Result: **PASS** for owner-observed persisted Pause, silence across due and
  no catch-up on late Resume, supported by the final schedule and zero attempts.
- Timing limitation: Resume occurred about 21 seconds after due, rather than
  the requested full minute; the saved timestamps establish a passed due time,
  while the precise duration of each silent observation is not independently
  measured. The transient Paused UI state is owner-confirmed.
- Evidence: `pause-resume-evidence.json` in the private commissioning directory.

### Schedule deletion retains delivery and acknowledgement history

- Recorded: 29 October 2026, approximately 19:49 EET.
- Owner deleted the earlier 19:26 Skip-test schedule using Delete and Confirm
  delete, refreshed, and confirmed: "Yes — schedule removed, Skipped/Delivered
  history retained".
- Schedule `860491f2-0d2b-4e69-b23b-93344c706078` is marked `deleted` at
  19:48:36.657 EET, with no next due time.
- Occurrence `6bb9f65b-4499-4cdb-bf27-888e9d79aa02` remains closed,
  disposition `skipped`, delivery `delivered`, one announcement started.
  Its original Skip acknowledgement at 19:26:16.592 and sole finished attempt
  `3a5ca117-4a0e-42d6-91eb-c240609c58d8` remain, including restored-volume evidence.
- Result: **PASS** for hiding the deleted schedule while retaining its history.
  This does not establish explicit history deletion or restart recovery.
- Evidence: `schedule-delete-retention-evidence.json` in the private
  commissioning directory; the prior Skip evidence is retained separately.

### Explicit history deletion and cancellation

- Recorded: 29 October 2026, approximately 19:50 EET.
- Owner selected Delete history on the synthetic 19:26 Skip-test occurrence,
  pressed Cancel and confirmed it stayed present, then repeated Delete history
  with Confirm delete history and refreshed.
- Owner confirmed: "Yes — Cancel kept it; confirmation removed only that entry".
- Fresh Controller reads show occurrence `6bb9f65b-4499-4cdb-bf27-888e9d79aa02`
  absent, its acknowledgement absent, and no associated attempts remaining.
- The earlier 19:17 Done, 19:29 Snooze/Done, 19:37 repeat/Done and 19:43
  Stopped occurrences remain with their prior dispositions and delivery
  `delivered`. This independently checks that deletion did not remove those
  four neighbouring records.
- Result: **PASS** for owner cancellation and deliberate deletion of this
  terminal synthetic history entry. Its commissioning evidence remains in
  the separate private files and this record. Restart recovery and the
  active-history deletion guard remain separate acceptance checks.
- Evidence: `history-delete-evidence.json` in the private commissioning directory.

### Phone portrait layout — date/time overlap found and corrected

- Owner report: "Everything ok except the date and time inputs are overlapping
  a bit and the time input overflows on the right a bit". Initial result:
  **FAIL**, rather than acceptance of the overall mobile layout.
- Reproduced using the existing isolated announcement browser host with fake
  external integrations and a separate native Playwright Chromium driver.
  At 320 CSS pixels the date input extended to x=211 while the time input
  began at x=166: overlapping control bounds. Whole-page overflow was 16 pixels.
- Root cause: the date/time row retained two columns on phones, and native
  inputs retained intrinsic minimum widths. A separate heading minimum width
  also caused page overflow at 320 pixels.
- Fix: stack only the Once date/time row at widths up to 540 pixels; allow form
  controls and recurring time inputs to shrink within their grid tracks; wrap
  the heading at very narrow widths. Native date/time types, labels and 44-pixel
  minimum control heights remain. Bumped the stylesheet URL to `20261029-1`.
- Focused browser verification **PASS** at 320, 375, 393, 430, 768 and 1440 CSS
  pixels: editable date/time values, zero whole-page horizontal overflow,
  vertically separated date/time bounds on phones and separated columns on
  wider screens. Inspected the 393-pixel field screenshot. Browser timezone
  was America/Los_Angeles; this check does not establish timezone scheduling.
- Additional existing scheduling browser flow did **not** pass: it reached
  its acknowledgement assertion expecting `Not recorded`, while unchanged
  current JavaScript renders `Not acknowledged`. This stale text assertion is
  separate from the CSS fix; no full browser-suite success is claimed.
- Verification tooling used a temporary Python environment under `/tmp` and
  Chromium downloaded to the user browser cache; no OS packages installed.
  Private local layout script/screenshots are under
  `/tmp/announcement-layout-check-20261029` and may be cleaned automatically.
- Before dev restart: zero future active schedules, zero waiting/announcing
  occurrences and zero queued/dispatched/playing attempts. Restarted only the
  isolated development stack to refresh the cached template/asset version.
- Physical-phone fix acceptance remains **UNVERIFIED** until owner refreshes
  and checks the same controls. The original overlap report remains retained.

### iPhone Chrome retest — overlap resolved, overflow remains

- Owner retested the first phone fix: "no overlap but still overflow on the
  right on both date and time". Device/browser confirmed: "iphone with chrome".
  Result remains **FAIL** for physical-phone acceptance; passing desktop
  Chromium geometry did not establish the iOS control rendering.
- WebKit upstream bug 301648 documents iOS date/time inputs with padding
  incorrectly calculating 100% width, while desktop rendering differs:
  https://bugs.webkit.org/show_bug.cgi?id=301648 . This matches the reported
  symptom; the precise device rendering has not been reproduced locally.
- Second fix removes native date/time appearance for these controls, retaining
  their input types, explicit border-box sizing, full width, zero minimum width
  and 44-pixel minimum height; adds a maximum width and reserves a line for
  the WebKit date/time value. Applies to recurring time controls as well.
  Updated stylesheet version to `20261029-2`.
- Repeated focused desktop Chromium checks pass at 320, 375, 393, 430, 768 and
  1440 pixels with zero overflow and correct phone/wide placement. These are
  desktop checks, not an iPhone simulator or physical-device result.
- Attempted a separate Playwright WebKit launch, but the host lacks required
  browser shared libraries. No OS packages were installed and no WebKit pass
  is claimed. Browser files are in the user cache; temporary driver/host data
  remain separate from the dev and production instances.
- Restarted only the isolated dev stack to refresh the stylesheet version.
  The second fix remains **UNVERIFIED on the owner's iPhone** until retested.

### iPhone Chrome second retest — controls fit and pickers work

- Recorded: 29 October 2026, approximately 19:57 EET.
- Owner refreshed the second stylesheet correction (`20261029-2`) and confirmed:
  "Yes — both fit and both pickers work".
- Result: **PASS** for the reported phone portrait overlap/overflow defect and
  continued native date/time picker operation. The rest of the page had been
  owner-reported as okay in the initial phone check. This does not establish
  every iOS version, browser, text-size preference or landscape layout.
- No reminder was saved and no audio was requested for this retest. Both earlier
  failed observations remain in this record rather than being overwritten.

### Recurring and multiple-day checks skipped at owner request

- Owner instruction: "skip this and all multiple day checks".
- The proposed daily schedule, tomorrow cursor, and subsequent Pause check
  was not accepted as performed. All daily/selected-weekday and other checks
  requiring multiple-day behaviour remain **UNVERIFIED**, skipped by owner
  choice; software tests do not replace this omitted live acceptance.
- Continue commissioning with same-day checks only. No new recurrence test
  or future-day playback is authorized by the skipped step.

### Same-day edit changes the pending time and spoken text

- Recorded: 29 October 2026, approximately 20:05 EET.
- Owner confirmed: "Yes — old time stayed silent; edited text played once at
  the new time". Requested check saved a Once reminder, then edited both
  its pending time and text before due, refreshed, listened and marked Done.
- Schedule `7a2897e5-7523-4d94-8e16-49aa75dd54c8`: created 20:02:16.141,
  updated 20:02:47.253 EET, revision 2, text `Tämä on muokattu testi.`,
  revised due 20:05:00 EET and no next due time after delivery.
- Its sole saved occurrence `d901db85-d4b8-432f-b28a-ec046d3444d4` uses
  revision 2 and edited text; closed, delivered, Done, one announcement started
  and no next attempt queued. No occurrence from revision 1 exists.
- Sole attempt `2ae1a310-8a81-4afa-86a4-9cbb1e8495e0` uses edited text,
  playback observed 20:05:08.110, finished 20:05:11.201 EET, volume restored.
  Done recorded 20:05:21.547 EET.
- Result: **PASS** for this edit before delivery, absence of an old-revision
  attempt, one delivery of edited text at the revised time and persisted Done.
  Original due time was not captured before editing; its silence is supported
  by owner observation and absence of any revision-1 occurrence/attempt.
- Evidence: `same-day-edit-evidence.json` in the private commissioning directory.

### JBL Left acceptance authorized and prepared

- Owner replied "Approved" to enabling JBL Left alongside JBL Right, verifying
  its identity and restarting only development for individual-speaker checks.
- Read-only Cast discovery at 192.168.1.148:8009 returned name JBL Left, UUID
  `f4d7de9a-9ad4-a1e8-3478-e5dbc25e91b1`, model JBL Link Portable,
  manufacturer HARMAN International Industries. Volume approximately 21.4%,
  unmuted. No playback, volume or media-control command was sent by this probe.
- Before restart: no future active schedules, no waiting/announcing occurrences
  and no queued/dispatched/playing attempts.
- Stopped dev, backed up protected dev.env with mode 0600 in the private
  commissioning directory, added only the named left speaker with its verified
  identity to DEV_ACCEPTANCE, validated configuration and started dev.
- Running login banner independently confirms both jbl-right and jbl-left at
  their selected addresses. No Discord destination configured; groups and
  Spotify remain disabled. Production is still active with unchanged start
  timestamp 29 October 2026 02:00:01 EET.
- Result: **PASS for authorized setup and machine identity**, physical left/right
  identity, audio comfort and overlapping playback remain **UNVERIFIED**.
- Evidence: `left-preflight-evidence.json` in the private commissioning directory.

### JBL Left listening and volume restoration

- Owner response: "All ok. This is enough for left speaker only tests".
  Interpreted as confirming the requested physical left-only audio identity,
  clear/comfortable speech and restored-volume result, and ending further
  left-speaker-only checks.
- Speaker test `24f163ba-dc4d-47cc-9393-0f26de90a1e8`, attempt
  `fb3714df-867a-432e-b34c-bb03d398fcc4`, verified device UUID
  `f4d7de9a-9ad4-a1e8-3478-e5dbc25e91b1`: playback observed 20:08:59.558,
  finished 20:09:05.717 EET, restored 20:09:06.217 EET, no failure reason.
- Actual saved applied volume was **30%**, rather than the suggested 15%.
  Prior approximately 21.4% volume was restored. Owner comfort confirmation
  applies to this actual test; do not relabel it as a 15% measurement.
- Result: **PASS** for physical Left identity, audio/comfort and this normal
  restoration. No further Left-only checks will be requested. Overlapping
  individual-speaker reminders remain a separate approved two-speaker check.
- Evidence: `left-listening-evidence.json` in the private commissioning directory.

### Two individual speakers play overlapping reminders

- Recorded: 29 October 2026, approximately 20:13 EET.
- Owner confirmed: "Yes — both overlapped, finished and restored volume".
  Two Once reminders targeted different individual speakers at the same
  20:12:00 EET due time, using the longer neutral overlap-test speech.
- Right occurrence `dbeba3fd-f612-4b38-8ac1-773c88ada80e`, attempt
  `cf588b05-44e6-4c81-9496-709098954308`: playback observed 20:12:03.032,
  finished 20:12:25.194, volume restored 20:12:25.756 EET.
- Left occurrence `25cd9b3f-c873-4130-bdf9-fe1bce0b0e6f`, attempt
  `b6cf2159-b8a8-46cf-94a7-fa1ec5456bf7`: playback observed 20:12:03.032,
  finished 20:12:24.610, volume restored 20:12:25.177 EET.
- Saved playback intervals overlap approximately 21.58 seconds. Matching start
  observation timestamps are service polling evidence, not proof of acoustic
  synchronization. Both occurrences are closed/delivered, one attempt each,
  with Done acknowledgements at approximately 20:12:31 EET.
- Actual saved applied volume was **30% on both**, rather than suggested 15%.
  Right restored to approximately 40.7%, Left to approximately 21.4%.
- Result: **PASS** for overlapping independent-speaker delivery, completion,
  restoration and subsequent acknowledgement. No synchronized Cast group was
  enabled or tested. Owner requested no further Left-only tests.
- Evidence: `two-speaker-overlap-evidence.json` in the private commissioning
  directory.

### Owner checkpoint and Claude handoff

- Owner requested the handoff workflow for Claude to continue this process and
  explicitly stated: "Also discord acceptance can be skipped".
- Discord acceptance is **SKIPPED / UNVERIFIED**; do not select a channel,
  enable a webhook or send a fallback as part of continuing this session.
  The earlier multiple-day exclusion and no-more-Left-only-tests instruction
  remain in force. Other unperformed checks remain unverified.
- Checkpoint machine read: zero future active schedules, zero waiting/announcing
  occurrences, zero queued/dispatched/playing attempts. Dev remains running
  with the two authorized individual speakers; no automatic return to safe-off
  was requested. Production remains unchanged and main is clean at `162d2a6`.
- Candidate worktree remains at `e8d2bd3` with uncommitted commissioning fixes
  and this record. No commit, push, deployment, issue closure or new GitHub
  commissioning comment was performed.
- Claude continuation document is saved outside the repository in the OS
  temporary directory, per the selected handoff skill.

### Claude continuation — browser flows and hidden weekday defect

- Recorded: 29 October 2026, approximately 20:25 EET. Continued by Claude from
  the handoff; usage at start 58% five-hour, 50% weekly (Claude Pro readings).
  Runtime matched the checkpoint: dev running this worktree with both approved
  speakers; production `jannenkoti.service` active since 02:00:01 EET; main
  clean at `162d2a6`.
- Stale assertion: `tests/browser/announcements_schedule_flow.py` expected
  `Not recorded`. Commit `76db14f` deliberately replaced that fact with
  `acknowledgementText()` → `Not acknowledged`, and the sibling acknowledgement,
  snooze and history flows already expect the new text. Updated only those two
  expectations; runtime text unchanged.
- Running all eight announcement browser flows then exposed a **real UI defect**
  in `announcements_recurring_flow.py`: `.announce-weekdays { display: flex }`
  overrides the `hidden` attribute, so the Weekdays checkboxes remain visible
  for Once and Every day. The rule predates this session (merge `6814f09`);
  JavaScript sets `hidden` correctly. Fix: `.announce-weekdays[hidden]
  { display: none; }`, matching the car-heater stylesheets' convention.
  Stylesheet version bumped to `20261029-3`.
- Verification: each flow run with the existing isolated host
  (`announcements_host.py`, temporary SQLite, fake clock/Cast/synthesis/Discord,
  external HTTPS aborted) under the dedicated dev Python, and the driver under
  the temporary Playwright Chromium environment. On the final source all
  **16 runs passed** (8 flows × 393 and 1440 pixels): speaker test/stop,
  schedule, acknowledgement, history, recurring, snooze, group and Spotify.
  Every host log contains Eventlet's "monkey_patching" warning with
  application/request-context tracebacks, because the host imports Flask
  before patching; it appears in passing runs too and is harness noise.
- Limitations: the pytest wrapper `tests/test_announcements_browser.py` was not
  run as such, because no single environment has both app dependencies and
  Playwright; the host/driver split reproduces what it does. No WebKit run.
  The earlier full suite (883 passed) predates both CSS/template changes; the
  Python sources are unchanged since it ran.
- Before the dev restart: zero future active schedules, zero waiting/announcing
  occurrences and zero queued/dispatched/playing attempts (read-only Controller
  read). Restarted only dev; the banner again reports both speakers and
  `e8d2bd3 + uncommitted changes`. Production start time unchanged.
- Owner visual check: asked to refresh and switch Once, Every day and Selected
  weekdays without saving; owner replied "yes" to "hidden for Once and Every
  day, shown for Selected weekdays" (approximately 20:26 EET). Result: **PASS**
  for that phone (iPhone Chrome assumed from earlier checks; not restated).
  No reminder was saved and no audio was played.

### Active-history deletion guard

- Recorded: 29 October 2026, approximately 20:30 EET.
- Requested: Once reminder `Tämä on poistosuojan testi.`, JBL Right, 15%,
  chime 40%, two announcements two minutes apart, due 20:31; after the first
  clip, Delete history and Confirm, refresh, let the repeat play, then Done.
- Actual saved settings differed: due **20:28**, interval **1 minute**, speaker
  volume **30%**, expiry 15 minutes. The owner's answer applies to these.
- Owner confirmed: "Yes — deletion refused while waiting, entry kept, second
  clip played, Done saved".
- Schedule `3c495aed-1fc6-40f0-85f2-c7d70a30283b`, occurrence
  `38529456-3730-4c3c-89b3-6cbfadb2989b`: closed, delivery `delivered`,
  disposition `done`, two of two announcements started, no next attempt.
  Done at 20:29:17.383 EET, retaining prior status `closed` and delivery.
- First attempt `ec63b315-07c3-4e54-a975-a84d970a2c89`: playback observed
  20:28:08.221, finished 20:28:12.342. Repeat attempt
  `2488c1fd-bf5a-4fed-b317-e16bd6f166e0` (purpose `repeat`): observed
  20:29:07.733, finished 20:29:10.805. Both restored approximately 40.7%
  after applying 30%; no failure reason.
- Result: **PASS** for the owner-observed refusal while the repeat was pending,
  supported by the retained occurrence, both attempts and the Done record.
- Limitation: the refusal itself has **no server-side evidence**. The guard
  raises `history_active` without a log line and the dev log has no request
  access log, so the exact refusal time and message are owner-reported only.
  The deletion attempt must have fallen in the approximately 54-second waiting
  window (20:28:12–20:29:06). Done was saved about 6 seconds after the second
  clip finished. The entry was not deleted afterwards.
- Evidence: `active-history-guard-evidence.json` in the private commissioning
  directory (allowlisted fields; no media capabilities).

### Dev restart between clips — no replay, one repeat

- Recorded: 29 October 2026, approximately 20:35 EET.
- Requested: Once reminder `Tämä on uudelleenkäynnistystesti.`, JBL Right,
  15%, chime 40%, two announcements three minutes apart, due 20:34. A read-only
  watcher (Controller reads every second) restarted only dev, through the dev
  tool, once the first attempt had finished and restored while the occurrence
  waited for its repeat.
- Actual saved settings differed: due **20:32**, interval **1 minute**, speaker
  volume **30%**, expiry 15 minutes.
- Watcher log: first clip complete and occurrence waiting, repeat due
  20:33:04.577, at 20:32:11.542; `dev stop` finished 20:32:13.170 and
  `dev start` reported ready 20:32:15.140 (both exit 0). The banner again
  showed both approved speakers; production start time unchanged.
- Owner confirmed: "Yes — no replay after restart, exactly one repeat at
  ~20:33, Done saved".
- Schedule `023c86fe-0c57-44d8-9b16-7edca4ea3e9b`, occurrence
  `f00aab83-d3e9-47e8-bbf8-e0df4dc91751`: closed 20:33:11.698, delivery
  `delivered`, disposition `done`, two of two announcements started, exactly
  two attempts. Done at 20:33:22.303 EET.
- First attempt `31a3b2e6-5510-491d-a19d-455ffd7581cd`: observed 20:32:06.051,
  finished 20:32:10.184, restored 20:32:10.714 (before the restart). Repeat
  `e5c48903-8963-440e-b4ec-e8892dd2f016` (purpose `repeat`) requested by the
  restarted process at 20:33:05.433, observed 20:33:06.958, finished
  20:33:11.110, restored 20:33:11.686. Both: approximately 40.7% prior, 30%
  applied, no failure reason.
- Result: **PASS** for a clean dev restart while an occurrence waits: no
  replay of the delivered clip, the persisted repeat played once on schedule
  and the limit closed the occurrence.
- Limitations: this restart fell between clips. A restart **during** speech
  (in-flight attempt → `unknown`, never replayed) and a restart across an
  expiry were not exercised live. Done was saved about 11 seconds after the
  repeat, so absence of a third clip relies on the saved limit closure. Dev's
  stop/start is the development tool, not the production service restart.
- Evidence: `restart-recovery-evidence.json` and `restart-recovery-watch.log`
  in the private commissioning directory.

### Stop speaking followed by Snooze

- Recorded: 29 October 2026, approximately 20:43 EET.
- Requested: Once reminder `Tämä on pysäytyksen ja torkun testi.`, JBL Right,
  15%, chime 40%, one announcement, due 20:37; Stop speaking mid-sentence,
  then a five-minute Snooze, refresh, one replay, Done.
- Owner confirmed: "Yes — stopped, snooze accepted, one replay at ~20:42,
  Done saved".
- Saved state contains two schedules with this text, both at **30%** speaker
  volume, one announcement, expiry 15 minutes:
  - Schedule `126350ec-b4e4-45f8-b1b8-c1b4dbdfe705`, due **20:35**: its
    occurrence `14c2df9c-8b2c-4772-8df1-56b6db716095` played once to completion
    (attempt `b9c43548-6956-4e11-98b8-51aa0dc33897`, observed 20:35:05.672,
    finished 20:35:09.797, restored) and Done at 20:35:18.619, with no Stop
    or Snooze. Not reported by the owner; treated as a preliminary run and
    not counted toward this check.
  - Schedule `bfbcf6e8-f85f-48db-b504-f49de647a0fa`, due **20:36**, is the
    tested sequence below.
- Occurrence `19b86892-83e4-4c39-9e89-59648d6f1338`: attempt
  `dace376d-6371-4d97-9ba1-44bd17f1cc60` observed playing 20:36:08.231; Stop
  requested 20:36:19.298 and observed 20:36:19.399 (`stopped`); volume restored
  20:36:19.960 from 30% to approximately 40.7%.
- Snooze `84d592f3-3543-4265-9ad1-149428bd1891` at 20:36:29.203 for five
  minutes, recording **prior disposition `stopped`**. It moved due from 20:36:00
  to 20:41:29.203 and expiry from 20:51:00 to 20:56:29.203.
- Sole snooze attempt `5bdeba22-8132-4a6a-a992-c3c5d5aa8f85` (purpose
  `snooze`): observed 20:41:32.645, finished 20:41:51.131, restored
  20:41:51.614. Occurrence closed delivered with two started (one automatic
  plus one deliberate snooze replay); Done at 20:41:56.740.
- Result: **PASS** for Stop followed by Snooze. The stopped occurrence stayed
  snoozable without being acknowledged, then replayed exactly once after the
  delay, and Done persisted. Saved evidence corroborates the owner's report.
- Limitations: the actual due time and volume differed from the suggestion
  (20:36, 30%). The silence during the five-minute delay is owner-observed,
  supported by the absence of other attempts. Done came about 5 seconds after
  the replay.
- Evidence: `stop-then-snooze-evidence.json` in the private commissioning
  directory (both schedules included).

### Music wait and urgent interruption (speaker-side only)

- Owner requested "quick spotify checks" before stopping acceptance. Spotify
  **account** restoration stays blocked by the adapter; only speaker-side
  behaviour with owner-started Spotify playback on JBL Right was tested. No
  Spotify credentials were used or configured.

#### 10a — ordinary reminder waits for music — PASS

- Recorded: approximately 20:49 EET. Owner: "10a passed".
- Schedule `b2a300a6-69d9-49fe-9531-3b183c900cfc`, occurrence
  `9ae94758-61e3-4330-bb21-76e68b752437`, not urgent, due 20:47, 30%.
  Waiting history: `media_unknown` from 20:47:00.580, `music_playing` from
  20:48:21.137. Attempt `65385408-8f8f-4312-9d83-3ee0dcc8b9e5` dispatched
  20:48:27.202, observed 20:48:29.261, finished 20:48:33.370, volume restored
  to the music's prior 15%. Closed delivered; no acknowledgement saved.
- Limitation: track transitions were not observed; the exact pause time is
  owner-reported, so the debounce interval is not measured.

#### 10b — urgent reminder interrupts music — FAIL

- Recorded: approximately 20:50 EET. Owner: "configured volume was set but
  music didn't stop and no announcement".
- Schedule `3e4e1d97-cc20-49da-b356-2fd5362b4bde`, occurrence
  `2a01d3f7-ebb0-4336-82d9-f3c91fa7078d`, urgent, due 20:49, 30%. The log shows
  "interrupting media state=unknown", volume set to 0.30, dispatch at
  20:49:04.701, then attempt `d83cb982-772f-4ae9-8e73-f12fb1deab16` **failed
  `interrupted`** at 20:49:05.718 with no playback observed. `media_restore`
  is `unavailable`, `volume_restore=skipped_newer_intent`. The occurrence closed
  with delivery `failed` and fallback `failed`. The fallback attempt was
  `not_configured → unavailable` because Discord is disabled; nothing was sent.
- Read-only device read at 20:51:06 EET (no command sent): JBL Right still
  runs the Spotify Cast receiver (`CC32E753`), media paused, volume **30%**.
  The announcement volume was therefore left on the owner's music instead of
  its prior 15%.
- Hypothesis, **not confirmed**: `play_media` while the Spotify receiver app
  owns the speaker. Our media appears to have been loaded or attempted, then
  ended IDLE/INTERRUPTED before PLAYING, while Spotify kept or regained the
  receiver. The listener's idle detail is not persisted or logged, so the
  exact Cast reason is unavailable. Restoration was skipped because the
  adapter no longer owned the receiver/volume, which matches the
  ownership-safety design but leaves the owner's music louder.
- Not fixed in this session. A follow-up needs a real-speaker reproduction
  with the idle reason/app transition captured, then a decision on how urgent
  delivery should take over a foreign Cast app and whether to restore volume
  after such a failure.
- Evidence: `music-wait-evidence.json`, `urgent-interrupt-evidence.json`
  and `right-after-urgent-failure.json` (track text removed) in the private
  commissioning directory.

### Acceptance stopped at owner request

- Owner: "let's do quick spotify checks and then stop acceptance here".
  Acceptance ended at approximately 20:52 EET on 29 October 2026.
- Final machine read: zero future active schedules, zero waiting/announcing
  occurrences and zero queued/dispatched/playing attempts. Dev remains running
  with jbl-right and jbl-left selected (no safe-off requested). Production
  `jannenkoti.service` unchanged since 02:00:01 EET; main clean at `162d2a6`.
  No commit, push, release, issue comment or account effect occurred.
- **Failed, then fixed:** 10b urgent interruption of Spotify playback; see the diagnosis and retest sections below.
- **Unverified, not attempted:** quiet hours and expiry; restart during
  speech; live backup/restore; speaker media-capability expiry; startup
  latency measurement.
- **Skipped by owner:** Discord fallback (7); multiple-day/recurrence (8i).
- **Blocked by adapter:** synchronized groups (9); Spotify account restoration
  and owner-intent checks (10).
- Issues #30 and #39 remain open; acceptance decision rests with the owner.

### 10b diagnosis — load sent to the running Spotify receiver

- Owner approved diagnosis and up to three real-speaker capture runs on JBL
  Right while owner-started Spotify played (approximately 20:53–20:58 EET).
- Feedback loop: the worktree's real `PyChromecastTransport.play/poll/owns`
  against JBL Right, logging every receiver and media status. The clip was the
  existing harmless Finnish synthesis check, served by a temporary plain-Python
  HTTP server on the Pi's LAN address only for each run. No volume command
  was sent. RED = interrupted/failed without playback (the 10b symptom).
- First attempt hung before contacting the speaker: importing the app package
  applies Eventlet patching, which deadlocked an in-process HTTP server.
  Nothing played. The server was moved to a separate process.
- **Capture 1 — RED in 0.66 s.** Spotify `CC32E753` PLAYING, session 2. Our
  media appeared in **Spotify's session 2** as `IDLE/INTERRUPTED`; the receiver
  never switched apps; **the speaker never fetched the clip** (empty HTTP log).
  Ownership false, so volume restoration would be skipped.
- Cause (library code confirms): PyChromecast's `MediaController` uses
  `app_must_match=False`. If the running app accepts the media namespace,
  `play_media` sends LOAD to that app rather than launching the Default Media
  Receiver. Spotify's receiver discards the foreign load.
- **Capture 2 — GREEN.** Explicit `start_app(CC1AD845)` before the same load:
  Spotify yielded, the Default Media Receiver ran after about 1.4 s, the speaker
  fetched the clip (HTTP 200), played it to `FINISHED`, and the adapter kept
  playback and volume ownership. Rejected hypotheses: the JBL refusing the
  switch; volume ordering (capture 1 sent none); the listener misreading
  real playback (nothing was fetched).
- Fix: `PyChromecastTransport.play` launches the Default Media Receiver when
  another app is running, with a decision log; a failed launch raises
  `launch_failed` with `certain_not_played=True`, since our load was never
  sent. Regression tests replay the captured behaviour with the real adapter:
  takeover plays and keeps ownership, and a refused launch is certain-not-played.
- Verification: cast-ownership tests 8 passed; announcement and dev-acceptance
  tests **236 passed, 17 skipped**. That run also exposed four page tests
  still pinning stylesheet `20261027-10`, stale since the earlier commissioning
  CSS bumps; they now expect `20261029-3`. Ruff lint/format passed. Full suite
  log: `foreign-app-fix-full-suite.log`.
- After the captures JBL Right ran the Default Media Receiver (Spotify
  stopped by capture 2's takeover), volume approximately 3.2%. Dev was restarted
  with the fix (no active work beforehand); production unchanged.
- Owner retest of 10b with the fix: **pending**. Evidence:
  `foreign-app-capture-1.jsonl`, `foreign-app-capture-2.jsonl` and
  `foreign-app-capture-script.py` in the private commissioning directory (no
  URLs or track details).

### 10b retest with the takeover fix — PASS

- Recorded: 29 October 2026, approximately 21:13 EET. Owner: "10b passed with
  no issues" (urgent reminder while owner-started Spotify played on JBL Right).
- Schedule `c48ef037-838d-4ebd-9a21-801bf80df0d7`, occurrence
  `4188f440-bc54-4f69-929c-b79ec96b8b17`: urgent, due **21:11**, speaker volume
  30%; closed, delivery `delivered`, one announcement, no fallback.
- The app log records "launching media receiver over app CC32E753" for
  attempt `31093a86-3ebe-44f2-9154-d318be4b706d`: dispatched 21:11:03.146,
  playback observed 21:11:04.160, finished 21:11:07.243. Volume restored
  21:11:08.011 to the prior approximately 3.2% (the level the owner's music had
  after the captures). `media_restore=unavailable`: music stays stopped, as
  designed while Spotify restoration is unavailable.
- No acknowledgement is saved on the retest occurrence. The original failed
  occurrence `2a01d3f7-…` was separately marked Done at 21:00:19 EET; its
  failed delivery evidence is unchanged.
- Result: **PASS** for urgent takeover of a foreign Cast app, delivery and
  volume restoration. Spotify account restoration remains blocked/unverified.
- Evidence: `urgent-interrupt-retest-evidence.json` in the private
  commissioning directory (includes both urgent occurrences).

### Final software verification and release decision

- Full suite on the final source (takeover fix, CSS/template, test updates):
  **885 passed, 51 skipped, 0 failed**, 32 warnings, 17:27, using the dedicated
  dev environment with pytest temporary files on the root disk. Log:
  `foreign-app-fix-full-suite.log`. An earlier attempt failed only because the
  4 GB `/tmp` tmpfs filled with pytest temporary directories (5 failed, 88
  errors, all `No space left on device`); those directories were removed and
  production logged no disk errors. That log is `…-diskfull.log`.
- Owner decision (approximately 21:20 EET): release to production, shipping the
  code with the eight announcement migrations while announcements stay
  **unconfigured in production**. PyChromecast, Piper/voices, speaker targets
  and LAN media settings are a separate, later owner-approved step. Dev is
  stopped during the release to reduce memory pressure.
- Added committed synthetic rehearsal fixtures (`migrations/rehearsal.sql`,
  `migrations/rehearsal-verify.sql`). Checked on a disposable private-socket
  PostgreSQL 17 cluster: seed at `20261021_0019`, upgrade to head, verify
  passes; tampered data makes verification raise.
