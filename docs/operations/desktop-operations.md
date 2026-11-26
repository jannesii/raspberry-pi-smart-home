# Operating production from the desktop

Production runs on the Pi (`192.168.1.50`) from the release layout in
`/home/jannesi/jannenkoti-releases`; development runs on the desktop
([ADR 0005](../adr/0005-operate-pi-production-from-desktop-development.md)).
[scripts/ops.py](../../scripts/ops.py) is the desktop entry point for production:
it reaches the Pi through the `raspi` SSH alias and needs no production
credentials on the desktop. It currently provides status and logs; database
queries use [scripts/db_query.py](database.md#query-command).

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
function jk-ops --description 'Inspect Pi production'
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

| Exit | Meaning |
| --- | --- |
| `0` | Done; for `status`, production is healthy |
| `1` | Not healthy, health unknown, or the logs could not be read |
| `2` | Refused arguments; nothing was contacted |
| `3` | The Pi is unreachable over SSH, or the account cannot read the journal |

## Boundaries

Inspection only reads. Units are queried with an unprivileged
`systemctl is-active` and the health endpoints with `GET`. Nothing restarts a
service, deploys code, takes the release lock or writes to the database.
`release.py status` also re-evaluates retained candidates, which hashes their
files and, for rehearsed candidates, reads the production schema revision on the
Pi; `ops.py` shows only the production part. Restarts,
deployments and recovery stay with `release.py` on the Pi
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
bounded and followed logs, and journal permission failures and hints. The
stand-in `ssh` runs scripts with `sh`, not through fish. The real read-only
check against the Pi is recorded in
[the commissioning record](desktop-operations-commissioning-2026-11-26.md).
