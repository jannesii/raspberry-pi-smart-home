# Run the release test suite on the desktop

Development and its full test suite moved to the desktop
([ADR 0005](0005-operate-pi-production-from-desktop-development.md)), where the
suite takes minutes; on the Pi it took 13 to 25 minutes, and its temporary files
competed with production for the Pi's RAM-backed `/tmp`. A release candidate's
full-suite gate is therefore satisfied by a desktop pass for the exact commit,
and Pi preparation never runs the suite. The desktop runs the complete suite on
an export of that commit, with the checkout's virtualenv matching every pin in
the commit's `requirements.txt`, and records the pass with the commit, tree,
pins and Python version. The Pi accepts it only when commit, tree and pins match
the candidate, the Python major.minor version equals the candidate venv's, and
the run was complete with no failures or errors; it then binds the pass to the
candidate's own code and dependency identities like any other evidence. The Pi
still installs the candidate's dependencies and runs lint, readiness checks,
migration rehearsal and backup, and post-deploy health checks, and owner
acceptance is unchanged.

## Consequences

- The suite no longer exercises the Pi's Python patch version, CPU architecture
  or system libraries; those are covered by the Pi's own dependency install,
  lint, readiness checks and post-deploy health checks. Requiring the exact Pi
  patch version would block releases after every Debian Python update.
- A pass is an assertion from the owner's desktop over the authenticated SSH
  connection, like owner acceptance. It is not signed and does not expire; it
  becomes stale when the candidate's code or dependencies change.
- Preparation uses the running release's `release.py`, so the release that
  introduces this rule is prepared once with the suite on the Pi.
