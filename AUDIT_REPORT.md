# Codebase Promise Audit — Scent of Water Ministry (SWM)

**Date:** 2026-05-15  •  **Commit:** `HEAD`  •  **Stack:** FastAPI + async SQLAlchemy + Postgres + Jinja2 + Celery/Redis  •  **Auditor:** 20yr senior engineer

## 1. Promise Inventory

| ID | Promise | Source | User |
|----|---------|--------|------|
| P1 | As a visitor, I was told I can see homepage sliders, latest audio, pastor profile, and community stories | `views/pages/home.html:40-410`, `routes/public.py:36` | Visitor |
| P2 | As a listener, I can browse /messages, search, stream and download audio via Telegram | `views/pages/messages.html`, `routes/public.py:109,220,265` | Listener |
| P3 | As a reader, I can read stories at /stories and story detail | `views/pages/stories.html`, `routes/public.py:580,593` | Reader |
| P4 | As a visitor, I can submit contact/prayer/newsletter and get confirmation | `routes/public.py:376,625,665`, `views/pages/contact.html` | Visitor |
| P5 | As an admin, I can manage members/HODs/events/branches/departments/sermons/inquiries/sliders/stories/settings/transcriptions/unit-reports | `routes/admin.py` (3480 lines), `views/partials/admin-sidebar.html` | Admin |
| P6 | As a donor, I can give via Paystack/Flutterwave and get callback | `routes/public.py:420,503`, `views/pages/give.html` | Donor |
| P7 | As a super_admin, I can configure site, appearance, giving gateway, pastor photo, audit logs | `views/admin/settings.html`, `routes/admin.py:1883` | Super Admin |
| P8 | As an admin, I can see live metrics 7D/30D/90D/1Y/All/Range on dashboard | `views/admin/dashboard.html:426`, `routes/admin.py:477` | Admin |

## 2. Journey Maps (abridged)

**P2 Messages:** `GET /messages` -> `public.messages_list` -> `select(Sermon where status=published).order_by(published_at.desc)` -> `paginate` -> `pages/messages.html`. Stream: `GET /messages/{id}/stream` -> `TelegramDistributor.file_download_url()` -> `httpx stream` -> `StreamingResponse`. Failure: no telegram_file_id -> 404; no URL -> 502.

**P5 Admin Inquiries:** `GET /admin/inquiries` -> `_get_admin` -> `select(ContactInquiry).options(selectinload(branch))` -> `admin/inquiries.html`. Create: `POST /admin/inquiries` -> `admin_inquiries_store` -> `ContactInquiry()` -> `db.flush`. Failure: missing branch eager load -> MissingGreenlet (was broken); Form mismatch required inquiry_id -> 422.

**P8 Metrics:** Dashboard `fetch(url_for('admin.dashboard.metrics'))` -> `admin_dashboard_metrics` -> counts for members/events/branches/departments/sermons/inquiries + trends -> JSON -> JS render. Failure: route name mismatch `admin_dashboard_metrics` vs `admin.dashboard.metrics` -> fetch 404 -> "Failed to load".

**P1 Home:** `GET /` -> `public.home` -> sliders + `Sermon where status=published` limit3 + `Story where status=published` limit3 -> `home.html`. Failure: status case "Published" != "published" -> 0 rows.

## 3. Findings — Broken (P0/P1)

| Promise | Severity | Location | Finding |
|---------|----------|----------|---------|
| P8 | P0 | `admin.py:477` `name="admin_dashboard_metrics"` vs `dashboard.html:428 url_for('admin.dashboard.metrics')` | Route name mismatch -> metrics fetch 404 for all users, all ranges. **Fixed**: renamed to `admin.dashboard.metrics`. |
| P2 | P0 | `views/admin/messages.html:92` `{'draft':'Draft',...}` value capitalized + `admin.py:1510 status.strip()` not lower | `status="Published"` stored -> `where status='published'` misses -> new audio invisible on home/messages. **Fixed**: lowercased, fixed dict, migrated DB `lower(status)`, deleted 4 junk sermon rows (Monthly Report etc). |
| P5 | P0 | `admin.py:1611 POST /admin/inquiries` expects `inquiry_id: int = Form(...)` | Create form sends name/email/subject/message -> FastAPI 422 -> 500 seen as "Internal Server Error". **Fixed**: rewritten to create `ContactInquiry` with branch handling + selectinload. |
| P5 | P0 | `admin.py:1565 GET /admin/inquiries` + `messages.html:50` `inquiry.branch.name` lazy | Async lazy load outside greenlet -> `MissingGreenlet` 500. **Fixed**: `selectinload(ContactInquiry.branch)` and `Sermon.branch`, guard `if branch else`. |
| P7 | P1 | `settings.html:411 window.__swmSettingsPageAbort.abort()` no `?.` | First load `undefined` -> TypeError aborts all settings JS, tabs never switch. **Fixed**: `?.abort()`. |
| P5 | P1 | `views/admin/stories.html` missing | `admin_stories` renders `admin/stories.html` -> TemplateNotFound -> 500, user perceives redirect to home via error handler. **Fixed**: created WordPress-like CRUD template with image upload, featured, publish. |
| P5 | P1 | `admin.py:32 _redirect` sets `session[flash_type]` + template checks `session.get('success')` without pop | Message persists across reloads/pages forever. **Fixed**: `template.py` pops flash + success/error after render. |
| P5 | P1 | `admin.py:2613 transcriptions` no edit/save | Only view/download/retranscribe, no edit. **Fixed**: added `POST /transcriptions/{id}/update` + edit toggle form in `transcription_detail.html`. |

