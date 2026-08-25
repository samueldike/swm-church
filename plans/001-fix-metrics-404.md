# Plan 001: Fix metrics 404 and timezone mismatch

> **Executor instructions**: Follow step by step. Run verifications. If STOP conditions occur, stop. Update status in plans/README.md.
> **Drift check**: `git diff --stat HEAD -- app/routes/admin.py app/views/admin/dashboard.html`

## Status
- Priority: P0
- Effort: S
- Risk: LOW
- Depends on: none
- Category: bug
- Planned at: commit `HEAD`, 2026-05-15

## Why this matters
Dashboard fetch to `url_for('admin.dashboard.metrics')` 404s because route named `admin_dashboard_metrics`. All ranges show "Failed to load". Fix restores live metrics.

## Current state
- `app/routes/admin.py:391` `name="admin_dashboard_metrics"` vs `dashboard.html:428` `url_for('admin.dashboard.metrics')`
- `admin_dashboard_metrics` uses `datetime.now(timezone.utc)` aware vs DB naive -> asyncpg error.

## Commands
| Purpose | Command | Expected |
| Typecheck | `py -m py_compile app/routes/admin.py` | exit 0 |
| Test | `py -c "import app.routes.admin; print('ok')"` | ok |

## Scope
In scope: `app/routes/admin.py`, `app/views/admin/dashboard.html`
Out of scope: other routes

## Steps
1. Rename route to `admin.dashboard.metrics` and implement full naive handling (see AUDIT_REPORT).
2. Verify `py -m py_compile`.

## Test
Manual: login as admin@scentofwater.org/admin123, GET /admin/metrics?mode=7d returns 200 JSON with stats.

## Done
- [ ] route renamed
- [ ] timezone naive
- [ ] compile ok

## STOP
If file already has stories routes, skip.

## Maintenance
If new metrics added, update both route and JS render.
