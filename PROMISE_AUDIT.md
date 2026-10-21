# Codebase Promise Audit — Scent of Water Ministry (SWM)

**Date:** 2026-09-24 · **Stack:** FastAPI + async SQLAlchemy + Postgres 16 + Jinja2 + Celery/Redis · **Method:** promise inventory → journey tracing → verification against a **clone of the real Postgres database** (`swm_db` → `swm_audit`, so real row counts, real column types, real sequences) → fixes → regression tests.

This audit follows on from the May 2026 `AUDIT_REPORT.md`, whose findings are all still fixed. Everything below is new.

## Executive summary

Read-only journeys (browsing the site, admin lists, calendar, feed, the HarvestTrack API, Books Pub) worked against real data: a 132-URL crawl of every GET route with ids taken from the real database found one failure. The failures were concentrated in **writes on the migrated tables**, in **features that collected data nobody could see**, and in **last-mile feedback** to visitors. Three promises remain only partly verifiable because they need things this audit could not exercise (live payment keys, the Telegram/transcription worker, outbound email — which does not exist).

The single most damaging problem was invisible in every earlier test because those ran on empty SQLite databases: **the real Postgres database had 8 out-of-date id sequences** (for example `members` holds ids up to 2,780 but its sequence was at 1). Every "create" on those tables returned *Internal Server Error* — including the transcript upload that triggered this audit. It is fixed at the root: the app now resyncs every sequence at startup.

Three visitor-facing promises were silently hollow: **prayer requests**, **newsletter sign-ups** and **donations** were collected but no admin could see them, and the visitor often never saw a confirmation or error message. Those now have admin pages, and public notices work on every page.

## 1. Promise inventory

| ID | Promise ("As a … I was told I can … in order to …") | Source | User |
|----|---|---|---|
| P1 | Visitor: see the ministry, pastor, sliders, latest audio and stories on the home page | `home.html`, profile docs | Visitor |
| P2 | Listener: browse, search, **stream and download** audio messages, and seek within them | `messages*.html`, `public.py` | Listener |
| P3 | Reader: read stories | `stories*.html` | Reader |
| P4 | Visitor: **send a message** and be told "we will get back to you" | `contact.html` ("We read every one") | Visitor |
| P5 | Visitor: **send a prayer request**; "everything is received, nothing leaves this room" | `prayer.html` | Visitor |
| P6 | Visitor: **subscribe to the newsletter** for updates, sermons and events | legacy footer copy | Visitor |
| P7 | Donor: **give online** (Paystack/Flutterwave) and have the gift recorded | `give.html` | Donor |
| P8 | Visitor: view events and a month calendar | `events.html`, `calendar.html` | Visitor |
| P9 | Visitor: pick up the podcast feed / sitemap | `feed.xml`, `sitemap.html` | Visitor |
| P10 | Admin: sign in securely and manage members, HODs, departments, branches, events, sliders, photos, flyers, stories | admin console | Admin |
| P11 | Admin: upload/manage audio messages, get transcripts, distribute to Telegram | `messages*.html`, AGENTS.md | Admin |
| P12 | Admin: read and answer **contact inquiries** | `inquiries.html` | Admin |
| P13 | Admin: **see prayer requests** | implied by P5 | Pastoral team |
| P14 | Admin: **see the newsletter list** and export it | implied by P6 | Super admin |
| P15 | Admin: **see donations** and totals (dashboard "giving trend") | dashboard, implied by P7 | Super admin |
| P16 | Super admin: configure site, giving gateways, security, integrations; keys stored encrypted | `settings.html` | Super admin |
| P17 | HOD: submit monthly unit reports; admin reviews them | `hod/reports`, `unit-reports` | HOD |
| P18 | Worker (HarvestTrack app): register, log in, record contacts, follow-ups, reminders | `/api/harvesttrack/*` | Field worker |
| P19 | Admin: build a **Daily Devotional / Book** from sermon transcripts with AI, edit, export | Books Pub | Publisher |
| P20 | Admin: invite HODs / branch admins | invitation flows | Admin |
| P21 | Admin: dashboard metrics 7D…All | `dashboard.html` | Admin |
| P22 | Any admin: safe from CSRF; sessions expire per setting | AGENTS.md | Admin |
| P23 | Visitor: contact details on the site are correct | contact page | Visitor |
| P24 | Visitor: read privacy policy and terms | footer links | Visitor |
| P25 | Admin: HarvestTrack teams/workers management | `harvesttrack.html` | Admin |
| P26 | Everyone: the site is fast and does not break on unusual input | implicit | All |

