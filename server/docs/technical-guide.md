# Technical Guide

Technical documentation for the Jannenkoti Flask server.

---

## Directory Layout

```text
server/
├── app/
│   ├── __init__.py          # create_app(); config; extension init; registers blueprints
│   ├── extensions.py        # Limiter, CSRF, LoginManager, SocketIO singletons
│   ├── config.py            # Centralized environment settings
│   ├── security.py          # Rate limiting setup + whitelist filter
│   ├── assets.py            # Template asset registry helper
│   ├── blueprints/          # Flask blueprints grouped by area
│   │   ├── __init__.py      # register_blueprints(app)
│   │   ├── auth/            # login/logout, user loader, rate‑limit handler
│   │   ├── web/             # protected HTML routes
│   │   └── api/             # JSON API routes
│   ├── sockets/
│   │   └── handlers.py      # Socket.IO event handlers (views/clients/esp32)
│   ├── services/
│   │   ├── ac/              # Tuya AC controller and thermostat loop
│   │   ├── bmp_sensor/      # BMP pressure sensor thread
│   │   ├── car_heater/      # Shelly-based car heater control + kFactor calibration
│   │   ├── dhcp/            # DHCP lease monitoring
│   │   ├── hue/             # Philips Hue time-based routine
│   │   ├── novpn/           # NoVPN device bypass configuration
│   │   ├── sodexo/          # Sodexo lunch menu Discord webhook
│   │   ├── weather/         # FMI weather data service
│   │   └── ynab/            # YNAB categorizer client + suggestion service
│   ├── core/
│   │   ├── controller.py    # Business logic + DB gateway
│   │   ├── database.py      # Thread‑safe SQLite wrapper
│   │   └── models.py        # Dataclass DTOs and config models
│   ├── utils.py             # Web helpers, flashes, validation
│   ├── static/              # CSS & JS assets
│   └── templates/           # Jinja2 templates
├── run.py                   # Development entry‑point (debug server)
├── requirements.txt         # Python deps
├── AGENTS.md                # AI development guide
└── readme.md                # Feature overview
```

---

## Requirements

- Python 3.11+
- Eventlet
- Redis (for Flask‑Limiter storage and Socket.IO message queue)

Install deps:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration

All settings are read from environment variables (see `app/config.py`).

### Required Variables

```bash
export SECRET_KEY="$(openssl rand -hex 32)"
export DB_PATH="/path/to/database.db"
```

### Optional Variables

```bash
# Initial admin user (created on first run)
export WEB_USERNAME=admin
export WEB_PASSWORD=change-me

# WebSocket origins
export ALLOWED_WS_ORIGINS='["http://127.0.0.1:5555"]'

# Rate limiting
export RATE_LIMIT_WHITELIST='["127.0.0.1","192.168.10.0/24"]'
export RATE_LIMIT_STORAGE_URI="redis://localhost:6379"
```

### Integration Variables

**Tuya AC (cloud)**:
- `TUYA_ACCESS_ID`, `TUYA_ACCESS_KEY`, `TUYA_API_ENDPOINT`
- `TUYA_USERNAME`, `TUYA_PASSWORD`, `TUYA_COUNTRY_CODE`, `TUYA_SCHEMA`, `TUYA_DEVICE_ID`

**Philips Hue**:
- `HUE_BRIDGE_IP`, `HUE_USERNAME`

**Car Heater (Shelly)**:
- Configured via web UI settings

**Weather (FMI)**:
- Uses Finnish Meteorological Institute open data (no API key required)

**Sodexo Discord Webhook**:
- `SODEXO_WEBHOOK_URL`

**YNAB Categorizer**:
- `YNAB_API_KEY`, `YNAB_BUDGET_ID`
- Optional: `YNAB_HTTP_TIMEOUT_S`, `YNAB_HTTP_RETRIES`

---

## Quick Start (Development)

```bash
source .venv/bin/activate
export SECRET_KEY=dev
export WEB_USERNAME=admin WEB_PASSWORD=admin
export DB_PATH=/tmp/jannenkoti.db
python run.py
```

Browse http://127.0.0.1:5555 and log in with the credentials above.

> **Note**: Session cookies use the Secure flag. Some browsers may not send Secure cookies over plain HTTP on localhost. Running behind TLS (e.g., via Nginx) avoids this.

---

## Production Deployment

### 1. Gunicorn Service

Create `/etc/systemd/system/jannenkoti.service`:

```ini
[Unit]
Description=Jannenkoti home automation server
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/Code/server
EnvironmentFile=/home/pi/Code/server/jannenkoti.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/pi/Code/server/.venv/bin/gunicorn \
          --worker-class eventlet \
          --workers 1 \
          --bind 127.0.0.1:5555 \
          --access-logfile - \
          --error-logfile - \
          run:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jannenkoti
```

### 2. Nginx Reverse Proxy with HTTPS

