# Working in this repository

## Orientation

Python 3.11+; Flask, Flask-SocketIO/Eventlet, SQLAlchemy Core, PostgreSQL,
Alembic, Jinja2; vanilla JavaScript/CSS with no frontend build step.

- `app/blueprints/`: HTTP routes; `app/sockets/`: browser/printer events.
- `app/core/controller.py` composes database mixins in `app/core/_controller/`.
- `app/core/schema.py`: schema; `app/core/models.py`: dataclass DTOs.
- `app/services/`: integrations and background work attached to the Flask app.
- `app/templates/` and `app/static/`: browser UI.
- `esp32_ws/`: ESP32 WebSocket gateway; Redis connects it to the main app.
- `migrations/`: Alembic migrations; `tests/`: pytest suite.

Read nearby implementation and tests before editing. See [readme.md](readme.md)
for setup/configuration. For device, automation, or financial features, read
only the relevant section of [feature contracts](docs/development/feature-contracts.md).

## Boundaries to preserve

- Route database operations through Controller mixins and `self._sa_engine`.
  Do not add runtime access through legacy `DatabaseManager` or introduce ORM.
- Change `schema.py` and add an Alembic migration together for schema changes;
  review generated migrations. Restarting the app is not a migration strategy.
- Production uses PostgreSQL; SQLite supports tests/local development.
  `DB_PATH` is still needed at bootstrap. Preserve bounded PostgreSQL pooling:
  global `NullPool` can cause Eventlet descriptor collisions.
- Existing timestamp columns use ISO text: compare them with ISO strings,
  not Python datetime objects. Check the actual column definition first.
- Main-app Socket.IO serves browsers/printer clients, not ESP telemetry.
  ESP32 ingress belongs in `esp32_ws` and the Redis bridges. Preserve shared
  persistence/automation side effects across WebSocket and HTTP fallbacks.
- Preserve DTO/JSON field names, payload nesting, and queued/executed command
  stages. Merge partial status updates without resetting absent fields.
- Reuse app-owned services; handle optional integrations being unavailable.
  Preserve Eventlet bootstrap ordering and the main app's single-worker model.

## Security and browser changes

- Preserve authentication, Root-Admin guards, and browser-mutation CSRF checks.
  API-key device endpoints may be explicitly exempt; `/api/` alone is not an
  exemption. Root authorization must fail closed.
- Keep CORS opt-in through `API_ALLOWED_ORIGINS`. API keys belong in headers;
  query-string keys remain disabled by default. Redirect targets must be safe
  local absolute paths, rejecting schemes, `//`, backslashes, and controls.
- Keep credentials out of code/logs. `alembic.ini` is ignored; track only the
  credential-free example and supply the connection through `DATABASE_URL`.
- Match the page's existing JS/CSS conventions, guard missing elements/data,
  and display missing measurements as `—`. Avoid shared global helper names.
- Bump the corresponding template's versioned asset URLs for visible JS/CSS
  changes. Do not intentionally disconnect Socket.IO on recoverable restarts;
  doing so disables automatic reconnection.
- Add useful, credential-free debug logs for decisions and failure paths.
  Do not log every function entry, entire payload, poll, or telemetry item.

## Validation and delivery

Run commands from the `server/` directory (this file’s directory):

```sh
.venv/bin/pytest -q tests/test_relevant_feature.py
.venv/bin/ruff check path/to/changed.py
.venv/bin/ruff format --check path/to/changed.py
python3 scripts/check_agent_instructions.py
```

Replace the example paths with the affected files. Run `.venv/bin/pytest -q`
for changes spanning shared behavior. Use isolated test databases and mocked
integrations; do not use live devices, webhooks, or production data as tests.
For UI changes, check the affected interaction and responsive layout when a
browser is available. Report what was verified and any remaining limitations.
Update `readme.md` when user-facing features or setup instructions change.

Before the final response, review all tracked changes, including staged and
pre-existing changes. If any remain, end the response with one fenced,
paste-ready Conventional Commit message covering all of them. Use
`type(scope): imperative summary` (at most 72 characters, no trailing period),
with an optional short bulleted body. Do not commit unless asked.

## Keep this file small

- Root `AGENTS.md` has a hard limit of 120 lines and 1,100 words. All tracked
  server `AGENTS.md` files plus `.github/copilot-instructions.md` together have a
  limit of 180 lines and 1,600 words. Pre-commit and CI check these budgets.
- Add an instruction only if it is durable, specific to this repository, and
  changes how an agent should act. Replace or remove obsolete text first.
- Do not append incident histories, feature inventories, tutorials, copied
  code/schema/configuration, or rules already enforced by tools.
- Put regression protection in tests, local rationale beside the code, and
  reference detail in the relevant feature documentation. Update that detail
  with behavior changes; read it on demand, never as a mandatory bulk preload.
- Do not bypass the budget by moving this guide into another always-loaded
  file or increasing the limits. A budget change needs explicit user approval.
