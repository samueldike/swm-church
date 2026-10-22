# Audit report — Scent of Water Ministry platform

Branch `audit/lead-engineer-review` · audited commit `509c899` · 2026-10-22 · Phase 0–2 complete; **stopped for your approval of sections 4 and 5.**

Supporting documents: `audit/00-discovery.md` (stack, routes, models, roles, lint/test output), `audit/01-reality.md` (users and capabilities), `audit/routes.tsv` (all 301 route/method pairs with their required permission), `audit/screenshots/` (534 captures: 89 pages × 360/768/1280 px × light/dark, plus `_results*.json` with automated checks), `audit/seed_local.py` (fresh local test database), `audit/verify_findings.py` (reproduces the top findings).

## 1. Executive summary

- **Overall health: good bones, not yet safe to call "launched".** The admin console is well structured: every `/admin` URL passes a central permission check that a test enforces, branch isolation is checked per record, the CSRF and secrets handling are sound, and 181 automated tests pass. Across 534 screenshots there is **no horizontal overflow, no JavaScript error and no broken image**, and dark mode is consistent.
- **Two security holes that let a low-trust account act as someone else**, both reproduced locally: any signed-in role (even a department head) can delete or rewrite any church's events through `/api/events` (SEC-01). A branch admin can upload an `.html` file as "audio" and get it served from the church's own domain, which would let them hijack the HQ Administrator's session (SEC-02).
- **Money can be wrong:**
  - Online gifts are only confirmed if the donor's browser comes back. There is no webhook and no re-check, and **all 17 online gifts on production are still `pending`** (DATA-01).
  - Bank-transfer notices can never be confirmed (DATA-02).
  - The "Test/Live" switch does nothing; production says *test* (GIVE-01).
  - Remittances report "HQ owes each branch everything" while no HQ share is configured (UX-04).
- **Data can be lost:**
  - There are **no scheduled backups** (OPS-02).
  - Deleting a department deletes every church's reports for it (reproduced, DATA-03).
- **Production config:**
  - The site runs in development mode: `/docs` is public, and every SQL statement is logged with personal data (OPS-01).
  - The live site shows **four invented testimonies** (CONT-01).
- **For visitors:**
  - The home page is 5 MB and takes about 22 s to first paint on slow 3G (UX-02).
  - Missing pages show raw JSON (UX-01).
  - Service times are hard-coded on two pages (UX-05).
- **Go-live readiness: not yet.** About 2–3 focused days of fixes clear every P0 and P1 below (none needs a new feature). After that, launch is reasonable with the go-live checklist.
- **Local machine:** your **C: drive is full** (about 100 MB free). The audit's own output is 44 MB of JPEGs. Please free space before Phase 3; a full disk can corrupt the local Postgres.

## 2. Inferred context and assumptions

Pentecostal ministry, HQ in Abakaliki (Ebonyi, Nigeria) with 6 Nigerian branches in production and diaspora branches now possible; English; NGN via Paystack/Flutterwave and bank transfer; mobile-first, low-bandwidth audience; one small HQ office of non-technical staff; audio messages distributed to Telegram are the flagship content; attendance is reported monthly in aggregate (unit and branch reports), not per service; no member portal. Details and evidence: `audit/00-discovery.md` and `audit/01-reality.md`.

Assumptions I could not confirm: whether a HarvestTrack mobile app exists outside this repo (it uses `/api/harvesttrack/*`); whether anything outside the repo calls `/api/events`; the launch domain (production is `swm.tutapis.com`). Data note: production's HQ branch row says Lagos (`NG-LA`) but the address is Abakaliki (Ebonyi) — not changed.

How findings were verified: code reading by me and four parallel reviewers (security, data integrity, operations, relevance), every claim re-checked against the code; the five most serious **reproduced** against a fresh seeded local database (`audit/verify_findings.py`); production facts checked **read-only** (container mounts, env var names, row counts, password-hash comparison inside the container). One reviewer claim was **rejected** after checking production: "uploaded images are lost on every deploy" — the server bind-mounts `app/static/images`, so they persist (kept as part of OPS-03).

## 3. Findings

IDs: SEC = security/access, DATA = integrity/reliability, OPS = operations, GIVE/CONT/DEP = giving/content/dependencies, UX = visual & UX, HYG = hygiene.

