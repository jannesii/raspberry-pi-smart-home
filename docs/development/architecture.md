# Server architecture

## App lifecycle

[run.py](../../run.py) loads the working directory's `jannenkoti.env`, calls
`create_app()`, and exposes `app` to Gunicorn. Importing [app](../../app/__init__.py)
applies Eventlet monkey-patching before the application components initialize.

The app factory loads settings, creates the Controller and SQLAlchemy engine,
installs logging, seeds the Root-Admin account, configures login and Socket.IO,
starts services, and registers routes/assets. Calling the factory starts real
background work; it is not a read-only diagnostic shell.

The main process uses one Eventlet worker. Services and connection tracking
hold in-process state; additional workers are not a supported scaling strategy.
The ESP32 gateway runs independently with ordinary threaded WebSocket handling.

## HTTP and persistence

Routes in [blueprints](../../app/blueprints/) use the app-owned Controller for
shared runtime data. [Controller](../../app/core/controller.py) combines feature
mixins from `app/core/_controller/`. Those use SQLAlchemy Core and the shared
engine; [schema.py](../../app/core/schema.py) defines tables and
[models.py](../../app/core/models.py) defines DTOs.

Production uses PostgreSQL through `DATABASE_URL`; local development and tests
can use SQLite. The legacy SQLite manager is still initialized from `DB_PATH`.
It is not the production data-access interface. The separate
[Villenkoti module](../../app/blueprints/villenkoti/controller.py) has its own
SQLite storage and does not use the shared Controller database.

Schema changes require Alembic migrations. Existing date/time columns often
store ISO text, so check column types before writing comparisons. See
[database operations](../operations/database.md).

## Live data

- Browsers and printer/timelapse clients connect to main-app Socket.IO.
  [Socket modules](../../app/sockets/) handle role-specific events and view
  broadcasts. Printer action handlers currently disable control commands.
- ESP32 devices connect to [esp32_ws](../../esp32_ws/README.md), not main-app
  Socket.IO. Redis carries telemetry, commands, and execution results.
- The main-app Redis bridges normalize device payloads and invoke shared
  persistence, automation, and broadcast paths. HTTP fallbacks must preserve
  the same side effects.

Templates render initial data and load vanilla JS/CSS. Live events may contain
partial state: consumers merge present fields. Keep normalized status fields
and nested payloads consistent with initial page data. A queued command is not
proof of device execution.

## Integrations and authorization

[Service initialization](../../app/services/__init__.py) wires AC, Hue, car
heater, weather, YNAB, alerts, and Redis integrations onto the app. Optional
credentials determine availability. Device control and scheduled work can
start during bootstrap. Legacy BMP polling is not started here.

Browser routes use session authentication, with admin/Root-Admin checks where
required. Browser mutations retain CSRF protection; selected device HTTP
endpoints use API keys. Route decorators are authoritative: do not infer
protection merely from a URL prefix. The gateway has a separate shared-key
configuration, not the main app's database-backed API-key verifier.

For setup/security settings use the [README](../../readme.md). For regression
constraints use [feature contracts](feature-contracts.md); for exact API and
Socket.IO payloads read the corresponding handler and its tests.
