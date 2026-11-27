# Recovery backups

The Pi takes an encrypted recovery backup of production every day at 03:15
Europe/Helsinki and keeps 14 daily copies
([ADR 0005](../adr/0005-operate-pi-production-from-desktop-development.md)).
A backup covers PostgreSQL and the configuration, secrets and persistent files
needed to rebuild the application, not only a database dump. The Pi encrypts
with a public key; only the desktop holds the private key, and the owner keeps
a recovery copy of it in 1Password. Restoration goes into isolated targets only
and is verified against what the backup recorded.

These are daily recovery points, not replication: expect to lose up to about a
day of writes. Copies stay on the Pi until off-Pi collection runs, so an SD
card failure can still lose them. The release pre-migration backups described
in [releases](releases.md#migration-rehearsal-and-backup) are separate and
unaffected.

[scripts/recovery_backup.py](../../scripts/recovery_backup.py) implements
`create`, `status` and `restore`.

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

To recover on a new desktop, download the document from 1Password and import
it with `gpg --import`, entering the passphrase. `backup.json` records the
recipient fingerprint, so you can check a backup was made for this key.
Replacing the key: install the new public key on the Pi; older backups still
need the old private key until they age out of retention.

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

## Isolated restore

Restoring never targets production and never starts the application. It is
how a backup is proven usable, and the first step of a real recovery onto a new
host. Run it on the desktop, which holds the private key.

1. Copy a completed backup directory from the Pi (both files):

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
   "$repo/.venv/bin/python" "$repo/scripts/recovery_backup.py" restore /home/jannesi/jannenkoti-recovery/20261128T011500Z --target-env /home/jannesi/.config/jannenkoti-ops/restore.env --restore-dir /home/jannesi/jannenkoti-recovery/restored-20261128
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

Automated tests cannot do these; record them separately when done:

1. Generate the key pair and install the public key on the Pi.
2. Owner: store the private key and its passphrase in 1Password.
3. Deploy a release containing this command and install the units.
4. Start one backup and check `status` reports it healthy.
5. Copy that backup to the desktop and run an isolated restore; confirm
   `verified` and a plausible row total. Drop the database afterwards.
6. Next morning, confirm the 03:15 run in `status` and the journal.

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
calendar` when available). The real units, key and Pi state are commissioned
as above.
