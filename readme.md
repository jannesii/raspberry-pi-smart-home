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

## Issue dependency labels

The `Synchronize unblocked issue labels` GitHub Actions workflow maintains
`unblocked` on every open issue with no open native GitHub issue blockers,
regardless of its triage labels. It removes the label from blocked or closed
issues, skips pull requests, and preserves unrelated labels. Dependency API
errors fail the run rather than treating unavailable dependencies as absent.

The workflow reacts to issue lifecycle events and reconciles all issues at
minutes 7, 22, 37, and 52 UTC each hour. Dependency additions/removals and
cross-repository blocker changes are picked up by reconciliation; scheduled
runs can be delayed. It uses the built-in `GITHUB_TOKEN` with `issues: write`
and `contents: read`; no additional secret is required for normal use.

After committing and pushing the workflow and script to the default branch,
open **Actions → Synchronize unblocked issue labels → Run workflow** once to
backfill existing issues, then check the run log. Local installation alone
does not enable the automation.

VS Code issue queries:

- Ready for an agent: `is:issue is:open label:ready-for-agent label:unblocked repo:jannesii/raspberry-pi-smart-home`
- All unblocked issues: `is:issue is:open label:unblocked repo:jannesii/raspberry-pi-smart-home`

Implementation: [workflow](.github/workflows/sync-unblocked-labels.yml) and
[Python runner](.github/scripts/sync-unblocked-labels.py). GitHub references:
[issue dependency API](https://docs.github.com/en/rest/issues/issue-dependencies)
and [workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

## Deprecated components

`client/` and `esp32_server/` are no longer in use. They remain in the repository so existing installations can refer to the historical code, but new work should target `server/` and `device_watcher/`.

---

Licensed under the MIT License.