Promise sources were the ministry profile documents, page copy, the admin UI and AGENTS.md. P13–P15 are **inferred** (the public promise implies someone can act on what was collected).

## 2. Journey maps (the promises that failed)

```
P10/P11  Admin creates a message (the reported error)
  POST /admin/messages/create-transcript -> Sermon(...) -> db.flush()
  -> INSERT INTO sermons -> id from sermons_id_seq (value 1)
  -> Postgres: duplicate key sermons_pkey (Key (id)=(1) already exists)   [database.py / migrated data]
  -> unhandled IntegrityError -> "Internal Server Error"
  Same hop fails for members, events, departments, unit_reports, sliders, contact_inquiries, harvest_workers.

P5  Visitor sends a prayer request
  POST /prayer (public.py prayer_store) -> PrayerRequest(status="pending") -> flash "standing with you in prayer"
  -> redirect /prayer -> prayer.html reads session['prayer_success'] (never set)  => visitor sees nothing
  -> admin: no route, no page, no sidebar link for PrayerRequest anywhere       => nobody ever reads it
  Also model default status is "new", form wrote "pending".

P6  Visitor subscribes
  The only <form action=newsletter> lives in partials/site-footer.html, referenced by 0 templates => no live page has it.
  POST /newsletter -> NewsletterSubscriber -> no admin page / export.

P7/P15  Donor gives
  POST /give/checkout -> Giving(pending) -> gateway -> GET /give/callback
  -> verify -> status = "completed"      | dashboard _giving_trend_data filters status == "success"  => total always 0
  -> a pending (unfinished) payment is stamped "failed"; paid amount is never compared with the recorded amount
  -> gateway/network exception => 500;  purpose longer than 50 chars => DataError 500 on Postgres
  -> no admin list of donations

P2  Listener plays a message
  GET /messages/{slug} -> template reads sermon.branch.name -> lazy load outside a greenlet => MissingGreenlet 500
  GET /messages/{id}/stream -> Content-Disposition built from the raw title => UnicodeEncodeError (curly quotes, accents) => 500
  -> local files served with FileResponse: no Range support (starlette 0.37) => seeking/resume broken; remote branch advertised Accept-Ranges but ignored Range

P4/P5/P6  Any public form error
  _redirect(... "error") -> only give.html rendered errors; contact/prayer/every other page rendered nothing
```

## 3. Findings

Severity: **P0** breaks a core promise for most users or data integrity · **P1** breaks it for some users or realistic conditions · **P2** degraded experience/risk · **P3** cleanup. Status: **Fixed** (with a test), **Open** (owner action or not verifiable here).

### Broken

| Promise | Sev | Location | Finding | Status |
|---|---|---|---|---|
| P10, P11, P12, P17, P25 | **P0** | `app/database.py`, live data | 8 Postgres id sequences behind `MAX(id)` (`members` 2780 vs 1, `unit_reports` 294, `events` 25, `sermons`, `departments`, `contact_inquiries`, `harvest_workers`, `sliders`). Every create on those tables → 500 `duplicate key … _pkey`. Caused the reported transcript-upload error. | **Fixed** — `sync_sequences()` runs at startup (idempotent). Verified: "Resynced 8 out-of-date id sequences". |
| P2 | **P0** | `public.py:sermon_detail`, `models/*` | Public message page 500s for any published sermon that has a branch (`MissingGreenlet` on `sermon.branch.name`). Crawler found it on the real data. | **Fixed** — eager load + `lazy="selectin"` on all many-to-one relationships used by templates (16 relationships), removing the whole class of bug. |
| P15 | **P0** | `admin.py:_giving_trend_data` vs `public.py:give_callback` | Callback writes `status="completed"`, dashboard sums `status=="success"`, so **the giving trend/total was always ₦0**. | **Fixed** — both accepted; canonical value is `completed`. |
| P7 | **P0** | `public.py:give_callback` | Paid amount never checked against the gift (under-payment recorded as paid); an unfinished payment stamped `failed`; provider/network exception → 500. | **Fixed** — `paid/pending/failed/error` states, amount check (`mismatch` status), friendly message on outage. |
| P5, P4, P6 | **P0** | `prayer.html`, all public pages | Prayer confirmation read `session['prayer_success']` which is never set; contact/prayer/newsletter **error and info notices rendered nowhere** (only give.html showed errors). Visitors never learned if submission worked. | **Fixed** — one `partials/public-flash.html` on every public page. |
| P10 | **P1** | `admin.py` departments, harvest teams/workers, messages | Duplicate department **name** (unique), duplicate harvest team/worker email, or two messages with the same title (slug unique) → 500. | **Fixed** — pre-checks with friendly messages; unique sermon slugs; global `IntegrityError` handler as a safety net (redirect + message, 409 for API). |
| P2 | **P1** | `public.py` stream/download | Titles with curly quotes/accents/non-Latin letters raise `UnicodeEncodeError` (headers are latin-1) → 500. | **Fixed** — RFC 5987 `Content-Disposition`. |
| P7 | **P1** | `public.py:give_checkout` | Purpose >50 chars overflowed `VARCHAR(50)` → 500; `nan`/`inf`/1e12 amounts accepted; gateway exception → 500. | **Fixed** — clamps, range/finite check, email check, failed gift marked. |
| P4, P5, P18 | **P1** | public forms, API | Over-long name/subject/phone fields overflowed VARCHAR → 500 on Postgres. | **Fixed** — clamps. |
| P2 | **P2** | `public.py` | `HEAD` on stream/download returned 405 (players/probes). | **Fixed**. |

