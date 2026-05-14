# AGENTS.md - AI Development Guide

## Quick Reference

```
STACK: Python 3.11 | Flask | Flask-SocketIO | Eventlet | SQLAlchemy Core | PostgreSQL | SQLite (tests only) | Alembic | Jinja2
FRONTEND: Vanilla JS (ES6+) | CSS3 Grid/Flexbox | No build tools
REALTIME: Socket.IO (views/clients/esp32 roles)
AUTH: Flask-Login + session cookies | CSRF protection
PATTERNS: Singleton services | Dataclass DTOs | Blueprint organization
```

---

## 0. Mandatory Rules (READ FIRST)

**ALWAYS DO THESE:**

1. **Update AGENTS.md** when you discover new critical patterns, pitfalls, or when existing instructions become outdated.
2. **Update readme.md** after every feature change. Keep it concise—don't clutter with implementation details.
3. **Add debug logging where it makes sense** to functions/methods you touch. Use `logger.debug()` for key control flow, inputs/outputs, and failure paths, but **avoid spammy logging** (tight loops, per-item processing, high-frequency or hot paths). Prefer concise, actionable debug statements.

---

## 1. Architecture Overview

### 1.1 Layer Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│  TEMPLATES (Jinja2)         app/templates/*.html            │
│  STATIC ASSETS              app/static/{css,js}/*           │
├─────────────────────────────────────────────────────────────┤
│  BLUEPRINTS                 app/blueprints/                 │
│    ├── web/     → HTML routes (login_required)              │
│    ├── api/     → JSON endpoints (CSRF exempt, API keys)    │
│    └── auth/    → Login/logout, user loader                 │
├─────────────────────────────────────────────────────────────┤
│  SOCKETS                    app/sockets/handlers.py         │
│    → Real-time events, role-based client tracking           │
├─────────────────────────────────────────────────────────────┤
│  SERVICES                   app/services/*/                 │
│    → Background threads, external integrations              │
├─────────────────────────────────────────────────────────────┤
│  CORE                       app/core/                       │
│    ├── controller.py  → Business logic facade               │
│    ├── schema.py      → SQLAlchemy Core table definitions   │
│    ├── database.py    → Legacy SQLite (migration only)      │
│    └── models.py      → Dataclass DTOs                      │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Request Flow

```
Browser → Flask Route → Controller → Database
              ↓              ↓
         Template      Service (optional)
              ↓              ↓
         HTML/JSON ← Socket.IO broadcast
```

---

## 2. Backend Conventions

### 2.1 File Organization

| Path | Purpose | Import Pattern |
|------|---------|----------------|
| `app/blueprints/web/` | HTML page routes | `from ...core import Controller` |
| `app/blueprints/api/` | JSON API endpoints | `from ...utils import get_ctrl` |
| `app/services/*/` | Background services | `from ...core import <models>` |
| `app/core/controller.py` | All DB operations | SQLAlchemy Core via `self.sa_engine` |
| `app/core/models.py` | Dataclass definitions | No imports from app |
| `app/core/schema.py` | SQLAlchemy Core table definitions | Primary schema source |
| `app/core/database.py` | Legacy SQLite DatabaseManager | Migration methods only |
| `migrations/` | Alembic migrations | Target Postgres via `DATABASE_URL` |

### 2.2 Controller Pattern

**ALWAYS** use Controller for database operations:

```python
# ✓ CORRECT - Use controller
from ...utils import get_ctrl
ctrl = get_ctrl()
data = ctrl.get_some_data()

# ✗ WRONG - Direct database access in routes
from ...core.database import DatabaseManager
db = DatabaseManager(path)  # Never do this
```

### 2.3 Dataclass Models

All data transfer uses `@dataclass` from `app/core/models.py`:

```python
@dataclass
class SomeStatus:
    field_a: str
    field_b: float | None = None
    timestamp: datetime | None = None

# Convert to dict for JSON/socket
from dataclasses import asdict
payload = asdict(status_obj)
```

**CRITICAL**: Field names in dataclass = field names in JSON/socket payloads.

