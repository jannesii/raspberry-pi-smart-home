# AGENTS.md - AI Development Guide

## Quick Reference

```
STACK: Python 3.11 | Flask | Flask-SocketIO | Eventlet | SQLite | SQLAlchemy Core (phase-in) | Alembic (phase-in) | Jinja2
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
3. **Add verbose debug logging** to every function/method you touch. Use `logger.debug()` liberally.

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
│    ├── database.py    → Thread-safe SQLite                  │
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
| `app/core/controller.py` | All DB operations | Direct database access |
| `app/core/models.py` | Dataclass definitions | No imports from app |
| `app/core/schema.py` | SQLAlchemy Core metadata (phase-in) | Use for Alembic/migrations |
| `migrations/` | Alembic migrations (phase-in) | Target Postgres via `DATABASE_URL` |

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

**RULE: Add verbose debug logging to EVERY function you modify.**

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

### 4.3 Event Naming

| Event | Direction | Purpose |
|-------|-----------|---------|
| `car_heater_status` | Server→Client | Status updates |
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

### 6.1 Field Name Mismatches

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

### 6.2 Null Safety

```javascript
// ✓ CORRECT - Check all levels
if (data && data.nested && data.nested.value != null) {
    element.textContent = data.nested.value;
}

// ✗ WRONG - Assumes data exists
element.textContent = data.nested.value;  // Throws if null
```

### 6.3 Socket Event Data Structure

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

### 6.4 CSS Grid Column Count

When adding/removing hero stats, update grid columns:

```css
/* 3 items */
grid-template-columns: repeat(3, 1fr);

/* 4 items */
grid-template-columns: repeat(4, 1fr);
```

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

### 8.1 Core Tables

```sql
-- Users & Auth
users (id, username, password_hash, is_admin, is_root_admin, is_temporary, expires_at)
api_keys (id, key_id, name, secret_hash, created_at, created_by, revoked)

-- Car Heater
car_heater_status (id, timestamp, is_heater_on, instant_power_w, ambient_temp, ...)
car_heater_events (id, event_type, timestamp, details)
car_heater_kfactor_* (calibration tables)

-- Sensors
esp32_temperature_humidity (id, location, timestamp, temperature, humidity, ac_on)
bmp_pressure (id, timestamp, pressure_hpa, temperature_c, altitude_m)
```

### 8.2 Adding Tables

1. Add schema in `database.py` `_create_tables()`
2. Add dataclass in `models.py`
3. Add CRUD methods in `controller.py`
4. Export in `core/__init__.py`
5. If Alembic/SQLAlchemy phase-in is enabled, mirror table in `app/core/schema.py`

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

1. [ ] Add table definition in `database.py`
2. [ ] Add dataclass in `models.py`
3. [ ] Add CRUD in `controller.py`
4. [ ] Export in `core/__init__.py`
5. [ ] Restart service (auto-migration creates table)

---

## 11. Environment Variables

```bash
# Required
SECRET_KEY=...           # Flask secret
DB_PATH=/path/to/db.sqlite

# Optional
WEB_USERNAME=admin       # Initial admin user
WEB_PASSWORD=...         # Initial admin password
RATE_LIMIT_WHITELIST=["127.0.0.1"]
ALLOWED_WS_ORIGINS=["http://localhost:5555"]
DATABASE_URL=postgresql+psycopg://user:pass@localhost/dbname
USE_SQLA_READS=1         # Enable SQLAlchemy read paths (phase-in)
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

*Last updated: 2026-01-25*
