# Spoken announcement operations and commissioning

Software tests use isolated databases, a controlled clock and external transport
doubles. They cannot establish audibility, group synchronization, voice quality,
physical volume ownership or delivery to an actual Discord account. The owner
commissioning record below remains **UNVERIFIED** until observed and dated.

Current session: [29 October 2026 commissioning results](spoken-announcements-commissioning-2026-10-29.md).
Development commissioning must also follow the explicit integration boundaries
in [selected announcement acceptance](development-environment.md#selected-announcement-acceptance).

## Prepare a deployment

Use [the announcement setup guide](../../readme.md#spoken-announcements) for
configuration and [optional Spotify setup](../../readme.md#optional-spotify-restoration).
Package installation, migration, configuration changes and service restarts are
separate deployment actions; this checklist does not authorize performing them.
Before enabling schedules, preserve a recoverable database backup and the current
protected environment, and rehearse restoration in an isolated environment.

- Install Piper in its separate environment and PyChromecast in the app environment
  only after deployment approval. Keep Piper's executable and both `.onnx` and
  `.onnx.json` voice files readable by the service account. Check each selected
  model's own model card/license and retain required attribution; dataset licenses
  alone do not establish all model redistribution rights. There is no automatic
  cloud speech fallback. Measure first synthesis and cached synthesis on the Pi.
- Pin actual left/right Cast UUIDs rather than trusting IP addresses or names.
  Discover the existing Google Home group, verify its UUID, leader and Cast port,
  and record member UUIDs. Spotify device IDs are separate identities and require
  an explicit match. Group playback uses one stream; the app never substitutes
  two unsynchronized individual streams when the group is unavailable.
- Set a speaker-reachable `ANNOUNCEMENT_MEDIA_ORIGIN` and authenticated browser
  `ANNOUNCEMENT_PUBLIC_ORIGIN`. Expose only the media location on the LAN proxy;
  keep management authenticated and Root-Admin protected. Suppress media path
  logging and OAuth callback query logging. Media URLs are short-lived bearer
  capabilities, not public permanent audio links; do not paste them into issues.
- Configure `ALERT_WEBHOOK_URL` through the protected server environment. The
  announcement channel uses it immediately, independently of hourly alert batches.
  Never put the webhook in screenshots or test artifacts. Real delivery requires
  a deliberate owner check; HTTP acceptance is independent of reading or Done.
- Basic delivery works without Spotify credentials. Configure the optional account,
  redirect, encryption key and exact target mapping only if restoration is wanted.
  Connecting alone leaves restoration disabled. Readiness and history expose
  absent access, API rejection and uncertain ownership separately from audio.

## Retained history and deliberate deletion

No scheduled history purge is introduced. Schedule deletion cancels future and
pending work while preserving its recorded occurrences. **Delete history** is a
separate Root-Admin, CSRF-protected operation with explicit confirmation. Delete
only records you no longer need; deleting evidence cannot undo an external message
or a health action. Done is reminder handling, never a dose or blood-pressure entry.

Active audio, waiting delivery, pending Discord retries and active restoration
prevent deletion (`409 history_active`). This includes an eligible fallback whose
first attempt has not opened yet. Done/Skip cancels pending Discord retries,
including retries after confirmed rejection; an already sending, accepted or
unknown outcome remains evidence. Let pending control settle before trying again.
Deleting an absent item is idempotent. A minimal consumed identity remains to
prevent replay, with no retained reminder text/configuration. Deletion does not
rewind schedule cursors or reset retry budgets. Speaker tests have their own
separate history; deleting one must not alter reminders or schedules.

Backups made before deletion still contain that content. Apply the owner's backup
retention policy to those copies separately; deleting the live row does not erase
an archived database. Treat dumps and restore environments as sensitive reminder
data and encrypted account credentials, with restricted access.

## Backup, restore and cache

Use a database-consistent PostgreSQL backup (`pg_dump` custom archive, including
all announcement tables) or SQLite's online backup interface for local/test data.
A live file copy of SQLite can omit committed WAL content. Store the environment
and voice configuration separately. Back up
`ANNOUNCEMENT_SPOTIFY_ENCRYPTION_KEY` securely and separately from the database;
losing/changing it requires reconnecting the optional account. Do not export
plaintext tokens. Cache files are sensitive generated speech but are not the
history store and are not needed to preserve recorded outcomes.

Restore first to isolated storage, with matching application/migration version
and outbound integrations disabled or replaced by external doubles. Never run a
rehearsal pointed at production URLs, real speakers, Spotify or the real webhook.
Verify text/config snapshots, due and actual timestamps, acknowledgements, audio,
fallback and restoration evidence independently. Check recurring cursors,
superseded/snoozed states, consumed identities and exhausted retry budgets. Only
then arrange a separately approved production cutover.

Fresh services reconcile interrupted audio/Discord requests as unknown rather
than blindly replaying/resending. A Spotify restoration lease is process-local;
a restart does not authorize restoring old playback. Confirmed pending Discord
rejections retain the original bounded budget and deadline. Repeated ticks must
not revive deleted, accepted, unknown, acknowledged or superseded work.

The audio cache is bounded to seven days/64 MB by default. Cache eviction removes
reusable audio, never occurrence evidence. New eligible work can regenerate from
its saved text/configuration; failure to regenerate is visible and may lead to
fallback. Evict only with no active playback using those files; never remove the
database or voices as a cache cleanup. Expiring media capabilities must not be
reused as a backup or replay mechanism.

## Owner commissioning record — all UNVERIFIED

Record date, app commit, Pi OS/Python/package versions, speaker firmware, target
UUIDs/group membership, voice model versions/license provenance, safe origin names
and observed results. Keep secrets and media capabilities out of this record.
For every row record PASS/FAIL/UNVERIFIED, evidence and a limitation; no blank row
means acceptance. Perform with harmless reminder text and a comfortable starting
volume. These checks require separately authorized deployment/live use.

| Owner-observed check | Status | Observation / limitation |
| --- | --- | --- |
| Left and right UUIDs/IPs identify the intended physical JBLs | UNVERIFIED | No hardware observations recorded |
| Existing group plays synchronously; absent member is represented accurately | UNVERIFIED | Cast group report does not prove per-member audibility |
| Speakers fetch audio from the configured origin; expired tokens stop working | UNVERIFIED | Browser preview cannot prove LAN media reachability |
| Finnish/English pronunciation, chime, about 0.5 s gap and first-start latency | UNVERIFIED | Local engine/model performance not measured on Pi |
| Comfortable announcement/chime volumes and prior-volume restoration | UNVERIFIED | Numeric defaults are uncalibrated |
| Stop mid-speech, then Done/Skip/Snooze without conflating acknowledgement | UNVERIFIED | Command acceptance does not prove physical silence |
| Ordinary music wait across track transitions; urgent queue/group interruption | UNVERIFIED | Real Cast/Spotify transition timing unobserved |
| Optional Spotify restore; absent credentials/rejection clearly visible | UNVERIFIED | Account access and physical playback untested |
| Owner track/seek/device/volume changes win during interruption | UNVERIFIED | Unobserved equal-volume changes cannot be proven |
| Real Discord accepted delivery, correct wording/login link, no mentions | UNVERIFIED | A transport double proves neither actual receipt nor reading |
| Phone/desktop handling and Helsinki times from another timezone | UNVERIFIED | Automated browser availability/results reported separately |
| Isolated backup restore, history deletion and no replay after restart | UNVERIFIED | Automated database evidence is separate from deployed restore |

Spec #30 and commissioning ticket #39 remain open until the required owner
observations are recorded and remaining failures/limitations are agreed.
