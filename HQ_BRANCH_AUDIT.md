# HQ + Branches Operating-Model Audit — Scent of Water Ministry (SWM)

> **Status (implemented):** the fixes below were built after this audit, following your decisions — branch messages never reach the HQ website; every branch brings its own Telegram bot; giving supports both "central through HQ" and "each branch collects" with an admin switch. `scripts/rbac_matrix.py` now reports **0 gaps** (was 28) and `scripts/hq_branch_checks.py` runs 91 end-to-end checks. Open items: multi-branch users, HQ-wide announcements to branches, attendance/offering by service, email/SMS delivery, per-branch public pages, and read-only visibility of HQ events for branches.


**Question audited:** if a church **HQ** (which promotes the General Overseer) used this system to run its own operations *and* those of its branches, does the system fit reality? Where does it grant access nobody would expect, or assume things that are not true?

**Evidence:** every admin route read; the real database inspected (7 branches, 1 super admin, 42 HODs, 0 branch admins, 2,780 members across the 7 branches, 1 published branch sermon); and an **executable access matrix** (`scripts/rbac_matrix.py`: HQ super admin, two branch admins, two HODs, seeded data in HQ/A/B, 60 probes of who can read/change whose data). 28 probes confirmed real gaps (two further flags were soft expectations about HQ/branch event visibility). Re-run it any time with `py scripts/rbac_matrix.py`.

## 1. Verdict

**Not yet fit for HQ + branches.** The data model *looks* multi-branch (a `branch_id` on members, events, sermons, inquiries, reports, workers), and **list screens** are mostly branch-filtered. But the model stops there:

1. **Authorisation is only cosmetic.** Filtering happens on list pages; the actions behind them (edit, delete, publish, invite) do not check whose record it is. A branch admin can edit or delete another branch's members and HQ's audio messages.
2. **The public website and every integration are single-tenant.** The site, the podcast feed, the Telegram channel, the payment gateway and the bank accounts belong to "the ministry", not to HQ-vs-branch. Your example is the clearest case: a branch pastor's message is published on the HQ website and is offered the HQ Telegram channel by default.
3. **There is one power level above a branch.** `super_admin` can do everything, including changing payment keys. Real HQs need several HQ roles (media, finance, pastoral care) and branch roles (resident pastor, branch admin, department head) with different reach.

Today this is hidden because the live system has **one super admin and no branch admins** (all 42 HODs are department heads). The moment branch admins are invited, the gaps below become live.

## 2. How a real HQ/branch church works (the yardstick)

| Concern | Reality |
|---|---|
| Identity | HQ is the *brand and covering*; branches are local congregations under a resident pastor. |
| Media | HQ media promotes the General Overseer. **Branch pastors' messages are the branch's**, distributed on the branch's channels and paid for by the branch. |
| Money | Tithes/offerings are collected **per branch** (own bank accounts); branches remit an agreed portion to HQ. HQ needs branch-level giving and remittance visibility; branches must not see each other's or HQ's. |
| People | Members belong to one branch (and transfer). Pastoral/confidential data (prayer, counselling, follow-up) stays with the pastor of that branch. |
| Reporting | Departments report monthly to the resident pastor; HQ sees roll-ups and comments. |
| Authority | HQ can see everything below it; a branch sees only itself; department heads see only their department's work. Nobody edits a peer branch's records. |
| Website | HQ site = HQ content + a directory of branches. Branches get their own page (times, address, pastor), not a seat in HQ's feed. |

## 3. Findings

Severity: **P0** breaks trust/security or the core reality · **P1** blocks realistic operation · **P2** friction.

### 3.1 Accesses that should not exist (verified by the matrix)

Actor = *Branch-A admin* (role invited as `branch_super_admin`; every guard treats it as "not super admin").

