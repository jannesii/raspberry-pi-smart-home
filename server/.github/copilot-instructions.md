# Jannenkoti AI Coding Instructions

Flask-based home automation server for Raspberry Pi. Real-time dashboard with WebSocket, REST API, and multiple service integrations (car heater, AC, 3D printer, Hue lights).

## Architecture Overview

```
app/
├── __init__.py       # create_app() factory, bootstraps all extensions
├── extensions.py     # Singletons: limiter, csrf, login_manager, socketio
├── core/
│   ├── controller.py # Controller = ControllerBase + feature mixins
│   ├── _controller/  # Mixin classes using SQLAlchemy + PostgreSQL
│   ├── database.py   # Legacy SQLite wrapper (not used by mixins)
│   ├── models.py     # Dataclass DTOs (User, ThermostatConf, etc.)
│   └── schema.py     # SQLAlchemy table definitions for Alembic
├── blueprints/       # Flask blueprints: auth/, web/, api/, villenkoti/
├── services/         # Long-running integrations: ac/, car_heater/, hue/, weather/
├── sockets/          # Socket.IO handlers split by feature (*_handlers.py)
└── templates/        # Jinja2 templates
```

### Key Patterns

- **Controller Mixin Pattern**: `app.ctrl` is a singleton composed of feature mixins (`AuthMixin`, `CarHeaterKFactorMixin`, etc.) — add new DB operations by creating a mixin in `app/core/_controller/`
- **SQLAlchemy + PostgreSQL**: All mixins in `_controller/` use `self._sa_engine` (SQLAlchemy) with PostgreSQL. Use `pg_insert` for upserts, raw `sqlalchemy` statements for queries. Tests substitute SQLite via the same engine interface.
- **Socket.IO Events**: Split into feature modules in `app/sockets/` (e.g., `kfactor_handlers.py`). Handler class is singleton via `SocketEventHandler`
- **Services Init**: `app/services/__init__.py` → `init_services(app)` starts background threads (AC thermostat loop, Hue routines)

## Development Commands

```bash
# Setup
source .venv/bin/activate
pip install -r requirements.txt

# Run locally (dev)
export SECRET_KEY=dev DB_PATH=/tmp/jannenkoti.db WEB_USERNAME=admin WEB_PASSWORD=admin
python run.py

# Pre-commit + restart service (production)
./scripts/restart.sh

# Run tests
pytest tests/

# Lint & format
ruff check --fix app/
ruff format app/
```

## Code Conventions

- **Line length**: 100 chars (`pyproject.toml` ruff config)
- **Imports**: Use `from __future__ import annotations` for forward refs; `TYPE_CHECKING` guards for type-only imports
- **Timezone**: Always use `Europe/Helsinki` via `pytz.timezone("Europe/Helsinki")` — see `models.py` for examples
- **Flask blueprints**: Register in `app/blueprints/__init__.py` → `register_blueprints(app)`
- **API routes**: Prefix with `/api/`, return JSON, allow CORS via `@app.after_request`
- **WebSocket events**: Client→Server and Server→Client events documented in `docs/technical-guide.md`

## Database & Migrations

- **Production**: PostgreSQL via SQLAlchemy (`DATABASE_URL` env var)
- **Schema**: Table definitions in `app/core/schema.py` (SQLAlchemy Core tables)
- **DTOs**: Dataclasses in `app/core/models.py` — mixins return these, not ORM objects
- **Migrations**: Alembic in `migrations/versions/` with `YYYYMMDD_####_description.py` naming
- Run migrations: `alembic upgrade head`
- Generate migration: `alembic revision --autogenerate -m "description"`

## Testing Patterns

Tests in `tests/` use pytest fixtures. SQLite substitutes for PostgreSQL via SQLAlchemy engine:

```python
@pytest.fixture
def controller(temp_db: str):
    ctrl = Controller(db_path=temp_db)
    # SQLite as PostgreSQL stand-in for tests
    engine = create_engine(f"sqlite:///{pg_db_path}", echo=False)
    metadata.create_all(engine)
    ctrl._sa_engine = engine
    yield ctrl
    engine.dispose()
```

- Tests use SQLite for speed; production uses PostgreSQL
- Inject `Controller` instance with test engine
- Test files: `test_<feature>.py` pattern

## Service Integration Notes

| Service    | Config Env Vars                      | Notes                                  |
| ---------- | ------------------------------------ | -------------------------------------- |
| AC/Tuya    | `AC_DEV_ID`, `AC_IP`, `AC_LOCAL_KEY` | Local TinyTuya control                 |
| Car Heater | Web UI settings                      | Shelly PM1 relay + kFactor calibration |
| Weather    | None (FMI open data)                 | Finnish Meteorological Institute       |
| Hue        | `HUE_BRIDGE_IP`, `HUE_USERNAME`      | Local bridge API                       |

## Common Gotchas

- **Eventlet monkey-patching**: Must be first import in `app/__init__.py`
- **Socket.IO message queue**: Requires Redis at `redis://localhost:6379`
- **Session cookies**: Use `Secure` flag — browsers may reject over plain HTTP localhost
- **PostgreSQL upserts**: Use `from sqlalchemy.dialects.postgresql import insert as pg_insert` for `ON CONFLICT` clauses
- **DatabaseManager singleton**: Thread-safe but single SQLite connection; don't call from multiple workers

## Related: ESP32 Firmware

`ESP32C3-Car-Heater/` contains PlatformIO firmware for the car heater controller. It posts status to the server via HTTP (`/api/car_heater/status/test`) and receives commands. The Python server handles this in `app/blueprints/api/car_heater/` and `app/sockets/car_heater_handlers.py`.
