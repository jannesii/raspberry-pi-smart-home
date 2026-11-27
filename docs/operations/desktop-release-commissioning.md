# Desktop release transfer and preparation: commissioning

The real transfer and preparation of an exact desktop commit on the Pi through
`raspi`, kept separate from automated tests and from deployment acceptance.
Preparation changes only the Pi's release root (`transfer/`, `jobs/`, a new
candidate and the retention of older inactive candidates), never the running
release, its services or the production database. Deploying a candidate still
needs the owner's acceptance and decision.

Run by the coding agent for issue #54 with the owner's go-ahead, from the
issue's worktree, with the system `python3` and `/tmp` as the working
directory.

## Procedure

```fish
cd /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
python3 scripts/ops.py status
python3 scripts/ops.py prepare HEAD
python3 scripts/ops.py candidate (git rev-parse --short=12 HEAD)
python3 scripts/ops.py status
ssh raspi git -C /home/jannesi/jannenkoti-releases/transfer remote
```

## Before

- `status`: healthy. Current `80fcf23461c5`, previous `f5beafbfa754`, last
  operation `deploy 80fcf23461c5 succeeded 2026-11-19T10:57:37`.
- `jannenkoti.service` main PID 950561 since 2026-11-26 02:00:02 EET;
  `esp32_ws.service` main PID 571050 since 2026-11-19 12:57:35 EET.
- No `transfer/` or `jobs/` in the release root.

## Run 1: 2026-11-26, `1475e9b1565f`

- Exit `1` (a required check failed), the expected outcome for a blocked
  candidate.
- The push ran `git-receive-pack` through the Pi's fish login shell and created
  `transfer/`. The Pi verified commit `1475e9b1565f346f0aeba2a36a5abc982e91cd87`
  and tree `c612b607…`. The candidate recorded that revision.
- Lint passed; tests: 3 failed, 999 passed, 53 skipped in 13 min. Two failures
  were a real defect: jobs started with `&` left SIGINT ignored for everything
  they ran, breaking signal tests during preparation (fixed in `af93d2b`). The
  third was a test expecting ssh's error text, which the Pi's git reported
  after other lines (fixed in the same commit).
- Deployment blockers also included `candidate lacks production migrations
  20261119_0028`. Production runs `80fcf23` from the unmerged branch
  `fix/car-heater-interruption-alerts`, so `main` lacks that migration. Every
  release from `main` stays blocked until that branch is merged.
- Retention removed inactive candidates `15163d016364` and `406ef7150d0b`.

## Run 2: 2026-11-27, `af93d2bb552e` with `--keep 10`

- Transfer and identity verification as in run 1 (tree `c028ca36…`); nothing
  was pruned.
- Tests: 14 failed and 139 errors, `No space left on device`. The Pi's `/tmp`
  is a 4 GiB tmpfs and pytest had kept run 1's 2.4 GiB of test files. The
  agent removed only this commissioning's test directories
  (`/tmp/pytest-of-jannesi/pytest-4` to `pytest-6`), leaving another test run's
  directory alone, and `/tmp` returned to 3% used.
- To keep it from recurring: pytest now keeps only its latest run and only
  failed tests' files (`pyproject.toml`), and
  `/etc/tmpfiles.d/jannenkoti-tests.conf` removes test directories unused for
  a day (see [releases](releases.md#test-files-in-tmp)).

## After runs 1 and 2

- `status`: healthy on `80fcf23461c5`; both processes report that revision and
  the database `ok`.
- `esp32_ws.service` kept PID 571050. `jannenkoti.service` has PID 1019071
  since 2026-11-27 02:00:02 EET, the same nightly 02:00 restart seen the day
  before; neither run started near that time.
- `transfer/` has no remote; its refs are the two transferred candidates.

## Run 3: 2026-11-27, `22b0c87a7b02` with `--keep 10`

- The owner stopped the desktop while it was watching. The detached job on the
  Pi carried on and finished, as designed.
- Lint passed; tests passed: 1003 passed, 53 skipped in 12 min 20 s. `/tmp` was
  at 2% used afterwards, so the retention settings and tmpfiles rule held.
- Remaining blockers: owner feature acceptance and the missing production
  migration `20261119_0028` (see run 1).
- The owner then decided that the full suite runs on the desktop
  ([ADR 0006](../adr/0006-run-the-release-test-suite-on-the-desktop.md)).

****## Run 4: 2026-11-27, bootstrap release `7b52cbaa04e1`

The release that introduces desktop passes, prepared once by the running
release's `release.py`. Its tree equals `main` at `f158174`, after #59 (the
production fix with migration `20261119_0028`) and #60 were merged.

- `prepare` asked the Pi's `release.py` whether it accepts desktop passes, and
  reported that it predates them; no desktop check ran.
- The Pi verified commit `7b52cbaa04e1f45df7b7302cf502f26ffff8040d` and tree
  `035aadf1…`. Lint passed; the Pi's suite passed: 1046 passed, 53 skipped in
  13 min.
- Migrations: none required, so the `20261119_0028` blocker is gone. The only
  remaining blocker is owner feature acceptance.
- Production stayed healthy on `80fcf23461c5` with unchanged PIDs (app 1019071,
  gateway 571050); `/tmp` at 2%. The app's nightly 02:00 restart comes from a
  legacy root cron entry (see the bounded car-heater notes in the readme).

## Deployment of the bootstrap release: 2026-11-27

- The owner recorded feature acceptance of `7b52cbaa04e1` ("checked the desktop
  release commands and car-heater notifications in development") and deployed
  it with `ops.py deploy`.
- The maintenance window took 2.6 s (18:33:27.8 to 18:33:30.4 UTC). `status`:
  healthy, current `7b52cbaa04e1`, previous `80fcf23461c5`, both processes on the
  new revision and the database `ok`.

## Run 5: 2026-11-27, first desktop pass, `56582a250bec`

The first preparation with the full-suite gate on the desktop
([ADR 0006](../adr/0006-run-the-release-test-suite-on-the-desktop.md)).

- The first `ops.py check` of `main` (`f158174`) found three defects before any
  pass was saved. Test databases nested under the check's export exceeded the
  107-character Unix socket limit. A timelapse test depended on test order. A
  browser capture flow advanced its fake clock while a save was in flight. All
  three were fixed in `56582a250bec`, and failed runs now keep their logs.
- `ops.py check` with 12 workers: 1099 tests, 3 skipped, 142 s, Python 3.13.15.
- `ops.py prepare` asked the running `release.py`, which accepts desktop passes,
  reused the saved pass and transferred the commit. The Pi verified commit and
  tree, accepted the pass (Pi Python 3.13.5) and ran only lint and readiness
  checks: no `tests.log`. The Pi side took 36 s, against 13 min with its own
  suite run.
- The only blocker is owner feature acceptance; this commissioning candidate
  was not deployed. Production stayed on `7b52cbaa04e1`, healthy, with the
  processes started by the deployment; `/tmp` at 2%.
