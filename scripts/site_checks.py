"""End-to-end checks for the public website features (SQLite, in-process, email provider mocked).

Run:  py scripts/site_checks.py      (prints PASS/FAIL per check; exit code 1 on any failure)
Covers: pages render, legal defaults, placeholder phone hiding, Plan-a-visit, branch directory, events (detail, .ics,
reminders), unsubscribe, prayer + contact + giving emails and pages, message search/filters/transcripts, sitemap/robots,
built-in analytics, email campaigns, the Website-content settings tab and the access rules around all of it.
"""
import asyncio, json, os, re, sys, tempfile
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT); sys.path.insert(0, ROOT)
DB = os.path.join(tempfile.gettempdir(), "swm_sitechecks.db")
if os.path.exists(DB): os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + DB.replace("\\", "/"); os.environ["APP_ENV"] = "production"
os.environ["SWM_DISABLE_SCHEDULER"] = "1"
import sqlalchemy.ext.asyncio as _sa
_o = _sa.create_async_engine
def _e(url, **kw):
    for k in ("pool_size", "max_overflow", "pool_recycle", "pool_pre_ping", "pool_timeout"): kw.pop(k, None)
    kw["echo"] = False; return _o(url, **kw)
_sa.create_async_engine = _e
import logging
if not os.environ.get("SITE_DEBUG"): logging.disable(logging.CRITICAL)
from datetime import datetime, timedelta
from starlette.testclient import TestClient
from app.main import app
from app.core import throttle, mailer, scheduler, analytics

