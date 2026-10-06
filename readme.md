# raspberry-pi-smart-home

Home automation and monitoring stack built around a Raspberry Pi. The project currently focuses on the Flask + Socket.IO backend in `server/` and the companion network watcher in `device_watcher/`. Older camera capture clients remain in the repository for reference but are no longer maintained.

## Active components

### `server/`
- Flask application with Jinja UI, REST endpoints, and Socket.IO streams for live data.
- Integrations for HVAC control (Tuya/Smart Life), Philips Hue lighting, and Bambu Lab timelapse ingestion.
- SQLite persistence, Redis-backed rate limiting, and Cloudflare/Nginx deployment guides.
- ESP32 firmware for the temperature sensors (`server/ESP32_temperature/`) and car heater (`server/ESP32C3-Car-Heater/`), built with PlatformIO.
- See `server/readme.md` for detailed setup, environment variables, and deployment instructions.

### `device_watcher/`
- Monitors AdGuard Home DHCP leases to detect new devices joining the network.
- Emits notifications via pluggable notifiers or webhooks and persists state for deduplication.
- Ships with a shell probe helper and minimal Python dependencies (`requirements.txt`).
- Designed to run alongside the Flask server but can operate standalone on any Linux host.

## Quick start

1. Create and activate a Python 3.11+ virtual environment.
2. Install server requirements: `pip install -r server/requirements.txt`.
3. Export the mandatory environment variables (`SECRET_KEY`, `WEB_USERNAME`, `WEB_PASSWORD`, `DB_PATH`) and optional integrations described in `server/app/config.py`.
4. Launch the development server: `python server/run.py` and browse to `http://127.0.0.1:5555`.
5. (Optional) Configure `device_watcher/connected_devices_watcher.py` with your AdGuard Home paths and run it as a service to surface presence events inside the web UI or via your own webhook.

## Repository layout

- `server/` - live backend powering the dashboard, REST API, Socket.IO streams, and background services.
- `device_watcher/` - active network watcher.
- `client/` - deprecated Raspberry Pi timelapse capture client. Kept for historical reference only.
- `esp32_server/` - deprecated ESP32-side helper scripts. Retained for archival purposes.
- `device_watcher/requirements.txt`, `server/requirements.txt` - per-component Python dependencies.

## Deprecated components

`client/` and `esp32_server/` are no longer in use. They remain in the repository so existing installations can refer to the historical code, but new work should target `server/` and `device_watcher/`.

---

Licensed under the MIT License.
