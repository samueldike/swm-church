# AGENTS.md — SWM FastAPI

## Stack
FastAPI + async SQLAlchemy + Postgres (asyncpg) + Jinja2 + Celery/Redis. Python 3.12. No `pyproject.toml` — deps in `requirements.txt`.

## Commands
| Purpose | Command | Expected |
| Typecheck | `py -m py_compile app/routes/admin.py && py -m py_compile app/main.py` | exit 0 |
| Tests | `pytest -q` | all pass |
| Lint | `ruff check .` (if installed) else `py -m py_compile` | exit 0 |
| Dev server | `uvicorn app.main:app --reload --port 8000` | http://localhost:8000 |
| DB | `DATABASE_URL=postgresql+asyncpg://swm_user:swm_password@localhost:5433/swm_db` | — |
| Migrate | `py migrate_mysql.py` | idempotent, handles `&amp;` ; inside strings |

## Conventions
- Routes: `app/routes/admin.py` is god file (~2500 lines). Split by `# ─── Section ───` comments. Keep new admin routes there, use `_get_admin` + `_redirect`.
- Templates: `app/views/admin/*.html` — Jinja, `url_for('admin.xxx')` with dots (e.g., `admin.dashboard.metrics`). Check `app/routes/admin.py` name.
- CSRF: all POST forms include `csrf_token`; `app/main.py` middleware validates against `session["csrf_token"]`.
- Flash: `_redirect` sets both `flash_messages` and `session[flash_type]`; `app/core/template.py` pops after render — message shows once.
- Auth: `app/core/auth.py` reads `admin_session_timeout_minutes` from settings_cache, not hardcoded 1800.
- Uploads: whitelist `.jpg/.jpeg/.png/.webp/.gif` for images, audio `mp3/wav/m4a/...` max 50MB images 5MB; use `slugify` + suffix for branch/department/story slugs.

## Gotchas
- Postgres columns are `TIMESTAMP WITHOUT TIME ZONE` (naive). Always compare with naive `datetime.utcnow()`, not `timezone.utc` aware, else asyncpg `can't subtract offset-naive` error (see metrics).
- Sermon `status` is lowercase `published/draft`; form sends capitalized `Published` — lower it.
- `Branch.slug` / `Department.slug` must be unique — use loop with `select(...).where(slug==...)`.
- `ContactInquiry.branch` and `Sermon.branch` need `selectinload` else `MissingGreenlet` on `inquiry.branch.name`.
- `migrate_mysql.py` regex for `INSERT ... VALUES (...);` breaks on `;` inside `'...&amp;...'` — use scan for `;` outside quotes.

## Verification
After any admin change: `py -m py_compile app/routes/admin.py` and `pytest -k test_journeys -q` should pass. Manual smoke: login `admin@scentofwater.org` / `admin123`, check `/admin/metrics?mode=7d` 200, `/admin/inquiries` 200, `/admin/stories` 200.