```nginx
server {
    listen 80;
    server_name jannenkoti.com www.jannenkoti.com;

    location /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
    }

    location / {
        proxy_pass              http://127.0.0.1:5555;
        proxy_set_header Host   $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version      1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Run **Certbot** once, then replace port 80 listener with the `listen 443 ssl;` block Certbot inserts.

### 3. Cloudflare Tunnel (No Port Forwarding)

Install `cloudflared` and create `~/.cloudflared/config.yml`:

```yaml
tunnel: d5768148-your-uuid
credentials-file: /home/pi/.cloudflared/d5768148.json

ingress:
  - hostname: jannenkoti.com
    service: http://localhost:5555
  - hostname: www.jannenkoti.com
    service: http://localhost:5555
  - service: http_status:404
```

Enable the service:
```bash
sudo systemctl enable --now cloudflared
```

### End-to-End Flow

```
Browser → Cloudflare edge → cloudflared tunnel → Nginx (TLS offload) → Gunicorn → Flask / Socket.IO
```

---

## HTTP API

All routes require authentication (session cookie or API key).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/temphum?date=YYYY-MM-DD` | GET | Raspberry Pi sensor readings |
| `/api/esp32_temphum?date=YYYY-MM-DD&location=<name>` | GET | ESP32 sensor readings |
| `/api/timelapse_config` | GET | Current timelapse configuration |
| `/api/gcode` | GET | Queued/submitted G-code commands |
| `/api/previewJpg` | GET | Serves `/tmp/preview.jpg` |
| `/api/ac/status` | GET | Current AC status |
| `/api/hvac/avg_rates_today` | GET | Cooling/heating rates (°C/h and W) |
| `/api/car_heater/status` | GET | Car heater status |
| `/api/bmp` | GET | BMP pressure sensor data |
| `/api/ynab-categorizer/queue` | GET | Grouped uncategorized YNAB queue (Root-Admin) |
| `/api/ynab-categorizer/apply` | POST | Apply category to selected transaction IDs (Root-Admin, CSRF) |
| `/api/ynab-categorizer/bootstrap` | POST | Seed local payee-category stats from 24-month history |
| `/live/<path:filename>` | GET | HLS assets from `/srv/hls` |

---

## Socket.IO Events

### Client → Server

| Event | Payload | Description |
|-------|---------|-------------|
| `image` | `{ image: <base64> }` | Camera frame upload |
| `esp32_temphum` | `{ location, temperature_c, humidity_pct }` | ESP32 sensor data |
| `status` | `{ status: str }` | Printer status update |
| `printerAction` | `{ action: 'pause'\|'resume'\|'stop'\|'home'\|... }` | Printer control |
| `ac_control` | `{ action: 'power_on'\|'power_off'\|'set_setpoint'\|... }` | AC control |
| `car_heater_control` | `{ action: 'turn_on'\|'turn_off'\|... }` | Heater control |
| `keep_at_temp_settings` | `{ enabled, target_temp_c, hysteresis_c }` | Keep-at-temp settings |
| `ready_by_schedule` | `{ ready_by_ts, target_temp_c }` | Ready-by scheduling |
| `kfactor_control` | `{ action: 'start'\|'stop'\|'save'\|... }` | kFactor calibration |

### Server → Client

| Event | Payload | Description |
|-------|---------|-------------|
| `image` | `{}` | New frame available |
| `esp32_temphum` | `{ location, temperature, humidity, ac_on }` | Sensor broadcast |
| `car_heater_status` | `{ status, weather, command_status }` | Heater status |
| `kfactor_extended_data` | `{ live_session, recent_sessions, bucket_coverage, ... }` | Calibration data |
| `ac_status` | `{ power, mode, setpoint, ... }` | AC status |
| `thermostat_status` | `{ enabled, setpoint, ... }` | Thermostat state |
| `flash` | `{ category, message }` | Flash message |

---

## Database Schema

SQLite file path is controlled by `DB_PATH`. Tables include:

### Authentication
- `users` — accounts (admin/root-admin flags; temporary expiry)
- `api_keys` — API key management

### Sensors
- `temphum` — Raspberry Pi temperature/humidity
- `esp32_temperature_humidity` — ESP32 sensors with location and AC state
- `bmp_pressure` — Barometric pressure readings

### Car Heater
- `car_heater_status` — Status history
- `car_heater_events` — Event log
- `car_heater_kfactor_sessions` — Calibration sessions
- `car_heater_kfactor_results` — Calibration results
- `car_heater_kfactor_bucket_params` — Temperature bucket parameters
- `car_heater_kfactor_active_params` — Active kFactor parameters

### 3D Printer
- `status` — Printer status messages
- `images` — Camera frames (pruned)
- `timelapse_conf` — Capture intervals

### AC / Thermostat
- `thermostat_conf` — Thermostat settings
- `ac_events` — AC on/off transitions

### Logs
- `application_logs` — Application error logs

---

## Troubleshooting

```bash
# Tail live logs
journalctl -u jannenkoti -f --no-pager

# Open interactive Python shell with app context
python - <<'PY'
from app import create_app
app, socketio = create_app()
ctx = app.app_context(); ctx.push()
print(app.ctrl.get_all_users())
PY
```

---

*See [AGENTS.md](../AGENTS.md) for AI development guidelines.*
