# 00 — Discovery

Branch `audit/lead-engineer-review`, from `509c899` (2026-10-22). Production at that commit: https://swm.tutapis.com (one VPS, Docker Compose).

## Stack (as installed)

| Layer | What | Version |
|---|---|---|
| Web | FastAPI on Starlette, served by uvicorn (1 process) | fastapi 0.111.0, starlette 0.37.2 |
| Templates | Jinja2, server-rendered; Turbo for page swaps; Tailwind v4 (admin CSS precompiled, public CSS compiled **in the browser** by `tailwind-browser.global.js`) | jinja2 3.1.2 |
| DB | PostgreSQL via async SQLAlchemy 2 + asyncpg. No migrations: `create_all` + hand-written `ADD COLUMN` list (`app/database.py:_ADDED_COLUMNS`) | sqlalchemy 2.0.31 |
| Jobs | Celery + Redis: `worker` (Telegram distribution, Books Pub), `transcriber` (faster-whisper `large-v3-turbo`, int8), `beat` (empty schedule). In-process asyncio scheduler for reminders, campaigns, analytics, housekeeping | celery 5.5.3 |
| Auth | Session cookie (Starlette `SessionMiddleware`, signed with `SECRET_KEY`); bcrypt passwords; `session_version` revocation; per-email/IP login throttle. HarvestTrack mobile API: bearer tokens (hashed, expiring) | passlib/bcrypt 4.0.1 |
| Files | Audio → `MEDIA_ROOT` (`/app/media`, bind-mounted); images → `app/static/images/...` (bind-mounted on the server only); Telegram archive → `/app/telegram-archive` | — |
| Providers | Paystack + Flutterwave (giving), Resend (email), Telegram Bot API (self-hosted `telegram-bot-api` for 2 GB files), Telethon (archive), Anthropic/OpenRouter/Groq/Gemini (Books Pub) | keys in Admin → Settings, Fernet-encrypted |
| Geo/money | pycountry, babel, phonenumbers, pytz/tzdata — ISO country/region codes, E.164 phones, per-church IANA time zone, one platform currency (HQ's) | — |
| Deploy | `deploy.py` (paramiko, root + password from env): git pull → `docker build` → `compose up` → smoke test. The server keeps its **own, untracked** `docker-compose.yml`; a host nginx proxies to `127.0.0.1:8085` | — |

No README. `.env.example` exists (incomplete — see report OPS findings). `AGENTS.md` is the de-facto developer doc (has duplicated and stale sections).

## Size

- 316 tracked files; 238 py/html/js/css excluding vendored assets.
- `app/routes/admin.py` 8,023 lines (god file), `public.py` 1,702, `api.py` 1,012, `public_extra.py` 541.
- 26 model modules, 40 tables.
- **301 route+method pairs**: 229 admin, 52 public, 20 `/api` (full list: `audit/routes.tsv`).

## Data model (main relationships)

- `branches` (one `is_hq`) ← everything branch-owned: `members`, `ministers`, `events`, `sermons`, `givings`, `remittances`, `contact_inquiries`, `prayer_requests`, `branch_reports`, `unit_reports`, `branch_settings`, `harvest_*`.
- `admins` (role, `branch_id`, `department_id` for HODs) → `admin_audit_logs`, `admin_branches`.
- `departments` are **global** (not per branch) → `unit_reports` (CASCADE), HOD admins.
- `members` have no link to departments, households, or attendance. There is no per-service attendance table: attendance exists only as numbers inside monthly unit reports (JSON weekly grid) and branch reports (`metrics.avg_attendance`).
- `givings` (`amount Numeric`, `currency`, `status` pending/completed/failed, `method` online/cash/transfer…, `branch_id`, `gateway_owner`) — no edit/void routes; `remittances` branch↔HQ.
- Content: `sliders`, `photos`, `program_flyers`, `stories`, `site_settings` (key/value CMS), `publications` → `publication_sources` (→ `sermons`, CASCADE) → `publication_subtopics`.
- Engagement: `newsletter_subscribers`, `email_campaigns`, `email_logs`, `event_reminders`, `analytics_counters`.
- `telegram_archive_items` → `sermons`.

## Roles as enforced (not as labelled)

Enforcement: (1) `app/core/policy.py:admin_policy` on every `/admin/*` URL via `access._RULES` (a test fails if a route is uncovered); (2) `_foreign(admin, branch_id)` object checks in handlers. `/api/*` is **outside both layers** and only checks "signed in" for event writes.

| Role | Permissions (`access.ROLE_PERMS`) | Scope |
|---|---|---|
| `super_admin` (HQ Administrator) | everything | all churches |
| `hq_media` | media, audio, events, analytics, announcements.view | HQ records |
| `hq_finance` | donations, remittances | HQ |
| `hq_pastoral` | prayer, inquiries, members, reports, branch_reports, harvest, ministers | HQ |
| `branch_admin` | members, events, audio, inquiries, prayer, harvest, hods, reports, branch_reports, donations, remittances, branch_settings, ministers, activity | own branch |
| `resident_pastor` | dashboard, reports (review gate), ministers.view | own church |
| `hod` | hod_reports | own department reports |
| HarvestTrack worker (separate table, API token) | own contacts, follow-ups, reminders | self |

There is no member/visitor portal: visitors only submit public forms.

## Website content management

Admin → Settings (key/value `site_settings`): service times, beliefs, our story, pastor story, WhatsApp/Telegram links, prayer response time, legal pages, controlled lists, giving model/keys, bank accounts. Separate admin CRUD for sliders, photos, flyers, stories (rich text, **stored unsanitised**), events, messages (audio upload → Telegram → transcription), announcements. Readiness checklist (`app/core/readiness.py`) on Settings and the HQ dashboard. Several public texts are still hard-coded (outreach pages, CASOM page, devotional block, service-time table on `/watch` and `/calendar`, address fallbacks).

## Inferred context (with evidence)

- **Country/locale:** Nigeria — HQ in Abakaliki, Ebonyi State (`main.py` default `siteHeadquartersAddress`); Paystack/Flutterwave; NGN; `Africa/Lagos`; NDPA-oriented privacy text (`app/core/legal_defaults.py`). Production branches: HQ + Abuja, Port Harcourt, Ibadan, Kano, Enugu, Benin (all Nigeria). Diaspora branches are now supported (country codes).
- **Language:** English.
- **Denomination cues:** Pentecostal/charismatic (Resident Pastor, HOD unit reports, "Holy Ghost" style services, harvest/follow-up, CASOM school of ministry, General Overseer's office).
- **Size:** HQ + 6 branches; 19 HOD accounts; 7-ish departments; dozens of audio messages; 17 online gift attempts (all pending). Medium church network, not a megachurch.
- **Users:** a small HQ office (one HQ Administrator does most work), branch admins, HODs; non-technical; heavy mobile use.
- **Data note:** production HQ branch row says Lagos (`NG-LA`), the address says Abakaliki (Ebonyi). Flagged, not changed.

## Build, lint, tests (verbatim summaries)

- **Compile:** `py -m py_compile app/routes/admin.py app/main.py` → exit 0.
- **Tests:** `pytest tests -q` → `181 passed, 28 warnings in 315.02s` (warnings: `datetime.utcnow()` deprecation ×many).
- **Lint:** `ruff check .` (ruff 0.17.1, repo's own config) → `Found 1017 errors. [*] 250 fixable`. Real-bug classes (F821/E9/PLE) → **0**. Breakdown of meaningful ones: 12 ASYNC230 blocking `open()` in async handlers (incl. the 2 GB sermon upload loop), 8 F841 unused variables, 4 B023 (false positives on inspection), rest style (UP017, C408, SIM…). `--select F,B,E9,PLE,ASYNC` → 406, of which 357 are B008 (FastAPI `Depends` idiom) / F401.
- **Type checker:** none configured (no mypy/pyright config). Not added.
- **Root-level `test_*.py`** (10 files) are ad-hoc scripts, not collected by pytest.

## Seeded local environment

`audit/seed_local.py` builds a **fresh local** database `swm_launch` (refuses non-localhost hosts and the names `swm_db`/`swm_audit`) via the real app startup, then seeds: 6 churches (HQ + Abuja, Enugu, Port Harcourt, Accra GH, London GB), **408 members**, 10 ministers, 14 messages, 6 events, **320 gifts over 6 months** (completed/pending/failed, online/transfer/cash), a remittance, 12 branch reports over 6 months, 4 unit reports (draft/submitted/reviewed), inquiries, prayer requests, subscribers, stories, an announcement, HarvestTrack worker + contacts. Logins for every role, password `Launch-Audit-2026`. All personal data is invented (`@example.org`). Server: `uvicorn app.main:app --port 8010` with `DATABASE_URL=…/swm_launch SWM_DISABLE_SCHEDULER=1 SWM_EMAIL_DRY_RUN=1`.

Production was only **read** (container mounts, env var names, row counts, password-hash comparison inside the container). Nothing was written to production during the audit.