| # | Severity | What is possible | Where |
|---|---|---|---|
| A1 | **P0** | **Edit any branch's member, event, inquiry, transcript, harvest worker, HOD account**, and **rename any department**. | `admin_members_update`, `admin_events_update`, `admin_inquiries_update`, `admin_transcriptions_update`, `admin_harvesttrack_workers_update`, `admin_hods_update`, `admin_departments_update`: only `if not admin` — no ownership check. |
| A2 | **P0** | **Delete** another branch's member, **HQ's audio messages** and **HQ events**. | `*_destroy` routes: same missing check. |
| A3 | **P0** | Edit/unpublish an **HQ** audio message (removing the General Overseer's message from the site). | `admin_messages_update`. |
| A4 | **P0** | Read another branch's and HQ's **transcripts** by id; open **any branch's page** (`/admin/branches/{id}`). | `admin_transcriptions_show`, `admin_branches_show`. |
| A5 | **P1** | **A department head (HOD) can open**: full member list (name/phone/email of the whole branch), all inquiries, audio messages, events, transcriptions, the dashboard, the HarvestTrack export, HOD list, departments; can **create inquiry records**; and can **invite a new HOD into a different branch**. The sidebar hides these links but the routes do not. | `_get_admin` used as the only guard on ~40 routes; `hods/invite` trusts the posted `branch_id`. |
| A6 | **P1** | **Prayer requests** (my new page) are visible to every non-HOD admin, but requests are not tied to a branch, so a branch admin would read confidential requests meant for HQ pastors. | `admin_prayer_requests`. |
| A7 | **P2** | Vocabulary drift: `admin`, `branch_super_admin`, `super_admin`, `hod` are all in use; `Admin.role` default is `admin` while invitations create `branch_super_admin`; guards compare only against the strings they happen to know. | `models/admin.py`, `admin.py:391,1610,3625`. |

Correctly blocked (the parts that *are* multi-branch): a branch admin cannot list other branches' members/messages/inquiries/transcriptions/events, cannot open Settings, Security Logs, Admins, Branches list, Donations, Subscribers, Sliders, Stories, Books Pub, and cannot see other branches' HODs or reports.

### 3.2 Unrealistic instances (the system assumes things that are not true)

| # | Severity | Drift from reality | Evidence |
|---|---|---|---|
| U1 | **P0** | **Branch audio appears on the HQ website and home page.** The public site lists `Sermon.status == "published"` with **no branch filter**; the real database already has a Port Harcourt Branch message published on the HQ site. | `public.py` `messages`, `home`, `feed.xml`, `stream`, `download` (no `branch_id` predicate). Matrix: visitor sees branch messages on `/messages` and `/`. |
| U2 | **P0** | **One Telegram bot and one channel for the whole ministry**, and the "Distribute to Telegram" box is **ticked by default for every uploader**. A branch pastor's upload is sent to the HQ channel (and HQ bears the cost/quota and the 2 GB local-API server). | Settings keys `telegram_bot_token`, `telegram_audio_chat_id` are global; `messages_create.html` default `['telegram']`; `admin_messages_store` queues `distribute_audio_task` for any admin. |
| U3 | **P0** | **Any branch admin can publish straight to the public HQ site** — there is no approval step between "branch uploads" and "HQ website shows it". | Matrix: branch admin publishes a message → `published`. |
| U4 | **P1** | **Transcription/AI cost is shared and unmetered**: Whisper `large-v3` on HQ's worker runs for any branch upload; no quota, no owner. | `transcribe_audio_task` queued for every upload. |
| U5 | **P1** | **Giving is not branch-aware.** The Give page, bank accounts, Paystack/Flutterwave keys are single settings. A donor cannot say "give to Abuja branch"; `Giving` has no `branch_id`; there is no branch finance view, no remittance tracking. Branches' tithes either go to HQ's account or are collected off-system. | `models/giving.py`; `settings.church_accounts`; `give_checkout`. |
| U6 | **P1** | **Public forms have no branch.** Contact inquiries and prayer requests arrive with `branch_id = NULL`, so a branch admin (who only sees their branch) **never sees a visitor's message meant for their branch**, and HQ sees everything. | `contact_store`, `prayer_store`; `admin_inquiries` branch filter. |
| U7 | **P1** | **No public branch presence.** One site, one set of contact details (currently placeholders), no per-branch page (address, service times, resident pastor, local contact), no way to "attend this branch". Branch rows carry phone/email/address but the site does not use them per branch. | `Branch` model vs public templates. |
| U8 | **P1** | **A person can belong to only one branch.** `Admin.branch_id` is a single value (there is a `admin_branches` link table but only super admins are synced to HQ). A regional overseer or a pastor covering two branches cannot be modelled; members cannot be **transferred** between branches. | `models/admin.py`, `hq_branch.py`. |
| U9 | **P1** | **Departments are global, not per branch**: one "Ushers" department shared by everyone; any admin can rename it (A1). HODs are per branch, but the department itself, its description and unit-report template are not. | `departments` has no `branch_id`. |
| U10 | **P2** | Branch lifecycle is missing: no deactivate/close/merge, no approval of a new branch, and the ORM relationships on `Branch` are `cascade="all, delete-orphan"` — any future "delete branch" would silently delete its members, sermons, events and admins. | `models/branch.py`. |
| U11 | **P2** | **HQ super admin sits in a branch** (`branch_id` = HQ) *and* has an `admin_branches` row; HQ content and "all branches" are mixed (HQ events invisible to branch admins). No concept of *HQ-wide announcements/resources pushed to all branches*. | `hq_branch.py`, `admin_events`. |
| U12 | **P2** | Books Pub (HQ publishing) can pull any branch's draft transcript by id, and drafts created by Books Pub uploads increment the Audio Messages badge. | `_pub_form_sermons`, `_admin_context`. |

### 3.3 What the system does that fits HQ/branch reality (keep)

- Branch-scoped lists, dashboard and metrics; branch-scoped member import and HarvestTrack export.
- HOD monthly **unit reports** with a **resident-pastor comment** and a **senior-pastor comment** — exactly the reporting chain of a real church.
- HQ-only Settings, Security Logs (audit trail), Admins, Branches, Sliders, Stories, Books Pub, Donations, Subscribers.
- Invitations for branch admins and HODs (link-based; no email is sent).
- HarvestTrack (follow-up of first-timers) with branch-aware workers.

### 3.4 Capabilities a real HQ needs that do not exist

| Need | Status |
|---|---|
| Roles beyond super/branch/HOD: **HQ media, HQ finance, HQ pastoral care, resident pastor, branch secretary** with per-permission control | Missing |
| **Per-branch distribution settings** (Telegram bot + channel, podcast feed, YouTube) and a per-message *distribution scope* (Branch only / HQ channel / Both) with **HQ approval** to promote a branch message to the HQ channel | Missing |
| **Per-branch giving** (accounts, gateway sub-account/split, branch on each gift, remittance report) | Missing |
| **Attendance and offering by service per branch** (only free-text/JSON inside monthly unit reports) | Missing |
| **Member transfer** between branches; member ownership history | Missing |
| **HQ → branches broadcast** (announcements, resources, HQ event pushed to all branches) and email/SMS/WhatsApp delivery (nothing is ever sent) | Missing |
| **Roll-up reporting for HQ** comparing branches (attendance, giving, growth, report submission rate) | Partly (dashboard totals only) |
| **Per-branch public pages** and branch directory with service times | Missing |
| **Audit of HQ-visible actions across branches** (who changed what in which branch) | Partly (audit log exists; not branch-attributed or filterable by branch) |
| Data-protection: branch-confidential pastoral data (prayer, counselling, follow-up) with explicit visibility rules | Missing |

## 4. Recommended target model

**Scopes (one rule, applied everywhere):** every record has an *owner branch*. A user has a *role* and a *set of branches* they may act on.

| Role | Reach |
|---|---|
| `general_overseer` / `hq_admin` (a few people) | All branches; settings, keys, roles |
| `hq_media` | HQ audio, sliders, stories, Books Pub, HQ distribution channels; cannot see money |
| `hq_finance` | Donations, remittances, branch financial summaries; nothing else |
| `hq_pastoral` | Prayer/inquiries/counselling routed to HQ; reports' senior-pastor comments |
| `branch_pastor` | Own branch(es): everything; approves branch content; sees own reports |
| `branch_admin` | Own branch: members, events, own audio (draft), inquiries |
| `hod` | Own department in own branch: unit reports only (+ their team's members if needed) |
| `worker` | HarvestTrack API only |

**Content visibility:** audio/events/stories carry `owner_branch` and `visibility` (`branch` / `hq_promoted`). The HQ website shows **HQ-owned + promoted** items; each branch page shows its own. Podcast feed = HQ-owned + promoted. Branch upload defaults: **draft, no Telegram**; branch pastor can publish on the branch page; *promotion* to the HQ site/channel is an HQ action.

**Distribution:** `branch_channels` (branch_id, kind, bot token (encrypted), chat id, enabled). A message is sent only to channels of **its owner branch** (plus HQ's if promoted). The global Telegram setting becomes HQ's own channel.

**Giving:** `Giving.branch_id`; donor picks HQ or a branch; per-branch accounts on the Give page; branch and HQ finance views; CSV per branch.

**Authorisation mechanics:** one helper `can_access(admin, record)` / `scope_query(admin, Model)` used by **every** read, update, delete and create (post-load check, not just list filters), plus tests that run the access matrix in CI.

## 5. Fix plan

### Phase 0 — close the holes (no design decisions needed; ~1–2 days)
1. Add ownership checks to all `update`/`destroy`/`show`/`retranscribe`/`reassign` routes listed in A1–A4 (`branch_id` must equal the admin's branch unless super admin; HQ-owned records editable by HQ only).
2. Enforce **role guards on routes**, not just hidden sidebar links: HOD → only `/admin/hod/*`, profile, logout.
3. Server-side ignore the posted `branch_id` for non-super admins (hods/invite, messages, events, inquiries).
4. Restrict prayer requests to HQ until they are branch-attributed.
5. Normalise role names into one enum; migrate existing values.
6. Turn the access matrix (`rbac_matrix.py`) into a permanent test.

### Phase 1 — the reality fixes you described (design confirmation needed)
7. Owner-branch + visibility on Sermon; public site/feed show HQ-owned (+ promoted) only; branch uploads default to draft and **no Telegram**.
8. Per-branch distribution channels (encrypted tokens), scoped distribution task, HQ "promote" action.
9. Public forms take a branch (contact/prayer route to that branch's admins).

### Phase 2 — finance and structure
10. Branch on every gift, per-branch accounts, remittance/finance views; `hq_finance` role.
11. Multi-branch users, member transfer, department-per-branch, branch lifecycle (deactivate, no cascade delete).
12. Granular permissions and the HQ roles above.

### Phase 3 — operations
13. Attendance/offering by service, roll-up dashboards, HQ→branch broadcast, email/SMS delivery, per-branch public pages.

## 6. Decisions needed from you

1. **Who is "HQ"?** Only the General Overseer's team, or also regional overseers who cover several branches?
2. **Branch content on the HQ site:** never, or only when HQ *promotes* it? (recommended: promote-only)
3. **Branch Telegram/YouTube channels:** does each branch already have its own bot/channel, or should HQ create them for them?
4. **Money:** does each branch collect into its own account and remit, or does everything pass through HQ?
5. **HQ roles:** which staff should never see donations / prayer / settings?
6. Should a branch pastor be able to **publish on their own branch page without HQ approval**?

## 7. Residual risk
- The matrix ran on SQLite with role `admin`; the live invitation flow assigns `branch_super_admin`. Every guard compares only to `super_admin`, so behaviour is the same, but confirm with a real invited branch admin.
- Financial, remittance and church-governance rules differ between denominations; the target model above is a proposal to validate with HQ leadership, not a given.
