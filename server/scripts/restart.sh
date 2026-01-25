#!/usr/bin/env bash
echo "[INFO] Restarting jannenkoti service..."
sudo systemctl restart jannenkoti

echo "[INFO] Tailing jannenkoti service logs..."
journalctl -u jannenkoti.service -f