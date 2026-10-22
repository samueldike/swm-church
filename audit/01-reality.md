# 01 — Reality: who uses this and what they need

## Inferred context and assumptions

- A Pentecostal ministry headquartered in **Abakaliki, Nigeria** (HQ + 6 Nigerian branches in production), English-speaking, growing a diaspora (the platform now supports branches in other countries).
- **One HQ office runs the system.** The HQ Administrator (General Overseer's office) does most configuration; branch admins run their own branch; heads of departments (HODs) file monthly unit reports; a Resident Pastor reviews them where one is appointed.
- **Money is NGN**, giving is via Paystack/Flutterwave and bank transfer; tithe/offering/building/missions are the purposes. Platform currency and country are HQ's (user decision, 2026-10-21).
- **Mobile-first, low bandwidth**: most visitors and many admins use mid-range Android phones on Nigerian mobile data.
- **Messages (audio sermons) are the flagship content**: upload → Telegram channel → transcript → website.
- **Communication** that works in context is WhatsApp and Telegram, more than email. The system sends email (Resend) and links to WhatsApp/Telegram; it does not send SMS or WhatsApp itself.
- Assumption: there is **no member portal** and none is expected (no route, no model, no mention in docs).
- Assumption: per-service attendance is reported **monthly in aggregate** through unit/branch reports (the church's existing paper-form shape: weekly male/female/total grid). That matches the HOD report form, which mirrors a printed template.

## Real users mapped to this system

| User | In this system | Main device | What they need most |
|---|---|---|---|
| Senior pastor / GO's office | `super_admin` (HQ Administrator) | phone + laptop | dashboard: giving, attendance from reports, branch performance, follow-up; announcements to branches |
| Church administrator / secretary | `super_admin` at HQ, `branch_admin` at a branch | laptop on Mon, phone on Sun | members, events, messages upload, website content, inquiries |
| Treasurer / finance | `hq_finance`, branch admin for a branch | laptop | correct totals per currency/period, recorded cash/transfer gifts, remittances, export, audit trail |
| Department heads | `hod` | phone | the monthly unit report, nothing else |
| Resident Pastor | `resident_pastor` | phone | review department reports before the office |
| Follow-up / welcome team | HarvestTrack workers (mobile API) + `hq_pastoral` | phone | assigned contacts, next follow-up, outcome |
| Media team | `hq_media` | laptop | messages, sliders, stories, campaigns, Books Pub |
| Public visitors | public site | phone | service times, location, what to expect, contact/prayer, give, listen to messages |

## Core capabilities: present and working?

Legend: ✅ works end-to-end on the seeded DB · ⚠️ exists with defects (see report IDs) · ❌ missing

| Capability | Status | Evidence / notes |
|---|---|---|
| Member records (contact, birthday, status, country/region) | ✅ | `/admin/members`, CSV import, branch/country filters; DOB stored |
| Households / families | ❌ | no model or field (ministers have `spouse_name` only) |
| Birthdays / anniversaries list or messages | ❌ | DOB stored, never used anywhere |
| Members ↔ departments | ❌ | members are not assigned to departments; the old dashboard "by department" chart was fabricated (fixed in `509c899`) |
| Member export (CSV) | ❌ | import only; exports exist for donations, subscribers, harvest, security logs |
| First-timer / visitor capture | ⚠️ | `/visit` form → `ContactInquiry(kind="visit")`; follow-up lives separately in HarvestTrack (mobile API); **no link** from a visit inquiry to a follow-up contact (manual re-entry) |
| Follow-up workflow (captured→assigned→contacted→outcome) | ⚠️ | HarvestTrack has stages + follow-ups + reminders, but workers have **no web UI** (API only, no client in repo) and anyone can self-register (SEC-07) |
| Attendance per service | ⚠️ | only monthly aggregates in unit/branch reports; no per-service headcount entry; dashboard attendance = submitted branch reports |
| Giving: online | ⚠️ | checkout + callback verify amount/currency; **no webhook, no re-verify**; production: all 17 online gifts still `pending` (DATA-01) |
| Giving: record cash/transfer | ⚠️ | `/admin/donations/record` works; **transfer notices can never be confirmed** (DATA-02) |
| Giving: totals, per currency, per period, export | ✅ | per-currency totals, filters, CSV export (formula-safe) |
| Giving: immutable audit trail | ⚠️ | no edit/delete routes (good) and record actions are audit-logged; but no void/correct path either |
| Expenses | ❌ | not in scope of the codebase (no model). Not proposing it unless you want it |
| Departments with leaders and scoped access | ⚠️ | HOD scoping works; **invited HODs get no department** (SEC-04); deleting a department deletes all its reports (DATA-03) |
| Events / calendar | ⚠️ | admin CRUD, public calendar, ICS, reminders; `/api/events` write bypass (SEC-01) |
| Announcements (HQ → branches) | ✅ | `/admin/announcements`, dashboard display |
| Communication | ⚠️ | email campaigns + transactional email (Resend); WhatsApp/Telegram are links only; campaign batches can double-send (DATA-08) |
| Reports leadership reads | ✅ | dashboard (giving trend, member distribution, country summary), branch reports, unit reports with RP gate |
| Roles & permissions enforced server-side | ⚠️ | `/admin` is well covered (policy + `_foreign`, tested); `/api` is not |
| Website CMS without a developer | ⚠️ | Settings covers service times, story, beliefs, links, legal, lists; but some pages still hard-code service times/addresses (UX findings); story HTML unsanitised (SEC-05) |
| Online audio (messages) | ✅ | upload up to 2 GB, Telegram distribution, transcription, public player with Range |
| Data safety: backups | ❌ | no scheduled backup on the server (only ad-hoc dumps taken before deploys) |
| Data safety: privacy | ⚠️ | NDPA privacy text; analytics without IP/cookies; but production logs every SQL statement including personal data (OPS-01) |
| Children's data | n/a | no children's records in the system (Children Church is only a department name) |

## Context realities applied

- Phones: E.164 storage, country-aware parsing ✅. Currency: one platform currency, babel formatting ✅. Time: per-church IANA zone, ICS in UTC ✅; some list views still compare church time with UTC (DATA-13).
- Low bandwidth: the **public site compiles Tailwind in the browser** (268 KB JS on every page) and loads full-size images; measured in the report (UX section).
- Local payment rails: Paystack/Flutterwave + bank transfer details ✅; webhook missing ⚠️.
- Non-technical admins: plain-language admin copy is mostly good; destructive actions use a confirm dialog (`site-confirm.js`); a few admin instructions leak onto public pages (UX findings).