## 3a. Code findings
### P0 — Critical

| ID | Area | Location | What's wrong | Why it matters | Reproduce | Proposed fix |
|---|---|---|---|---|---|---|
| SEC-01 | Access | `app/routes/api.py:273-402` | `POST/PUT/DELETE /api/events` only check "signed in". `/api` is outside the admin policy, `_foreign` and CSRF; PUT also trusts a posted `branch_id`. | A department head, finance user or Resident Pastor of any branch can delete or rewrite HQ's public calendar or any branch's events. | `audit/verify_findings.py` (HOD session → `DELETE /api/events/5`) | Require `has_perm(role,"events")` + `_foreign()`; force `branch_id` for non-HQ; or remove the write routes (no client in the repo uses them). Add `/api` to the route-coverage test. |
| SEC-02 | Uploads | `app/routes/admin.py:2620-2627` | Audio upload rejects a file only if content-type is non-audio **and** extension unknown, so `x.html` sent as `audio/mpeg` is stored as `media/sermons/<hex>.html` and served from the site's own origin. | Stored XSS on the site origin: a branch admin or HQ media user sends the link to the HQ Administrator, whose browser then acts as HQ Administrator (create admin, read keys page). | `audit/verify_findings.py` (branch admin uploads `probe.html`) | Extension whitelist only (as Books Pub upload does), sniff first bytes, serve `/media` with `nosniff` and `attachment` for non-audio. Clean up any non-audio file in `media/sermons`. |
| DATA-01 | Money | `app/routes/public.py:1385,1438`; `app/core/payment.py:56-58,83-87` | No Paystack/Flutterwave webhook and no re-verification of pending gifts; a provider 5xx during verify marks a paid gift `failed`. | Money taken but never counted; totals and remittances understated. **Production: all 17 online gifts are `pending`** (28–29 Aug, probably abandoned test checkouts, but nothing can ever reconcile them). | Pay, close the tab before redirect → gift stays `pending` forever. | Signature-verified, idempotent webhooks; treat `error` as pending; scheduled re-verify of pending gifts; admin "Re-check with gateway" button. |
| DATA-03 | Integrity | `app/routes/admin.py:4418`; `app/models/department.py:29`; `app/models/unit_report.py:15` | Departments are global and deleting one cascades (ORM + FK `ondelete=CASCADE`) to **every church's** unit reports for it. Only blocked by accident if an HOD still points at it. | One click erases years of HQ and branch reports. | `audit/verify_findings.py` (DATA-03) | Refuse delete when reports/HODs exist; offer Deactivate (`is_active` exists); FK → `RESTRICT`. |
| OPS-02 | Data safety | server (no cron/backup job) | No scheduled database or media backup. The only dumps are ones taken by hand before deploys (`/opt/swm-backups`, 2 files). `SECRET_KEY` is not backed up off-server either (losing it makes all saved keys unreadable). | A disk failure or bad command loses members, gifts, reports and every audio file. | `crontab -l` on the server: only certbot. | Nightly `pg_dump` (14-day retention) + media/archive sync off-site + `.env` copy in the church's password manager; one tested restore; runbook in README. |

### P1 — High

