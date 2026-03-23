#!/usr/bin/env bash
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

echo "[INFO] Tailing jannenkoti service logs..."
journalctl -u jannenkoti.service -f
