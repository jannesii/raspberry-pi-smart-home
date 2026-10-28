# Sourced by the legacy production restart scripts before they act.
#
# They restart the production services, so they run only from the production
# checkout and only while production still runs from that checkout. Once a
# release is deployed, production restarts go through `release.py restart`,
# which verifies the running release. Development uses scripts/dev.py.
PRODUCTION_CHECKOUT=/home/jannesi/Code/server
RELEASE_ROOT="${JANNENKOTI_RELEASE_ROOT:-/home/jannesi/jannenkoti-releases}"
checkout="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [ -e "$RELEASE_ROOT/current" ]; then
  echo "[ERROR] Production runs a release from $RELEASE_ROOT, not from a checkout." >&2
  echo "        Restart and verify it with:" >&2
  echo "        .venv/bin/python scripts/release.py restart --release-root $RELEASE_ROOT" >&2
  echo "        For development use: .venv/bin/python scripts/dev.py start|stop|status" >&2
  exit 2
fi
if [ "$checkout" != "$PRODUCTION_CHECKOUT" ]; then
  echo "[ERROR] $checkout is not the production checkout ($PRODUCTION_CHECKOUT)." >&2
  echo "        Refusing to restart production services." >&2
  echo "        For development use: .venv/bin/python scripts/dev.py start|stop|status" >&2
  exit 2
fi
