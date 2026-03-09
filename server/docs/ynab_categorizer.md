## YNAB Categorizer - Repo-Native Implementation Plan

### Goal
Add a new YNAB categorization module to this Flask app that:
- shows uncategorized transactions grouped by payee,
- suggests category from local payee history,
- applies categories only on explicit user action,
- never auto-applies.

This plan follows this repo's real conventions:
- SQLAlchemy Core tables in `app/core/schema.py`,
- dataclass DTOs in `app/core/models.py`,
- controller mixins in `app/core/_controller/`,
- web/API blueprint aggregators in `app/blueprints/*/__init__.py`,
- login + CSRF patterns used by current internal UI APIs.

## 1) Scope and Non-Goals

### In scope
- New YNAB categorizer web page and internal JSON API.
- New YNAB client/service layer using `requests`.
- New DB tables for local suggestion learning and audit.
- Bootstrap endpoint to seed stats from categorized YNAB history (24 months).

### Out of scope
- Automatic categorization without user confirmation.
- Background polling/watcher pipeline.
- Changing existing car heater/ESP32 features.

## 2) File/Module Plan (Project Conventions)

### 2.1 Web and API blueprints
1. Add `app/blueprints/web/ynab_categorizer_web.py`:
- route `GET /ynab-categorizer`
- `@login_required`
- render template with initial payload.

2. Add `app/blueprints/api/ynab_categorizer_api.py`:
- blueprint with `url_prefix="/ynab-categorizer"` (mounted under `/api`)
- internal endpoints with `@login_required`
- no `@csrf.exempt` for browser-driven endpoints.

3. Register in aggregators:
- `app/blueprints/web/__init__.py`: import module for side effects.
- `app/blueprints/api/__init__.py`: `api_bp.register_blueprint(ynab_categorizer_bp)`.

4. Navigation:
- add navbar link in `app/templates/navbar.html`.

### 2.2 Services
1. Add `app/services/ynab/ynab_client.py`:
- adapt `docs/ynab_client.py` to production usage:
- explicit timeout,
- retry/backoff for transient failures,
- response validation and structured error logging,
- methods:
  - `get_transactions_since(since_date: str)`,
  - `get_categories()`,
  - `update_transactions_bulk(items: list[dict[str, str]])`.

2. Add `app/services/ynab/ynab_categorizer_service.py`:
- orchestration layer between YNAB API and controller storage.
- suggestion and grouping logic lives here.

3. Export module in `app/services/ynab/__init__.py`.

4. In `app/services/__init__.py`, initialize singleton service and attach:
- `app.ynab_categorizer_service = ...`

### 2.3 Core schema + controller
1. Add tables to `app/core/schema.py` (SQLAlchemy Core):
- `ynab_payee_category_stats`
- `ynab_apply_events`
- `ynab_bootstrap_state`

2. Use `Text` for timestamps/date-like fields (repo SQLite test compatibility).

3. Add dataclasses in `app/core/models.py`:
- `YnabPayeeCategoryStat`
- `YnabApplyEvent`
- `YnabBootstrapState`

4. Add controller mixin `app/core/_controller/ynab_categorizer.py`:
- reads/writes/upserts for the three tables,
- query helpers for suggestion building.

5. Wire mixin:
- `app/core/_controller/__init__.py`
- `app/core/controller.py`
- `app/core/__init__.py` exports.

### 2.4 Migrations
1. Create Alembic revision for three new tables and indexes.
2. Keep schema/migration parity with `app/core/schema.py`.
3. Include unique constraints:
- stats: `(budget_id, payee_normalized, category_id)`
- apply events: `(budget_id, transaction_id)`

## 3) Data Model (Repo-Compatible)

### 3.1 `ynab_payee_category_stats`
- `id` (PK autoincrement)
- `budget_id` (Text, not null)
- `payee_normalized` (Text, not null)
- `category_id` (Text, not null)
- `count` (Integer, not null, default 0)
- `last_used_at` (Text)
- `created_at` (Text, not null)
- `updated_at` (Text, not null)
- unique `(budget_id, payee_normalized, category_id)`
- indexes `(budget_id, payee_normalized)`, `(budget_id, category_id)`

### 3.2 `ynab_apply_events`
- `id` (PK autoincrement)
- `budget_id` (Text, not null)
- `transaction_id` (Text, not null)
- `payee_normalized` (Text, not null)
- `category_id` (Text, not null)
- `applied_by_user_id` (Integer, nullable) or username Text if user id unavailable
- `applied_at` (Text, not null)
- unique `(budget_id, transaction_id)`