| ID | Area | Location | What's wrong | Why it matters | Reproduce | Proposed fix |
|---|---|---|---|---|---|---|
| OPS-01 | Config | `app/config.py:8`, `app/main.py:43,56-57,67`, `app/database.py:10` | Production runs with `APP_ENV` unset → "development": session cookie without `Secure`, **`/docs` and `/openapi.json` public** (verified 200 on production), SQL echo logs every statement and its parameters (prayer text, donor emails, password hashes) to Docker logs; the default-`SECRET_KEY` guard is skipped. | Privacy promise broken in logs; attack surface listed publicly; logs grow without bound. | `curl https://swm.tutapis.com/docs` → 200 | Default `APP_ENV` to production, set it on the server, `openapi_url=None` in prod, SQL echo opt-in, refuse to start on a placeholder/short `SECRET_KEY`. |
| SEC-03 | Rate limits | `admin.py:402`, `public.py:121`, `api.py:37` | Client IP = first `X-Forwarded-For` entry, which the client controls. | Every per-IP throttle (login 10/IP, public forms, checkout, transfer notice, Harvest login) is bypassable. | `audit/verify_findings.py` (SEC-03) | Use the proxy-added hop (`X-Real-IP` / last XFF) or uvicorn `--proxy-headers --forwarded-allow-ips`. |
| SEC-04 | Core flow | `admin.py:4190-4197` | Accepting an HOD invitation creates the account **without `department_id`** and with no minimum password length. | Every invited HOD files reports with no department; branch roll-up counts nothing; 1-character passwords accepted. | Invite HOD → accept with password `a` → submit report: department blank. | Set `department_id=invitation.department_id`; enforce ≥ 8 chars; repair existing HOD rows from their invitations. |
| DATA-02 | Money | `public_extra.py:469`; no admin route changes `Giving.status` | Bank-transfer notices are saved `pending` and can never be confirmed or rejected. | Finance must record a second, manual gift; the pending one stays forever; confirming later would double-count. | Submit `/give/transfer-notice`, open Donations: no action. | Confirm / reject actions for `pending` transfer rows (sets `completed` + `paid_at`, audit-logged). |
| GIVE-01 | Money/config | `settings.html:19,298-300`; `app/core/payment.py` | "Gateway mode: Test/Live" is saved (production value: **test**) but never read. | A treasurer believes the site is in test mode while live keys take real money, or vice versa. | Grep `giving_mode` in `app/core/payment.py`: no hits. | Enforce it (refuse a `sk_live` key in test mode and vice versa, show a "TEST MODE" banner on /give) or remove the control. |
| CONT-01 | Fake content | production `stories` table; `seed_content.py:34-55` | Four seeded stories with invented testimonies ("Michael T.", "downtown shelter") are **published on production**. | Visitors read made-up testimonies as real. | `select slug,status from stories` on production. | Unpublish (you decide whether to delete); remove `seed_content.py`. |
| DEP-01 | Dependencies | `requirements.txt` | Starlette 0.37.2 (< 0.40, CVE-2024-47874 multipart DoS on public forms); most packages unpinned, so a fresh build can pull new majors. | Public forms can be used to exhaust memory; unreproducible builds. | `pip-audit -r requirements.txt` | Bump fastapi/starlette to a fixed release; pin everything from the known-good image (`pip freeze`). |
| OPS-03 | Deploy | `deploy.py:110-133`; server `/opt/swm/docker-compose.yml` | The real production compose (which holds the image and media bind-mounts) is untracked; `deploy.py` copies it to `/tmp` and restores it after `git pull` — a failed pull leaves the repo compose live and the next run overwrites the backup. Images are tagged `latest`, so there is no rollback. | One failed deploy can lose the only copy of the production config; recreated containers could start on empty volumes. | Read `deploy.py`. | Commit `docker-compose.prod.yml`; deploy with `-f`; tag images by git sha; `pg_dump` as deploy step 0. |

### P2 — Medium