### 2.4 Service Pattern

Services are singletons attached to `current_app`:

```python
# In __init__.py or bootstrap
app.some_service = SomeService(ctrl)

# In routes/handlers
svc = getattr(current_app, "some_service", None)
if svc:
    result = svc.do_something()
```

### 2.5 Logging

**RULE: Add debug logging to each function you modify where it is useful and non-spammy.**

```python
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Default level

# Use structured logging
logger.info("Action completed: %s", action_name)
logger.debug("Debug data: %s", data_dict)
logger.exception("Error occurred: %s", error)  # Auto-includes traceback

# ✓ CORRECT - Verbose debug logging
def process_data(self, data: dict) -> Result:
    logger.debug("process_data called with: %s", data)
    result = self._transform(data)
    logger.debug("process_data result: %s", result)
    return result

# ✗ WRONG - No debug logging
def process_data(self, data: dict) -> Result:
    return self._transform(data)
```

### 2.6 Blueprint Routes

```python
# Web route (returns HTML)
@web_bp.route('/page', methods=['GET'])
@login_required
def get_page():
    ctrl = get_ctrl()
    data = ctrl.get_data()
    return render_template('page.html', data=data)

# API route (returns JSON)
@api_bp.route('/data', methods=['GET'])
@login_required
def get_data():
    ctrl = get_ctrl()
    return jsonify(ctrl.get_data())

# API route with CSRF exempt (for external callers)
@api_bp.route('/webhook', methods=['POST'])
@csrf.exempt
@require_api_key
def webhook():
    return jsonify({"ok": True})
```

---

## 3. Frontend Conventions (IMPORTANT)

### 3.1 File Structure

```
app/static/
├── css/
│   ├── base.css           # Global styles
│   ├── car_heater.css     # Page-specific
│   └── car_heater_*.css   # Feature-specific
└── js/
    ├── car_heater.js      # Main page logic
    └── car_heater_*.js    # Feature modules
```

### 3.2 JavaScript Architecture

**Reference implementation**: `car_heater.js`

#### Section Organization

```javascript
/**
 * Module Name - Description
 * Brief explanation of purpose
 */

console.log('🚗 module_name.js loaded');

// ============================================
// Utility Functions
// ============================================

function fmtTs(ts) { /* ... */ }
function fmtNum(v, unit = '', digits = 1) { /* ... */ }

// ============================================
// Feature Section Name
// ============================================

function updateFeatureUI(data) { /* ... */ }

// ============================================
// Socket.IO Event Handlers
// ============================================

if (window.socket) {
    window.socket.on('event_name', data => {
        console.log('📡 event_name:', data);
        // Handle event
    });
}

// ============================================
// Initialization
// ============================================

document.addEventListener('DOMContentLoaded', () => {
    initializeFeature();
});
```

#### Element Selection Pattern

```javascript
// ✓ CORRECT - getElementById with null checks
function updateUI(data) {
    const element = document.getElementById('elementId');
    if (!element) return;
    element.textContent = data.value;
}

// ✗ WRONG - querySelector without null check
document.querySelector('#element').textContent = value;
```

#### Formatting Utilities

**ALWAYS** use these patterns for display values:

```javascript
// Timestamp formatting
function fmtTs(ts) {
    if (!ts) return '—';
    try {
        const d = new Date(ts);
        return d.toLocaleString(undefined, {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit'
        });
    } catch { return '—'; }
}

// Number formatting with unit
function fmtNum(v, unit = '', digits = 1) {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    return `${n.toFixed(digits)}${unit}`;
}

// Compact large numbers
function fmtCompact(v, unit = '', digits = 0) {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k${unit}`;
    return `${n.toFixed(digits)}${unit}`;
}
```

**KEY**: Always return `'—'` (em-dash) for missing/invalid values, never empty string.

### 3.3 HTML Templates

#### Base Template Extension

```html
{% extends 'base.html' %}

{% block title %}Page Title{% endblock %}

