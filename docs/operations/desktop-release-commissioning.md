# Desktop release transfer and preparation: commissioning

The real transfer and preparation of an exact desktop commit on the Pi through
`raspi`, kept separate from automated tests and from deployment acceptance.
Preparation changes only the Pi's release root (`transfer/`, `jobs/`, a new
candidate and the retention of older inactive candidates), never the running
release, its services or the production database. Deploying a candidate still
needs the owner's acceptance and decision.

## Status

Not yet commissioned. It needs the owner's go-ahead because preparation writes
to the production host, runs the full test suite there and prunes inactive
candidates beyond the newest three.

## Procedure

From the desktop, with a committed revision:

```fish
cd /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
python3 scripts/ops.py status
python3 scripts/ops.py prepare HEAD
python3 scripts/ops.py candidate (git rev-parse --short=12 HEAD)
python3 scripts/ops.py status
```

Record:

- the prepared candidate, its revision and the desktop commit, which must match;
- the `transfer/` repository and job log created under the release root;
- the check results and what remains before deployment;
- that `status` before and after shows the same current release, and that
  `systemctl show` reports unchanged main PIDs for both units;
- that the Pi's fish login shell ran `git-receive-pack` for the push, which
  the automated tests run with `sh`;
- that the transfer repository has no remote such as GitHub; this prints
  nothing:

  ```fish
  ssh raspi git -C /home/jannesi/jannenkoti-releases/transfer remote
  ```

A real deployment from the desktop is recorded with the release it deploys, not
here.
