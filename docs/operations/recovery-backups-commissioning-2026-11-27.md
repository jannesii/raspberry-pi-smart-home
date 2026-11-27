# Recovery backups: commissioning, 2026-11-27

Commissioning of [recovery backups](recovery-backups.md) for issue #55, run by
the coding agent with the owner. The owner created the key, entered its
passphrase and accepted each release; the agent ran the rest.

## Keys and schedule

- The owner generated `Jannenkoti recovery backup` on the desktop (fingerprint
  `EFDD4EA872954F102064AAE981623F34A810649C`, passphrase-protected). Its public
  key is at `/home/jannesi/.config/jannenkoti-backup/recovery-public.asc` on
  the Pi; the Pi reads the same fingerprint. The owner stores the private key
  and passphrase in 1Password; the agent cannot observe that.
- `jannenkoti-backup.service` and `.timer` were installed from the examples
  and the timer enabled. `systemd-analyze verify` reported nothing. The first
  scheduled run is 2026-11-28 03:15 EET.

## Releases and findings

1. `e509d41f2bf5` (the backup command) deployed; healthy. Its first backup
   failed with `ValueError: flush of closed file`: Python 3.13.5 on the Pi
   raises it when `communicate()` flushes the closed gpg pipe, which the
   desktop's 3.13.15 tolerated. Nothing was published, no plaintext remained,
   and `status` reported the failure. The desktop `.venv` was rebuilt on
   3.13.5 with identical packages; the pre-fix tests then fail the same way.
2. `9d1789d71fbd` (pipe fix) deployed; backup `20261127T194709Z` succeeded
   (160 MB, 56 tables, 2 SQLite files, 16 files, 40 s). Its isolated restore
   failed verification: `car_heater_sensor_frame` restored 339,944 rows
   against 339,973 recorded. A table scan of production also gave 339,944;
   the primary key index returned 29 rows outside the queried id range.
3. Index check, as the PostgreSQL administrator: `amcheck`
   (`bt_index_check` with heap verification, in a transaction rolled back so
   the extension was not left installed) passed all 98 btree indexes. A
   read-only comparison of full index scans with table scans found stale
   entries in `car_heater_sensor_frame_pkey` and
   `idx_car_heater_sensor_frame_received_at` (29 each) and
   `car_heater_status_pkey` (60); 93 indexes matched and 2 could not be
   compared (the planner would not use them). `fsync`, `full_page_writes` and
   `synchronous_commit` are on; the kernel log since the 2026-11-09 boot shows
   no storage errors. The pattern fits lost index writes, for example on the
   SD card; watch for recurrence.
4. With the owner's authorization the three indexes were rebuilt with
   `REINDEX INDEX CONCURRENTLY` (1 s, 6 s, 2 s; services stayed healthy).
   Afterwards index and table scans agreed: 340,067 and 764,400 rows.
5. `feed74e03c75` (row counts by table scan) deployed; healthy. Backup
   `20261127T195923Z` succeeded; retention pruned the same day's earlier
   backup, as designed.

## Verified restore

Backup `20261127T195923Z` was copied to the desktop and restored into the
empty local database `recovery_test_20261127` and a new directory:

- `verified: 56 tables, 4208031 rows, Alembic ['20261119_0028'], 2 SQLite
  file(s), 16 file(s) with matching checksums`; exit `0`.
- Both SQLite copies passed `PRAGMA integrity_check`. The restore directory was
  mode 0700 and `isolated-overrides.env` mode 0600, with `APP_ENV=development`
  and the restored copies' paths. Nothing was written to original paths, and
  the app was not started.
- The restore database, the restored files and the target file were removed
  afterwards.

Still open: the first scheduled 03:15 run (check `status` and
`journalctl -u jannenkoti-backup.service` on 2026-11-28), and off-Pi collection
(#56). Until then the only copies are on the Pi.