### Missing

| Promise | Sev | Finding | Status |
|---|---|---|---|
| P13 | **P0** | No admin page for prayer requests at all. | **Fixed** — `/admin/prayer-requests` (search, status filter, mark praying/answered/archived, delete for super admin, sidebar badge). |
| P15 | **P0** | No admin list of donations. | **Fixed** — `/admin/donations` (filters by status/gateway/date/text, confirmed total, CSV export). |
| P14 | **P1** | No admin view/export of newsletter subscribers. | **Fixed** — `/admin/subscribers` (search, unsubscribe/reactivate, CSV export). |
| P6 | **P1** | The newsletter form existed on **no live page** (only in an unreferenced legacy partial). | **Fixed** — form in the home and shared footers; email format validated. |
| P2 | **P1** | Seeking/resume in audio impossible (no HTTP Range). | **Fixed** — range-capable local file serving (206/416/suffix ranges); remote proxy no longer claims ranges. |
| P4 | P2 | Visitors are promised "we will get back to you" but **nothing notifies staff** and no email is ever sent (also true for invitations, prayer). Staff only see the sidebar badge. | **Open** — needs an outbound-email design (Resend key field exists in Settings but no sender). |

### Needs improvement

| Promise | Sev | Finding | Status |
|---|---|---|---|
| P22, P10 | **P0** | Admin login had **no brute-force protection**, while AGENTS.md publishes the default admin credentials and they work on the real database. Failed attempts on unknown emails were not even logged. | **Fixed (code)** — 5 failures/email and 10/IP per 15 minutes. **Open (owner):** change the default `admin123` password. |
| P18 | **P1** | `/api/harvesttrack/login` unthrottled; `register` is open to the internet. | **Fixed** — throttled. Open registration is unchanged (it may be intentional for the mobile app) — decide if it should be admin-only. |
| P4–P7 | P1 | Public forms had no abuse guard. | **Fixed** — per-IP limits on contact, prayer, newsletter, give. |
| P14, P15 | P2 | CSV exports could carry spreadsheet formula injection. | **Fixed** — cells starting `= + - @` are neutralised. |
| P22 | P2 | Default `SECRET_KEY` (`change-me`) would make sessions forgeable and saved keys easy to decrypt. | **Fixed (detection)** — critical log at startup and a red banner in Settings. The current `.env` value is **not** the default. |
| P5 | P3 | Prayer status vocabulary mismatch (`pending` vs model default `new`). | **Fixed**. |

### Not useful / dead weight

| Location | Finding | Status |
|---|---|---|
| `partials/site-footer.html`, `site-header.html`, `site-head-assets.html` | Legacy layout, referenced by 0 templates; carried the only newsletter form and dotted `url_for` names that hid the missing-form problem. | **Removed**. |
| `churchaly/` (62 MB), `public/` (8.8 MB), Laravel-era `temp/`, `migrate_*.py`/`extract_mysql.py`/`seed_*.py`/`ins_audit.py` at repo root | Untracked or one-off tooling cluttering the root. | **Open (P3)** — move under `scripts/` or delete when the owner confirms. |
| Books Pub upload drafts | Draft sermons created by Books Pub uploads also increment the **Audio Messages** sidebar badge. | **Open (P3)** — exclude by a flag if it confuses staff. |

### Owner actions (content/data, not code — nothing was changed in the real database)