{% block styles %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/page.css') }}">
{% endblock %}

{% block content %}
<!-- Page content -->
{% endblock %}

{% block scripts %}
<script>
    // Pass server data to JS
    const PAGE_DATA = {{ data | tojson | safe }};
</script>
<script src="{{ url_for('static', filename='js/page.js') }}"></script>
{% endblock %}
```

#### Element ID Conventions

```html
<!-- State indicators -->
<div id="stateIndicator" class="state-indicator state-unknown">
<span id="stateIcon">❓</span>
<h3 id="stateTitle">Unknown</h3>

<!-- Values with labels -->
<div id="heroValue" class="hero-stat-value">—</div>
<div class="hero-stat-label">Label</div>

<!-- Form controls -->
<input type="number" id="settingInput" name="settingInput">
<button id="btnAction" type="button">Action</button>

<!-- Status displays -->
<span id="statusText" class="status-text"></span>
```

**ID Naming**:
- `hero*` - Main display values
- `detail*` - Secondary info
- `btn*` - Buttons
- `*Input` - Form inputs
- `*Toggle` - Checkboxes/switches
- `*Status` - Status indicators

### 3.4 CSS Conventions

#### Grid Layouts

```css
/* Card grid - responsive */
.hero-stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
}

/* Responsive breakpoints */
@media (max-width: 768px) {
    .hero-stats {
        grid-template-columns: repeat(2, 1fr);
    }
}
```

#### State Classes

```css
/* State indicators */
.state-on { background: var(--color-success); }
.state-off { background: var(--color-muted); }
.state-unknown { background: var(--color-warning); }

/* Status badges */
.status-scheduled { color: var(--color-info); }
.status-running { color: var(--color-success); }
.status-error { color: var(--color-danger); }
```

---

## 4. Socket.IO Integration

### 4.1 Server-Side Emit

```python
# In handlers.py
def emit_to_views(self, event: str, payload: dict):
    """Emit only to browser views (not ESP32/clients)."""
    for sid in self.view_sids:
        self.socketio.emit(event, payload, room=sid)

# Usage pattern
handler.emit_to_views('car_heater_status', {
    'status': asdict(status),
    'weather': weather_data,
    'command_status': asdict(cmd_status),
})
```

### 4.2 Client-Side Handling

```javascript
if (window.socket) {
    window.socket.on('car_heater_status', data => {
        console.log('📡 car_heater_status:', data);
        
        // Handle nested structures
        if (data && data.status) {
            updateStatusUI(data.status);
        }
        if (data && data.weather) {
            updateWeatherDisplay(data.weather);
        }
    });
}
```

**Reconnect behavior**: Do not call `window.socket.disconnect()` for recoverable
server restarts or shutdown notices. Socket.IO treats client disconnects as
intentional and stops automatic reconnection; close the underlying engine or let
the transport drop so the shared client can reconnect.

### 4.3 Event Naming

| Event | Direction | Purpose |
|-------|-----------|---------|
| `car_heater_status` | Server→Client | Status updates |
| `car_heater_logs` | Server→Client | Latest ESP32 log snapshot |
| `car_heater_control` | Client→Server | Control commands |
| `kfactor_extended_data` | Server→Client | Calibration data |
| `*_action_result` | Server→Client | Command acknowledgment |

---

## 5. Data Flow Patterns

### 5.1 Initial Page Load

```
1. Route fetches data from Controller
2. Data passed to template as Python dict
3. Template renders HTML with {{ data }}
4. JS initializes from embedded JSON:
   const DATA = {{ data | tojson | safe }};
5. JS calls update functions with initial data
```

### 5.2 Real-Time Updates

```
1. Service detects change
2. Service calls handler.emit_to_views()
3. Socket.IO sends to all view_sids
4. JS socket.on() receives event
5. JS update function modifies DOM
```

### 5.3 User Action

```
1. User clicks button
2. JS sends socket event or fetch() API call
3. Server processes in handler or route
4. Server broadcasts result to all views
5. All connected browsers update
```

---

## 6. Common Pitfalls

### 6.1 Field Names & Data Types

**CRITICAL**: Backend Python field names MUST match frontend JS expectations.

```python
# Backend dataclass
@dataclass
class Status:
    start_ts: datetime    # Note: start_ts
    is_active: bool       # Note: is_active