| ID | Location | What's wrong → fix |
|---|---|---|
| SEC-05 | `pages/stories_show.html:19`, `admin.py:5551,5563` | Story content rendered `|safe` and stored unsanitised (HQ media can plant script on the public site) → run `publication_html.sanitize` on save. |
| SEC-06 | `admin.py:4097-4099, 5394-5430` | HOD can rewrite a report after it was submitted/reviewed (breaks the Resident Pastor gate) → edit only while `draft`. |
| SEC-07 | `api.py:409-457` | Anyone can self-register an active HarvestTrack worker in any branch → create inactive pending approval, or remove self-registration. |
| SEC-10 | nginx / `main.py` | No `X-Frame-Options`/CSP `frame-ancestors`, `nosniff`, HSTS → add headers. |
| SEC-11 | `public_extra.py:342-381, 422-479` | Transfer-notice and event-reminder forms email any address with sender-controlled text → per-recipient cap, keep free text out of outgoing mail (needs SEC-03). |
| DATA-04 | `app/core/lists.py:67` vs `giving.giving_type(50)`, `contact_inquiries.topic(40)`, `ministers.education_level(40)` | A list item of 51–60 chars makes public checkout / contact return 500 → clamp to column length + `DataError` handler. |
| DATA-05 | `public.py:180,187,432,540,615,867`; 38 handlers in `admin.py` | `except Exception: pass` without rollback: one failed query aborts the transaction and the rest of the page silently renders empty → savepoints (`begin_nested`) or rollback + log. |
| DATA-06 | `admin.py:4894-4900` | "Re-transcribe" clears the existing transcript before the new one succeeds, and queues before commit → keep old text until success; commit before enqueue. |
| DATA-07 | `publication.py:65`, `admin.py:4519` | Deleting a (draft) message cascades to Books Pub chapters/days built from it; audio file left on disk → block/warn when used by a publication; delete file after commit. |
| DATA-08 | `scheduler.py:93-128` | Campaign batches not claimed atomically: crash/restart mid-batch re-sends up to 40 emails → conditional cursor update. |
| DATA-09 | `telegram.py:162`, `tasks.py:88` | A read timeout after Telegram accepted a large upload is retried → duplicate channel post → skip when already `sent` unless "Send again". |
| OPS-04 | server Docker | No log rotation (`/etc/docker/daemon.json` absent) — with SQL echo on, logs grow without limit → `max-size`/`max-file`. |
| OPS-05 | `app/main.py` | No `/healthz`, no container health checks; `deploy.py` ignores a failing smoke test → add both; external uptime monitor. |
| OPS-06 | `app/main.py` | No logging configuration or error monitoring; `logger.info` dropped → `logging.basicConfig` with timestamps; optional Sentry DSN in Settings. |
| OPS-07 | `admin.py:2637,3079,3103,3754,5479,7263` | Blocking file writes in async handlers (incl. the 2 GB sermon upload loop) stall every request during uploads → `asyncio.to_thread`/aiofiles. |
| OPS-08 | uploads / deploy | No disk-space guard on uploads; dangling images from every build never pruned → `usable_space()` check, `docker image prune -f` after deploy. |
| OPS-09 | nginx static | `/static` cached 30 days `immutable` with no cache-busting → browsers keep old CSS/JS after deploys → `?v=<sha>` in `url_for('static')`. |
| OPS-10 | `.env.example`, no README | Incomplete env docs (WHISPER_*, SWM_*, POSTGRES_PASSWORD), wrong hosts for Docker, no README/runbook → write both. |

### P3 — Low

| ID | Location | What's wrong → fix |
|---|---|---|
| SEC-09 | `admin.py` list filters (`and admin.branch_id`) | Branch filters fail open for a non-HQ admin with no branch (production: 0 such accounts) → one fail-closed helper. |
| SEC-12 | `admin.py:1651,1686` | HarvestTrack team not checked against worker's branch. |
| SEC-13 | `/media` | Draft/branch audio reachable by direct (unguessable) link. |
| SEC-14 | `admin.py:4842` | Transcript `.txt` download header built from raw title → 500 on `’` → use `_content_disposition`. |
| SEC-15 | `admin.py:5174,5188` | Branch page lists metadata of drafts/RP-pending reports. |
| SEC-16 | `payment.py:77` | Flutterwave `transaction_id` not validated before use in URL → `isdigit()`. |
| SEC-17 | `api.py:184,226,261` | `GET /api/events` returns other branches' unpublished drafts to any signed-in role. |
| SEC-19 | `auth.py:95`, `admin.py:405,413,3956` | Logout doesn't bump `session_version`; 5 bad passwords lock any account 15 min; login timing reveals existing emails; profile email change needs no password. |
| DATA-12 | `scheduler.py:47` | Reminders stuck in `sending` after a crash. |
| DATA-13 | `public.py:861,874`, `admin.py:617` | Upcoming-event lists compare church-local times with UTC (off by the zone offset). |
| DATA-14 | `admin.py:2596,4447,4451` | Message title not clamped (500 after a large upload); edit accepts any status string. |
| DATA-15 | `public.py:1349,1419` | Two simultaneous callbacks both mark completed and send two receipts. |
| DATA-16 | `public_extra.py:469` | HQ transfer notices stored with `branch_id=None` (left out of per-branch/country summaries). |
| DATA-17 | `public.py:1253` | Gateway HTTP call made while a DB transaction is open. |
| DATA-18 | `admin.py:6786` | No upper bound on remittance amount (Numeric(14,2) overflow → 500). |
| HYG-01 | repo root | 10 ad-hoc `test_*.py`, `run_server.py`, `temp_admin_*.html`, legacy MySQL scripts, empty `alembic/`, stale `TODO.md`/`AUDIT_REPORT.md`, unused deps (aiosmtplib, aiofiles, openpyxl, alembic, itsdangerous) — see Remove list. |
| HYG-02 | `ruff` | 1017 style findings (0 real-bug class); 8 unused variables; `datetime.utcnow()` deprecation warnings in tests. |
| HYG-03 | `AGENTS.md` | Duplicated sections (Ministers/RP ×2, CSRF ×2) and stale statements ("No emails are ever sent", "UTC+1", `url_for('admin.xxx')` with dots, smoke login `admin123`). |
| HYG-04 | `GET /admin/login` | Returns 405 instead of redirecting to the login page. |
| OPS-11 | Dockerfile/compose | Runs as root; `gcc` in final image; unpinned `telegram-bot-api:latest`; idle `beat` container; Redis without a volume (queued jobs lost on recreate); `deploy.py` uses root+password and accepts any host key. |