### 3.3 `ynab_bootstrap_state`
- `budget_id` (Text, PK)
- `bootstrapped_at` (Text, not null)
- `history_start_date` (Text, not null)
- `history_end_date` (Text, not null)

## 4) Business Logic Rules

### 4.1 Payee normalization
- `trim`
- NFKD diacritic fold
- uppercase
- non-alnum => single space
- collapse spaces

### 4.2 Suggestion ranking
- top category = max `count`
- tie-break by latest `last_used_at`
- confidence = `top_count / total_count_for_payee`
- labels:
  - High: `>= 0.80` and `top_count >= 3`
  - Medium: `>= 0.60` and `top_count >= 2`
  - Low: otherwise
- sort groups: High -> Medium -> Low, then newest transaction date desc

### 4.3 Lifecycle
- Queue source = live YNAB uncategorized + non-deleted transactions.
- Apply source of truth = YNAB API response.
- On successful apply:
  - update YNAB category,
  - record apply event (idempotent by transaction),
  - increment/update payee stats.
- Bootstrap:
  - fetch categorized history for last 24 months,
  - seed stats and bootstrap metadata.

## 5) HTTP Interfaces

### 5.1 Web page
- `GET /ynab-categorizer` (login required)

### 5.2 API (under `/api/ynab-categorizer`)
- `GET /queue`
- `POST /apply`
  - body: `{ "transaction_ids": ["..."], "category_id": "..." }`
- `POST /bootstrap`
  - body: optional `{ "force": false }`
- `GET /bootstrap-status`

### 5.3 Response shape guidelines
- consistent `{ "ok": bool, ... }` envelope for mutation endpoints
- include actionable error reason codes where useful
- never return YNAB token or raw authorization headers

## 6) Frontend Plan

### 6.1 New files
- `app/templates/ynab_categorizer.html`
- `app/static/css/ynab_categorizer.css`
- `app/static/js/ynab_categorizer.js`

### 6.2 UI behavior
- grouped by normalized payee
- suggestion badge (High/Medium/Low)
- category dropdown override
- checkbox list
- actions:
  - Apply selected
  - Apply all in group
  - Bootstrap now
- optimistic disabled states + clear error banners

### 6.3 JS conventions
- vanilla JS, null-safe DOM access
- utility formatting functions return `'—'` for missing data
- structured console logs with module prefix

## 7) Configuration and Security

### 7.1 New env vars
- `YNAB_API_KEY`
- `YNAB_BUDGET_ID`
- `YNAB_HTTP_TIMEOUT_S` (default e.g. `15`)
- `YNAB_HTTP_RETRIES` (default e.g. `2`)

### 7.2 Security rules
- login required for web + internal API
- keep CSRF enabled for browser POSTs
- avoid storing raw YNAB tokens in DB logs
- sanitize and bound external payload sizes

## 8) SQLAlchemy and DB Notes (Important Here)

1. This codebase uses SQLAlchemy Core + controller methods, not ORM models.
2. Timestamp/date columns should remain `Text` (ISO strings) for SQLite tests.
3. Upsert should be dialect-aware:
- PostgreSQL path can use `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update`.
- SQLite test path should use compatible conflict handling or fallback update-then-insert logic.

## 9) Tests (Repo Style)

### 9.1 Unit tests
- payee normalization edge cases
- suggestion confidence and tie-break logic
- grouping/sort order

### 9.2 Controller tests
- stats upsert behavior
- apply events idempotency
- bootstrap state read/write

### 9.3 API tests
- queue returns only uncategorized and non-deleted
- apply updates YNAB and persists local updates
- bootstrap seeds stats and writes bootstrap state
- unauthorized user is blocked

### 9.4 Candidate test files
- `tests/test_ynab_categorizer_logic.py`
- `tests/test_ynab_categorizer_api.py`
- `tests/test_ynab_categorizer_sqla.py`

## 10) Execution Checklist

1. Add schema + migration + controller mixin.
2. Add YNAB service/client module.
3. Add web/API blueprints and register.
4. Add template/CSS/JS and navbar link.
5. Add tests for logic/controller/API.
6. Run tests and fix lint/format hooks.
7. Update `readme.md` with concise feature note after implementation.

## 11) Default Decisions

1. Manual-only apply (no auto apply).
2. 24-month bootstrap window.
3. Queue based on live YNAB state.
4. Suggestion data stored locally in app DB.