# Frontend JS must use same names
function update(data) {
    const started = data.start_ts;     // ✓ Matches
    const started = data.started_ts;   // ✗ Wrong name
    const active = data.is_active;     // ✓ Matches
    const active = data.active;        // ✗ Wrong name
}
```

**kFactor toggle**: The UI toggle expects `autonomous_enabled` in status payloads. Include this key (keep it boolean) in both initial page data and Socket.IO status to avoid the toggle snapping back to `true`.

**Boolean parsing**: Avoid `bool("false")` or `bool("0")` in backend handlers; parse booleans from strings explicitly to prevent unintended `True` values.

### 6.2 Static Asset Caching

When changing user-visible JS or CSS in production, bump the versioned asset URLs in the corresponding template so the browser cache does not mask the fix:

- `car_heater.js` → bump version in `templates/car_heater.html`
- `temperatures.js` / `temperatures.css` → bump version in `templates/temperatures.html`
- `ynab_categorizer.js` / `ynab_categorizer.css` → bump version in `templates/ynab_categorizer.html`

### 6.3 Car Heater & KFactor

**Logs**: ESP32 log snapshots are emitted in a dedicated `car_heater_logs` Socket.IO event. Frontend can still read `status.logs` from `car_heater_status` for compatibility, but should dedupe because both WS and HTTP paths may carry the same snapshot.

**JS helper scope**: `car_heater.js` and `car_heater_settings_*.js` share the same page. Do not leave generic helper names like `fmtTs` or `fmtNum` in the global scope in settings modules — they will override the main page formatters at runtime.

**WS payload shape**: Browser `car_heater_status` handlers expect the normalized `CarHeaterStatus` payload shape (`is_heater_on`, `instant_power_w`, etc.), not the raw ESP JSON (`shelly`, `temperature`, `ws_stats`). Normalize before emitting from Redis/WS paths.

**Source field**: The UI `status.source` field represents the transport path (`WS`/HTTP fallback), not Shelly's internal `source` value such as `HTTP_in`. For websocket-originated status, override the normalized payload source to `WS`.

**Action-result stages**: `car_heater_action_result` is two-stage: the web handler emits a local `stage=queued` ack immediately, while the Redis bridge emits `stage=executed` when the ESP reports success/failure. Keep these distinct so the UI does not treat queue ack as command completion.

**WS runtime side effects**: Websocket-originated status must run the same runtime side effects as HTTP `/api/car_heater/status` (charge-mode state updates, keep-at-temp tick, ready-by tick, kFactor tick, alerts, DB persistence), otherwise automation silently breaks in websocket-primary mode.

**KFactor multi-worker**: KFactor config changes are persisted in the DB. If multiple Gunicorn workers are running, each worker must refresh config from DB (e.g., during tick/snapshot) to avoid stale `enabled` state.

**KFactor config updates**: Use `KFactorCalibrator.update_config()` (not direct `_cfg` assignment) so submodules (`_physics`, `_session`, `_snapshot`) stay in sync.

### 6.4 AC / TinyTuya

**Diagnostics**: Initialize the AC TinyTuya device with env-driven `AC_TUYA_VERSION` (and timeout/persist knobs when needed) instead of relying on library defaults. When status/control fails, inspect the raw TinyTuya `Err` code in logs; `914` points to local key/version mismatch, `901` can occur even when the device IP answers ping if the local-control handshake cannot complete.

### 6.5 ESP32 WebSocket Service

**Restart**: `esp32_ws.service` runs Gunicorn with long-lived WebSocket requests. Keep the systemd stop path hard-kill based (`KillSignal=SIGKILL`, short `TimeoutStopSec`) so restarts do not block on stuck Gunicorn shutdown.

**Diagnostics**: Flask-Sock/simple-websocket handles WS control frames below the route handler. If you need to verify device heartbeat pings in server logs, keep the custom server shim in `esp32_ws/main.py`; route-level JSON logging alone will not show protocol `PING` frames.

**Gunicorn bootstrap**: `esp32_ws.service` runs `gunicorn main:app`, so bootstrap that must exist in production cannot live only under `if __name__ == "__main__":`. Keep Redis/WebSocket manager startup reachable from module import or another Gunicorn-executed path.

**Reconnect identity**: When replacing an existing device connection with the same `device_id`, stale socket cleanup must be identity-aware (`ws`/connection object), or the old socket's `finally` block can unregister the new replacement and make live status look like it came from an unknown device.

**Redis bridge logging**: `ESP32RedisBridge` should log the first live status summary at `INFO`, but recurring heartbeat summaries should stay at `DEBUG` by default. Only raise recurring summaries to `INFO` by explicitly setting `ESP32_REDIS_STATUS_INFO_REPEAT_INTERVAL_S`.

**Temperature ESP WebSocket path**: Temperature ESP firmware uses `esp32_ws` as its device transport. Temperature telemetry and RPC responses must use the dedicated Redis channels (`esp32:temperature:telemetry`, `esp32:temperature:rpc_results`, `esp32:temperature:commands`) and should still flow through the main app's `esp32_temphum` persistence/broadcast helper so thermostat/weather side effects stay consistent with the legacy HTTP POST path.

**ESP firmware JSON**: Active ESP firmware JSON payloads should be built with ArduinoJson `JsonDocument` and `serializeJson()`/`deserializeJson()`. Do not add hand-concatenated JSON to active firmware paths.

### 6.6 YNAB Categorizer

**Queue filtering**: Default queue mode is `strict` (skip transfers + split parents). Keep this as the default for both initial page data and API fallback.

**Config persistence**: Queue filtering mode is persisted in `ynab_categorizer_config` (singleton `id=1`, budget-aware row). Update/read config via controller methods, not direct table writes in routes.

**CSRF**: Internal browser-driven YNAB mutation endpoints (`/api/ynab-categorizer/*` POST) keep CSRF enabled. Frontend must include CSRF token (`X-CSRFToken` or `X-CSRF-Token`) for JSON POSTs.

**Reconciled filter**: Treat transactions as reconciled when `tx["cleared"] == "reconciled"` (case-insensitive). Default queue behavior hides reconciled items unless explicitly enabled in config.

**Queue limit**: Queue limiting is config-driven (`queue_limit_enabled`, `queue_limit_value`, `queue_limit_unit`) and should be applied on transaction date (`YYYY-MM-DD`) with supported units `days|months|years`.

**Quick-apply**: `quick_apply_include_medium` is persisted in `ynab_categorizer_config` and defaults to `false`; keep High-confidence-only bulk suggestion apply as the safe default unless explicitly enabled.

**Starting Balance**: Exclude "Starting Balance" transactions from the categorization queue, even if uncategorized. Match by normalized `payee_name` (`STARTING BALANCE`) and fallback `payee_id` values (`starting_balance`, `starting-balance`).

**Row display**: Do not surface raw transaction UUIDs in queue rows. Prefer actionable context (`From/To account`) plus memo/date/amount while keeping UUIDs only as hidden checkbox values for apply actions.

**Default suggestion**: Persist `default_category_id` in `ynab_categorizer_config`. When payee stats yield no suggestion, use this category as fallback only if it exists in the visible category set.

**Category list**: Queue category lists should exclude `deleted` and `hidden` categories. Order with top 10 locally most-used (from `ynab_payee_category_stats` aggregate counts) first, then remaining categories alphabetically.

**Approvals**: Unapproved transactions are fetched via YNAB transactions endpoint with `type=unapproved` and approved in bulk via PATCH payloads `{"id": "...", "approved": true}`. Keep Root-Admin guard and CSRF enforcement identical to categorize apply endpoints.

**Merged review**: The web page uses `/api/ynab-categorizer/queue` as the single review source for both uncategorized and categorized-but-unapproved transactions. Do not reintroduce the old two-tab page flow unless the queue payload is explicitly split again.

**Apply semantics**: `/api/ynab-categorizer/apply` sends both `category_id` and `approved: true` in the same YNAB bulk PATCH. Treat manual category apply as categorize-and-approve, and keep `/api/ynab-categorizer/approve` only for approve-as-is on already categorized items.

**Custom rules**: Custom categorization rules are persisted in `ynab_categorizer_config.custom_rules_json` via controller methods. Rules run top-to-bottom, first match wins, and payee comparisons must use the existing canonical `normalize_payee()` helper.

**Test mode**: `ynab_categorizer_config.test_mode_enabled` is a persisted workspace safety toggle. When enabled, `/api/ynab-categorizer/apply` and `/api/ynab-categorizer/approve` must simulate success without calling YNAB or mutating local learning stats, so the review queue stays reusable for repeated testing.

### 6.7 Hue & Temperatures

**Hue availability**: `app.hue_ctrl` is optional at runtime. Guard Hue API routes with `getattr(..., "hue_ctrl", None)` and return a 503-style error when Hue env/config is missing.

**Hue timeout**: Hue bridge HTTP calls should always use an explicit timeout. Configure via `HUE_HTTP_TIMEOUT_S` and preserve timeout usage in new Hue controller methods.

**Temperatures boot**: `app/templates/temperatures.html` boots the page with `window.TEMPERATURES_BOOTSTRAP` (`locations`, `outside_location_names`, `language`). Keep the temperatures workspace inline; do not reattach `settings_modal.html` or the shared chart modal to this page.

**Mobile overlays**: Do not place fixed-position mobile sheets inside ancestors with persistent `transform` styles, including reveal animations that use `animation-fill-mode: both`. Mobile browsers can anchor the sheet to the transformed layout instead of the viewport.

### 6.8 API Security

**CORS**: Cross-origin access for `/api/*` is opt-in via `API_ALLOWED_ORIGINS`. Do not reintroduce wildcard CORS defaults; same-origin browser traffic does not need CORS headers.

**API key transport**: Query-string API keys are disabled by default. Use `Authorization: Bearer <token>` or `X-API-Key`, and only enable `ALLOW_API_KEY_QUERY_PARAM=1` for temporary backward compatibility.

### 6.9 Database & Migrations

**PostgreSQL sequence drift**: If rows are imported with explicit `id` values, the serial sequence may lag behind `MAX(id)` and cause duplicate PK errors on inserts. For kFactor insert paths, use controller-level sequence realignment (`pg_get_serial_sequence` + `setval`) and retry once.

**Migration helpers**: Controller-level legacy migration helpers (`migrate_3d_to_pg`, `migrate_auth_to_pg`, `migrate_ac_to_pg`, `migrate_car_heater_to_pg`, `migrate_ready_by_to_pg`) use `DB_PATH` as the SQLite source and `_sa_engine` as the SQLAlchemy target. Keep them idempotent.

**Pytest bootstrap**: Repo-root test runs should work without manually exporting `PYTHONPATH`; keep root import bootstrap intact for `.venv/bin/pytest -q`.

### 6.10 Null Safety

```javascript
// ✓ CORRECT - Check all levels
if (data && data.nested && data.nested.value != null) {
    element.textContent = data.nested.value;
}

// ✗ WRONG - Assumes data exists
element.textContent = data.nested.value;  // Throws if null
```

### 6.11 Socket Event Data Structure

```python
# Server emits nested structure
handler.emit('event', {
    'status': asdict(status_obj),
    'extra': extra_data
})

# Client must unwrap
socket.on('event', data => {
    // data.status is the actual status, not data itself
    updateStatus(data.status);
});
```

### 6.12 CSS Grid Column Count

When adding/removing hero stats, update grid columns:

```css
/* 3 items */
grid-template-columns: repeat(3, 1fr);

