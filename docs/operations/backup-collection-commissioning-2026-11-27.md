# Off-Pi backup collection: commissioning, 2026-11-27

Commissioning of [desktop collection and coverage](recovery-backups.md#off-pi-copies-on-the-desktop)
for issue #56. The owner ran the first collection; the coding agent installed
the timer and checked the results. Nothing was written to the Pi.

## First collection

- The owner ran `ops.py collect` from the branch worktree at
  2026-11-27 20:48 UTC: `Copied backup 20261127T195923Z (167783030 bytes)`,
  in 3 s. The Pi held one completed backup.
- The copy in `/home/jannesi/jannenkoti-backups/daily/20261127T195923Z`:
  `backup.tar.gpg` mode 0600, 167,783,030 bytes. Its SHA-256 `1d4f3fbd…3b0b`
  matches the Pi's record and `sha256sum` of the Pi's artifact. The record names
  recipient `EFDD4EA872954F102064AAE981623F34A810649C`, the key from the
  [backup commissioning](recovery-backups-commissioning-2026-11-27.md).
  `copy.json` names `raspi:/home/jannesi/jannenkoti-backups/daily/20261127T195923Z`.
- `ops.py backups` then reported `coverage fresh` (exit `0`): Pi backup
  and desktop copy both `20261127T195923Z`, 0.8 h old.

## Timer

- The units were installed from the examples as user units and the timer
  enabled. Enabling the timer started a run right away. That run used the
  main checkout, which does not have `collect` until this change merges, so
  argparse rejected it (exit `2`) before it recorded anything. Hourly runs
  fail the same way until the merge. The `last_success` record still names
  the owner's run, and coverage flags it once it is 36 hours old.
- The next elapse showed as 23:00 EET (`OnCalendar=hourly`).
- One collection ran inside the user manager (`systemd-run --user`, the
  unit's settings) with the branch's `ops.py`. It reached the Pi through the
  1Password SSH agent without a terminal and reported `Desktop copies are up
  to date` (exit `0`).

## Still open

- **Isolated restore of the collected copy.** Decryption needs the owner's
  passphrase. Restore `/home/jannesi/jannenkoti-backups/daily/20261127T195923Z`
  as in [isolated restore](recovery-backups.md#isolated-restore), then add the
  `verified:` line here. The Pi-side restore of this same backup was already
  verified (56 tables, 4,208,031 rows).
- After the merge: confirm in the journal that an hourly run succeeds from the
  main checkout, and check that coverage after the 03:15 backup shows the new
  copy.