| Promise | Finding |
|---|---|
| P23 | The public **contact page shows `+234 (555) 123-4567`** (obviously a placeholder) and `info@scentofwater.org`, while the ministry profile says phone/email are "not publicly available". Settings → General site → Support phone / Public contact email. |
| P23 | The published address reads "Ota Ewa Road … Ebonyi **Sate**" — profile says "Otta Ewa … State". |
| P24 | Privacy policy and Terms pages say "has not been published yet". Add them under Settings → Legal pages. |
| P10 | Default admin `admin@scentofwater.org / admin123` is documented in AGENTS.md and works. Change it. |

## 4. Test strategy (mapped to promises)

| Promise | Journey | Scenarios | Success signal | Type | Coverage now |
|---|---|---|---|---|---|
| P10/P11 | Any create on migrated tables | Create member/event/dept/message on a DB whose sequences lag | Record exists, no 500 | Integration on **Postgres clone** | `audit_fixes` run (duplicate names/slugs/emails); `sync_sequences` verified on the real DB |
| P2 | Play/download/seek | curly-quote title; `bytes=a-b`, `-n`, out-of-range; HEAD; published sermon with branch | 200/206/416, header encodes, page renders | Unit + integration | `tests/test_audit_regressions.py` + Postgres run |
| P5/P4/P6 | Public forms → admin | valid, invalid, over-long, spam | Visitor sees notice; admin sees record | Integration | Postgres run (81 checks) |
| P7/P15 | Give → callback → dashboard/admin | pending, under-payment, outage, replay, both gateways, bad amounts | Status correct; dashboard total; donations list/CSV | Unit (mock transport) + integration | `test_audit_regressions.py` + Postgres run |
| P22 | Login | 5 failures then correct password | Locked, other accounts unaffected | Unit + integration | Both |
| P18 | HarvestTrack API | register/login/CRUD/follow-up/reminder/isolation/logout/throttle | Every call correct; workers isolated | API integration | Postgres run |
| P19 | Books Pub | full pipeline, all providers, secrets | Document built/edited/exported | Unit + integration + browser | 55 + 124 checks (earlier work) |
| P7 (live) | Real Paystack/Flutterwave | Test-mode card payment end to end | Gift `completed` after redirect | **Manual QA, needs keys** | **None — gap** |
| P11 | Upload → transcribe → Telegram | Worker + Whisper + bot token | Transcript appears | **Manual/e2e, needs worker** | **None — gap** |
| P4 | Staff notified of new inquiry/prayer | (feature missing) | Email/notification received | Integration | **None — gap** |

## 5. Final fix plan

### Executive summary
The application's *code* was in decent shape; its **data and last-mile behaviour** were not. One root cause (stale Postgres sequences) broke every create for migrated tables, two features (prayer, newsletter) collected data nobody could see, and visitors never saw error messages. All of that is fixed and now guarded by tests. What remains is decisions and content only the owner can make (contact details, default password, email notifications) and end-to-end checks that need live credentials.

### Prioritised list
**P0 — done**
1. Resync id sequences at startup (`app/database.py:sync_sequences`, `app/main.py`). *S — done.*
2. Eager-load many-to-one relationships; fix public message page. *S — done.*
3. Giving: status agreement, amount verification, pending/failed states, outage handling, input limits. *M — done.*
4. Prayer/Subscribers/Donations admin pages + navigation; newsletter form on live pages; public notices on every page. *M — done.*
5. Login brute-force protection. *S — done.*

**P1**
6. **Change the default admin password; set a strong `SECRET_KEY`.** *S — owner.*
7. Replace the placeholder phone/email/address on the contact page. *S — owner.*
8. Decide whether HarvestTrack self-registration should stay open. *S — owner.*
9. Live payment test with each gateway in test mode. *S — needs keys.*

**P2**
10. Outbound email (notify staff of new inquiries/prayers; send invitations) — the Resend key field exists but no sender does. *M.*
11. Move throttle state to Redis if more than one web worker is used (currently per-process). *S.*

**P3**
12. Clean repo root (`churchaly/`, one-off scripts); publish privacy/terms; separate Books Pub drafts from the Audio Messages badge. *S.*

### Sequencing
Items 1–5 were independent and are complete. Do 6–9 before the next public announcement (they are configuration, minutes each). 10 is the only sizeable item and unlocks the "we will get back to you" promise properly.

### Residual risks
- **Not verifiable here:** live Paystack/Flutterwave, Telegram distribution, Whisper transcription and the Celery worker (Redis is not reachable from the host in this environment), and outbound email (does not exist).
- The Postgres verification used a **clone**; nothing in the real database was modified except the automatic sequence resync at app start.
- The throttle is in-process; behind several workers an attacker gets up to N× the limit.
- Promises P13–P15 are inferred, not stated anywhere; confirm with the pastoral team that these admin views match how they actually work.