/* 4 items */
grid-template-columns: repeat(4, 1fr);
```

### 6.13 Medicine Calculator

**Scope**: Medicine calculator is Root-Admin only and reachable only from Settings. Keep both the page and `/api/medicine-calculator/*` guarded with `require_root_admin_or_redirect`.

**Purchase model**: Store exact `pieces_bought`; do not reintroduce bottle-count assumptions. Each purchase stores a snapshot of `dose_per_dosing_day` and selected dosing weekdays, so later schedule changes must not recalculate old rows unless that row is edited.

**Calculation semantics**: Use the latest purchase per normalized medicine name for the primary next-date calculation. Older purchases are history only; do not carry over stock unless explicitly requested. Purchase date + 1 is day one, only selected weekdays consume stock, and Finnish `joustoaika` is subtracted as calendar days from the run-out date.

---

## 7. Testing & Debugging

### 7.1 Backend Logging

```python
# Add debug logging
logger.debug("Payload: %s", payload)

# Check logs
sudo journalctl -u jannenkoti -f --no-pager
```

### 7.2 Frontend Debugging

```javascript
// Console logging with emoji prefixes
console.log('🚗 Status update:', data);
console.log('📡 Socket event:', eventName, payload);
console.log('⚙️ Settings changed:', settings);
console.log('❌ Error:', error);
```

### 7.3 Socket.IO Debugging

```javascript
// In browser console
window.socket.onAny((event, ...args) => {
    console.log('Socket event:', event, args);
});
```

---

## 8. Database Schema

### 8.1 Database Architecture

**Production**: PostgreSQL via `DATABASE_URL` environment variable  
**Testing**: SQLite in-memory via `sqlite:///:memory:`  
**All operations**: SQLAlchemy Core (no ORM)

**DB_PATH note**: `DB_PATH` is still required at startup because `DatabaseManager` is instantiated in `ControllerBase.__init__`, but `DatabaseManager` is dormant — no runtime path calls it. All actual reads/writes go through `self._sa_engine`. `DB_PATH` can be any valid path; it is never read at runtime.

### 8.2 Core Tables

```sql
-- Users & Auth
users (id, username, password_hash, is_admin, is_root_admin, is_temporary, expires_at)
api_keys (id, key_id, name, secret_hash, created_at, created_by, revoked)

-- Car Heater
car_heater_status (id, timestamp, is_heater_on, instant_power_w, ambient_temp, ...)
car_heater_events (id, event_type, timestamp, details)
car_heater_kfactor_* (7 calibration tables)

-- Sensors
esp32_temphum (id, location, timestamp, temperature, humidity, ac_on)
bmp_sensor_data (id, timestamp, temperature, pressure, altitude)
```

### 8.3 Adding Tables

1. Add table in `app/core/schema.py` using SQLAlchemy Core `Table()`
2. Add dataclass in `models.py`
3. Add CRUD methods in `app/core/_controller/<feature>.py` using SQLAlchemy
4. Create Alembic migration: `alembic revision --autogenerate -m "add_feature_table"`
5. Export in `core/__init__.py`

### 8.4 SQLAlchemy Patterns

```python
# Import schema tables
from ..schema import my_table

# Basic select
with self.sa_engine.connect() as conn:
    stmt = select(my_table).where(my_table.c.id == some_id)
    row = conn.execute(stmt).first()

# Insert with RETURNING (PostgreSQL)
from sqlalchemy.dialects.postgresql import insert as pg_insert
stmt = pg_insert(my_table).values(**data).returning(my_table.c.id)
result = conn.execute(stmt)
conn.commit()
new_id = result.scalar()

# Upsert (PostgreSQL ON CONFLICT)
stmt = pg_insert(my_table).values(**data)
stmt = stmt.on_conflict_do_update(
    index_elements=[my_table.c.unique_col],
    set_={"field": stmt.excluded.field}
)
conn.execute(stmt)
conn.commit()
```

### 8.5 Timestamp Columns

**IMPORTANT**: Timestamp columns are stored as TEXT (ISO format strings) for SQLite compatibility in tests.

When comparing timestamps in queries, convert Python `datetime` to ISO string:

```python
# ✓ CORRECT - Compare as strings
cutoff = datetime.now() - timedelta(days=7)
stmt = select(...).where(table.c.timestamp >= cutoff.isoformat())

# ✗ WRONG - datetime vs TEXT comparison fails in PostgreSQL
stmt = select(...).where(table.c.timestamp >= cutoff)
```

---

## 9. Service Lifecycle

### 9.1 Service Initialization

```python
# In app/__init__.py create_app()
from .services.feature import FeatureService
app.feature_service = FeatureService(app.ctrl)
```

### 9.2 Background Threads

```python
class BackgroundService:
    def __init__(self, ctrl: Controller):
        self.ctrl = ctrl
        self._running = False
        self._thread = None
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
    
    def _run(self):
        while self._running:
            self._tick()
            time.sleep(self.interval)
```

---

## 10. Quick Reference: Adding Features

### 10.1 New Page Checklist

1. [ ] Create `app/blueprints/web/feature_web.py`
2. [ ] Create `app/templates/feature.html`
3. [ ] Create `app/static/css/feature.css`
4. [ ] Create `app/static/js/feature.js`
5. [ ] Register blueprint in `blueprints/__init__.py`
6. [ ] Add navbar link in `templates/navbar.html`

### 10.2 New API Endpoint Checklist

1. [ ] Add route in `app/blueprints/api/feature_api.py`
2. [ ] Add controller methods if needed
3. [ ] Add dataclass models if needed
4. [ ] Register blueprint if new file

### 10.3 New Socket Event Checklist

1. [ ] Add handler in `app/sockets/handlers.py`
2. [ ] Add emit function for broadcasting
3. [ ] Add JS listener in relevant `.js` file
4. [ ] Add console.log for debugging

### 10.4 New Database Table Checklist

1. [ ] Add table definition in `app/core/schema.py`
2. [ ] Add dataclass in `models.py`
3. [ ] Add CRUD in `controller.py`
4. [ ] Export in `core/__init__.py`
5. [ ] Restart service (auto-migration creates table)

---

## 11. Environment Variables

```bash
# Required
SECRET_KEY=...           # Flask secret
DATABASE_URL=postgresql+psycopg://user:pass@localhost/dbname  # PostgreSQL connection (Alembic + SQLA paths)

# Required at runtime (current app factory)
DB_PATH=/path/to/db.sqlite  # Required at startup (legacy DatabaseManager instantiation); never read at runtime

# Optional
WEB_USERNAME=admin       # Initial admin user
WEB_PASSWORD=...         # Initial admin password
RATE_LIMIT_WHITELIST=["127.0.0.1"]
API_ALLOWED_ORIGINS=["https://example.com"]  # Explicit CORS allowlist for /api/*
ALLOW_API_KEY_QUERY_PARAM=0  # Keep disabled unless a legacy integration still depends on it
ALLOWED_WS_ORIGINS=["http://localhost:5555"]
HUE_HTTP_TIMEOUT_S=5.0

# Optional (YNAB Categorizer)
YNAB_API_KEY=...
YNAB_BUDGET_ID=...
YNAB_HTTP_TIMEOUT_S=15
YNAB_HTTP_RETRIES=2
```

---

## 12. File Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Web routes | `{feature}_web.py` | `car_heater_web.py` |
| API routes | `{feature}_api.py` | `car_heater_api.py` |
| Services | `{feature}_service.py` | `car_heater_service.py` |
| CSS | `{feature}.css` | `car_heater.css` |
| JS | `{feature}.js` | `car_heater.js` |
| Templates | `{feature}.html` | `car_heater.html` |

---

## 13. Code Style

### Python
- Type hints for function signatures
- Docstrings for public functions
- `from __future__ import annotations` for forward refs
- `logging` module, not print()

### JavaScript
- ES6+ features (const/let, arrow functions, template literals)
- No jQuery - vanilla JS only
- Semicolons optional but consistent
- Console.log with emoji prefixes for debugging

### CSS
- CSS custom properties (variables) for colors
- BEM-ish naming for components
- Mobile-first responsive design
- Grid/Flexbox for layouts

---

*Last updated: 2026-04-14*
