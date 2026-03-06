## YNAB Categorization Assistant v2 (Flask Plugin, SQLAlchemy/Postgres)

### Summary
Build a new categorization-focused module inside your existing Flask server, reusing only the YNAB API integration logic (`ynab_client.py` concept) and replacing the old CSV/watcher pipeline entirely.  
The feature will show a web review queue of **only uncategorized YNAB transactions**, grouped by payee, with **manual apply only** (no auto-apply).

### Implementation Changes
1. Add a new finance domain module in the server app:
- `app/blueprints/web/ynab_categorizer_web.py`: authenticated page route `/ynab-categorizer`
- `app/blueprints/api/ynab_categorizer_api.py`: JSON API under `/api/ynab-categorizer/*`
- Register in existing blueprint aggregators.

2. Add a new YNAB service layer (requests-based, adapted from current `ynab_client.py`):
- `get_transactions_since(date)` and filter to `deleted == false` and `category_id is null` for queue.
- `get_categories()` for category picker labels.
- `update_transactions_bulk([{id, category_id}])` to set **category only**.
- Keep auth via existing env-stored YNAB token/budget id; add explicit timeout/retry handling.

3. Add SQLAlchemy models + Alembic migrations (Postgres):
- `ynab_payee_category_stats`
  - `id`, `budget_id`, `payee_normalized`, `category_id`, `count`, `last_used_at`, `created_at`, `updated_at`
  - Unique: `(budget_id, payee_normalized, category_id)`
  - Index: `(budget_id, payee_normalized)`, `(budget_id, category_id)`
- `ynab_apply_events`
  - `id`, `budget_id`, `transaction_id`, `payee_normalized`, `category_id`, `applied_by_user_id`, `applied_at`
  - Unique: `(budget_id, transaction_id)` (idempotency/audit)
- `ynab_bootstrap_state`
  - `budget_id` PK, `bootstrapped_at`, `history_start_date`, `history_end_date`

4. Implement suggestion engine (payee-frequency rules):
- Normalize payee with fixed rule:
  - trim -> NFKD diacritic fold -> uppercase -> non-alnum to single space -> collapse spaces.
- Suggest top category by highest `count`; tie-break by latest `last_used_at`.
- Confidence formula: `top_count / total_count_for_payee`.
- Label thresholds:
  - High: `>= 0.80` and `top_count >= 3`
  - Medium: `>= 0.60` and `top_count >= 2`
  - Low: otherwise
- Queue group sort: `High -> Medium -> Low`, then newest transaction date desc.

5. Bootstrap and runtime learning:
- One bootstrap action reads categorized YNAB history for **last 24 months** and seeds `ynab_payee_category_stats`.
- Every successful category apply updates/increments stats immediately.
- Queue lifecycle is derived from live YNAB data only; transaction disappears once categorized in YNAB.

### Public Interfaces
1. Web route:
- `GET /ynab-categorizer`
  - Login required (reuse existing server auth).

2. API routes:
- `GET /api/ynab-categorizer/queue`
  - Returns grouped uncategorized transactions with suggestion/confidence/category options.
- `POST /api/ynab-categorizer/apply`
  - Body: `{ "transaction_ids": ["..."], "category_id": "..." }`
  - Action: patch category only in YNAB, then update local stats/events.
- `POST /api/ynab-categorizer/bootstrap`
  - Body: optional `{ "force": false }`
  - Action: build stats from last 24 months categorized history.
- `GET /api/ynab-categorizer/bootstrap-status`
  - Returns last bootstrap metadata and counts.

3. UI behavior:
- Batch-by-payee panel.
- Each group shows suggested category + confidence badge + transaction list (checkboxes).
- “Apply to selected” and “Apply to all in group” actions.
- Manual category override dropdown always available.

### Test Plan
1. Unit tests:
- Payee normalization deterministic cases (diacritics, punctuation, whitespace).
- Suggestion ranking and confidence threshold behavior.
- Stats increment/upsert correctness and tie-break handling.

2. API/service tests:
- Queue endpoint returns only uncategorized, non-deleted transactions.
- Apply endpoint updates only `category_id` and records `ynab_apply_events`.
- Idempotency on repeated apply for same transaction.
- Bootstrap creates expected stats from mocked 24-month history.

3. Integration/acceptance:
- Logged-in user can open `/ynab-categorizer`, review grouped queue, and apply category in batch.
- After apply, affected transactions no longer appear in queue after refresh.
- Suggested category quality improves after repeated confirmed actions.

### Assumptions and Defaults
- SQLAlchemy + Alembic + Postgres infrastructure is the intended current architecture in this repo (even if not yet present in files).
- Existing Flask login/session system remains the auth boundary.
- Another service continues importing transactions to YNAB; this module only categorizes.
- No auto-apply in v1; all assignments require explicit user action.