res = []
def rec(name, ok, detail=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
def tok(h):
    m = re.search(r'name="csrf_token" value="([^"]+)"', h); return m.group(1) if m else ""

PW = "Pw#123456"
SENT, PENDING = [], []
async def _fake_send(to, subject, html, text=None, *, kind="general", reply_to=None, headers=None, cfg=None, log=True):
    SENT.append({"to": to, "subject": subject, "html": html, "text": text or "", "kind": kind, "headers": headers or {}})
    return True, "Sent."
async def _fake_cfg(): return mailer.MailConfig("key", "church@scentofwater.org", "SWM")
mailer.send_email = _fake_send
mailer.get_config = _fake_cfg
mailer.fire = lambda coro: PENDING.append(coro)
scheduler.CAMPAIGN_PAUSE = 0
def flush_mail():
    while PENDING:
        asyncio.run(PENDING.pop(0))
def mails(kind=None, to=None):
    return [m for m in SENT if (kind is None or m["kind"] == kind) and (to is None or m["to"] == to)]

async def seed():
    from app.database import async_session
    from app.models.admin import Admin
    from app.models.branch import Branch
    from app.models.sermon import Sermon
    from app.core.security import hash_password
    h = hash_password(PW)
    async with async_session() as db:
        hq = Branch(name="Scent of Water Ministry HQ", slug="scent-of-water-hq", is_hq=True, address="Lagos", phone="+2348100000001", email="hq@x.org")
        a = Branch(name="Abuja Branch", slug="abuja-branch", address="12 Garki Road", city="Abuja", state="FCT", phone="+234 803 111 2222", email="abuja@x.org")
        b = Branch(name="Kano Branch", slug="kano-branch", city="Kano", state="Kano", phone="+234 803 333 4444")
        db.add_all([hq, a, b]); await db.flush()
        db.add_all([
            Admin(full_name="HQ Super", email="super@x.org", password_hash=h, role="super_admin", is_active=True, branch_id=hq.id),
            Admin(full_name="HQ Media", email="media@x.org", password_hash=h, role="hq_media", is_active=True, branch_id=hq.id),
            Admin(full_name="HQ Finance", email="fin@x.org", password_hash=h, role="hq_finance", is_active=True, branch_id=hq.id),
            Admin(full_name="Admin A", email="a@x.org", password_hash=h, role="branch_admin", is_active=True, branch_id=a.id),
            Admin(full_name="Admin B", email="b@x.org", password_hash=h, role="branch_admin", is_active=True, branch_id=b.id),
        ])
        now = datetime.utcnow()
        db.add_all([
            Sermon(title="Faith That Moves", slug="faith-that-moves", status="published", type="audio", branch_id=hq.id, preacher="Pastor Sam", series="Foundations", scripture="Hebrews 11",
                   caption="What faith really is", published_at=now - timedelta(days=3), transcript="Faith is the substance of things hoped for. UNIQUEWORD in the transcript.", transcript_public=False, media_url="https://x.org/a.mp3"),
            Sermon(title="Grace Abounds", slug="grace-abounds", status="published", type="audio", branch_id=hq.id, preacher="Pastor Sam", series="Grace", scripture="Romans 5",
                   published_at=now - timedelta(days=400), transcript="Grace is unmerited. PUBLICWORD here.", transcript_public=True, media_url="https://x.org/b.mp3"),
            Sermon(title="Guest Word", slug="guest-word", status="published", type="audio", branch_id=hq.id, preacher="Guest Minister", published_at=now - timedelta(days=10), media_url="https://x.org/c.mp3"),
            Sermon(title="Kano Only", slug="kano-only", status="published", type="audio", branch_id=b.id, preacher="Pastor K", published_at=now - timedelta(days=1)),
            Sermon(title="Draft Message", slug="draft-message", status="draft", type="audio", branch_id=hq.id, published_at=now),
        ])
        await db.commit()

def setting(**kv):
    async def go():
        from app.database import async_session
        from app.core.settings_cache import update_settings
        async with async_session() as db:
            await update_settings(db, {k: v for k, v in kv.items()}); await db.commit()
    asyncio.run(go())

with TestClient(app, raise_server_exceptions=False, base_url="https://testserver") as c0:
    asyncio.run(seed())

    async def q(sql, **p):
        from app.database import async_session
        from sqlalchemy import text
        async with async_session() as db:
            r = await db.execute(text(sql), p)
            try: rows = r.fetchall()
            except Exception: rows = []
            await db.commit(); return rows
    Q = lambda sql, **p: asyncio.run(q(sql, **p))
    def fresh():
        throttle.reset_all()
        return TestClient(app, raise_server_exceptions=False, base_url="https://testserver", headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120"})
    def login(email):
        cl = fresh()
        t = tok(cl.get("/admin/").text)
        r = cl.post("/admin/login", data={"email": email, "password": PW, "csrf_token": t}, follow_redirects=False)
        assert r.status_code == 303, (email, r.status_code)
        return cl
    def ok_get(cl, path): return cl.get(path, follow_redirects=False).status_code == 200
    def form_token(cl, path="/prayer"): return tok(cl.get(path).text)
    hq_id = Q("select id from branches where is_hq")[0][0]
    A_id = Q("select id from branches where slug='abuja-branch'")[0][0]
    B_id = Q("select id from branches where slug='kano-branch'")[0][0]
    S, MEDIA, FIN, A, B = (login(e) for e in ("super@x.org", "media@x.org", "fin@x.org", "a@x.org", "b@x.org"))
    V = fresh()                                    # an anonymous visitor

    # ══ 1. every public page renders ══════════════════════════════════════
    pages = ["/", "/visit", "/branches", "/about", "/our-pastor", "/messages", "/events", "/calendar", "/prayer", "/contact", "/give", "/privacy-policy",
             "/terms-of-service", "/get-involved", "/stories", "/watch", "/sitemap", "/robots.txt", "/sitemap.xml", "/calendar.ics", "/services.ics", "/feed.xml"]
    bad = [(p, V.get(p).status_code) for p in pages if V.get(p).status_code != 200]
    rec("all public pages and files respond 200", not bad, str(bad))
    home = V.get("/").text
    rec("skip link + main landmark on every page shell", 'class="skip-link"' in home and 'id="main"' in home and 'id="main"' in V.get("/visit").text)
    about = V.get("/about").text
    rec("the current page is marked in the navigation (aria-current)", 'aria-current="page"' in about and re.search(r'is-active"[^>]*aria-current="page"[^>]*>About<', about) is not None)
    rec("navigation offers Branches and a Plan-a-visit call to action", ">Branches<" in home and 'Plan a visit' in home)

    # ══ 2. legal pages, placeholder phone, real content ═══════════════════
    priv = V.get("/privacy-policy").text
    rec("privacy policy is published out of the box (NDPA, processors, rights)", "Nigeria Data Protection Act" in priv and "Paystack" in priv and "Your rights" in priv and "has not been published yet" not in priv)
    terms = V.get("/terms-of-service").text
    rec("terms of service are published out of the box", "Governing law" in terms and "Giving" in terms)
    setting(privacy_policy="<p>Our own policy text.</p>")
    rec("a saved policy replaces the standard text", "Our own policy text." in V.get("/privacy-policy").text and "Your rights" not in V.get("/privacy-policy").text)
    setting(privacy_policy="")
    setting(siteSupportPhone="+234 (555) 123-4567", counsellingPhone="", siteContactEmail="info@scentofwater.org", siteHeadquartersAddress="1 Scent Close, Mile 50, Abakaliki")
    _leaks = [(p, [V.get(p).text[max(0, m.start() - 40):m.end() + 20] for m in re.finditer(r"\(555\)", V.get(p).text)][:2]) for p in ("/", "/contact", "/visit", "/prayer", "/privacy-policy") if re.search(r"\(555\)", V.get(p).text)]
    rec("a placeholder phone number is never shown to visitors", not _leaks, str(_leaks))
    setting(siteSupportPhone="+234 803 123 4567")
    rec("a real phone number is shown", "+234 803 123 4567" in V.get("/contact").text and 'href="tel:+2348031234567"' in V.get("/visit").text)

    # ══ 3. Plan your visit ═════════════════════════════════════════════════
    visit = V.get("/visit").text
    rec("visit page lists the service times and address", "First Service" in visit and "7:00 — 9:30 am" in visit and "Mile 50" in visit)
    rec("visit page has the church JSON-LD with opening hours", '"@type": "Church"' in visit and "openingHoursSpecification" in visit)
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', visit, re.S)
    try: ldj = json.loads(ld.group(1)); ld_ok = ldj["@type"] == "Church" and any(h["dayOfWeek"] == "Sunday" and h["opens"] == "07:00" for h in ldj["openingHoursSpecification"])
    except Exception: ld_ok = False
    rec("…and it is valid JSON with Sunday 07:00", ld_ok)
    t = tok(visit)
    r = V.post("/visit", data={"csrf_token": t, "name": "Ngozi Eze", "email": "ngozi@gmail.com", "phone": "0801", "branch_id": str(A_id), "visit_date": (datetime.utcnow() + timedelta(days=4)).strftime("%Y-%m-%d"), "party": "3", "note": "Two children", "subscribe": "1"}, follow_redirects=False)
    flush_mail()
    row = Q("select kind, branch_id, subject, message from contact_inquiries")[-1]
    rec("a visit plan is stored as an inquiry for the chosen branch", r.headers.get("location") == "/visit/thanks" and row[0] == "visit" and row[1] == A_id and "3 people" in row[3] and "Two children" in row[3], str(row))
    rec("the visitor gets a confirmation email with services and directions", any("We look forward" in m["subject"] and "Abuja Branch" in m["html"] for m in mails("visit", "ngozi@gmail.com")))
    staff = mails("staff")
    rec("the branch admin is told (no personal content in the notice)", any(m["to"] == "a@x.org" for m in staff) and not any("Ngozi" in m["html"] or "Two children" in m["html"] for m in staff))
    rec("ticking the box subscribes them with an unsubscribe token", Q("select source, token from newsletter_subscribers where email='ngozi@gmail.com'")[0][0] == "visit" and bool(Q("select source, token from newsletter_subscribers where email='ngozi@gmail.com'")[0][1]))
    th = V.get("/visit/thanks")
    rec("the thank-you page names the branch, is noindex, and is gone on reload", th.status_code == 200 and "Abuja Branch" in th.text and 'content="noindex' in th.text and V.get("/visit/thanks", follow_redirects=False).status_code == 303)
    n = Q("select count(*) from contact_inquiries")[0][0]
    V.post("/visit", data={"csrf_token": t, "name": "Bot", "email": "bot@gmail.com", "website": "http://spam"}, follow_redirects=False)
    rec("a filled honeypot stores nothing", Q("select count(*) from contact_inquiries")[0][0] == n)
    r = V.post("/visit", data={"csrf_token": t, "name": "Old", "email": "old@gmail.com", "visit_date": "2001-01-01"}, follow_redirects=False)
    rec("a past date is refused", Q("select count(*) from contact_inquiries")[0][0] == n and "/visit" in r.headers.get("location", ""))
    r = V.post("/visit", data={"csrf_token": t, "name": "NoMail", "email": "not-an-email"}, follow_redirects=False)
    rec("an invalid email is refused", Q("select count(*) from contact_inquiries")[0][0] == n)
    rec("a visit plan is a real inquiry: the branch admin sees it, another branch does not", "Plan a visit" in A.get("/admin/inquiries").text and "Ngozi" not in B.get("/admin/inquiries").text)
    rec("without the CSRF token the form is refused", V.post("/visit", data={"name": "X", "email": "x@gmail.com"}, follow_redirects=False).status_code in (303, 403))

    # ══ 4. Find a branch ═══════════════════════════════════════════════════
    rec("the home page lists headquarters only (from Settings) until HQ approves a branch",  "Abuja Branch" not in V.get("/").text and "Kano Branch" not in V.get("/").text and "Headquarters" in V.get("/").text and "+2348100000001" not in V.get("/").text)
    br = V.get("/branches").text
    rec("the directory starts empty: no branch is published until HQ approves it", "Abuja Branch" not in br and "being prepared" in br)
    rec("an unlisted branch page is a 404", V.get("/branches/abuja-branch").status_code == 404)
    a_page = A.get("/admin/branch-settings").text
    rec("the branch admin sees the public-profile form but no listing switch", "Public profile" in a_page and 'name="listed_publicly"' not in a_page)
    A.post("/admin/branch-settings", data={"csrf_token": tok(a_page), "public_pastor": "Pastor Ada", "public_about": "A young, growing church.", "service_times": "Sunday 8:00 am — Main service\nWednesday 5:00 pm — Bible study",
                                           "public_whatsapp": "0803 999 8888", "directions_url": "javascript:alert(1)", "listed_publicly": "1", "public_present": "1"})
    kv = dict(Q("select key, value from branch_settings where branch_id=:b", b=A_id))
    rec("a branch admin can fill in the profile but cannot approve its own listing; a bad directions link is dropped", kv.get("public_pastor") == "Pastor Ada" and kv.get("listed_publicly") != "1" and kv.get("directions_url", "") == "", str(kv))
    hq_page = S.get(f"/admin/branches/{A_id}/settings").text
    rec("HQ sees the listing switch", 'name="listed_publicly"' in hq_page)
    S.post(f"/admin/branches/{A_id}/settings", data={"csrf_token": tok(hq_page), "public_present": "1", "listed_publicly": "1", "public_pastor": "Pastor Ada", "hq_share_percent": "10"})
    br = V.get("/branches").text
    rec("after HQ approves, the branch is listed with its town and pastor", "Abuja Branch" in br and "Pastor Ada" in br and "Kano Branch" not in br)
    hm = V.get("/").text
    rec("…then the home page shows the approved branch with its details and never an unapproved one", "Abuja Branch" in hm and "12 Garki Road" in hm and "Kano Branch" not in hm)
    d = V.get("/branches/abuja-branch")
    rec("the branch page shows address, services, WhatsApp and JSON-LD", d.status_code == 200 and "12 Garki Road" in d.text and "Main service" in d.text and "wa.me/2348039998888" in d.text and '"@type": "Church"' in d.text)
    rec("Kano stays unlisted (404) and is not in the directory", V.get("/branches/kano-branch").status_code == 404)
    S.post(f"/admin/branches/{A_id}/active", data={"csrf_token": tok(S.get(f"/admin/branches/{A_id}").text)})
    rec("closing a branch removes it from the public directory", V.get("/branches/abuja-branch").status_code == 404 and "Abuja Branch" not in V.get("/branches").text)
    S.post(f"/admin/branches/{A_id}/active", data={"csrf_token": tok(S.get(f"/admin/branches/{A_id}").text)})
    rec("visit page suggests listed branches", "Abuja Branch" in V.get("/visit").text)
    rec("the home page does not show the branch's audio (branch messages never reach HQ site)", "Kano Only" not in V.get("/messages").text and "Kano Only" not in V.get("/").text)

    # ══ 5. Events: detail, calendar files, reminders ═══════════════════════
    now_local = datetime.utcnow() + timedelta(hours=1)
    def mk_event(title, days, published=1, branch=None, hours=10):
        start = (now_local + timedelta(days=days)).replace(hour=hours, minute=0, second=0, microsecond=0)
        Q("insert into events (title, description, location, starts_at, is_published, is_major, branch_id, created_at, updated_at) values (:t,:d,:l,:s,:p,0,:b,:n,:n)",
          t=title, d="Bring a friend & come early.", l="Main Auditorium", s=start, p=published, b=branch, n=datetime.utcnow())
        return Q("select id from events where title=:t", t=title)[0][0], start
    ev_far, start_far = mk_event("Annual Convention", 10, branch=hq_id)
    ev_soon, _ = mk_event("Tonight Vigil", 0, hours=(now_local + timedelta(hours=1)).hour)
    ev_draft, _ = mk_event("Unpublished Retreat", 12, published=0)
    ev_page = V.get(f"/events/{ev_far}")
    rec("event page shows date, place, calendar buttons and the reminder form", ev_page.status_code == 200 and "Main Auditorium" in ev_page.text and f"/events/{ev_far}/calendar.ics" in ev_page.text and "calendar.google.com" in ev_page.text and "Email me a reminder" in ev_page.text)
    rec("event page carries Event JSON-LD and Open Graph", '"@type": "Event"' in ev_page.text and 'og:title' in ev_page.text and "Annual Convention" in ev_page.text)
    rec("an unpublished event is a 404 (page, .ics and reminder)", V.get(f"/events/{ev_draft}").status_code == 404 and V.get(f"/events/{ev_draft}/calendar.ics").status_code == 404)
    ics_r = V.get(f"/events/{ev_far}/calendar.ics")
    rec("the .ics download is a valid VEVENT in church time", ics_r.status_code == 200 and "text/calendar" in ics_r.headers["content-type"] and "attachment" in ics_r.headers["content-disposition"] and "BEGIN:VEVENT" in ics_r.text
        and f"DTSTART;TZID=Africa/Lagos:{start_far.strftime('%Y%m%dT%H%M%S')}" in ics_r.text and "TZID:Africa/Lagos" in ics_r.text and "Annual Convention" in ics_r.text)
    feed = V.get("/calendar.ics").text
    rec("the subscribe feed lists published events only", "Annual Convention" in feed and "Unpublished Retreat" not in feed)
    rec("the weekly-services file repeats Sundays", "RRULE:FREQ=WEEKLY;BYDAY=SU" in V.get("/services.ics").text)
    _el, _cal = V.get("/events").text, V.get("/calendar?month=" + start_far.strftime("%Y-%m")).text
    rec("the events list and calendar link to the detail page", f"/events/{ev_far}" in _el and f"/events/{ev_far}" in _cal, (f"/events/{ev_far}" in _el, f"/events/{ev_far}" in _cal, re.findall(r'/events/\d+', _cal)[:6]))
    tk = tok(ev_page.text)
    r = V.post(f"/events/{ev_far}/remind", data={"csrf_token": tk, "email": "friend@gmail.com", "name": "Chidi"}, follow_redirects=False)
    flush_mail()
    rem = Q("select remind_at, status from event_reminders where email='friend@gmail.com'")
    expected_utc = start_far - timedelta(hours=24) - timedelta(hours=1)
    rec("a reminder is stored for 24 h before the event (converted to UTC)", len(rem) == 1 and rem[0][1] == "pending" and abs((datetime.fromisoformat(str(rem[0][0])) - expected_utc).total_seconds()) < 2, str(rem))
    rec("they are told once when it will be sent", any("Reminder set" in m["subject"] for m in mails("reminder_confirm", "friend@gmail.com")))
    V.post(f"/events/{ev_far}/remind", data={"csrf_token": tk, "email": "friend@gmail.com"}, follow_redirects=False)
    rec("a duplicate request does not create a second reminder", Q("select count(*) from event_reminders where email='friend@gmail.com'")[0][0] == 1)
    V.post(f"/events/{ev_soon}/remind", data={"csrf_token": tk, "email": "late@gmail.com"}, follow_redirects=False)
    rec("an event starting within hours cannot be reminded", Q("select count(*) from event_reminders where email='late@gmail.com'")[0][0] == 0)
    V.post(f"/events/{ev_far}/remind", data={"csrf_token": tk, "email": "not-mail"}, follow_redirects=False)
    V.post(f"/events/{ev_far}/remind", data={"csrf_token": tk, "email": "spam@gmail.com", "website": "x"}, follow_redirects=False)
    rec("bad email and honeypot create nothing", Q("select count(*) from event_reminders")[0][0] == 1)
    before = len(mails("reminder"))
    asyncio.run(scheduler.process_reminders(now=datetime.utcnow()))
    rec("the scheduler does not send a reminder before its time", len(mails("reminder")) == before)
    n_sent = asyncio.run(scheduler.process_reminders(now=datetime.utcnow() + timedelta(days=10)))
    rem = mails("reminder", "friend@gmail.com")
    rec("…and sends it once when due, with the event details", n_sent == 1 and len(rem) == 1 and "Annual Convention" in rem[0]["html"] and "Main Auditorium" in rem[0]["html"] and Q("select status from event_reminders")[0][0] == "sent")
    asyncio.run(scheduler.process_reminders(now=datetime.utcnow() + timedelta(days=11)))
    rec("a sent reminder is never sent twice", len(mails("reminder", "friend@gmail.com")) == 1)
    Q("update events set starts_at=:d where id=:i", d=datetime.utcnow() - timedelta(days=9), i=ev_far)
    asyncio.run(scheduler.housekeeping())
    rec("housekeeping removes reminders after their event (privacy promise)", Q("select count(*) from event_reminders")[0][0] == 0)

    # ══ 6. newsletter + unsubscribe ════════════════════════════════════════
    tk = form_token(V, "/")
    V.post("/newsletter", data={"csrf_token": tk, "email": "Reader@Gmail.com"}, headers={"referer": "https://testserver/"}, follow_redirects=False)
    flush_mail()
    sub = Q("select token, is_active, source from newsletter_subscribers where email='reader@gmail.com'")
    w = mails("welcome", "reader@gmail.com")
    rec("subscribing stores a token, sends a welcome email with a working unsubscribe link and headers",
        len(sub) == 1 and sub[0][1] and sub[0][2] == "footer" and len(w) == 1 and f"/unsubscribe/{sub[0][0]}" in w[0]["html"] and "List-Unsubscribe" in w[0]["headers"] and w[0]["headers"].get("List-Unsubscribe-Post"))
    token = sub[0][0]
    rec("the unsubscribe page asks for confirmation", "Yes, unsubscribe me" in V.get(f"/unsubscribe/{token}").text)
    raw = fresh()                                     # a mail client: no session, no CSRF token
    r = raw.post(f"/unsubscribe/{token}")
    rec("one-click unsubscribe works with no CSRF token (mail clients cannot send one)", r.status_code == 200 and "unsubscribed" in r.text.lower() and Q("select is_active from newsletter_subscribers where email='reader@gmail.com'")[0][0] in (0, False))
    rec("a wrong token is harmless", "not valid" in V.get("/unsubscribe/zzzzzzzzzzzzzzzzzzzzzz").text and raw.post("/unsubscribe/zzzzzzzzzzzzzzzzzzzzzz").status_code == 200)
    V2 = fresh(); V2.post(f"/resubscribe/{token}", data={"csrf_token": form_token(V2, "/")}, follow_redirects=False)
    rec("they can resubscribe", Q("select is_active from newsletter_subscribers where email='reader@gmail.com'")[0][0] in (1, True))
    V.post("/newsletter", data={"csrf_token": tk, "email": "not-an-email"}, headers={"referer": "https://testserver/"}, follow_redirects=False)
    rec("a bad address subscribes nobody", Q("select count(*) from newsletter_subscribers where email='not-an-email'")[0][0] == 0)

    # ══ 7. prayer requests ═════════════════════════════════════════════════
    setting(prayerResponseTime="two working days")
    pr = V.get("/prayer").text
    rec("the prayer form states the response time and has a honeypot + opt-in wording", "within two working days" in pr and 'name="website"' in pr and "Email me an update" in pr)
    SENT.clear()
    r = V.post("/prayer", data={"csrf_token": tok(pr), "name": "Amaka", "email": "amaka@gmail.com", "request": "Please pray for my SECRETMATTER.", "notify_me": "1", "branch_id": str(A_id)}, follow_redirects=False)
    flush_mail()
    rec("submitting redirects to a thank-you page", r.headers.get("location") == "/prayer/thanks")
    th = V.get("/prayer/thanks")
    rec("the thank-you page repeats the promised response time and names the branch", th.status_code == 200 and "within two working days" in th.text and "Abuja Branch" in th.text and 'content="noindex' in th.text and V.get("/prayer/thanks", follow_redirects=False).status_code == 303)
    pm = mails("prayer", "amaka@gmail.com")
    rec("the requester gets a confirmation that repeats the promise", len(pm) == 1 and "two working days" in pm[0]["html"] and "never published" in pm[0]["html"])
    rec("staff are told a request is waiting — without its content", any(m["to"] == "a@x.org" for m in mails("staff")) and not any("SECRETMATTER" in m["html"] or "Amaka" in m["html"] for m in mails("staff")))
    pid = Q("select id from prayer_requests")[0][0]
    A.post(f"/admin/prayer-requests/{pid}/status", data={"csrf_token": tok(A.get("/admin/prayer-requests").text), "status": "praying"})
    flush_mail()
    up = mails("prayer_update", "amaka@gmail.com")
    rec("marking it Praying emails the requester who asked for an update (once, no content)", len(up) == 1 and "SECRETMATTER" not in up[0]["html"] and Q("select notified_at from prayer_requests")[0][0] is not None)
    A.post(f"/admin/prayer-requests/{pid}/status", data={"csrf_token": tok(A.get("/admin/prayer-requests").text), "status": "answered"})
    flush_mail()
    rec("changing status again does not email twice", len(mails("prayer_update", "amaka@gmail.com")) == 1)
    r = V.post("/prayer", data={"csrf_token": tok(pr), "name": "Bot", "request": "spam", "website": "x"}, follow_redirects=False)
    rec("honeypot: nothing stored", Q("select count(*) from prayer_requests")[0][0] == 1)
    V.post("/prayer", data={"csrf_token": tok(pr), "name": "NoEmail", "request": "Help me", "notify_me": "1"}, follow_redirects=False)
    rec("a request with no email still works (no email is sent)", Q("select count(*) from prayer_requests")[0][0] == 2 and not mails("prayer", ""))

    # ══ 8. contact form ════════════════════════════════════════════════════
    SENT.clear()
    ct = V.get("/contact").text
    V.post("/contact", data={"csrf_token": tok(ct), "name": "Tunde Bello", "email": "tunde@gmail.com", "subject": "Question", "message": "Do you have a youth service?", "branch_id": str(hq_id)}, follow_redirects=False)
    flush_mail()
    rec("the sender gets an auto-reply and staff a notice", any(m["to"] == "tunde@gmail.com" and "received your message" in m["subject"] for m in SENT) and any(m["kind"] == "staff" and m["to"] == "super@x.org" for m in SENT))
    V.post("/contact", data={"csrf_token": tok(ct), "name": "Bot", "email": "b@gmail.com", "subject": "s", "message": "spam", "website": "x"}, follow_redirects=False)
    rec("contact honeypot stores nothing", Q("select count(*) from contact_inquiries where name='Bot'")[0][0] == 0)
    rec("the contact page offers WhatsApp/Telegram only once configured", "wa.me" not in ct)
    setting(whatsappNumber="0803 555 0100", whatsappChannelUrl="https://whatsapp.com/channel/abc", telegramJoinUrl="https://t.me/swmchannel")
    ct = V.get("/contact").text
    rec("…and then shows the WhatsApp chat, channel and Telegram links", "wa.me/2348035550100" in ct and "whatsapp.com/channel/abc" in ct and "t.me/swmchannel" in ct)
    rec("the footer carries them on every page", "whatsapp.com/channel/abc" in V.get("/about").text and "t.me/swmchannel" in V.get("/").text)
    setting(whatsappNumber="", whatsappChannelUrl="", telegramJoinUrl="")

    # ══ 9. giving: bank transfer notice + receipt ══════════════════════════
    setting(church_accounts=json.dumps([{"bank_name": "GTBank", "account_number": "0123456789", "account_name": "Scent of Water Ministry"}]))
    give = V.get("/give").text
    rec("the give page shows the account with a copy button and the transfer notice form", 'data-copy="0123456789"' in give and "Copy account number 0123456789" in give and "I have sent a transfer" in give and 'action="/give/transfer-notice"' in give)
    SENT.clear()
    tg = tok(give)
    r = V.post("/give/transfer-notice", data={"csrf_token": tg, "name": "Ola Ade", "email": "ola@gmail.com", "amount": "₦25,000.50", "purpose": "Tithe", "bank_ref": "REF123", "sent_on": "2026-09-20"}, follow_redirects=False)
    flush_mail()
    g = Q("select amount, method, status, giving_type, payment_gateway, gateway_owner, note from givings where email='ola@gmail.com'")
    rec("a transfer notice is recorded as a PENDING transfer awaiting finance confirmation", len(g) == 1 and float(g[0][0]) == 25000.5 and g[0][1] == "transfer" and g[0][2] == "pending" and "REF123" in g[0][6], str(g))
    rec("the giver is thanked by email with the amount and reference", any(m["to"] == "ola@gmail.com" and "₦25,000.50" in m["html"] and "REF123" in m["html"] for m in SENT))
    rec("finance sees it (pending) on the donations page", "ola@gmail.com" in FIN.get("/admin/donations").text or "Ola Ade" in FIN.get("/admin/donations").text)
    V.post("/give/transfer-notice", data={"csrf_token": tg, "name": "X", "email": "x@gmail.com", "amount": "abc"}, follow_redirects=False)
    V.post("/give/transfer-notice", data={"csrf_token": tg, "name": "X", "email": "x@gmail.com", "amount": "-5"}, follow_redirects=False)
    V.post("/give/transfer-notice", data={"csrf_token": tg, "name": "X", "email": "x@gmail.com", "amount": "100", "website": "spam"}, follow_redirects=False)
    rec("a bad amount and the honeypot record nothing", Q("select count(*) from givings")[0][0] == 1)
    from app.core import payment
    async def _paid(**kw): return {"state": "paid", "success": True, "amount_kobo": 500000}
    payment.verify_paystack = _paid
    Q("insert into givings (reference, email, full_name, amount, giving_type, is_anonymous, payment_gateway, gateway_owner, method, status, created_at, updated_at) values ('R-1','payer@gmail.com','Pat Payer',5000,'tithe',0,'paystack','hq','online','pending',:n,:n)", n=datetime.utcnow())
    SENT.clear()
    r = V.get("/give/callback?reference=R-1", follow_redirects=False)
    flush_mail()
    rc = mails("receipt", "payer@gmail.com")
    rec("a completed online gift emails a receipt (amount, purpose, reference)", Q("select status from givings where reference='R-1'")[0][0] == "completed" and len(rc) == 1 and "₦5,000.00" in rc[0]["html"] and "R-1" in rc[0]["html"] and "Tithe" in rc[0]["html"])
    V.get("/give/callback?reference=R-1", follow_redirects=False); flush_mail()
    rec("reloading the callback does not send the receipt again", len(mails("receipt", "payer@gmail.com")) == 1)

    # ══ 10. messages: search, filters, transcripts ═════════════════════════
    ml = V.get("/messages").text
    rec("the archive offers series / preacher / year filters", "All series" in ml and "Foundations" in ml and "All preachers" in ml and "Pastor Sam" in ml and "Any year" in ml)
    rec("branch and draft messages are absent", "Kano Only" not in ml and "Draft Message" not in ml)
    rec("filter by series", "Faith That Moves" in V.get("/messages?series=Foundations").text and "Grace Abounds" not in V.get("/messages?series=Foundations").text)
    rec("filter by preacher and by year", "Guest Word" in V.get("/messages?preacher=Guest+Minister").text and "Faith That Moves" not in V.get("/messages?preacher=Guest+Minister").text
        and "Grace Abounds" in V.get(f"/messages?year={datetime.utcnow().year - 1}").text or "Grace Abounds" in V.get(f"/messages?year={(datetime.utcnow() - timedelta(days=400)).year}").text)
    rec("search reaches title, scripture and series", "Faith That Moves" in V.get("/messages?search=Hebrews").text and "Grace Abounds" in V.get("/messages?search=grace").text and "Guest Word" in V.get("/messages?search=guest").text)
    rec("a transcript is searchable ONLY once published", "Grace Abounds" in V.get("/messages?search=PUBLICWORD").text and "Faith That Moves" not in V.get("/messages?search=UNIQUEWORD").text)
    rec("no-match search says so, with a way out", "Nothing matched" in V.get("/messages?search=zzzzqqq").text)
    show = V.get("/messages/faith-that-moves").text
    src = re.search(r'<audio[^>]*src="([^"]+)"', show)
    rec("the audio player points at the stream URL (not the home page)", src is not None and re.fullmatch(r"/messages/\d+/stream", src.group(1)) is not None, src.group(1) if src else "")
    rec("Download button targets the download URL", re.search(r'href="/messages/\d+/download"', show) is not None)
    rec("an unreviewed transcript is NOT shown", "UNIQUEWORD" not in show and "Read the transcript" not in show)
    rec("the series and scripture are shown and link to the filter", "Series: Foundations" in show and "Hebrews 11" in show)
    pub = V.get("/messages/grace-abounds").text
    rec("a published transcript is shown with the accuracy notice", "PUBLICWORD" in pub and "Read the transcript" in pub and "may contain small errors" in pub)
    rec("message pages have AudioObject JSON-LD and share links", '"@type": "AudioObject"' in show and "wa.me/?text=" in show)
    rec("an admin can edit series, scripture and transcript visibility", "Main scripture" in MEDIA.get(f"/admin/messages/{Q('select id from sermons where slug=:s', s='faith-that-moves')[0][0]}/edit").text)
    sid = Q("select id from sermons where slug='faith-that-moves'")[0][0]
    MEDIA.post(f"/admin/messages/{sid}/update", data={"csrf_token": tok(MEDIA.get(f"/admin/messages/{sid}/edit").text), "title": "Faith That Moves", "speaker": "Pastor Sam", "series": "Foundations 2", "scripture": "Heb 11:1", "transcript_public": "1", "status": "published"})
    rec("…and the change reaches the public page", "UNIQUEWORD" in V.get("/messages/faith-that-moves").text and "Foundations 2" in V.get("/messages/faith-that-moves").text)

    # ══ 11. sitemap + robots + SEO basics ══════════════════════════════════
    sm = V.get("/sitemap.xml")
    rec("sitemap.xml is XML and lists pages, messages, events and listed branches — not drafts, unlisted or branch audio",
        "xml" in sm.headers["content-type"] and "/visit" in sm.text and "/messages/grace-abounds" in sm.text and "/branches/abuja-branch" in sm.text and "draft-message" not in sm.text and "kano-only" not in sm.text and "unpublished" not in sm.text.lower())
    rb = V.get("/robots.txt").text
    rec("robots.txt blocks admin/api and points at the sitemap", "Disallow: /admin" in rb and "Sitemap: https://testserver/sitemap.xml" in rb)
    v = V.get("/visit").text
    rec("each page has a unique title, description and Open Graph tags", "<title>Plan your visit —" in v and 'name="description"' in v and 'property="og:image"' in v)
    rec("the home page has Church JSON-LD", '"@type": "Church"' in V.get("/").text)

    # ══ 12. built-in analytics ═════════════════════════════════════════════
    analytics._buffer.clear()
    Mob = TestClient(app, raise_server_exceptions=False, base_url="https://testserver", headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17) Mobile Safari", "referer": "https://l.facebook.com/x"})
    Mob.get("/about"); Mob.get("/visit"); Mob.get("/about")
    TestClient(app, base_url="https://testserver", headers={"user-agent": "Googlebot/2.1"}).get("/about")
    TestClient(app, base_url="https://testserver", headers={"user-agent": "Mozilla/5.0 Chrome", "dnt": "1"}).get("/about")
    Mob.get("/static/css/admin.css"); Mob.get("/robots.txt"); Mob.get("/nope-404")
    asyncio.run(analytics.flush())
    pg = dict(Q("select key, sum(count) from analytics_counters where kind='page' group by key"))
    rec("only real public HTML views are counted (bots, DNT, files, admin, 404s are not)", pg.get("/about") == 2 and pg.get("/visit") == 1 and not any(k.startswith(("/static", "/admin", "/robots")) for k in pg) and "/nope-404" not in pg, str(pg))
    rec("referrer and device are recorded in aggregate", dict(Q("select key, sum(count) from analytics_counters where kind='ref' group by key")).get("Facebook") == 3 and dict(Q("select key, sum(count) from analytics_counters where kind='device' group by key")).get("mobile") == 3)
    vis = Q("select key from analytics_counters where kind='visitor'")
    rec("visitors are counted as an irreversible per-day hash, never an IP or user-agent", len(vis) >= 1 and all(re.fullmatch(r"[0-9a-f]{20}", k[0]) for k in vis))
    cols = [r[1] for r in Q("pragma table_info(analytics_counters)")]
    rec("the analytics table has no IP / user-agent / cookie column", not ({"ip", "ip_address", "user_agent", "ua", "cookie"} & set(cols)), str(cols))
    an = MEDIA.get("/admin/analytics")
    rec("HQ media and the HQ Administrator see the traffic page with the numbers", an.status_code == 200 and "/about" in an.text and "Facebook" in an.text and ok_get(S, "/admin/analytics"))
    rec("branch admins and finance cannot", not ok_get(A, "/admin/analytics") and not ok_get(FIN, "/admin/analytics"))

    # ══ 13. email campaigns ════════════════════════════════════════════════
    Q("update newsletter_subscribers set is_active=0 where email='ngozi@gmail.com'")
    n_active = Q("select count(*) from newsletter_subscribers where is_active=1")[0][0]
    rec("only HQ media / HQ Administrator can open campaigns", ok_get(MEDIA, "/admin/campaigns") and ok_get(S, "/admin/campaigns") and not any(ok_get(x, "/admin/campaigns") for x in (A, FIN, B)))
    form = MEDIA.get(f"/admin/campaigns/create?message_id={Q('select id from sermons where slug=:s', s='grace-abounds')[0][0]}")
    rec("a campaign can start from a published message", "Grace Abounds" in form.text and "Listen now" in form.text)
    rec("branch audio cannot be pulled into an HQ campaign", "Kano Only" not in MEDIA.get(f"/admin/campaigns/create?message_id={Q('select id from sermons where slug=:s', s='kano-only')[0][0]}").text)
    r = MEDIA.post("/admin/campaigns", data={"csrf_token": tok(form.text), "subject": "New message: Grace Abounds", "body_text": "Hello <b>friends</b>,\n\nA new message is out.\nhttps://scentofwater.org/messages", "button_label": "Listen now", "button_url": "https://scentofwater.org/messages/grace-abounds", "kind": "message"}, follow_redirects=False)
    cid = Q("select id from email_campaigns")[0][0]
    prev = MEDIA.get(f"/admin/campaigns/{cid}")
    rec("the draft preview is the real email, escaped", prev.status_code == 200 and "Listen now" in prev.text and "friends" in prev.text and "<b>friends" not in prev.text and f"Send to {n_active} subscriber" in prev.text, prev.text[prev.text.find("srcdoc") : prev.text.find("srcdoc") + 300] + " | n_active=" + str(n_active) + " " + str("Send to" in prev.text))
    SENT.clear()
    MEDIA.post(f"/admin/campaigns/{cid}/test", data={"csrf_token": tok(prev.text)})
    rec("a test goes to the admin only", len(SENT) == 1 and SENT[0]["to"] == "media@x.org" and SENT[0]["subject"].startswith("[TEST]"))
    SENT.clear()
    MEDIA.post(f"/admin/campaigns/{cid}/send", data={"csrf_token": tok(prev.text)})
    rec("sending marks it 'sending' with the recipient count", Q("select status, total from email_campaigns")[0] == ("sending", n_active))
    asyncio.run(scheduler.process_campaigns())
    camp = [m for m in SENT if m["kind"] == "campaign"]
    rec("every ACTIVE subscriber gets exactly one email; the unsubscribed do not", sorted(m["to"] for m in camp) == sorted(r[0] for r in Q("select email from newsletter_subscribers where is_active=1")) and "ngozi@gmail.com" not in [m["to"] for m in camp])
    rec("each email has its own unsubscribe link and one-click headers", all(re.search(r"/unsubscribe/[A-Za-z0-9_-]{16,}", m["html"]) and m["headers"].get("List-Unsubscribe-Post") for m in camp))
    asyncio.run(scheduler.process_campaigns())
    st = Q("select status, sent_count, failed_count from email_campaigns")[0]
    rec("the campaign finishes as 'sent' and a second pass sends nothing more", st[0] == "sent" and st[1] == len(camp) and len([m for m in SENT if m["kind"] == "campaign"]) == len(camp), str(st))
    rec("a sent campaign cannot be edited or sent again", not ok_get(MEDIA, f"/admin/campaigns/{cid}/edit") or "draft" in MEDIA.get(f"/admin/campaigns/{cid}/edit", follow_redirects=True).text.lower())
    MEDIA.post("/admin/campaigns", data={"csrf_token": tok(form.text), "subject": "Broken", "body_text": "x", "button_label": "Go", "button_url": "javascript:alert(1)"})
    rec("a button needs a real https link", Q("select count(*) from email_campaigns where subject='Broken'")[0][0] == 0)

    # ══ 14. Website content settings ═══════════════════════════════════════
    st_page = S.get("/admin/settings").text
    rec("the settings page has the Website content tab, checklist and email test", 'data-tab="website"' in st_page and "Website checklist" in st_page and "Send a test email" in st_page and 'name="svc_day"' in st_page)
    rec("the HQ Administrator's dashboard shows what is left to finish; branch admins do not see it", "to finish on the public website" in S.get("/admin/dashboard").text and "to finish on the public website" not in A.get("/admin/dashboard").text)
    rec("the checklist flags what is still missing", "A real contact phone number" in st_page and "Bank account for transfers" in st_page)
    data = {"csrf_token": tok(st_page), "site_name": "Scent of Water Ministry", "svc_day": ["Sunday", "Thursday"], "svc_name": ["Celebration", "Miracle Night"], "svc_time": ["8:00 — 10:30 am", "6:00 — 8:00 pm"], "svc_lead": ["1", "0"],
            "exp_title": ["Parking", ""], "exp_text": ["Free parking on site.", ""], "bel_title": ["We believe in grace"], "bel_text": ["Grace alone."],
            "visitParking": "Ample parking", "ourStory": "It began in 1999.\n\nThen it grew.", "prayerResponseTime": "48 hours", "whatsappChannelUrl": "javascript:alert(1)", "plausibleDomain": "scentofwater.org<x>"}
    S.post("/admin/settings", data=data)
    ss = dict(Q("select key, value from site_settings"))
    rec("service times, visit list and teaching are saved as JSON from the rows", json.loads(ss["serviceTimes"])[1] == {"day": "Thursday", "name": "Miracle Night", "time": "6:00 — 8:00 pm", "lead": False} and json.loads(ss["visitExpect"]) == [{"title": "Parking", "text": "Free parking on site."}] and json.loads(ss["beliefs"])[0]["title"] == "We believe in grace")
    rec("an unsafe channel link is dropped and the Plausible domain is cleaned", ss.get("whatsappChannelUrl", "") == "" and ss["plausibleDomain"] == "scentofwater.orgx")
    rec("the public site follows: visit, home, about, contact", "Miracle Night" in V.get("/visit").text and "Miracle Night" in V.get("/").text and "Miracle Night" in V.get("/about").text and "Thursday" in V.get("/contact").text)
    rec("visit details appear only when filled", "Ample parking" in V.get("/visit").text and "What to wear" not in V.get("/visit").text)
    ab = V.get("/about").text
    rec("About shows the saved teaching and story (paragraphs), not the defaults", "We believe in grace" in ab and "The ground we stand on" in ab and "It began in 1999." in ab and "Then it grew." in ab and "The grace of God" not in ab)
    rec("the story section is hidden while empty", "It began in 1999." in ab and (setting(ourStory="") or True) and "How this house began" not in V.get("/about").text)
    rec("Plausible script is added when a domain is set", 'data-domain="scentofwater.orgx"' in V.get("/").text)
    rec("the prayer promise follows the setting", "within 48 hours" in V.get("/prayer").text)
    SENT.clear()
    S.post("/admin/settings/test-email", data={"csrf_token": tok(S.get("/admin/settings").text), "test_to": "boss@gmail.com"})
    rec("the test-email button sends through the provider", len(mails("test", "boss@gmail.com")) == 1)
    rec("only the HQ Administrator can use the settings and test email", not ok_get(MEDIA, "/admin/settings") and S.post("/admin/settings/test-email", data={"csrf_token": "x"}, follow_redirects=False).status_code in (303, 403))
    setting(serviceTimes="", visitExpect="", beliefs="")

    # ══ 15. invitations are emailed ════════════════════════════════════════
    SENT.clear()
    dep = Q("select id from departments limit 1")
    if not dep:
        Q("insert into departments (name, slug, is_active, created_at, updated_at) values ('Ushers','ushers',1,:n,:n)", n=datetime.utcnow()); dep = Q("select id from departments limit 1")
    A.post("/admin/hods/invite", data={"csrf_token": tok(A.get("/admin/hods/invite").text), "name": "New Hod", "email": "newhod@gmail.com", "department_id": str(dep[0][0]), "branch_id": str(A_id)})
    flush_mail()
    inv = mails("invitation", "newhod@gmail.com")
    tk_inv = Q("select token from hod_invitations where email='newhod@gmail.com'")
    rec("a HOD invitation is emailed with the activation link (copy-link still works)", len(inv) == 1 and tk_inv and f"/admin/hod/invitation/{tk_inv[0][0]}" in inv[0]["html"])

    # ══ 16. access + hygiene ═══════════════════════════════════════════════
    rec("public pages set no analytics/tracking cookies (only the session cookie)", set(k for k in V.cookies.keys()) <= {"swm_session"})
    rec("the admin console is not indexed and not in the sitemap", "/admin" not in sm.text)

print(f"\n==== {len(res)} checks, {res.count(False)} failed ====")
sys.exit(1 if False in res else 0)
