# YNAB transaction review

The categorizer is a Root-Admin tool available from Settings. It fetches a
live review queue from YNAB and learns payee/category preferences locally.
Suggestions are not applied automatically.

## Setup

Set `YNAB_API_KEY` and `YNAB_BUDGET_ID` in the main app's environment and
restart it. Optional `YNAB_HTTP_TIMEOUT_S` and `YNAB_HTTP_RETRIES` default to
15 seconds and 2 retries. Apply database migrations as described in the
[server README](../../readme.md#production).

Without the credentials, the workspace reports that the service is not
configured. The page and internal API require Root-Admin; browser POSTs also
require a CSRF token.

## Review workflow

1. Open the workspace to load uncategorized and categorized-but-unapproved
   transactions into one queue.
2. Use search and queue filters to choose which rows are visible. Review the
   proposed categories and change them as needed. These edits are local until
   submission; Reset visible restores the initial choices for visible rows.
3. Submit the visible rows. Every visible row must have a category. Submission
   includes unchanged visible rows too: those are approved as-is. Changed or
   previously missing categories are categorized and approved together.
4. The queue reloads after submission. Inspect any reported error before retrying.

The UI posts to `/api/ynab-categorizer/review-commit`. Separate `/apply` and
`/approve` endpoints remain for category-plus-approval and approval-only calls;
`/approvals-queue` is not the primary workspace source.

## Suggestions and filtering

Custom rules match top-to-bottom using normalized payees; first match wins.
Local category history supplies suggestions when no rule applies, with a
configured visible fallback category when history cannot provide one.
Existing categories are preserved as the initial choice for categorized rows.

Historical confidence is High at a share of at least 80% with three supporting
uses, Medium at 60% with two uses, otherwise Low. The bulk-suggestion setting
includes High confidence by default; Medium requires opt-in.

Default strict filtering omits transfers and split parents. Starting Balance
and deleted transactions are excluded, and reconciled transactions are hidden
unless enabled. Optional age limits use transaction dates and days/months/years.
Category choices exclude hidden/deleted categories and put the ten locally
most-used choices first. Filters, rules, fallback, and test mode persist in
budget-aware configuration through the Controller.

## Bootstrap and test mode

Bootstrap is manual. It replaces local payee/category statistics from eligible
categorized history covering the previous 730 days (approximately 24 months).
It records completion and skips repeat runs unless forced. It does not change
remote categories; a forced bootstrap does replace the local learning dataset.

Test mode suppresses remote writes and local learning updates for apply,
approve, and review submission. It leaves the remote review queue reusable.
It is **not offline mode**: queue loading and review validation still read
YNAB. Configuration saves and bootstrap still change local data.

## Implementation and validation

- [Service](../../app/services/ynab/ynab_categorizer_service.py): queue, rules,
  submission, confidence, and bootstrap behavior
- [Client](../../app/services/ynab/ynab_client.py): upstream requests and retries
- [API](../../app/blueprints/api/ynab_categorizer_api.py) and
  [UI](../../app/static/js/ynab_categorizer.js): request validation and review state
- [Controller](../../app/core/_controller/ynab_categorizer.py): config, statistics,
  bootstrap state, and idempotent apply-event recording

From `server/`, run `.venv/bin/pytest -q tests/test_ynab_categorizer_*.py`.
Use mocked requests and an isolated database; production YNAB is not a test fixture.