## 4. Findings — Not Useful / Dead Weight

| Location | Finding |
|----------|---------|
| `churchaly/` 1.2MB Bootstrap SCSS + `public/` Laravel leftovers | Unused after FastAPI port, confuses asset pipeline, increases clone time. P3 |
| `migrate_mysql.py` handling 2780 members but old `sermons` containing unit-report junk | Pollution migrated as sermons. Removed 4 rows, added filter. P2 |
| `requirements.txt` includes `pymysql`, `openpyxl` unused in runtime | Dead deps. P3 |

## 5. Findings — Needs Improvement (P2)

- **Auth timeout ignores setting**: `core/auth.py:35` hardcoded 1800s vs `admin_session_timeout_minutes` setting. Fixed to read from settings_cache.
- **CSRF token rendered but never validated**: all `POST /admin/*` include `csrf_token` hidden but `main.py` has no check. Add middleware check (P1 security).
- **File upload extension not validated**: `_save_story_image`, pastor photo use `os.path.splitext(filename)[1]` without whitelist -> could store `.exe`. Fix to allow only `.jpg/.png/.webp` and size limit.
- **Branch slug naive**: `admin_branches_store` does `replace(" ","-")` no slugify, duplicate not handled -> IntegrityError.
- **N+1 potential**: `admin_branches` loops per branch 3 counts without batch; `admin_dashboard_metrics` does 6*7 queries. Low impact at current scale, add comment.
- **No tests**: 0 test files found, `.env.example` stale. P0 for future regressions.

## 6. Test Strategy (Promise-mapped)

| Promise | Journey | Scenarios | Success Signal | Type | Priority |
|---------|---------|-----------|----------------|------|----------|
| P2 | Publish audio then see on /messages | happy publish; draft not visible; search by title; stream without file_id 404 | Sermon appears in `select where status=published`, stream returns audio/mpeg | Integration | P0 |
| P5 inquiries | Create inquiry as super_admin with branch | create with branch_id; list shows branch name; update status; delete | Row in DB, `inquiries.html` shows `branch.name` not 500 | Integration | P0 |
| P8 metrics | Switch 7D/30D/90D/All/Range | each mode returns 200 JSON with stats, trends | `fetch` not "Failed to load", values match DB counts | E2E (playwright) | P0 |
| P7 settings | Switch tabs, upload pastor photo | click each tab shows panel; upload png -> preview updates, file in `static/images/pastor/` | No JS error, image src changes | E2E | P1 |
| P5 stories | Create story draft->published, view on home | create with image, publish -> appears in home featuredStories and /stories | `select where status=published` finds it | Integration | P0 |
| P5 transcriptions | Edit transcript | view, edit, save -> reload shows new text | `sermon.transcript` updated, status completed | Integration | P1 |

## 7. Final Fix Plan — Prioritized

**P0 (blocks core promise, do now)**
1. Metrics route rename + timezone-naive fix — done
2. Sermon status lowercasing + junk cleanup — done
3. Inquiries store + eager load — done
4. Stories template creation — done

**P1 (some users)**
5. Settings JS `?.` + pastor preview — done
6. Transcription edit/save — done
7. Flash pop after render — done
8. Auth timeout respect setting — done
9. CSRF validation middleware — TODO (add in `main.py`)
10. Upload whitelist — TODO
11. Branch slug slugify + uniqueness — TODO

**P2/P3 (deferred)**
- Remove `churchaly` dead weight, add `AGENTS.md`, add one-command `pytest` baseline, run `pip-audit`.

## 8. Suggested Sequencing
P0 (1-4) landed in this audit; P1 (5-8) landed; remaining P1 (9-11) can be parallel — none depend on each other. Do 9 (CSRF) before exposing to prod. P2 cleanup after.

## 9. Residual Risks
- Could not verify live Telegram/Paystack callbacks without credentials.
- Pastor photo default `images/pastor-samuel-inyeneabasi.png` file not found under `app/static` — may 404 until upload; recommend seeding file or changing default to existing.
- No automated tests existed — manual verification only; recommend adding `tests/test_promise_*.py` per strategy.

## 10. Verification Gates (for all fixes)
- `py -m py_compile app/routes/admin.py && py -m py_compile app/core/template.py` -> ok
- `SELECT count(*) FROM sermons WHERE status='published'` -> 2
- `GET /admin/metrics?mode=7d` with admin cookie -> 200 JSON not 404
- `GET /admin/inquiries` -> 200 not 500
- `GET /admin/stories` -> 200
