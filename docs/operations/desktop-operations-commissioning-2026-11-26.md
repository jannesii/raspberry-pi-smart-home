# Desktop production inspection: commissioning, 2026-11-26

A read-only check of `scripts/ops.py` against the real Pi through `raspi`, run by
the coding agent for issue #53. It is not a deployment acceptance.

- Run from `/tmp` with the system `python3`, so no checkout directory or
  virtualenv was needed. The command came from the issue's worktree checkout.
- `status`: exit `0` in about 2 s. Current `80fcf23461c5`
  (`80fcf23461c51f0a371256b08a4221dc9c73ad46`), previous `f5beafbfa754`, last
  operation `deploy 80fcf23461c5 succeeded 2026-11-19T10:57:37`. Both units
  active. The app and gateway reported the current revision, `database ok` and
  `status healthy`; healthy `yes`.
- `logs app -n 5`, `logs gateway -n 3` and `logs services -n 4 --since
  '10 minutes ago'`: exit `0`, recent journal lines of the selected units.
- `logs release -n 2`: exit `0`. It showed the 2026-11-15 and 2026-11-19
  deployments with their steps, including the recorded pre-migration backup.
- `status --host nonexistent-raspi-alias`: exit `3`, health unknown, with the
  SSH resolution error.
- Afterwards `systemctl show` reported unchanged main PIDs and activation
  times (app since 2026-11-26 02:00:02, gateway since 2026-11-19 12:57:35).
  Nothing was restarted.

Not checked: the `--follow` stream against the real journal (covered by the
stand-in tests), and an unhealthy real production state.
