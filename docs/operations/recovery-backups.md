# Recovery backups

The Pi takes an encrypted recovery backup of production every day at 03:15
Europe/Helsinki and keeps 14 daily copies
([ADR 0005](../adr/0005-operate-pi-production-from-desktop-development.md)).
A backup covers PostgreSQL and the configuration, secrets and persistent files
needed to rebuild the application, not only a database dump. The Pi encrypts
with a public key; only the desktop holds the private key, and the owner keeps
a recovery copy of it in 1Password. Restoration goes into isolated targets only
and is verified against what the backup recorded.

The desktop copies completed backups off the Pi hourly while it is on, so
losing the Pi or its SD card does not lose every recovery copy
([off-Pi copies](#off-pi-copies-on-the-desktop)).

**Loss target.** These are daily recovery points, not replication or
point-in-time recovery. When the desktop holds a recent copy, recovering from
it loses about a day of writes at most: everything since the newest backup
(03:15 each night). That holds only while the off-Pi copy is recent. If the
desktop is off or cannot reach the Pi, its newest copy ages while the Pi keeps
backing up on schedule: a Pi lost after the desktop was off for three days
costs about four days of writes, or everything if the desktop never copied
anything. Coverage reports say so once the newest copy holds a backup older
than 36 hours. The release pre-migration backups described in
[releases](releases.md#migration-rehearsal-and-backup) are separate and
unaffected.

[scripts/recovery_backup.py](../../scripts/recovery_backup.py) implements
`create`, `status` and `restore`; [scripts/ops.py](../../scripts/ops.py)
implements the desktop's `collect` and `backups`.

## Recovery manifest

| Resource | Method | Why |
| --- | --- | --- |
| PostgreSQL (`DATABASE_URL` in `jannenkoti.env`) | `pg_dump --format=custom` inside one exported repeatable-read snapshot, with exact per-table row counts (by table scan, like the dump, never through an index) and the Alembic revision taken in the same snapshot | All application data, including car-heater photos and encrypted Spotify tokens |
| SQLite `DB_PATH` (`/opt/jannenkoti/jannenkoti.db`) and `VILLENKOTI_DB_PATH` (`/opt/jannenkoti/villenkoti.db`) | SQLite online backup API, then `PRAGMA integrity_check` on the copy | Bootstrap still opens `DB_PATH`; legacy and Villenkoti data |
| Files in [recovery-files.txt](production-host/recovery-files.txt) | Read whole, checksummed | Application and gateway environment files (all secrets, including the Spotify token key), installed units and drop-ins, Cloudflare Tunnel configuration and credentials, tmpfiles rule, kFactor test output, the recipient public key |

Each file entry is `required` (missing or unreadable fails the backup) or
`optional` (recorded as absent). A directory includes its regular files;
symbolic links are recorded as skipped. Add a file there in the same change
that makes production depend on it.

Deliberately excluded, because they can be recreated:

- release code and virtualenvs: rebuild with a release of the recorded commit;
- the release root's pre-migration dumps, `operations.log` and jobs;
- announcement audio cache (`ANNOUNCEMENT_CACHE_DIR`) and Piper voices
  (`PIPER_VOICE_DIR`, downloadable models);
- Redis: it carries live device status, Pub/Sub and rate-limit counters,
  which devices and requests refill;
- PostgreSQL roles and cluster settings: the dump has no globals. The
  application role comes from `DATABASE_URL` and the read-only role from
  [readonly.sql](production-host/readonly.sql); the dump records ownership,
  and the isolated restore drops it (`--no-owner --no-privileges`);
- system packages, `sudo` rules, SSH keys and cron: rebuilt by host setup.

**Consistency boundaries.** The PostgreSQL dump and its row counts are one
transactionally consistent snapshot. Each SQLite copy is consistent on its own.
The resources are copied one after another within seconds, so together they
are not one atomic point in time: a write landing between two copies appears
in one resource and not the other. Configuration files are read once each.

## Archive and backup root

The backup root `/home/jannesi/jannenkoti-backups` (mode 0700) holds:

```text
daily/<id>/backup.tar.gpg   completed backups: encrypted tar archive (mode 0600)
daily/<id>/backup.json      its record: status complete, size, SHA-256, key, contents
incomplete/<id>/            the attempt in progress (plaintext staging); never a backup
status.json                 last_attempt and last_success, replaced atomically
runs.log                    one JSON line per attempt
.lock                       held while an attempt runs
```

`<id>` is the UTC start time, such as `20261128T011500Z`. The archive contains
`database/postgresql.dump`, `sqlite/<SETTING>.sqlite3`, `files/<absolute path>`
and `manifest.json`, which records every part's SHA-256, the table row counts,
the Alembic revision and the source database's identity. `backup.json` holds
no secrets or row counts, only names and checksums of the ciphertext.

A backup appears in `daily/` only when complete: the attempt builds it in
`incomplete/<id>/` and renames that directory as the last step. Plaintext
(database dump, SQLite copies) exists only in that private staging directory
for the attempt and is removed on success, failure or `SIGTERM`. After a hard
kill or power loss, the next attempt removes the leftovers first. Files are
streamed from the source straight into `gpg`, never staged.

## Retention and status

After each **successful** backup, `create` keeps the newest backup of each of
the 14 most recent days that have one, counted in Europe/Helsinki whatever the
process's time zone, and removes older ones from `daily/`. An on-demand backup
therefore replaces that day's 03:15 backup in retention. It removes nothing else: unrecognized names in
`daily/`, `incomplete/` leftovers and anything outside the backup root are left
alone. A failed attempt prunes nothing. If pruning itself fails, the new
backup still counts as the latest success, the attempt records `prune_error`,
`status` reports it and `create` exits `1`.

`status.json` is the metadata later status integration reads:

```json
{
  "last_attempt": {"id": "...", "started_at": "...", "finished_at": "...",
                    "result": "running|success|failed", "error": "...",
                    "pruned": [], "prune_error": "..."},
  "last_success": {"id": "...", "finished_at": "...", "path": "...",
                    "size": 0, "sha256": "..."}
}
```

An attempt is recorded as `running` when it starts. A failed attempt replaces
only `last_attempt`; `last_success` keeps naming the previous complete backup.
An attempt killed outright (`SIGKILL`, power loss) stays `running`; `status`
reports it as not finished once no backup holds the lock. Errors name the failing step (dump, a missing file,
encryption) without credentials or data.

```fish
ssh raspi /home/jannesi/jannenkoti-releases/current/venv/bin/python /home/jannesi/jannenkoti-releases/current/code/scripts/recovery_backup.py status
ssh raspi /home/jannesi/jannenkoti-releases/current/venv/bin/python /home/jannesi/jannenkoti-releases/current/code/scripts/recovery_backup.py status --json
```

`status` is healthy only when the latest attempt succeeded (or is still
running) and the latest success is at most 36 hours old. An unreadable record
is reported as a problem.

| Exit | Meaning |
| --- | --- |
| `0` | Done; for `status`, a fresh success |
| `1` | Failed; for `status`, failed, stale or no backup |
| `2` | Refused before any change (restore target or backup unusable) |
| `3` | Another backup operation holds the backup root |

## Key setup

The key pair is created once on the desktop. The private key never goes to
the Pi.

1. Generate an encryption key with a passphrase (GnuPG prompts for it). Save
   the passphrase in 1Password at once.

   ```fish
   gpg --quick-generate-key 'Jannenkoti recovery backup' default default never
   gpg --list-keys --with-colons 'Jannenkoti recovery backup' | grep '^fpr' | head -1
   ```

2. Export the public key and install it on the Pi:

   ```fish
   gpg --armor --export 'Jannenkoti recovery backup' > /home/jannesi/jannenkoti-recovery-public.asc
   ssh raspi install -d -m 700 /home/jannesi/.config/jannenkoti-backup
   scp /home/jannesi/jannenkoti-recovery-public.asc raspi:/home/jannesi/.config/jannenkoti-backup/recovery-public.asc
   ```

3. **Owner commissioning action:** export the private key and store it in
   1Password as a document beside the passphrase, then delete the exported
   file:

   ```fish
   gpg --armor --export-secret-keys 'Jannenkoti recovery backup' > /home/jannesi/jannenkoti-recovery-private.asc
   # Attach /home/jannesi/jannenkoti-recovery-private.asc to the 1Password item, then:
   shred -u /home/jannesi/jannenkoti-recovery-private.asc
   ```

`backup.json` records the recipient fingerprint, so you can check a backup was
made for this key. Replacing the key: install the new public key on the Pi;
older backups still need the old private key until they age out of retention.

### Retrieving the key from 1Password

When the desktop or its GnuPG keyring is lost, the 1Password recovery copy is
the only way to decrypt any backup. On the new or rebuilt desktop:

1. In 1Password, open the item that holds the recovery key (the private key
   document `jannenkoti-recovery-private.asc` and its passphrase). Download the
   document to `/home/jannesi/jannenkoti-recovery-private.asc`.
2. Import it, entering the passphrase from the same item when GnuPG asks, then
   delete the downloaded file:

   ```fish
   gpg --import /home/jannesi/jannenkoti-recovery-private.asc
   shred -u /home/jannesi/jannenkoti-recovery-private.asc
   gpg --list-secret-keys --with-colons 'Jannenkoti recovery backup' | grep '^fpr' | head -1
   ```

3. Compare the fingerprint with `encryption.recipient_fingerprint` in a
   backup's `backup.json`. The first key's fingerprint is recorded in the
   [commissioning record](recovery-backups-commissioning-2026-11-27.md).

Then restore as below, from the desktop's copies if the desktop survived, or
from the Pi's `daily/` if only the desktop was lost.

## Installing the schedule

The units are [jannenkoti-backup.service.example](systemd/jannenkoti-backup.service.example)
and [jannenkoti-backup.timer.example](systemd/jannenkoti-backup.timer.example).
The service runs `create` of the **current release** with its own virtualenv,
so the command becomes available on the Pi with the first release that contains
it, and deployments update it. Install them after that release is deployed and
the public key is in place:

```fish
set -l repo /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
ssh raspi sudo install -m 644 /dev/stdin /etc/systemd/system/jannenkoti-backup.service < "$repo/docs/operations/systemd/jannenkoti-backup.service.example"
ssh raspi sudo install -m 644 /dev/stdin /etc/systemd/system/jannenkoti-backup.timer < "$repo/docs/operations/systemd/jannenkoti-backup.timer.example"
ssh raspi sudo systemctl daemon-reload
ssh raspi sudo systemctl enable --now jannenkoti-backup.timer
ssh raspi systemctl list-timers jannenkoti-backup.timer
```

`Persistent=true` runs a missed backup after the Pi boots. Take one at once,
for example before risky maintenance, by starting the same unit; it waits
until done and fails visibly:

```fish
ssh raspi sudo systemctl start jannenkoti-backup.service
ssh raspi journalctl -u jannenkoti-backup.service -n 20
```

Backups use their own lock and root, independent of release operations and
their pre-migration backups. A second backup started while one runs exits `3`
without changing anything.

## Off-Pi copies on the desktop

`ops.py collect` copies the Pi's completed backups to the desktop over SSH
through the `raspi` alias. It only reads the Pi: it never takes the Pi's backup
lock, writes there or changes its schedule, so the Pi backs up at 03:15 whether
or not the desktop is on. The desktop copy root `/home/jannesi/jannenkoti-backups`
(mode 0700, `--copy-root` selects another) mirrors the Pi's layout:

```text
daily/<id>/backup.tar.gpg   a verified copy of the Pi's encrypted archive (mode 0600)
daily/<id>/backup.json      the Pi's record of that backup, unchanged
daily/<id>/copy.json        when and from where it was copied
incomplete/<id>/            a copy in progress; never a backup
status.json                 last_attempt and last_success of collection runs
runs.log                    one JSON line per collection run
.lock                       held while a collection runs
```

A copy directory is a complete backup for `recovery_backup.py restore`.

Each run:

1. Lists the Pi's `daily/` backups with a complete record. Attempts in the Pi's
   `incomplete/`, directories without a record and records not marked
   `complete` are never copied.
2. Selects what the retention policy keeps across the Pi's backups and the
   desktop's copies and the desktop lacks as a verified copy (size and SHA-256
   are checked again each run, so a damaged copy is copied afresh while the Pi
   still has it): the newest backup of each of the 14
   most recent Europe/Helsinki days. After an absence it therefore catches up
   on every retained day, newest first, and never copies a backup only to prune
   it.
3. Streams each artifact, at most the size its record names, into
   `incomplete/<id>/`, checks its size and SHA-256 against the Pi's record, and
   only then renames it into `daily/`, replacing a damaged copy. A cut
   connection, a timeout or a mismatch publishes nothing and removes the partial
   copy; one left by a hard kill is removed at the next run. A lost connection
   stops the run; a mismatch skips that backup and continues with the others.
4. Applies the same 14-day retention to the desktop's copies. It removes
   nothing else from the copy root.

A run that copied or checked everything records `success`; any failure records
`failed` with the reason, and the copies already made stay valid.

| Exit | Meaning |
| --- | --- |
| `0` | Every selected backup is copied, or none was needed |
| `1` | A backup could not be copied or verified, or the Pi's backup root is missing |
| `3` | The Pi was unreachable, or the connection was lost while copying |
| `5` | Another collection holds the copy root |

### Installing the desktop timer

The units are systemd *user* units:
[jannenkoti-backup-collect.service.example](systemd/jannenkoti-backup-collect.service.example)
and [jannenkoti-backup-collect.timer.example](systemd/jannenkoti-backup-collect.timer.example).
The timer runs a collection 5 minutes after the user session starts, which is
the catch-up after the desktop was off, and then every hour. The service runs
`ops.py collect` from the main desktop checkout with the system `python3`.

```fish
set -l repo /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
mkdir -p /home/jannesi/.config/systemd/user
cp "$repo/docs/operations/systemd/jannenkoti-backup-collect.service.example" /home/jannesi/.config/systemd/user/jannenkoti-backup-collect.service
cp "$repo/docs/operations/systemd/jannenkoti-backup-collect.timer.example" /home/jannesi/.config/systemd/user/jannenkoti-backup-collect.timer
systemctl --user daemon-reload
systemctl --user enable --now jannenkoti-backup-collect.timer
systemctl --user start jannenkoti-backup-collect.service
journalctl --user -u jannenkoti-backup-collect.service -n 20
```

The unit uses the same SSH configuration as your terminal. With the 1Password
SSH agent, collection works while 1Password is unlocked and has authorized the
key for `ssh`. A run while it is locked fails as unreachable, is recorded and
reported, and a later hourly run catches up. User timers run only while you
are logged in, so the desktop being on is not enough on its own.

### Coverage

```fish
jk-ops backups          # Pi backup, desktop copy, collection, problems
jk-ops backups --json
jk-ops status           # production health, then the same recovery section
```

`backups` asks the Pi's own `recovery_backup.py status` for the latest Pi
backup and attempt, and reads the desktop's copies and collection record
locally. Coverage is **fresh** only when all of these hold:

- the Pi reports healthy: its latest attempt succeeded and its latest success
  is at most 36 hours old;
- the desktop's newest copy matches its record (size and SHA-256 are checked
  again) and its **backup** is at most 36 hours old. A copy's age is the age of
  the backup it holds, not when it was copied, so copying an old backup today
  still reports old coverage;
- the latest collection run succeeded, or is still running, and a collection
  succeeded within the last 36 hours.

A damaged or partial copy is reported and skipped, and the next older verified
copy counts instead until the next collection replaces it. An unreachable Pi is reported as unknown Pi status, never
as fresh, while the desktop's own copies are still shown. `backups` exits `0`
for fresh coverage, `1` for anything needing attention and `3` when the Pi was
unreachable. `status` shows the same section, but its exit status stays
production health, because `deploy` and `recover` finish with it.

## Isolated restore

Restoring never targets production and never starts the application. It is
how a backup is proven usable, and the first step of a real recovery onto a new
host. Run it on the desktop, which holds the private key.

1. Choose a backup. Normally that is a desktop copy, which `collect` already
   verified; `jk-ops backups` names the newest. Restore straight from
   `/home/jannesi/jannenkoti-backups/daily/<id>`. Without a desktop copy, copy
   a completed backup directory from the Pi (both files):

   ```fish
   mkdir -p -m 700 /home/jannesi/jannenkoti-recovery
   scp -r raspi:/home/jannesi/jannenkoti-backups/daily/20261128T011500Z /home/jannesi/jannenkoti-recovery/
   ```

2. Create an empty database whose name starts with `recovery_test_` in the
   desktop's local PostgreSQL, owned by the development role:

   ```fish
   sudo -u postgres createdb -O jannenkoti_dev -T template0 recovery_test_20261128
   ```

3. Write the target environment file outside the repository, mode 0600. The
   marker line is required:

   ```fish
   install -m 600 /dev/null /home/jannesi/.config/jannenkoti-ops/restore.env
   printf '%s\n' 'DATABASE_URL=postgresql+psycopg://jannenkoti_dev:PASSWORD@127.0.0.1/recovery_test_20261128' 'RECOVERY_RESTORE_TARGET=isolated' > /home/jannesi/.config/jannenkoti-ops/restore.env
   ```

4. Restore and verify:

   ```fish
   set -l repo /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
   "$repo/.venv/bin/python" "$repo/scripts/recovery_backup.py" restore /home/jannesi/jannenkoti-backups/daily/20261128T011500Z --target-env /home/jannesi/.config/jannenkoti-ops/restore.env --restore-dir /home/jannesi/jannenkoti-recovery/restored-20261128
   ```

The command refuses (exit `2`, nothing changed, nothing decrypted) unless the
target file sets `RECOVERY_RESTORE_TARGET=isolated`, the URL is PostgreSQL on a
loopback address or Unix socket, the database name starts with
`recovery_test_` and is not the backed-up database recorded in `backup.json`,
and the restore directory is new or empty. It also
refuses a backup without a complete record or whose ciphertext does not match
its recorded SHA-256, such as an interrupted copy. It then decrypts with your
GnuPG keyring (`--gnupg-home` selects another), checks every part's checksum,
requires the database to be empty, runs `pg_restore --no-owner
--no-privileges`, and compares every table's row count and the Alembic
revision with the backup. It prints `verified` with the totals.

The restore directory (mode 0700) then holds `files/<original path>`,
`sqlite/<SETTING>.sqlite3`, `manifest.json` and `isolated-overrides.env`. Files
are never written to their original paths. The overrides set `APP_ENV=development`,
which keeps household effects disabled, and point `DATABASE_URL`, `DB_PATH` and
`VILLENKOTI_DB_PATH` at the restored copies. To browse the restored data in the
app, start development with a copy of your development env file whose values
are replaced by those overrides; never use the restored production
`jannenkoti.env` or `esp32_ws.env` itself on the desktop, because they enable
real integrations and the production device key. Nothing in the restore
starts the app or gateway, so the overrides are the guard for a deliberate
start. On failure the command removes what it restored (a directory you
created beforehand is kept, emptied) and leaves the target database for you
to drop.

The restored data and files include all production secrets. When finished:

```fish
sudo -u postgres dropdb recovery_test_20261128
rm -rf /home/jannesi/jannenkoti-recovery
```

A real recovery onto a replacement Pi uses the same archive: restore the
database into the new production database with `pg_restore` as its
administrator, after a separate owner decision, and put the files back from
`files/`. That is a manual, deliberate operation, not something this command does.

## Commissioning

Automated tests cannot do these. The first commissioning is recorded in
[recovery-backups-commissioning-2026-11-27.md](recovery-backups-commissioning-2026-11-27.md).

1. Generate the key pair and install the public key on the Pi.
2. Owner: store the private key and its passphrase in 1Password.
3. Deploy a release containing this command and install the units.
4. Start one backup and check `status` reports it healthy.
5. Copy that backup to the desktop and run an isolated restore; confirm
   `verified` and a plausible row total. Drop the database afterwards.
6. Next morning, confirm the 03:15 run in `status` and the journal.
7. Install the desktop timer, run one collection and check `jk-ops backups`
   reports fresh coverage. Restore the collected copy as above.

## Verification

[tests/test_recovery_backup.py](../../tests/test_recovery_backup.py) drives the
command as a subprocess with synthetic PostgreSQL rows on a disposable cluster,
SQLite files, configuration files and a throwaway GnuPG key. It encrypts,
decrypts and restores them, checking recovered rows, binary data, the Alembic
revision and required files. It covers a missing database, a failing `pg_dump`, an
unreadable key, `gpg` dying mid-stream and missing required files, each
preserving the last success without plaintext left behind; `SIGTERM` cleanup
and a `SIGKILL`ed attempt reported as not finished; killed attempts' staging;
truncated copies; unreadable status records; Helsinki retention days under
another process time zone; retention across 16 days and same-day backups, stale status, refused
production, remote, unmarked and nonempty targets, row counts that ignore stale index entries, a missing private key, the
development-mode overrides and the timer schedule (with `systemd-analyze
calendar` when available).

[tests/test_backup_collection.py](../../tests/test_backup_collection.py)
drives `ops.py collect`, `backups` and `status` as subprocesses through a
stand-in `ssh` that runs the remote scripts locally and can refuse connections
or cut a transfer off midway. One test creates a real encrypted backup,
collects it, checks that the Pi side is unchanged, and restores the desktop copy
into an isolated database. Others cover catch-up after the Pi was unreachable,
interrupted and killed copies, skipped incomplete, unfinished and corrupted
source backups, 14-day retention of desktop copies, a fresh transfer of a stale
backup, damaged and record-less copies reported and then replaced, a desktop
that stopped collecting, a failed Pi attempt, `status` integration, an
unreachable Pi, a busy copy root and the desktop timer. The real units, key,
Pi state and SSH agent are commissioned as above.
