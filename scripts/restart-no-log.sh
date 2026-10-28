#!/usr/bin/env bash
# Restarts the production services. Refuse from any other checkout so a
# worktree never falls through to restarting production with production code.
PRODUCTION_CHECKOUT=/home/jannesi/Code/server
checkout="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [ "$checkout" != "$PRODUCTION_CHECKOUT" ]; then
  echo "[ERROR] This restarts production and only runs from $PRODUCTION_CHECKOUT." >&2
  echo "[ERROR] For development use: .venv/bin/python scripts/dev.py start|stop|status" >&2
  exit 2
fi

echo "[INFO] Activating venv..."
source /home/jannesi/Code/server/.venv/bin/activate

echo "[INFO] Running pre-commit checks..."
pre-commit run --all-files
pre_commit_status=$?
if [ $pre_commit_status -ne 0 ]; then
  echo "[WARN] pre-commit failed. Retrying..."
  pre-commit run --all-files
  pre_commit_status=$?
  if [ $pre_commit_status -ne 0 ]; then
    echo "[ERROR] pre-commit failed twice. Aborting restart."
    exit $pre_commit_status
  fi
fi

echo "[INFO] Restarting jannenkoti service..."
sudo systemctl restart jannenkoti

echo "[INFO] Restarting esp32_ws service..."
sudo systemctl restart esp32_ws
