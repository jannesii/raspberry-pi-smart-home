# YNAB Categorizer Implementation TODO

## 0. Scope Lock
- [x] Root-Admin only access (web + API)
- [x] Manual bootstrap only (no auto-run)
- [x] Navigation via Settings tile only
- [x] Default queue mode: `strict` (skip transfers + split parents)
- [x] Queue mode configurable globally in DB

## 1. Core Data Layer
- [x] Add SQLAlchemy Core tables in `app/core/schema.py`:
  - [x] `ynab_payee_category_stats`
  - [x] `ynab_apply_events`
  - [x] `ynab_bootstrap_state`
  - [x] `ynab_categorizer_config`
- [x] Add indexes/constraints:
  - [x] Unique `(budget_id, payee_normalized, category_id)` on stats
  - [x] Unique `(budget_id, transaction_id)` on apply events
- [x] Add dataclasses in `app/core/models.py`
- [x] Add controller mixin `app/core/_controller/ynab_categorizer.py`
- [x] Wire mixin exports (`_controller/__init__.py`, `controller.py`, `core/__init__.py`)
- [x] Add non-spammy `logger.debug()` to touched/new methods

## 2. Migration
- [x] Create Alembic migration for all YNAB tables/indexes
- [x] Verify schema parity with `app/core/schema.py`

## 3. YNAB Services
- [x] Add `app/services/ynab/ynab_client.py`:
  - [x] timeout + retry/backoff
  - [x] validated responses + redacted logging
  - [x] methods for transactions/categories/bulk update
- [x] Add `app/services/ynab/ynab_categorizer_service.py`:
  - [x] payee normalization
  - [x] suggestion ranking/confidence
  - [x] queue grouping
  - [x] apply flow + local stats/audit update
  - [x] bootstrap seeding (24 months)
- [x] Export in `app/services/ynab/__init__.py`
- [x] Initialize singleton in `app/services/__init__.py`

## 4. Web + API Integration
- [x] Add web route `GET /ynab-categorizer` in `app/blueprints/web/ynab_categorizer_web.py`
- [x] Add API blueprint `app/blueprints/api/ynab_categorizer_api.py`:
  - [x] `GET /queue`
  - [x] `POST /apply`
  - [x] `POST /bootstrap`
  - [x] `GET /bootstrap-status`
  - [x] `GET/POST /config`
- [x] Register web/api blueprints in aggregators
- [x] Enforce root-admin guard + login_required
- [x] Keep CSRF enabled on browser POST endpoints

## 5. Frontend
- [x] Add template `app/templates/ynab_categorizer.html`
- [x] Add CSS `app/static/css/ynab_categorizer.css`
- [x] Add JS `app/static/js/ynab_categorizer.js`
- [x] Include hidden CSRF token in template
- [x] Add settings tile link in `app/templates/settings.html`
- [x] Implement UI:
  - [x] payee-grouped queue
  - [x] suggestion confidence badge
  - [x] per-group and selected apply actions
  - [x] bootstrap action + status
  - [x] queue mode selector

## 6. Configuration
- [x] Add env parsing for:
  - [x] `YNAB_API_KEY`
  - [x] `YNAB_BUDGET_ID`
  - [x] `YNAB_HTTP_TIMEOUT_S`
  - [x] `YNAB_HTTP_RETRIES`
- [x] Graceful `not_configured` responses if YNAB env vars missing

## 7. Tests
- [x] Add `tests/test_ynab_categorizer_logic.py`
- [x] Add `tests/test_ynab_categorizer_sqla.py`
- [x] Add `tests/test_ynab_categorizer_api.py`
- [x] Cover:
  - [x] normalization + ranking + sorting
  - [x] upserts + idempotency + config persistence
  - [x] root-admin access + CSRF + validation + happy paths

## 8. Docs
- [x] Update `readme.md` with concise feature + env vars
- [x] Update `AGENTS.md` with new critical YNAB pitfalls/patterns (if discovered)

## 9. Final Verification
- [x] Run targeted tests for YNAB
- [x] Run broader regression tests affected by touched modules
- [ ] Manual smoke test in browser for end-to-end flow
