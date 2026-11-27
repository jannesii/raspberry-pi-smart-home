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

## Still to record

- A preparation whose required checks pass on the Pi after these fixes.
