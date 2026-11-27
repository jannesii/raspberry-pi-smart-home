# Operating production from the desktop

Production runs on the Pi (`192.168.1.50`) from the release layout in
`/home/jannesi/jannenkoti-releases`; development runs on the desktop
([ADR 0005](../adr/0005-operate-pi-production-from-desktop-development.md)).
[scripts/ops.py](../../scripts/ops.py) is the desktop entry point for production:
it reaches the Pi through the `raspi` SSH alias and needs no production
credentials on the desktop. It shows status and logs, and releases exact
committed revisions through the Pi's existing [release process](releases.md);
database queries use [scripts/db_query.py](database.md#query-command).

## Setup

The command uses only the Python standard library and works from any directory.
It needs the `raspi` alias to log in without prompting, because it runs SSH in
batch mode. Check the alias resolves to the Pi and accepts the key:

```fish
ssh -G raspi | grep -E '^(hostname|user|identityfile) '
ssh -o BatchMode=yes raspi true
```

Save a fish function so the command is available everywhere:

```fish
function jk-ops --description 'Operate Pi production'
    python3 /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home/scripts/ops.py $argv
end
funcsave jk-ops
```

`--host` selects another SSH destination and `--release-root` another release
root on the Pi; the defaults are `raspi` and `/home/jannesi/jannenkoti-releases`.
The SSH account must be in the Pi's `adm` or `systemd-journal` group to read
logs (`jannesi` is in `adm`).

## Daily use

```fish
jk-ops status                       # releases, units, health, revisions, database
jk-ops status --json                # the same as a JSON summary
jk-ops logs app                     # last 100 lines of jannenkoti.service
jk-ops logs gateway -n 300          # esp32_ws.service
jk-ops logs services --since '1 hour ago'   # both units, interleaved
jk-ops logs app --follow            # keep printing new lines; Ctrl-C ends
jk-ops logs release -n 5            # the latest release operations and their steps
```

`status` runs the current release's own `release.py status --live --json` on the
Pi. It
shows the current and previous releases, the last deploy, recover or restart,
whether both units are active, and the revision each process reports. The app's
`/healthz` answers after a trivial database query, so `database ok` is the
database connectivity check. Production is **healthy** only when both units are
active, both health checks answer HTTP 200 with the current release's revision,
and the database and gateway report `ok` and `healthy`. Anything missing or
unreadable is listed as a problem and is never reported as healthy.

`logs` reads at most `--lines` journal lines (1 to 5000) and prints at most
`--max-bytes` (default 1 MiB), reporting a cut on stderr. `--since` takes any
`journalctl` time such as `today` or `2026-11-26 08:00`. `logs release` shows
finished operations from `operations.log` and an unfinished operation from
`operation.json`. Candidate preparation logs stay in
`releases/<candidate>/logs/` on the Pi.

## Releasing a desktop commit

Releases follow the [release guide](releases.md): preparation never changes the
running release, acceptance is the owner's statement bound to the candidate's
identities, and only `deploy` and `recover` stop and start the services. From
the desktop:

```fish
cd /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
jk-ops check main                   # optional: the full suite now, saved as a pass
jk-ops prepare main                 # or a tag, commit, or --source <worktree>
jk-ops candidate                    # all candidates and the running releases
jk-ops candidate <candidate>        # evidence and what still blocks deployment
jk-ops accept <candidate> --scope feature --note 'what you checked in development'
jk-ops deploy <candidate>           # maintenance window, then status
jk-ops recover                      # back to the previous release, then status
```

The full suite runs on the desktop, not on the Pi
([ADR 0006](../adr/0006-run-the-release-test-suite-on-the-desktop.md)).
`check` runs `scripts/test.py` on a fresh export of the exact commit, so
uncommitted and untracked files cannot affect it, with the selected checkout's
`.venv` and one worker per CPU (`--workers N` overrides). It refuses before
running when that `.venv` lacks any exact pin of the commit's
`requirements.txt`, and prints the `pip install` command that fixes it. A
complete run with no failures or errors is saved as a pass in
`~/.local/state/jannenkoti-ops/checks/<commit>.json` with the commit, tree,
pins, Python version, worker count, test counts and skipped tests; a failing run
saves nothing. Passes do not expire.

`prepare` resolves the revision in the selected checkout (default: the current
directory) and refuses, before any transfer, uncommitted changes to tracked
files, unknown or ambiguous names and revisions that track `*.env` or
`alembic.ini` files. It asks the Pi's running `release.py` whether it accepts
desktop passes, then reuses a saved pass for the commit or runs `check` first;
without a pass nothing is transferred. A running release that predates desktop
passes runs the full suite on the Pi instead, which `prepare` reports; that
happens once, for the release that introduces them. It then pushes exactly that commit, and only the Git objects
the Pi lacks, over SSH into `transfer/` in the release root, a repository without
a checkout that checks every received object. No remote such as GitHub is
fetched from or published to, and the Pi needs no GitHub credentials. The Pi
checks that the transferred commit and tree are the desktop's before the running
release's `release.py prepare` exports the commit, installs its dependencies on
the Pi, runs lint and readiness checks, and verifies and records the desktop
pass. Afterwards the candidate's recorded revision is
compared with the desktop commit, and the remaining steps are listed.
`--device-acceptance` and `--replace` are passed to `release.py prepare`.

A failed or dropped transfer prepares nothing. A failed or interrupted
preparation leaves an inspectable, never deployable candidate. Neither touches
the running release; prepare the revision again once fixed.

`accept` shows the candidate's revision and checks and records acceptance only
after you type the candidate name on your own terminal; without a terminal it
refuses. Record it only after observing that revision yourself, for a device
scope on the real device. An agent cannot accept on the owner's behalf.

A migration-bearing candidate needs its rehearsal on two empty disposable
PostgreSQL databases on the Pi, provisioned as the
[release guide](releases.md#migration-rehearsal-and-backup) describes; the
environment files are paths on the Pi:

```fish
jk-ops rehearse <candidate> --disposable-env /path/on/pi/rehearsal.env --restore-env /path/on/pi/restore-check.env
```

`deploy` refuses before any downtime when the candidate is not deployable:
missing, failed or stale checks or acceptance, unknown migration needs, a
missing or outdated rehearsal, or an incompatible database schema. Otherwise it
runs `release.py deploy` with its pre-migration backup, coordinated stop and
start of the app and gateway, and readiness wait. It then shows `status`, which
must report both processes on the candidate's revision and the database `ok`.
After a failure it prints the next steps. Database restoration is never offered:
it discards later writes and remains a separate owner decision, run with
`release.py restore` on the Pi
([explicit database restoration](releases.md#explicit-database-restoration)).

`prepare`, `rehearse`, `deploy` and `recover` run as jobs in their own session
on the Pi, with output in `jobs/` under the release root (kept 30 days). The
desktop follows the output; Ctrl-C or a lost connection only stops watching,
and a deployment never stops halfway. Check how it ended with
`jk-ops logs release` and `jk-ops status`.

| Exit | Meaning |
| --- | --- |
| `0` | Done; for `status`, production is healthy |
| `1` | Failed, not healthy, health unknown, or the logs could not be read; for `deploy`, compatible previous code was recovered |
| `2` | Refused before any change to production (by `ops.py` before contacting the Pi, or by `release.py` there) |
| `3` | The Pi is unreachable over SSH, or the account cannot read the journal |
| `4` | Production is not verified ready; follow the printed guidance |
| `5` | Another release operation holds the release root |
| `130` | Stopped watching; the operation continues on the Pi |

## Boundaries

`status`, `logs` and `candidate` only read. Units are queried with an unprivileged
`systemctl is-active` and the health endpoints with `GET`. Nothing restarts a
service, deploys code, takes the release lock or writes to the database.
`release.py status` also re-evaluates retained candidates, which hashes their
files and, for rehearsed candidates, reads the production schema revision on the
Pi; `ops.py status` shows only the production part. Release commands change
the Pi only through the running release's `release.py`, holding its lock; the
desktop sends no production credentials and none are needed. Restarts and
database restoration stay with `release.py` on the Pi
([releases](releases.md)). The command prints no environment or credentials. Log
lines are the services' own journal output.

Each command sends one fixed POSIX `sh` script on standard input to `sh -s` on
the Pi, so the Pi's fish login shell never interprets arguments (commissioned
against that shell). New desktop operations should send their scripts through
the same `Pi` class in `ops.py`: `run` bounds time and output, `stream` follows
until interrupted.

## Verification

[tests/test_desktop_operations.py](../../tests/test_desktop_operations.py) runs
the command as a subprocess from outside the checkout. A stand-in `ssh` records
its options and runs the script locally. Stand-in `systemctl`, `journalctl` and
`id`, a temporary release root and local health servers replace the Pi. It
covers healthy, mismatched, stopped, unreachable, missing and unreadable states,
bounded and followed logs, and journal permission failures and hints.
[tests/test_desktop_deployment.py](../../tests/test_desktop_deployment.py)
releases through the same stand-in `ssh`, which also serves `git push`, on the
deployment tests' world: a temporary release root whose running release
carries this repository's `release.py`, disposable PostgreSQL and a recording
service manager running stand-in units. The desktop checkout has no reachable
remote, and its `.venv` is this interpreter's. It covers desktop passes
recorded from an export, failing suites and unmatched pins, a saved pass reused
by `prepare`, an older Pi tool that runs its own suite, exact transfer and
preparation, a push cut off midway, a
Pi-side identity mismatch, interrupted preparation, unready production
settings, refusals before contact, terminal-only acceptance, stale evidence, a
missing rehearsal, a rehearsed migration deployed after its backup, deployment,
readiness failure with recovery, explicit recovery, Ctrl-C while watching and a
connection lost during the maintenance window. Backup failures and restoration
are covered by the release tests. The stand-in `ssh` runs scripts and
`git-receive-pack` with `sh`, not through fish. The real read-only check against the
Pi is recorded in
[the commissioning record](desktop-operations-commissioning-2026-11-26.md);
the real transfer and preparation are recorded separately in
[their commissioning record](desktop-release-commissioning.md). A production
release is not an automated test: it needs the owner's acceptance and decision.