## 3b. Visual & UX findings

Automated checks over all 534 captures (`audit/screenshots/_results_all.json`): 0 pages with horizontal overflow, 0 console errors, 0 broken images; every page returns 200 except the deliberate 404 probe. Titles, meta descriptions and Open Graph images are present on every public page. Admin pages were retaken with the inner scroll area unrolled so the whole page is visible (`_results.json`).

### P1

| ID | Location | What's wrong | Why it matters | Reproduce | Proposed fix |
|---|---|---|---|---|---|
| UX-01 | any unknown URL; `public.py:596,737` | 404 shows raw `{"detail":"Not Found"}`; some routes return plain "Sermon not found" text; no 500 page. | A visitor following an old WhatsApp link sees a broken-looking page. | `/no-such-page` (screenshot `404_not_a_page`) | Styled 404/500 on the public layout, JSON only for `/api`. |
| UX-02 | `/` (home) | 5.0 MB, 28 requests, first paint 21.8 s, load 104 s on slow 3G (400 kbps). One slider image is 3.4 MB; every public page loads the 271 KB in-browser Tailwind compiler. | Most visitors are on mobile data; many leave before anything shows. | Throttled Playwright run (numbers above). | Resize/compress uploads on save (WebP, max 1920 px), `loading="lazy"` below the fold, precompile the public CSS like the admin CSS. |
| UX-03 | `/admin/events` (`events-management.html:26-27`) | The calendar sits in a 640 px-minimum horizontal scroller, so at 360 px only Sun–Tue are visible. | You asked for a full-week responsive calendar; on a phone it still isn't. | 360 px screenshot `admin_events`. | Compact 7-column grid under 640 px (day numbers + event dots, tap a day for its list) — the public calendar already does this. |
| UX-04 | `/admin/remittances` | With no HQ share configured (0 %), every branch shows "HQ owes branch ₦1,590,000"-style balances. | A treasurer reads an invented debt as fact. | 360 px screenshot `admin_remittances`. | When the share is not set, show "Set this branch's HQ share to see balances" instead of a balance. |
| UX-05 | `watch.html:58-61`, `calendar.html:121-128`, `watch.html:41` | Service times hard-coded (the watch page lists 4 services, the visit page 6 from Settings); the watch page tells visitors to "Add them under Admin → Settings". | Times go wrong the day Settings change; admin instructions leak to visitors. | Compare `/watch` and `/visit` at 360 px. | Render from the injected `services`; hide empty sections. |

### P2

| ID | Location | What's wrong → fix |
|---|---|---|
| UX-06 | `admin-src.css:209-210` (from my commit `f7b9723`) | Negative word-spacing on monospace labels is inherited by sans text inside them, so spaces vanish: "BRANCH (branchadminsonly)" on `/admin/admins/create` → reset `word-spacing` on `.font-sans` children. |
| UX-07 | `admin/messages.html:64` | "Podcast feed" / "Website only" badges shown on **branch** messages, which never reach the HQ website or feed → show "Branch only" for branch-owned messages. |
| UX-08 | 70 admin pages at 360 px | Tap targets under 32 px (row actions, "Mark praying", Filter, avatar menu) — the public site meets 44 px, the admin doesn't → `min-height:40px` on `.btn-sm`/icon buttons on small screens. |
| UX-09 | home page reveal animation | Sections are `opacity:0` until JavaScript reveals them; with JS blocked/failed they stay invisible (verified with JS disabled) → only hide when `html.js` is set. |
| UX-10 | `/get-involved` vs `/departments` | 12 hard-coded units on one page, 7 departments from the database on the other → one source (departments table + optional public description). |
| UX-11 | HOD report form at 360 px | Sticky Submit / Save draft / Cancel bar takes three full-width rows (~150 px) of a small screen → one row, Cancel as a link. |

### P3

| ID | Location | What's wrong |
|---|---|---|
| UX-12 | contact/prayer branch pickers | "Headquarters (Headquarters)". |
| UX-13 | `/branches` with no listed branch | Large "0 branches" headline above the friendly empty text. |
| UX-14 | `/watch` | Channel list numbering starts at "02" when YouTube isn't set. |
| UX-15 | `/admin/dashboard` 360 px | Date strip clipped ("Oct 22, 202"). |
| UX-16 | public mobile menu (`theme-header.html:292,294`) | Two "Calendar" buttons. |
| UX-17 | `home.html:240`, `about.html:135`, `casom.html:142`, `contact.html:59`, events hero | Hard-coded and inconsistent address fallbacks ("#1 Scent Close…", "No. 1 Scent Close", "Mile 50") instead of Settings. |
| UX-18 | `GET /admin/login` | 405 instead of redirecting to the sign-in page (bookmarks). |

## 4. Remove / simplify (needs your approval)

Nothing here has been removed. "Remove" = delete from the repo; "hide" = keep the code, take it out of the UI.

| # | Item | Evidence it's unused / unrealistic | Risk of keeping | Recommendation |
|---|---|---|---|---|
| R1 | Fake seeded stories on production (4 published) + `seed_content.py` | Invented testimonies ("Michael T.", "downtown shelter"); `seed_content.py:34-55` | Visitors read fabricated testimonies as real | **Unpublish now** (data change on production, so I'll only do it with your yes); remove `seed_content.py` |
| R2 | `POST/PUT/DELETE /api/events` | No client in this repo; bypasses both access layers (SEC-01) | Security hole | **Remove** (or, if an outside app uses them, route them through the policy). Keep the read endpoints |
| R3 | "Gateway mode: Test / Live" setting | Saved, never read by `payment.py` (GIVE-01) | Treasurer misled about real money | **Simplify**: replace with an automatic badge derived from the key (`sk_test_…` → "Test mode") |
| R4 | Four read-only "not yet enforced" security settings (2FA, strong passwords, remember-me, audit retention) | `settings.html:362-369` | Look like features that don't exist | **Hide** until built |
| R5 | Disabled "Order now — coming soon" devotional button on the home page | `home.html:494-495` | Dead button on the home page | **Hide** (or link to a real devotional) |
| R6 | `/coming-soon` page | No link reaches it, not in the sitemap (`public.py:1496`) | None | **Remove** |
| R7 | Unused templates: `emails/branch-admin-invitation.html`, `emails/hod-invitation.html`, `emails/harvesttrack-worker-credentials.html`, `vendor/pagination/tailwind.html` | Never rendered (grep) | Confusion; worker-credentials would email a password | **Remove** |
| R8 | Old Vite bundle `app/static/build/` (169 KB), unused images (`pastor-samuel-inyeneabasi2.png`, devotional `.jpg`) | Not referenced | Image/repo weight | **Remove** (after checking `site_settings` doesn't point at them) |
| R9 | Repo-root leftovers: 10 `test_*.py` scripts, `run_server.py`, `temp_admin_*.html`, `ins_audit.py`, MySQL migration scripts (`migrate_mysql.py`, `extract_mysql.py`, `migrate_data.py`), `seed_2026_yearly_planner.py`, `seed_home2_media.py`, empty `alembic/` + `alembic.ini`, stale `TODO.md` (Laravel), `Html2WebApp.md`, old `AUDIT_REPORT.md`, `plans/` | One-off or obsolete; shipped into the Docker image by `COPY . .` | Clutter, confusion, image size | **Remove**, or move the migration scripts to `scripts/legacy/` |
| R10 | Unused Python deps: `aiosmtplib`, `aiofiles` (unless used by OPS-07), `openpyxl`, `alembic`, `itsdangerous`; `pymysql` (legacy scripts only) | Never imported by `app/` | Larger image, more CVE surface | **Remove** |
| R11 | Idle `beat` container | `beat_schedule={}` (`celery_app.py:27`) | A container that does nothing | **Remove** from compose |
| R12 | HarvestTrack **self-registration** (`POST /api/harvesttrack/register`) | Admins already create workers; anyone can register into any branch (SEC-07) | Impostor workers see contacts | **Remove** self-registration (keep the rest of the mobile API) — or **keep** if your mobile app depends on it (question Q2) |
| R13 | `seed_admin.py` creating `admin123` | Startup already flags default passwords | A known password in the repo | **Simplify** into a `scripts/reset_admin_password.py` recovery tool that prompts for a new password (pairs with gap M7) |

Kept on purpose (I considered and rejected removal): Books Pub (real use by the media team), Telegram archive (you asked for it), analytics (counts only, cheap), campaigns (email is used), multi-currency plumbing (needed for diaspora branches even with one platform currency).

## 5. Missing features (needs your approval)

### Must-have before go-live
| # | Gap | Why (one line) |
|---|---|---|
| M1 | Styled 404 and 500 pages | Visitors currently see raw `{"detail":"Not Found"}` |
| M2 | Scheduled backups + tested restore + runbook | One disk failure loses everything (OPS-02) |
| M3 | Payment webhooks + re-verify pending gifts + confirm/reject bank-transfer notices | Otherwise paid gifts go uncounted and transfer notices can never be reconciled (DATA-01/02) |
| M4 | Service times on `/watch` and `/calendar` from Settings; hide admin instructions shown to visitors on `/watch` | Wrong times on the public site if Settings change |
| M5 | Production configuration: `APP_ENV=production`, `siteUrl` set, security headers, log rotation | OPS-01, OPS-04, SEC-10 — config, not features, listed here because they need a decision on the domain |
| M6 | A lighter home page (compressed slider images, precompiled public CSS instead of in-browser Tailwind) | 5 MB / 22 s first paint on slow 3G for the most visited page |
| M7 | Admin password recovery path: "Forgot? Ask your administrator" on the login page; HQ/branch admins can reset their own HODs' passwords; a server-side recovery script for a locked-out HQ Administrator | Today a forgotten HQ password has no way back in |

### Should-have soon (first month)
| # | Gap | Why |
|---|---|---|
| S1 | CSV export of members (and ministers, reports), scoped to the user's branch | Office staff live in spreadsheets; data portability for the church |
| S2 | Link a `/visit` inquiry to a follow-up contact in one click | First-timer follow-up is the most valuable workflow and today requires re-typing |
| S3 | Web screen for follow-up workers (or confirm the mobile app exists) | Workers have logins but no web page to use them (question Q2) |
| S4 | Consent/privacy line next to every public form | NDPA; the privacy link is only on prayer and visit |
| S5 | Temporary passwords shown on a one-time page, not in a flash message stored in the session cookie | The cookie is signed, not encrypted |
| S6 | Birthdays this month (dashboard card) | DOB is collected but never used; a common pastoral need |
| S7 | Void/correct a recorded gift with a reason (audit-logged), instead of no way to fix a mistake | Treasurer needs to correct typos without deleting history |

### Nice-to-have later
| # | Gap | Why |
|---|---|---|
| N1 | Households/families on members | Useful for visitation; not used by any current screen |
| N2 | Per-service attendance headcount entry (multiple services per Sunday) | Today attendance only exists in monthly reports |
| N3 | Members ↔ departments | Enables "members by department" honestly |
| N4 | WhatsApp broadcast integration | Email-only bulk messaging is weak in Nigeria; WhatsApp Business API costs money and approval |
| N5 | Move remaining hard-coded public content (outreach pages, CASOM, devotional block, address fallbacks) into Settings | Admin independence from a developer |

## 6. Open questions for you

1. **Fake stories (CONT-01/R1):** may I unpublish the four seeded stories on production now? Delete them, or keep them as drafts?
2. **HarvestTrack mobile app:** does a mobile app use `/api/harvesttrack/*` today? If not, should workers get a simple web page (S3), or should I hide HarvestTrack logins?
3. **`/api/events` writes (R2):** does anything outside this repo use them? If unsure, I'll lock them to the same permissions as `/admin/events` instead of removing them.
4. **Gateway mode (GIVE-01/R3):** OK to replace the Test/Live switch with an automatic badge read from the key prefix?
5. **Backups (M2):** where should off-site copies go — Google Drive, Backblaze B2, or another server? (I need credentials or a target you own.)
6. **Launch domain:** stay on `swm.tutapis.com` or move to the church's own domain? It decides `siteUrl`, payment callback URLs and email sender.
7. **HQ location data:** production HQ row says Lagos; the address says Abakaliki (Ebonyi). Shall I set HQ to Ebonyi (`NG-EB`)?
8. **Deploy access:** may I switch `deploy.py` to an SSH key and a non-root deploy user (and you rotate the root password that was shared in chat)?
9. **Production config changes** (OPS-01: `APP_ENV=production`; OPS-04 log rotation): these change the live server; OK to apply them in Phase 3 with a backup first?

## 7. Proposed fix plan (in order)

Each step: small commit referencing IDs, a regression test where it's code, re-screenshot where it's UI, and a production backup before anything touches production.

1. **Stop the bleeding (P0, code only):** SEC-01 lock `/api/events` writes · SEC-02 audio extension whitelist + `nosniff`/attachment on `/media` · DATA-03 block department delete when reports/HODs exist (deactivate instead) · tests for all three (+ `/api` added to the route-coverage test).
2. **Money correctness (P0/P1):** DATA-01 webhooks (signature-verified, idempotent) + "error ⇒ pending" + scheduled re-verify + "Re-check" button · DATA-02 confirm/reject transfer notices · GIVE-01 (per your answer) · UX-04 remittance "share not set" state · DATA-15/16/18 small fixes.
3. **Production safety (P0/P1, with your OK):** OPS-02 nightly `pg_dump` + media sync + restore test + runbook · OPS-01 `APP_ENV=production`, `openapi_url=None`, SQL echo opt-in, refuse placeholder `SECRET_KEY` · OPS-04 log rotation · SEC-10 security headers · OPS-03 commit `docker-compose.prod.yml`, sha-tagged images · DEP-01 bump Starlette/FastAPI, pin requirements.
4. **Access and flows (P1/P2):** SEC-03 real client IP · SEC-04 HOD invite department + password length (and repair existing rows) · SEC-05 sanitise stories · SEC-06 lock submitted HOD reports · SEC-07 per your answer · SEC-11 email caps.
5. **Visitor experience (P1):** UX-01 404/500 pages · UX-02 image compression + lazy loading + precompiled public CSS · UX-05 service times from Settings · CONT-01 per your answer.
6. **Admin UX (P1/P2):** UX-03 phone calendar · UX-06 word-spacing regression · UX-07 branch badges · UX-08 tap targets · UX-09 reveal without JS · UX-10/11.
7. **Approved removals (section 4)**, then **approved must-haves (section 5)** not already covered above (M7 recovery path).
8. **P2 reliability:** DATA-04…09, OPS-05…10.
9. **P3 polish** and HYG cleanup (AGENTS.md duplicates, unused deps, ruff auto-fixes).
10. **Phase 4:** the nine 360 px scenarios, Resolution column in this report, go-live checklist, README.

## Draft go-live checklist (finalised in Phase 4)

- Server `.env`: `APP_ENV=production`, a 64-character `SECRET_KEY` (copy kept in the church's password manager), `APP_URL=https://<domain>`, a strong DB password.
- Backups: nightly DB dump + media sync off-site, one restore tested.
- Admin → Settings: `siteUrl`, contact email/phone, HQ address, service times, Resend key + verified sender, Paystack/Flutterwave live keys with callback/webhook URLs set in the gateway dashboards, bank accounts, Telegram bot + channel (and App api_id/hash for large files), legal pages reviewed, lists reviewed.
- HQ branch page: country, region (Ebonyi), currency, time zone.
- Smoke: `/`, `/messages`, `/give`, `/contact` 200; `/docs` and `/openapi.json` 404; cookie has `Secure`; one small live gift completes; one audio upload reaches Telegram and is transcribed; uptime monitor + disk alert set.
