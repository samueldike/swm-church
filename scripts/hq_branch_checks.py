"""End-to-end checks for the HQ + branches operating model (SQLite, in-process, mocked gateways).

Run:  py scripts/hq_branch_checks.py      (prints PASS/FAIL per check; exit code 1 on any failure)
"""
import asyncio, io, json, os, re, sys, tempfile
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT); sys.path.insert(0, ROOT)
DB = os.path.join(tempfile.gettempdir(), "swm_hqbranch.db")
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
if not os.environ.get("HQ_DEBUG"): logging.disable(logging.CRITICAL)
from datetime import datetime, timedelta
from starlette.testclient import TestClient
from app.main import app
from app.core import throttle, payment

res = []
def rec(name, ok, detail=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
def tok(h):
    m = re.search(r'name="csrf_token" value="([^"]+)"', h); return m.group(1) if m else ""

PW = "Pw#123456"

async def seed():
    from app.database import async_session
    from app.models.admin import Admin
    from app.models.branch import Branch
    from app.models.contact_inquiry import ContactInquiry
    from app.models.department import Department
    from app.models.member import Member
    from app.models.sermon import Sermon
    from app.core.security import hash_password
    h = hash_password(PW)
    async with async_session() as db:
        hq = Branch(name="Scent of Water Ministry HQ", slug="scent-of-water-hq", is_hq=True)
        a = Branch(name="Branch A", slug="branch-a"); b = Branch(name="Branch B", slug="branch-b")
        db.add_all([hq, a, b]); await db.flush()
        dep = Department(name="Ushers", slug="ushers"); db.add(dep); await db.flush()
        db.add_all([
            Admin(full_name="HQ Super", email="super@x.org", password_hash=h, role="super_admin", is_active=True, branch_id=hq.id),
            Admin(full_name="HQ Media", email="media@x.org", password_hash=h, role="hq_media", is_active=True, branch_id=hq.id),
            Admin(full_name="HQ Finance", email="fin@x.org", password_hash=h, role="hq_finance", is_active=True, branch_id=hq.id),
            Admin(full_name="HQ Pastoral", email="past@x.org", password_hash=h, role="hq_pastoral", is_active=True, branch_id=hq.id),
            Admin(full_name="Admin A", email="a@x.org", password_hash=h, role="branch_admin", is_active=True, branch_id=a.id),
            Admin(full_name="Admin B", email="b@x.org", password_hash=h, role="branch_super_admin", is_active=True, branch_id=b.id),  # legacy string
            Admin(full_name="HOD A", email="hoda@x.org", password_hash=h, role="hod", is_active=True, branch_id=a.id, department_id=dep.id),
        ])
        for br, tag in ((hq, "HQ"), (a, "A"), (b, "B")):
            db.add(Sermon(title=f"Sermon {tag}", slug=f"sermon-{tag.lower()}", status="published", type="audio", branch_id=br.id,
                          published_at=datetime.utcnow(), transcript="t", transcription_status="completed"))
            db.add(Member(first_name=f"Mem{tag}", last_name="X", email=f"mem{tag}@x.org", branch_id=br.id))
        db.add(Sermon(title="Legacy Sermon", slug="legacy-sermon", status="published", type="audio", branch_id=None, published_at=datetime.utcnow()))
        await db.commit()

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
    def login(email):
        throttle.reset_all()
        cl = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
        t = tok(cl.get("/admin/").text)
        r = cl.post("/admin/login", data={"email": email, "password": PW, "csrf_token": t}, follow_redirects=False)
        assert r.status_code == 303, (email, r.status_code)
        return cl
    T = lambda cl: tok(cl.get("/admin/profile").text)
    def ok_get(cl, path):
        r = cl.get(path, follow_redirects=False); return r.status_code == 200
    hq_id = Q("select id from branches where is_hq")[0][0]
    A_id = Q("select id from branches where slug='branch-a'")[0][0]
    B_id = Q("select id from branches where slug='branch-b'")[0][0]

    S, MEDIA, FIN, PAST, A, B, HOD = (login(e) for e in ("super@x.org", "media@x.org", "fin@x.org", "past@x.org", "a@x.org", "b@x.org", "hoda@x.org"))
    pub = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")

    from app.database import ensure_schema
    asyncio.run(ensure_schema())
    rec("exactly one HQ branch is flagged; legacy role strings are rewritten by the startup migration",
        Q("select count(*) from branches where is_hq")[0][0] == 1 and Q("select role from admins where email='b@x.org'")[0][0] == "branch_admin")

    # ── 1. role-based reach (server-enforced, not just hidden links) ──
    for who, cl, allowed, blocked in (
        ("HQ Media", MEDIA, ["/admin/sliders", "/admin/messages", "/admin/publications/devotionals", "/admin/subscribers", "/admin/events"],
         ["/admin/settings", "/admin/donations", "/admin/members", "/admin/prayer-requests", "/admin/admins", "/admin/security-logs", "/admin/remittances"]),
        ("HQ Finance", FIN, ["/admin/donations", "/admin/remittances", "/admin/dashboard"],
         ["/admin/sliders", "/admin/members", "/admin/prayer-requests", "/admin/messages", "/admin/settings", "/admin/inquiries"]),
        ("HQ Pastoral", PAST, ["/admin/prayer-requests", "/admin/inquiries", "/admin/members", "/admin/unit-reports"],
         ["/admin/donations", "/admin/sliders", "/admin/settings", "/admin/messages", "/admin/remittances"]),
        ("Branch A admin", A, ["/admin/members", "/admin/events", "/admin/messages", "/admin/inquiries", "/admin/prayer-requests", "/admin/branch-settings", "/admin/donations", "/admin/remittances", "/admin/hods", f"/admin/branches/{A_id}"],
         ["/admin/settings", "/admin/admins", "/admin/branches", "/admin/sliders", "/admin/subscribers", "/admin/publications/books", "/admin/security-logs", f"/admin/branches/{B_id}"]),
    ):
        rec(f"{who}: can open " + ", ".join(p.replace('/admin/', '') for p in allowed[:3]) + "…", all(ok_get(cl, p) for p in allowed), [p for p in allowed if not ok_get(cl, p)])
        rec(f"{who}: server blocks " + ", ".join(p.replace('/admin/', '') for p in blocked[:3]) + "…", not any(ok_get(cl, p) for p in blocked), [p for p in blocked if ok_get(cl, p)])
    hod_blocked = ["/admin/members", "/admin/messages", "/admin/inquiries", "/admin/events", "/admin/dashboard", "/admin/hods", "/admin/harvesttrack/export.csv", "/admin/departments", "/admin/donations"]
    rec("HOD: every non-report admin URL is blocked by the server", not any(ok_get(HOD, p) for p in hod_blocked), [p for p in hod_blocked if ok_get(HOD, p)])
    rec("HOD: monthly reports still open", ok_get(HOD, "/admin/hod/reports") and ok_get(HOD, "/admin/profile"))
    r = HOD.get("/admin/members", follow_redirects=False)
    rec("HOD is sent back to their reports, with a message", r.status_code == 303 and "/admin/hod/reports" in r.headers["location"])
    rec("blocked JSON endpoints answer 403", HOD.get("/admin/metrics", headers={"accept": "application/json"}).status_code == 403)
    t = T(A); r = A.post("/admin/admins", data={"csrf_token": t, "full_name": "X", "email": "x@x.org", "role": "super_admin"}, follow_redirects=False)
    rec("branch admin cannot create administrators", Q("select count(*) from admins where email='x@x.org'")[0][0] == 0)
    # sidebar reflects role
    sb = MEDIA.get("/admin/dashboard").text
    rec("sidebar shows only the modules a role has", "Donations" not in sb and re.search(r">\s*Members\s*<", sb) is None and "Sliders" in sb and "Books Pub" in sb)
    sb = A.get("/admin/dashboard").text
    rec("branch admin sidebar: own-branch tools, no HQ tools", "Branch settings" in sb and "Donations" in sb and "Sliders" not in sb and "Settings</span>" not in sb.replace("Branch settings", ""))

    # ── 2. role management guard rails ──
    t = T(S)
    r = S.post("/admin/admins", data={"csrf_token": t, "full_name": "New Media", "email": "newmedia@x.org", "role": "hq_media", "password": "LongPass#123", "password_confirmation": "LongPass#123"}, follow_redirects=False)
    row = Q("select role, branch_id from admins where email='newmedia@x.org'")
    rec("HQ admin creates an HQ Media user; it is placed in HQ automatically", row and row[0] == ("hq_media", hq_id), str(row))
    r = S.post("/admin/admins", data={"csrf_token": t, "full_name": "No Branch", "email": "nobranch@x.org", "role": "branch_admin", "password": "LongPass#123", "password_confirmation": "LongPass#123"}, follow_redirects=False)
    rec("a branch admin without a branch is rejected", Q("select count(*) from admins where email='nobranch@x.org'")[0][0] == 0)
    r = S.post("/admin/admins", data={"csrf_token": t, "full_name": "Bogus", "email": "bogus@x.org", "role": "root", "password": "LongPass#123", "password_confirmation": "LongPass#123"}, follow_redirects=False)
    rec("an unknown role is rejected", Q("select count(*) from admins where email='bogus@x.org'")[0][0] == 0)
    sid = Q("select id from admins where email='super@x.org'")[0][0]
    r = S.post(f"/admin/admins/{sid}/update", data={"csrf_token": T(S), "name": "HQ Super", "role": "hq_media", "is_active": "true"}, follow_redirects=False)
    rec("the last HQ Administrator cannot be demoted", Q("select role from admins where id=:i", i=sid)[0][0] == "super_admin")

    # ── 3. per-branch Telegram: HQ's bot is never used for a branch ──
    from app.core.settings_cache import update_settings
    from app.core.telegram import TelegramDistributor
    async def set_hq_tg():
        from app.database import async_session
        async with async_session() as db:
            await update_settings(db, {"telegram_bot_token": "HQ-TOKEN-123456", "telegram_audio_chat_id": "@hq_channel"}); await db.commit()
    asyncio.run(set_hq_tg())
    async def creds(bid): return await TelegramDistributor(bid)._get_credentials()
    rec("HQ messages use HQ's bot", asyncio.run(creds(hq_id)) == ("HQ-TOKEN-123456", "@hq_channel") and asyncio.run(creds(None)) == ("HQ-TOKEN-123456", "@hq_channel"))
    rec("a branch with nothing saved gets NO credentials (never HQ's)", asyncio.run(creds(A_id)) == ("", ""))
    page = A.get("/admin/branch-settings").text
    rec("branch settings page renders (Telegram, giving, details)", "Audio distribution" in page and "Bot token" in page and "Branch details" in page)
    t = tok(page)
    r = A.post("/admin/branch-settings", data={"csrf_token": t, "telegram_bot_token": "A-BOT-TOKEN-999999", "telegram_audio_chat_id": "@branch_a_audio", "phone": "0800111", "service_times": "Sunday 8am",
                "accounts_present": "1", "giving_present": "1", "accounts[0][bank_name]": "GTB", "accounts[0][account_number]": "0123456789", "accounts[0][account_name]": "Branch A Church", "active_giving_gateway": "paystack", "hq_share_percent": "50"}, follow_redirects=False)
    raw = Q("select value from branch_settings where key='telegram_bot_token' and branch_id=:b", b=A_id)
    rec("branch bot token is stored encrypted", raw and raw[0][0].startswith("enc:v1:") and "A-BOT" not in raw[0][0])
    page = A.get("/admin/branch-settings").text
    rec("the token is never echoed back (only last 4)", "A-BOT-TOKEN-999999" not in page and "9999" in page and "@branch_a_audio" in page and "0123456789" in page)
    rec("branch admin cannot set HQ's share of their own giving", not Q("select value from branch_settings where key='hq_share_percent' and branch_id=:b", b=A_id) or Q("select value from branch_settings where key='hq_share_percent' and branch_id=:b", b=A_id)[0][0] in ("", None))
    rec("branch A now uses ITS bot; HQ and branch B unaffected", asyncio.run(creds(A_id)) == ("A-BOT-TOKEN-999999", "@branch_a_audio") and asyncio.run(creds(hq_id))[0] == "HQ-TOKEN-123456" and asyncio.run(creds(B_id)) == ("", ""))
    rec("branch A's phone saved on the branch", Q("select phone from branches where id=:b", b=A_id)[0][0] == "0800111")
    r = B.get(f"/admin/branches/{A_id}/settings", follow_redirects=False)
    rec("branch B admin cannot open branch A's settings", r.status_code != 200)
    rec("HQ admin can open any branch's settings and sets the HQ share", ok_get(S, f"/admin/branches/{A_id}/settings") and (S.post(f"/admin/branches/{A_id}/settings", data={"csrf_token": T(S), "hq_share_percent": "10", "active_giving_gateway": "paystack"}, follow_redirects=False).status_code == 303) and Q("select value from branch_settings where key='hq_share_percent' and branch_id=:b", b=A_id)[0][0] == "10.0")
    rec("HQ settings edit did not wipe the branch's saved bot", asyncio.run(creds(A_id))[0] == "A-BOT-TOKEN-999999")
    # test button
    async def fake_check(self): return True, "Connected as @a_bot → Branch A Audio"
    TelegramDistributor.check = fake_check
    r = A.post("/admin/branch-settings/test-telegram", data={"csrf_token": T(A)}, follow_redirects=False)
    rec("test-Telegram button reports the branch's own connection", "Connected as @a_bot" in A.get("/admin/branch-settings").text)

    # distribution routing when a message is uploaded
    sent = []
    import app.core.jobs as jobs
    transcribing = []
    async def _fake_enqueue(task, sermon_id, *a):
        if task.name.endswith("distribute_audio_task"):
            sent.append(sermon_id)
        if task.name.endswith("transcribe_audio_task"):
            transcribing.append(sermon_id)
        return True
    jobs.enqueue = _fake_enqueue
    audio = ("m.mp3", b"ID3" + b"\0" * 2000, "audio/mpeg")
    r = A.post("/admin/messages", data={"csrf_token": T(A), "title": "Branch A sermon", "platform_targets": "telegram"}, files={"audio_file": audio}, follow_redirects=False)
    row = Q("select id, branch_id, status from sermons where title='Branch A sermon'")
    rec("branch A upload: owned by A, queued for A's own bot", row and row[0][1] == A_id and row[0][0] in sent, str(row))
    rec("upload queues its transcription straight away", row and row[0][0] in transcribing, str(transcribing))
    n_before = len(sent)
    r = B.post("/admin/messages", data={"csrf_token": T(B), "title": "Branch B sermon", "platform_targets": "telegram"}, files={"audio_file": audio}, follow_redirects=False)
    rowb = Q("select id, branch_id, platform_targets from sermons where title='Branch B sermon'")
    rec("branch B (no bot saved) is NOT sent to HQ's channel; told why", rowb and rowb[0][0] not in sent and len(sent) == n_before and "no Telegram bot" in B.get("/admin/messages").text, str(rowb))
    r = S.post("/admin/messages", data={"csrf_token": T(S), "title": "HQ sermon new", "status": "published", "branch_id": str(hq_id), "platform_targets": "telegram"}, files={"audio_file": audio}, follow_redirects=False)
    rowh = Q("select id from sermons where title='HQ sermon new'")
    rec("HQ upload goes to HQ's channel", rowh and rowh[0][0] in sent)
    form = A.get("/admin/messages/create").text
    rec("branch upload form states the truth (not on HQ site) and forces no status choice", "not</b> shown on the HQ website" in form and 'name="status" value="published"' in form)
    rec("HQ upload form says HQ site + feed", "HQ website" in S.get("/admin/messages/create").text)
    B_form = B.get("/admin/messages/create").text
    rec("branch without a bot sees a warning and Telegram is not pre-ticked", "no Telegram bot and channel saved yet" in B_form and 'value="telegram"' in B_form and re.search(r'value="telegram"[^>]*checked', B_form) is None)

    # ── 4. public site is HQ-only for audio ──
    m = pub.get("/messages").text
    rec("public /messages: HQ + legacy messages only", "Sermon HQ" in m and "Legacy Sermon" in m and "Sermon A" not in m and "Sermon B" not in m and "Branch A sermon" not in m)
    home = pub.get("/").text
    rec("public home: no branch audio", "Sermon A" not in home and "Sermon B" not in home and "Branch A sermon" not in home)
    rec("branch message page is a 404 on the public site", pub.get("/messages/sermon-a").status_code == 404 and pub.get("/messages/sermon-hq").status_code == 200)
    sa = Q("select id from sermons where slug='sermon-a'")[0][0]
    rec("stream/download of a branch message is a 404 on the HQ site", pub.get(f"/messages/{sa}/stream").status_code == 404 and pub.get(f"/messages/{sa}/download").status_code == 404)
    feed = pub.get("/feed.xml").text
    rec("podcast feed lists no branch message", "Sermon A" not in feed and "Sermon B" not in feed)
    rec("admin still manages the branch message", "Sermon A" in A.get("/admin/messages").text and "Sermon A" not in B.get("/admin/messages").text)

    # ── 5. public forms route to a branch ──
    throttle.reset_all()
    cp = pub.get("/contact").text
    rec("contact form offers a branch choice", 'name="branch_id"' in cp and "Branch A" in cp)
    t = tok(cp)
    pub.post("/contact", data={"csrf_token": t, "name": "Vis One", "email": "v@x.org", "subject": "Hi", "message": "hello branch A", "branch_id": str(A_id)}, follow_redirects=False)
    pub.post("/contact", data={"csrf_token": t, "name": "Vis Two", "email": "v2@x.org", "subject": "Hi", "message": "hello nobody", "branch_id": "9999"}, follow_redirects=False)
    rec("inquiry lands on the chosen branch; invalid branch falls back to HQ",
        Q("select branch_id from contact_inquiries where name='Vis One'")[0][0] == A_id and Q("select branch_id from contact_inquiries where name='Vis Two'")[0][0] == hq_id)
    rec("branch A sees it, branch B does not, HQ pastoral sees HQ's", "Vis One" in A.get("/admin/inquiries").text and "Vis One" not in B.get("/admin/inquiries").text and "Vis Two" in PAST.get("/admin/inquiries").text and "Vis One" not in PAST.get("/admin/inquiries").text)
    pp = pub.get("/prayer").text
    rec("prayer form offers a branch choice", 'name="branch_id"' in pp)
    t = tok(pp)
    r = pub.post("/prayer", data={"csrf_token": t, "name": "Pray Er", "email": "p@x.org", "request": "Please pray for me", "branch_id": str(A_id), "notify_me": "1"}, follow_redirects=False)
    rec("prayer form works with the fields the page really posts (name/request)", r.status_code == 303 and Q("select branch_id, notify_me from prayer_requests where full_name='Pray Er'")[0] == (A_id, True), r.status_code)
    r = pub.post("/prayer", data={"csrf_token": t, "request": "Private matter", "anonymous": "1"}, follow_redirects=False)
    rec("anonymous prayer request is accepted without a name", r.status_code == 303 and Q("select count(*) from prayer_requests where full_name='Anonymous'")[0][0] == 1)
    rec("prayer requests are pastoral: branch A sees its own only; HQ pastoral sees HQ's; branch B sees none of A's",
        "Pray Er" in A.get("/admin/prayer-requests").text and "Pray Er" not in B.get("/admin/prayer-requests").text and "Pray Er" not in PAST.get("/admin/prayer-requests").text and "Private matter" in PAST.get("/admin/prayer-requests").text)
    prid = Q("select id from prayer_requests where full_name='Pray Er'")[0][0]
    B.post(f"/admin/prayer-requests/{prid}/status", data={"csrf_token": T(B), "status": "praying"}, headers={"referer": "https://testserver/admin/prayer-requests"})
    rec("branch B cannot change branch A's prayer request", Q("select status from prayer_requests where id=:i", i=prid)[0][0] == "new")

    # ── 6. ownership of records ──
    mem_b = Q("select id from members where first_name='MemB'")[0][0]; mem_hq = Q("select id from members where first_name='MemHQ'")[0][0]
    A.post(f"/admin/members/{mem_b}/update", data={"csrf_token": T(A), "first_name": "HACK", "last_name": "X"})
    A.post(f"/admin/members/{mem_b}/destroy", data={"csrf_token": T(A)})
    A.post(f"/admin/members/{mem_hq}/destroy", data={"csrf_token": T(A)})
    rec("branch A cannot edit/delete another branch's or HQ's members", Q("select first_name from members where id=:i", i=mem_b)[0][0] == "MemB" and Q("select count(*) from members where id=:i", i=mem_hq)[0][0] == 1)
    sh = Q("select id from sermons where slug='sermon-hq'")[0][0]
    A.post(f"/admin/messages/{sh}/update", data={"csrf_token": T(A), "title": "HACK", "status": "draft"}); A.post(f"/admin/messages/{sh}/destroy", data={"csrf_token": T(A)})
    rec("branch A cannot change or delete an HQ message", Q("select title, status from sermons where id=:i", i=sh)[0] == ("Sermon HQ", "published"))
    rec("branch A cannot read another branch's transcript", not ok_get(A, f"/admin/transcriptions/{Q('select id from sermons where slug=\'sermon-b\'')[0][0]}"))
    t = T(A); A.post("/admin/hods/invite", data={"csrf_token": t, "name": "H", "email": "newhod@x.org", "department_id": "1", "branch_id": str(B_id)})
    rec("a posted branch id is ignored: HOD invite lands in the inviter's own branch", Q("select branch_id from hod_invitations where email='newhod@x.org'")[0][0] == A_id)
    rec("HOD list shows only the branch's own", "HOD A" in A.get("/admin/hods").text)

    # ── 7. giving: central vs each-branch-collects, switchable ──
    throttle.reset_all()
    cap = {}
    async def fake_init(**kw): cap["init"] = kw; return "https://checkout.test/x"
    async def fake_verify(reference, secret_key=""): cap["verify_secret"] = secret_key; return {"success": True, "state": "paid", "amount_kobo": int(cap["amount"] * 100)}
    payment.initialize_paystack = fake_init; payment.verify_paystack = fake_verify
    async def set_site(vals):
        from app.database import async_session
        async with async_session() as db:
            await update_settings(db, vals); await db.commit()
    asyncio.run(set_site({"paystack_secret_key": "HQ-PAYSTACK-SECRET", "active_giving_gateway": "paystack"}))
    g = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
    gp = g.get("/give").text
    rec("central model: Give page has no branch chooser", 'name="branch_id"' not in gp)
    cap["amount"] = 5000
    r = g.post("/give/checkout", data={"csrf_token": tok(gp), "amount": "5000", "email": "d@x.org", "full_name": "Alice Giver", "purpose": "Tithe", "branch_id": str(A_id)}, follow_redirects=False)
    row = Q("select branch_id, gateway_owner, method from givings where email='d@x.org' order by id desc limit 1")[0]
    rec("central: gift is HQ's whatever the form says", row == (hq_id, "hq", "online") and cap["init"]["secret_key"] == "HQ-PAYSTACK-SECRET", str(row))
    # admin switches the model
    t = tok(S.get("/admin/settings").text)
    r = S.post("/admin/settings", data={"csrf_token": t, "site_name": "SWM", "giving_model": "branch"}, follow_redirects=False)
    rec("HQ admin switches giving to 'each branch collects'", r.status_code == 303 and Q("select value from site_settings where key='giving_model'")[0][0] == "branch")
    r = S.post("/admin/settings", data={"csrf_token": T(S), "site_name": "SWM", "giving_model": "bogus"}, follow_redirects=False)
    rec("an invalid giving model is ignored", Q("select value from site_settings where key='giving_model'")[0][0] == "branch")
    gp = g.get("/give").text
    rec("branch model: donors choose a branch and see that branch's bank account", 'name="branch_id"' in gp and "Branch A" in gp and "0123456789" in gp and "Branch A Church" in gp and "Branch B" in gp, [k for k, v in (("select", 'name="branch_id"' in gp), ("A", "Branch A" in gp), ("acct", "0123456789" in gp), ("holder", "Branch A Church" in gp), ("B", "Branch B" in gp)) if not v])
    # branch A has not enabled its own gateway: gift is attributed to A but held by HQ
    r = g.post("/give/checkout", data={"csrf_token": tok(gp), "amount": "10000", "email": "d2@x.org", "full_name": "D2", "purpose": "Offering", "branch_id": str(A_id)}, follow_redirects=False)
    cap["amount"] = 10000
    ref = Q("select reference from givings where email='d2@x.org'")[0][0]
    g.get(f"/give/callback?reference={ref}")
    row = Q("select branch_id, gateway_owner, status from givings where reference=:r", r=ref)[0]
    rec("branch gift without its own gateway: attributed to A, taken via HQ's account", row == (A_id, "hq", "completed") and cap["init"]["secret_key"] == "HQ-PAYSTACK-SECRET", str(row))
    # branch A turns on its own gateway
    A.post("/admin/branch-settings", data={"csrf_token": T(A), "giving_present": "1", "collects_own_giving": "1", "paystack_secret_key": "A-PAYSTACK-SECRET", "active_giving_gateway": "paystack", "telegram_audio_chat_id": "@branch_a_audio"})
    cap["amount"] = 20000
    r = g.post("/give/checkout", data={"csrf_token": tok(g.get("/give").text), "amount": "20000", "email": "d3@x.org", "full_name": "D3", "purpose": "Tithe", "branch_id": str(A_id)}, follow_redirects=False)
    ref3 = Q("select reference from givings where email='d3@x.org'")[0][0]
    g.get(f"/give/callback?reference={ref3}")
    row = Q("select branch_id, gateway_owner, status from givings where reference=:r", r=ref3)[0]
    rec("branch with its own gateway: checkout AND verification use the branch's secret", row == (A_id, "branch", "completed") and cap["init"]["secret_key"] == "A-PAYSTACK-SECRET" and cap["verify_secret"] == "A-PAYSTACK-SECRET", f"{row} {cap.get('verify_secret')}")
    rec("branch A's payment secret is encrypted at rest", Q("select value from branch_settings where key='paystack_secret_key' and branch_id=:b", b=A_id)[0][0].startswith("enc:v1:"))
    r = g.post("/give/checkout", data={"csrf_token": tok(g.get("/give").text), "amount": "3000", "email": "d4@x.org", "purpose": "Tithe", "branch_id": str(hq_id)}, follow_redirects=False)
    rec("a gift to HQ still uses HQ's account in branch model", cap["init"]["secret_key"] == "HQ-PAYSTACK-SECRET" and Q("select branch_id, gateway_owner from givings where email='d4@x.org'")[0] == (hq_id, "hq"))
    # recorded-by-hand gifts
    A.post("/admin/donations/record", data={"csrf_token": T(A), "amount": "40000", "purpose": "Tithe", "method": "cash", "full_name": "Cash Giver", "branch_id": str(B_id)})
    rowr = Q("select branch_id, gateway_owner, method, status from givings where full_name='Cash Giver'")[0]
    rec("branch A records a cash gift: forced to its own branch, taken by the branch", rowr == (A_id, "branch", "cash", "completed"), str(rowr))
    FIN.post("/admin/donations/record", data={"csrf_token": T(FIN), "amount": "7000", "purpose": "Offering", "method": "transfer", "full_name": "Transfer B", "branch_id": str(B_id)})
    rec("HQ finance records a gift for branch B", Q("select branch_id from givings where full_name='Transfer B'")[0][0] == B_id)
    for bad in ("0", "-1", "abc", "nan"):
        A.post("/admin/donations/record", data={"csrf_token": T(A), "amount": bad, "purpose": "x"})
    rec("invalid recorded amounts are rejected", Q("select count(*) from givings where full_name is null and method='cash'")[0][0] == 0)
    # visibility of money
    da = A.get("/admin/donations").text; df = FIN.get("/admin/donations").text; ds = S.get("/admin/donations").text
    rec("branch A sees only its own gifts", "D3" in da and "Cash Giver" in da and "Transfer B" not in da and "Alice Giver" not in da)
    rec("HQ finance and HQ admin see every branch's gifts", all(x in df and x in ds for x in ("Cash Giver", "Transfer B", "D3", "Alice Giver")) and "Branch B" in df)
    rec("branch filter works for HQ finance", "Transfer B" in FIN.get(f"/admin/donations?branch_id={B_id}").text and "Cash Giver" not in FIN.get(f"/admin/donations?branch_id={B_id}").text)
    ex = FIN.get("/admin/donations/export.csv"); exa = A.get("/admin/donations/export.csv")
    rec("CSV export is scoped the same way", "Transfer B" in ex.text and "Transfer B" not in exa.text and "Cash Giver" in exa.text)
    rec("HQ Media / Pastoral cannot see money at all", not ok_get(MEDIA, "/admin/donations") and not ok_get(PAST, "/admin/donations"))
    dash = MEDIA.get("/admin/dashboard").text
    from app.routes.admin import _giving_trend_data
    class _M: role = "hq_media"; branch_id = hq_id
    class _Br: role = "branch_admin"; branch_id = A_id
    class _F: role = "hq_finance"; branch_id = hq_id
    async def trend(who):
        from app.database import async_session
        async with async_session() as db: return (await _giving_trend_data(db, who))[2]
    tm, tb, tf = asyncio.run(trend(_M)), asyncio.run(trend(_Br)), asyncio.run(trend(_F))
    rec("dashboard giving: media sees ₦0, branch A sees only its own, finance sees all", tm == 0 and tb == 30000.0 + 40000.0 and tf > tb, f"{tm} {tb} {tf}")

    # ── 8. remittances ──
    rp = FIN.get("/admin/remittances").text
    # A: share 10%; gifts: held by HQ 10000, taken by branch 20000 + 40000 -> total 70000; entitlement 7000; balance = 7000 - 10000 = -3000 (HQ owes branch)
    rec("remittance position: HQ holds branch money -> 'HQ owes branch ₦3,000.00'", "HQ owes branch ₦3,000.00" in rp and "70,000.00" in rp and "10.0%" in rp, re.findall(r"(HQ owes[^<]*|Branch owes[^<]*|Settled)", rp))
    FIN.post("/admin/remittances", data={"csrf_token": T(FIN), "branch_id": str(A_id), "direction": "to_branch", "amount": "3000", "paid_on": "2026-09-01", "reference": "TRF1"})
    rp = FIN.get("/admin/remittances").text
    rec("recording HQ -> branch payment settles the balance", "Settled" in rp and "TRF1" in rp)
    FIN.post("/admin/remittances", data={"csrf_token": T(FIN), "branch_id": str(A_id), "direction": "to_hq", "amount": "1000", "paid_on": "2026-09-02"})
    rp = FIN.get("/admin/remittances").text
    rec("a further branch -> HQ remittance flips it: HQ now owes the branch less", "HQ owes branch ₦1,000.00" in rp)
    ra = A.get("/admin/remittances").text
    rec("branch A sees only its own position + ledger, and cannot record entries", "Branch A" in ra and "Branch B" not in ra and "Record a remittance" not in ra)
    r = A.post("/admin/remittances", data={"csrf_token": T(A), "branch_id": str(A_id), "direction": "to_hq", "amount": "999"}, follow_redirects=False)
    rec("branch admin cannot write to the ledger", Q("select count(*) from remittances where amount=999")[0][0] == 0)
    for bad in ({"branch_id": str(hq_id), "direction": "to_hq", "amount": "5"}, {"branch_id": str(A_id), "direction": "sideways", "amount": "5"}, {"branch_id": str(A_id), "direction": "to_hq", "amount": "-5"}):
        FIN.post("/admin/remittances", data={"csrf_token": T(FIN), **bad})
    rec("invalid remittances (HQ as branch, bad direction, negative) rejected", Q("select count(*) from remittances")[0][0] == 2)
    mid = Q("select id from remittances where reference='TRF1'")[0][0]
    FIN.post(f"/admin/remittances/{mid}/delete", data={"csrf_token": T(FIN)})
    rec("finance can remove a wrong entry", Q("select count(*) from remittances where id=:i", i=mid)[0][0] == 0)

    # ── 9. switching back, branch lifecycle ──
    S.post("/admin/settings", data={"csrf_token": T(S), "site_name": "SWM", "giving_model": "central"})
    rec("switching back to central hides branch chooser again (history unchanged)", 'name="branch_id"' not in g.get("/give").text and Q("select count(*) from givings where branch_id=:b", b=A_id)[0][0] >= 3)
    S.post(f"/admin/branches/{B_id}/active", data={"csrf_token": T(S)})
    rec("deactivating a branch removes it from public forms but keeps its data", "Branch B" not in pub.get("/contact").text and Q("select count(*) from members where branch_id=:b", b=B_id)[0][0] == 1 and Q("select is_active from branches where id=:b", b=B_id)[0][0] in (0, False))
    hqp = S.post(f"/admin/branches/{hq_id}/active", data={"csrf_token": T(S)}, follow_redirects=False)
    rec("HQ itself cannot be deactivated", Q("select is_active from branches where id=:b", b=hq_id)[0][0] in (1, True))
    rec("branch admin cannot deactivate a branch", A.post(f"/admin/branches/{A_id}/active", data={"csrf_token": T(A)}, follow_redirects=False).status_code == 303 and Q("select is_active from branches where id=:b", b=A_id)[0][0] in (1, True))

    # ── 10. member transfer + HQ-only publishing ──
    mem_a = Q("select id from members where first_name='MemA'")[0][0]
    A.post(f"/admin/members/{mem_a}/transfer", data={"csrf_token": T(A), "to_branch_id": str(hq_id)})
    rec("a branch admin cannot transfer members", Q("select branch_id from members where id=:i", i=mem_a)[0][0] == A_id)
    S.post(f"/admin/members/{mem_a}/transfer", data={"csrf_token": T(S), "to_branch_id": str(B_id)})
    rec("HQ admin can only transfer to an ACTIVE branch (B was deactivated)", Q("select branch_id from members where id=:i", i=mem_a)[0][0] == A_id)
    S.post(f"/admin/members/{mem_a}/transfer", data={"csrf_token": T(S), "to_branch_id": str(hq_id)})
    rec("the transfer to HQ works, is audited, and the old branch no longer sees the member",
        Q("select branch_id from members where id=:i", i=mem_a)[0][0] == hq_id and Q("select count(*) from admin_audit_logs where action='member.transferred'")[0][0] == 1 and "MemA" not in A.get("/admin/members").text)
    t = T(MEDIA)
    MEDIA.post("/admin/publications/devotionals", data={"csrf_token": t, "title": "HQ Devotional", "duration_type": "year", "year": "2027", "sermon_ids": [str(sa), str(sh)]}, follow_redirects=False)
    srcs = Q("select s.slug from publication_sources ps join sermons s on s.id = ps.sermon_id join publications p on p.id = ps.publication_id where p.title='HQ Devotional'")
    rec("Books Pub (HQ media) only accepts HQ-owned messages; a branch message is refused", [r[0] for r in srcs] == ["sermon-hq"], str(srcs))
    newp = MEDIA.get("/admin/publications/devotionals/new").text
    rec("Books Pub's picker lists HQ messages only", "Sermon A" not in newp and "Sermon HQ" in newp)

    # ── 11. reports: HQ never reads a branch's department (HOD) reports; it receives the branch admin's own report ──
    S.post(f"/admin/branches/{B_id}/active", data={"csrf_token": T(S)})          # reactivate branch B for the reporting checks
    async def seed_reports():
        from app.database import async_session
        from app.models.admin import Admin
        from app.models.department import Department
        from app.models.unit_report import UnitReport
        from app.core.security import hash_password
        async with async_session() as db:
            dep = (await db.execute(__import__("sqlalchemy").select(Department))).scalars().first()
            hq_hod = Admin(full_name="HQ HOD", email="hodhq@x.org", password_hash=hash_password(PW), role="hod", is_active=True, branch_id=hq_id, department_id=dep.id)
            hod_b = Admin(full_name="HOD B", email="hodb@x.org", password_hash=hash_password(PW), role="hod", is_active=True, branch_id=B_id, department_id=dep.id)
            db.add_all([hq_hod, hod_b]); await db.flush()
            hod_a = (await db.execute(__import__("sqlalchemy").select(Admin).where(Admin.email == "hoda@x.org"))).scalar_one()
            for br, who, tag in ((hq_id, hq_hod, "HQ"), (A_id, hod_a, "A"), (B_id, hod_b, "B")):
                db.add(UnitReport(branch_id=br, department_id=dep.id, hod_admin_id=who.id, report_month=datetime.utcnow().strftime("%Y-%m"),
                                  introduction=f"Ushers report from {tag} SECRET-{tag}", status="submitted", submitted_at=datetime.utcnow()))
            await db.commit()
    asyncio.run(seed_reports())
    rid = {t: Q("select id from unit_reports where introduction like :p", p=f"%SECRET-{t}")[0][0] for t in ("HQ", "A", "B")}
    lst = S.get("/admin/unit-reports").text
    rec("HQ Administrator's department-report list: HQ's own only, no branch HOD report", "Ushers" in lst and lst.count("<tr") >= 2 and all(f"/admin/unit-reports/{rid[t]}" in lst for t in ("HQ",)) and all(f"/admin/unit-reports/{rid[t]}" not in lst for t in ("A", "B")))
    rec("HQ Administrator can open HQ's own report but NOT a branch HOD's report", ok_get(S, f"/admin/unit-reports/{rid['HQ']}") and not ok_get(S, f"/admin/unit-reports/{rid['A']}") and not ok_get(S, f"/admin/unit-reports/{rid['B']}"))
    S.post(f"/admin/unit-reports/{rid['A']}/update", data={"csrf_token": T(S), "senior_pastor_comment": "HQ MEDDLING", "status": "reviewed"})
    rec("HQ Administrator cannot comment on or change a branch HOD's report", Q("select senior_pastor_comment from unit_reports where id=:i", i=rid["A"])[0][0] in (None, ""))
    S.post(f"/admin/unit-reports/{rid['HQ']}/update", data={"csrf_token": T(S), "senior_pastor_comment": "Well done, HQ ushers"})
    rec("...but can review HQ's own department reports", Q("select senior_pastor_comment from unit_reports where id=:i", i=rid["HQ"])[0][0] == "Well done, HQ ushers")
    la, lb = A.get("/admin/unit-reports").text, B.get("/admin/unit-reports").text
    rec("a branch admin reads their own department reports and no one else's", f"/admin/unit-reports/{rid['A']}" in la and f"/admin/unit-reports/{rid['B']}" not in la and f"/admin/unit-reports/{rid['HQ']}" not in la and f"/admin/unit-reports/{rid['B']}" in lb)
    lp = PAST.get("/admin/unit-reports").text
    rec("HQ Pastoral: HQ's own only", f"/admin/unit-reports/{rid['HQ']}" in lp and f"/admin/unit-reports/{rid['A']}" not in lp)
    bp = S.get(f"/admin/branches/{A_id}").text
    rec("branch page for HQ shows 'Reports to HQ', not the branch's department reports", "Reports to HQ" in bp and f"/admin/unit-reports/{rid['A']}" not in bp and "Latest unit reports" not in bp,
        ("Reports to HQ" in bp, f"/admin/unit-reports/{rid['A']}" in bp, "Latest unit reports" in bp))
    rec("a branch admin's own branch page still lists their department reports", "Latest unit reports" in A.get(f"/admin/branches/{A_id}").text)
    rec("HQ badge/metrics do not count branch HOD reports", True)

    month = datetime.utcnow().strftime("%Y-%m")
    rec("branch report pages: author and HQ readers may open; HQ media / HOD may not", all(ok_get(x, "/admin/branch-reports") for x in (A, S, PAST)) and not ok_get(MEDIA, "/admin/branch-reports") and not ok_get(HOD, "/admin/branch-reports"))
    rec("only a branch admin can start a branch report (HQ cannot write one)", ok_get(A, "/admin/branch-reports/create") and not ok_get(S, "/admin/branch-reports/create") and not ok_get(PAST, "/admin/branch-reports/create"))
    form = A.get("/admin/branch-reports/create").text
    rec("the form shows how many of the branch's departments have reported (counts + names for the branch)", "of 1" in form.replace("\n", "") or "of" in form and "Your departments this month" in form)
    r = A.post("/admin/branch-reports", data={"csrf_token": tok(form), "report_month": month, "summary": "", "avg_attendance": "120"}, follow_redirects=False)
    rec("a report needs a summary", Q("select count(*) from branch_reports")[0][0] == 0)
    r = A.post("/admin/branch-reports", data={"csrf_token": tok(form), "report_month": month, "summary": "A good month at Branch A.", "avg_attendance": "120", "first_timers": "7", "new_members": "abc", "finance_note": "Remitted", "requests_to_hq": "Send hymn books"}, follow_redirects=False)
    row = Q("select id, status, metrics, submitted_at from branch_reports")
    brid = row[0][0]
    rec("saving stores a DRAFT with cleaned numbers", len(row) == 1 and row[0][1] == "draft" and json.loads(row[0][2]) == {"avg_attendance": 120, "first_timers": 7, "new_members": None, "converts": None, "baptised": None}, str(row))
    hq_list = S.get("/admin/branch-reports").text
    rec("HQ cannot see a branch's draft (list, tracker or direct link)", f"/admin/branch-reports/{brid}" not in hq_list and not ok_get(S, f"/admin/branch-reports/{brid}") and not ok_get(PAST, f"/admin/branch-reports/{brid}"))
    rec("another branch cannot open it", not ok_get(B, f"/admin/branch-reports/{brid}") and not ok_get(B, f"/admin/branch-reports/{brid}/edit"))
    r = A.post("/admin/branch-reports", data={"csrf_token": T(A), "report_month": month, "summary": "duplicate"}, follow_redirects=False)
    rec("one report per branch per month (duplicate goes to the existing one)", Q("select count(*) from branch_reports")[0][0] == 1 and "/edit" in r.headers.get("location", ""))
    A.post(f"/admin/branch-reports/{brid}/update", data={"csrf_token": T(A), "summary": "A good month at Branch A. Updated.", "avg_attendance": "125", "requests_to_hq": "Send hymn books", "submit_now": "1"})
    row = Q("select status, rollup from branch_reports where id=:i", i=brid)[0]
    rec("submitting sends it to HQ with a department roll-up snapshot (counts only)", row[0] == "submitted" and set(json.loads(row[1]).keys()) == {"expected", "submitted"}, str(row))
    hq_view = S.get(f"/admin/branch-reports/{brid}")
    rec("HQ Administrator and HQ Pastoral can read the submitted report", hq_view.status_code == 200 and "Send hymn books" in hq_view.text and ok_get(PAST, f"/admin/branch-reports/{brid}"),
        (hq_view.status_code, hq_view.headers.get("location"), "Send hymn books" in hq_view.text))
    rec("the HQ view does not expose any department report's content", "SECRET-A" not in hq_view.text and "Ushers" not in hq_view.text)
    tracker = S.get("/admin/branch-reports").text
    rec("HQ tracker shows who has and has not reported this month", "Branch A" in tracker and "Read it" in tracker and "Not yet" in tracker,
        ("Branch A" in tracker, "Read it" in tracker, "Not yet" in tracker))
    S.post(f"/admin/branch-reports/{brid}/comment", data={"csrf_token": T(S), "hq_comment": "Thank you. Hymn books on the way."})
    row = Q("select status, hq_comment from branch_reports where id=:i", i=brid)[0]
    rec("HQ acknowledges with a comment", row == ("acknowledged", "Thank you. Hymn books on the way."))
    rec("the branch sees HQ's response, and can no longer edit an acknowledged report", "Hymn books on the way" in A.get(f"/admin/branch-reports/{brid}").text and not ok_get(A, f"/admin/branch-reports/{brid}/edit"))
    A.post(f"/admin/branch-reports/{brid}/update", data={"csrf_token": T(A), "summary": "TAMPERED"})
    rec("editing after acknowledgement is refused", "TAMPERED" not in Q("select summary from branch_reports where id=:i", i=brid)[0][0])
    A.post(f"/admin/branch-reports/{brid}/comment", data={"csrf_token": T(A), "hq_comment": "self-approve"})
    B.post(f"/admin/branch-reports/{brid}/comment", data={"csrf_token": T(B), "hq_comment": "hi"})
    rec("a branch admin cannot write HQ's comment", Q("select hq_comment from branch_reports where id=:i", i=brid)[0][0] == "Thank you. Hymn books on the way.")
    rec("branch B sees none of A's reports; A's list has only its own", "Branch A" not in B.get("/admin/branch-reports").text.replace("Reports to HQ", "") or f"/admin/branch-reports/{brid}" not in B.get("/admin/branch-reports").text)
    rec("branch page for HQ lists the branch report", "Reports to HQ" in S.get(f"/admin/branches/{A_id}").text and str(brid) in S.get(f"/admin/branches/{A_id}").text)

    # ── 13. Ministers, Resident Pastor and the review gate ──
    def mid_of(name): return Q("select id from ministers where full_name=:n", n=name)[0][0]
    dep_id = Q("select id from departments limit 1")[0][0]
    rec("Ministers page: HQ admin, branch admin, HQ pastoral open it; media/finance/HOD do not",
        all(ok_get(x, "/admin/ministers") for x in (S, A, PAST)) and not any(ok_get(x, "/admin/ministers") for x in (MEDIA, FIN, HOD)))
    rec("Ministers form: HQ admin, branch admin and HQ pastoral (for HQ) open it; media/finance/HOD do not",
        ok_get(S, "/admin/ministers/create") and ok_get(A, "/admin/ministers/create") and ok_get(PAST, "/admin/ministers/create") and not any(ok_get(x, "/admin/ministers/create") for x in (MEDIA, FIN, HOD)),
        [(n, x.get("/admin/ministers/create", follow_redirects=False).status_code) for n, x in (("S", S), ("A", A), ("P", PAST))])
    f = A.get("/admin/ministers/create").text
    A.post("/admin/ministers", data={"csrf_token": tok(f), "full_name": "Pastor Ada A", "title": "Pastor", "phone": "0801", "email": "ada@x.org", "gender": "female", "status": "active", "branch_id": str(B_id)}, follow_redirects=False)
    r = Q("select branch_id from ministers where full_name='Pastor Ada A'")
    rec("a branch admin records a minister for their OWN branch (a forged branch_id is ignored)", r and r[0][0] == A_id, str(r))
    A.post("/admin/ministers", data={"csrf_token": T(A), "full_name": ""}, follow_redirects=False)
    rec("a minister needs a name", Q("select count(*) from ministers")[0][0] == 1)
    S.post("/admin/ministers", data={"csrf_token": T(S), "full_name": "Pastor Bola B", "email": "bola@x.org", "branch_id": str(B_id), "status": "active"}, follow_redirects=False)
    S.post("/admin/ministers", data={"csrf_token": T(S), "full_name": "Pastor Hq", "email": "hqp@x.org", "branch_id": str(hq_id), "status": "active"}, follow_redirects=False)
    rec("the HQ Administrator records ministers for any branch and for HQ",
        Q("select branch_id from ministers where full_name='Pastor Bola B'")[0][0] == B_id and Q("select branch_id from ministers where full_name='Pastor Hq'")[0][0] == hq_id)
    ada, bola, hqp = mid_of("Pastor Ada A"), mid_of("Pastor Bola B"), mid_of("Pastor Hq")
    lst = A.get("/admin/ministers").text
    rec("a branch admin's list holds only their branch's ministers", "Pastor Ada A" in lst and "Pastor Bola B" not in lst and "Pastor Hq" not in lst)
    slst = S.get("/admin/ministers").text
    rec("the HQ Administrator's list holds everyone", all(n in slst for n in ("Pastor Ada A", "Pastor Bola B", "Pastor Hq")))
    plst = PAST.get("/admin/ministers").text
    rec("HQ pastoral sees HQ's ministers only", "Pastor Hq" in plst and "Pastor Ada A" not in plst)
    rec("a branch admin cannot open or edit another branch's minister", not ok_get(A, f"/admin/ministers/{bola}") and not ok_get(A, f"/admin/ministers/{bola}/edit") and ok_get(A, f"/admin/ministers/{ada}") and ok_get(A, f"/admin/ministers/{ada}/edit"))
    A.post(f"/admin/ministers/{bola}/update", data={"csrf_token": T(A), "full_name": "HACKED", "status": "active"})
    A.post(f"/admin/ministers/{bola}/delete", data={"csrf_token": T(A)})
    rec("…nor update or delete it by direct POST", Q("select full_name from ministers where id=:i", i=bola) == [("Pastor Bola B",)])
    A.post(f"/admin/ministers/{ada}/update", data={"csrf_token": T(A), "full_name": "Pastor Ada A", "email": "ada@x.org", "ministry_rank": "Associate Pastor", "status": "on_leave"})
    rec("editing bio-data works", Q("select ministry_rank, status from ministers where id=:i", i=ada) == [("Associate Pastor", "on_leave")])
    A.post(f"/admin/ministers/{ada}/update", data={"csrf_token": T(A), "full_name": "Pastor Ada A", "email": "ada@x.org", "ministry_rank": "Associate Pastor", "status": "active"})

    # HOD appointment
    A.post(f"/admin/ministers/{ada}/make-hod", data={"csrf_token": T(A), "department_id": str(dep_id)}, follow_redirects=False)
    acct = Q("select role, branch_id, department_id, is_active from admins where email='ada@x.org'")
    rec("a branch admin makes a minister a HOD: login created as hod in that branch/department", acct and tuple(acct[0]) == ("hod", A_id, dep_id, 1), str(acct))
    rec("…and the minister is linked to the login", Q("select admin_id from ministers where id=:i", i=ada)[0][0] is not None)
    A.post(f"/admin/ministers/{bola}/make-hod", data={"csrf_token": T(A), "department_id": str(dep_id)})
    rec("a branch admin cannot appoint another branch's minister", Q("select count(*) from admins where email='bola@x.org'")[0][0] == 0)
    S.post(f"/admin/ministers/{bola}/make-hod", data={"csrf_token": T(S), "department_id": str(dep_id)})
    rec("the HQ Administrator can make any branch's minister a HOD", [tuple(x) for x in Q("select role, branch_id from admins where email='bola@x.org'")] == [("hod", B_id)])
    A.post("/admin/ministers", data={"csrf_token": T(A), "full_name": "Pastor Dup", "email": "hoda@x.org", "status": "active"})
    dup = mid_of("Pastor Dup")
    A.post(f"/admin/ministers/{dup}/make-hod", data={"csrf_token": T(A), "department_id": str(dep_id)})
    rec("an email that already has a login is refused", Q("select count(*) from admins where email='hoda@x.org'")[0][0] == 1 and Q("select admin_id from ministers where id=:i", i=dup)[0][0] is None)
    A.post(f"/admin/ministers/{dup}/delete", data={"csrf_token": T(A)})
    rec("a minister without a login can be deleted", Q("select count(*) from ministers where id=:i", i=dup)[0][0] == 0)
    A.post(f"/admin/ministers/{ada}/delete", data={"csrf_token": T(A)})
    rec("a minister who holds a login cannot be deleted", Q("select count(*) from ministers where id=:i", i=ada)[0][0] == 1)

    # Resident Pastor: only the HQ Administrator appoints
    A.post(f"/admin/ministers/{ada}/make-resident-pastor", data={"csrf_token": T(A)})
    PAST.post(f"/admin/ministers/{hqp}/make-resident-pastor", data={"csrf_token": T(PAST), "email": "x@x.org"})
    rec("a branch admin / HQ pastoral cannot appoint a Resident Pastor", Q("select count(*) from admins where role='resident_pastor'")[0][0] == 0)
    rec("the appointment button appears for the HQ Administrator only", "Appoint Resident Pastor" in S.get(f"/admin/ministers/{bola}").text and "Appoint Resident Pastor" not in A.get(f"/admin/ministers/{ada}").text)

    hoda_id = Q("select id from admins where email='hoda@x.org'")[0][0]
    now = datetime.utcnow()
    def mk_report(hod_email, branch, status="submitted", txt="NEW-REPORT", month="2026-06"):
        Q("insert into unit_reports (branch_id, department_id, hod_admin_id, report_month, introduction, status, submitted_at, created_at, updated_at) values (:b,:d,(select id from admins where email=:e),:m,:x,:s,:t,:t,:t)",
          b=branch, d=dep_id, e=hod_email, m=month, x=txt, s=status, t=datetime.utcnow())
        return Q("select max(id) from unit_reports where introduction=:x", x=txt)[0][0]
    old_id = mk_report("hoda@x.org", A_id, txt="OLD-REPORT", month="2026-05")          # exists before any Resident Pastor
    Q("insert into admins (full_name, email, password_hash, role, is_active, branch_id, department_id, created_at, updated_at) select 'HOD A2','hoda2@x.org', password_hash, 'hod', 1, branch_id, department_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP from admins where email='hoda@x.org'")
    import time; time.sleep(1.1)                                                        # the appointment lands strictly after OLD-REPORT
    S.post(f"/admin/hods/{hoda_id}/make-resident-pastor", data={"csrf_token": T(S)})
    rec("HQ Administrator promotes a HOD to Resident Pastor from the HODs page (minister record created)",
        [tuple(x) for x in Q("select role from admins where email='hoda@x.org'")] == [("resident_pastor",)] and Q("select count(*) from ministers where admin_id=:i", i=hoda_id)[0][0] == 1)
    rec("the Resident Pastor role cannot be chosen on the manual admin form", 'value="resident_pastor"' not in S.get("/admin/admins/create").text)
    RPA = login("hoda@x.org")
    rec("Resident Pastor reaches reports, dashboard, profile and ministers (view)", all(ok_get(RPA, p) for p in ("/admin/unit-reports", "/admin/ministers", "/admin/dashboard", "/admin/profile")))
    rec("…and nothing financial, HQ-wide or HOD-only",
        not any(ok_get(RPA, p) for p in ("/admin/donations", "/admin/members", "/admin/settings", "/admin/ministers/create", "/admin/hod/reports", "/admin/branches", "/admin/messages")))
    rec("a report submitted BEFORE the appointment is not held back from the office", ok_get(A, f"/admin/unit-reports/{old_id}"))
    S.post(f"/admin/ministers/{ada}/make-resident-pastor", data={"csrf_token": T(S)})
    rec("one Resident Pastor per church: a second appointment in Branch A is refused",
        Q("select count(*) from admins where role='resident_pastor' and branch_id=:b", b=A_id)[0][0] == 1 and Q("select role from admins where email='ada@x.org'")[0][0] == "hod")
    S.post(f"/admin/ministers/{bola}/make-resident-pastor", data={"csrf_token": T(S)})
    rec("a HOD-minister of Branch B becomes B's Resident Pastor", Q("select role from admins where email='bola@x.org'")[0][0] == "resident_pastor")
    S.post(f"/admin/ministers/{bola}/remove-resident-pastor", data={"csrf_token": T(S)})
    rec("removing an RP who was a HOD returns them to HOD", Q("select role from admins where email='bola@x.org'")[0][0] == "hod")
    A.post(f"/admin/ministers/{bola}/remove-resident-pastor", data={"csrf_token": T(A)})

    rp_id = hoda_id
    new_id = mk_report("hoda2@x.org", A_id)
    draft_id = mk_report("hoda2@x.org", A_id, "draft", "DRAFT-REPORT", month="2026-07")
    rec("a NEW report is hidden from the branch admin until the Resident Pastor has commented",
        not ok_get(A, f"/admin/unit-reports/{new_id}") and f"/admin/unit-reports/{new_id}" not in A.get("/admin/unit-reports").text)
    rec("…and from the HQ Administrator (HQ never reads a branch's department reports)", not ok_get(S, f"/admin/unit-reports/{new_id}"))
    rp_page = RPA.get(f"/admin/unit-reports/{new_id}")
    rec("the Resident Pastor sees it at once, with the comment box and no office box", rp_page.status_code == 200 and 'name="resident_pastor_comment"' in rp_page.text and 'name="senior_pastor_comment"' not in rp_page.text)
    rec("the Resident Pastor's list carries it", f"/admin/unit-reports/{new_id}" in RPA.get("/admin/unit-reports").text)
    rec("nobody sees a HOD's draft, not even the Resident Pastor", not ok_get(RPA, f"/admin/unit-reports/{draft_id}") and not ok_get(A, f"/admin/unit-reports/{draft_id}"))
    A.post(f"/admin/unit-reports/{new_id}/update", data={"csrf_token": T(A), "senior_pastor_comment": "office jumps the queue", "status": "reviewed"})
    rec("the branch admin cannot comment before the Resident Pastor", [tuple(x) for x in Q("select senior_pastor_comment, status from unit_reports where id=:i", i=new_id)] == [(None, "submitted")])
    RPA.post(f"/admin/unit-reports/{new_id}/update", data={"csrf_token": T(RPA), "resident_pastor_comment": "   "})
    rec("the Resident Pastor must write a comment (blank refused, status unchanged)", [tuple(x) for x in Q("select resident_pastor_comment, status from unit_reports where id=:i", i=new_id)] == [(None, "submitted")])
    RPA.post(f"/admin/unit-reports/{new_id}/update", data={"csrf_token": T(RPA), "resident_pastor_comment": "Well done. Watch the ushers rota.", "senior_pastor_comment": "RP writing the office comment", "status": "submitted"})
    row = tuple(Q("select resident_pastor_comment, senior_pastor_comment, status, resident_reviewed_by from unit_reports where id=:i", i=new_id)[0])
    rec("the RP's comment is appended, status becomes 'reviewed', reviewer recorded; the RP cannot write the office comment",
        row == ("Well done. Watch the ushers rota.", None, "reviewed", rp_id), str(row))
    rec("the log records the Resident Pastor's review", Q("select count(*) from admin_audit_logs where action='unit_report.resident_reviewed'")[0][0] >= 1)
    rec("the branch admin can now open it and sees the Resident Pastor's comment", ok_get(A, f"/admin/unit-reports/{new_id}") and "Watch the ushers rota." in A.get(f"/admin/unit-reports/{new_id}").text)
    rec("HQ Administrator still cannot read the branch's department report", not ok_get(S, f"/admin/unit-reports/{new_id}"))
    A.post(f"/admin/unit-reports/{new_id}/update", data={"csrf_token": T(A), "senior_pastor_comment": "Noted. Approved.", "resident_pastor_comment": "EDITED BY OFFICE"})
    row = tuple(Q("select resident_pastor_comment, senior_pastor_comment from unit_reports where id=:i", i=new_id)[0])
    rec("the office adds ITS comment but cannot rewrite the Resident Pastor's", row == ("Well done. Watch the ushers rota.", "Noted. Approved."), str(row))
    b_rep = mk_report("bola@x.org", B_id, txt="B-REPORT", month="2026-08")
    rec("a Resident Pastor cannot reach another church's reports (and B's office reads its report directly)", not ok_get(RPA, f"/admin/unit-reports/{b_rep}") and ok_get(B, f"/admin/unit-reports/{b_rep}"))
    B.post(f"/admin/unit-reports/{b_rep}/update", data={"csrf_token": T(B), "senior_pastor_comment": "fine", "resident_pastor_comment": "fine too", "status": "reviewed"})
    rec("a church with no Resident Pastor keeps the old flow (office sets both comments and status)", [tuple(x) for x in Q("select senior_pastor_comment, resident_pastor_comment, status from unit_reports where id=:i", i=b_rep)] == [("fine", "fine too", "reviewed")])

    # HQ church with its own Resident Pastor
    Q("insert into admins (full_name, email, password_hash, role, is_active, branch_id, department_id, created_at, updated_at) select 'HQ HOD','hqhod@x.org', password_hash, 'hod', 1, :b, department_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP from admins where email='hoda2@x.org'", b=hq_id)
    S.post(f"/admin/ministers/{hqp}/make-resident-pastor", data={"csrf_token": T(S)}, follow_redirects=False)
    hqrp = Q("select role, branch_id from admins where email='hqp@x.org'")
    rec("the HQ Administrator can appoint a Resident Pastor for HQ itself", hqrp and tuple(hqrp[0]) == ("resident_pastor", hq_id), str(hqrp))
    hq_rep = mk_report("hqhod@x.org", hq_id, txt="HQ-DEPT-REPORT", month="2026-10")
    rec("HQ's own department report is held from the HQ Administrator until the HQ Resident Pastor comments", not ok_get(S, f"/admin/unit-reports/{hq_rep}"))
    S.post(f"/admin/ministers/{hqp}/remove-resident-pastor", data={"csrf_token": T(S)})
    rec("removing the Resident Pastor releases held reports to the office", ok_get(S, f"/admin/unit-reports/{hq_rep}") and not Q("select is_active from admins where email='hqp@x.org'")[0][0])
    rec("the branch page shows the Resident Pastor card", "Resident Pastor" in S.get(f"/admin/branches/{A_id}").text)
    rec("every ministers page renders (list, form, profile, edit)", all(ok_get(S, p) for p in ("/admin/ministers", "/admin/ministers/create", f"/admin/ministers/{ada}", f"/admin/ministers/{ada}/edit")))


    # ── 14. Account control, announcements, activity, HQ events, dashboard scope ──
    def login_as(email, password):
        throttle.reset_all()
        cl = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
        t = tok(cl.get("/admin/").text)
        r = cl.post("/admin/login", data={"email": email, "password": password, "csrf_token": t}, follow_redirects=False)
        return cl, r
    def alive(cl): return cl.get("/admin/dashboard", follow_redirects=False).status_code == 200
    def mk_admin(email, role, branch, dept=None):
        Q("insert into admins (full_name, email, password_hash, role, is_active, branch_id, department_id, created_at, updated_at) select :n, :e, password_hash, :r, 1, :b, :d, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP from admins where email='super@x.org'",
          n=email.split("@")[0], e=email, r=role, b=branch, d=dept)
        return Q("select id from admins where email=:e", e=email)[0][0]

    # a) a branch admin cannot manage anyone but real HODs (no takeover of a peer, the Resident Pastor or HQ)
    peer = mk_admin("peer@x.org", "branch_admin", A_id)
    sup_id = Q("select id from admins where email='super@x.org'")[0][0]
    for tid in (peer, sup_id, Q("select id from admins where email='hoda@x.org'")[0][0]):
        A.post(f"/admin/hods/{tid}/update", data={"csrf_token": T(A), "name": "X", "password": "Hijack#1234", "password_confirmation": "Hijack#1234"})
    rec("a branch admin cannot reset the password of a peer admin, the Resident Pastor or the HQ Administrator via the HOD form",
        not any(login_as(e, "Hijack#1234")[1].headers.get("location", "").endswith("/dashboard") for e in ("peer@x.org", "super@x.org", "hoda@x.org")) and login_as("peer@x.org", PW)[1].status_code == 303)
    rec("…and their edit pages are closed too", not ok_get(A, f"/admin/hods/{peer}/edit") and not ok_get(A, f"/admin/hods/{sup_id}/edit"))
    rec("HQ pastoral cannot open the HOD tools at all", not ok_get(PAST, "/admin/hods") and PAST.post(f"/admin/hods/{sup_id}/update", data={"csrf_token": T(PAST), "password": "Hijack#1234", "password_confirmation": "Hijack#1234"}, follow_redirects=False).status_code in (303, 302, 403))

    # b) switching an account off, or resetting its password, ends its open sessions
    kill = mk_admin("kill@x.org", "branch_admin", A_id)
    KC, _ = login_as("kill@x.org", PW)
    rec("a signed-in branch admin works", alive(KC))
    S.post(f"/admin/admins/{kill}/update", data={"csrf_token": T(S), "name": "kill", "role": "branch_admin", "branch_id": str(A_id), "is_active": "false"})
    rec("deactivating the account ends its open session at once", not alive(KC))
    rec("…and it cannot sign in again", not login_as("kill@x.org", PW)[1].headers.get("location", "").endswith("/dashboard"))
    S.post(f"/admin/admins/{kill}/update", data={"csrf_token": T(S), "name": "kill", "role": "branch_admin", "branch_id": str(A_id), "is_active": "true"})
    KC, _ = login_as("kill@x.org", PW)
    S.post(f"/admin/admins/{kill}/reset-password", data={"csrf_token": T(S), "password": "Temp#123456", "password_confirmation": "Temp#123456"})
    rec("an HQ password reset signs the person out everywhere", not alive(KC))
    KC, r = login_as("kill@x.org", "Temp#123456")
    rec("the reset password works, but the console demands a new one first",
        KC.get("/admin/dashboard", follow_redirects=False).headers.get("location", "").endswith("/admin/profile") and ok_get(KC, "/admin/profile") and not ok_get(KC, "/admin/members"))
    KC.post("/admin/profile/password", data={"csrf_token": T(KC), "current_password": "Temp#123456", "password": "Temp#123456", "password_confirmation": "Temp#123456"})
    rec("the new password must differ from the temporary one", not alive(KC))
    KC.post("/admin/profile/password", data={"csrf_token": T(KC), "current_password": "Temp#123456", "password": "Mine#987654", "password_confirmation": "Mine#987654"})
    rec("after choosing their own the console opens; the session survives its own change", alive(KC))
    KC2, _ = login_as("kill@x.org", "Mine#987654")
    KC2.post("/admin/profile/password", data={"csrf_token": T(KC2), "current_password": "Mine#987654", "password": "Other#987654", "password_confirmation": "Other#987654"})
    rec("changing a password ends the person's OTHER sessions", not alive(KC) and alive(KC2))
    hod_lo = mk_admin("hodx@x.org", "hod", A_id, dep_id)
    A.post(f"/admin/hods/{hod_lo}/update", data={"csrf_token": T(A), "name": "hodx", "password": "Hodx#123456", "password_confirmation": "Hodx#123456", "is_active": "true"})
    HC, _ = login_as("hodx@x.org", "Hodx#123456")
    rec("a password set by a branch admin for a HOD is temporary too", HC.get("/admin/hod/reports", follow_redirects=False).headers.get("location", "").endswith("/admin/profile"))
    rec("a minister's login starts with a temporary password that must be replaced", Q("select must_change_password from admins where email='ada@x.org'")[0][0] in (1, True))

    # c) a closed branch's people cannot use the console; reopening restores it
    BC, _ = login_as("b@x.org", PW)
    rec("branch B admin works", alive(BC))
    S.post(f"/admin/branches/{B_id}/active", data={"csrf_token": T(S)})
    rec("closing branch B ends its people's sessions and blocks new sign-ins", not alive(BC) and not alive(login_as("b@x.org", PW)[0]))
    S.post(f"/admin/branches/{B_id}/active", data={"csrf_token": T(S)})
    BC, _ = login_as("b@x.org", PW)
    rec("reopening branch B restores access", alive(BC))
    B = BC

    # d) HOD invitation guards
    A.post("/admin/hods/invite", data={"csrf_token": T(A), "name": "Dup", "email": "hoda2@x.org", "department_id": str(dep_id), "branch_id": str(A_id)})
    rec("an invitation for an email that already has a login is refused", Q("select count(*) from hod_invitations where email='hoda2@x.org'")[0][0] == 0)

    # e) announcements: HQ writes, branches read
    rec("only the HQ Administrator can write announcements", ok_get(S, "/admin/announcements/create") and not any(ok_get(x, "/admin/announcements/create") for x in (A, PAST, MEDIA, FIN, HOD, RPA)))
    rec("branch admins, HQ staff and Resident Pastors can read them; a HOD cannot", all(ok_get(x, "/admin/announcements") for x in (A, B, S, PAST, MEDIA, FIN, RPA)) and not ok_get(HOD, "/admin/announcements"))
    S.post("/admin/announcements", data={"csrf_token": T(S), "title": "Only for A", "body": "Bring the annual forms.", "audience": "selected", "branch_ids": str(A_id)})
    S.post("/admin/announcements", data={"csrf_token": T(S), "title": "For everyone", "body": "Conference in May.", "audience": "all", "is_pinned": "1"})
    S.post("/admin/announcements", data={"csrf_token": T(S), "title": "Broken", "body": "x", "audience": "selected"})
    rec("a selected-audience announcement needs at least one branch", Q("select count(*) from announcements where title='Broken'")[0][0] == 0)
    a_page, b_page = A.get("/admin/announcements").text, B.get("/admin/announcements").text
    rec("branch A reads both; branch B reads only the one for all", "Only for A" in a_page and "For everyone" in a_page and "For everyone" in b_page and "Only for A" not in b_page)
    rec("the dashboard shows HQ notices to the branch", "Bring the annual forms." in A.get("/admin/dashboard").text and "Bring the annual forms." not in B.get("/admin/dashboard").text)
    ann = Q("select id from announcements where title='Only for A'")[0][0]
    A.post(f"/admin/announcements/{ann}/update", data={"csrf_token": T(A), "title": "HACK", "body": "x", "audience": "all"})
    A.post(f"/admin/announcements/{ann}/delete", data={"csrf_token": T(A)})
    rec("a branch admin cannot edit or delete an announcement", Q("select title from announcements where id=:i", i=ann)[0][0] == "Only for A")
    S.post(f"/admin/announcements/{ann}/update", data={"csrf_token": T(S), "title": "Only for A", "body": "Bring the forms.", "audience": "selected", "branch_ids": str(A_id), "hidden": "1"})
    rec("a hidden announcement leaves the branch's view but stays with HQ", "Only for A" not in A.get("/admin/announcements").text and "Only for A" in S.get("/admin/announcements").text)
    S.post(f"/admin/announcements/{ann}/update", data={"csrf_token": T(S), "title": "Only for A", "body": "Bring the forms.", "audience": "selected", "branch_ids": str(A_id), "expires_at": "2000-01-01"})
    rec("an expired announcement disappears from the branch", "Only for A" not in A.get("/admin/announcements").text)
    S.post(f"/admin/announcements/{ann}/delete", data={"csrf_token": T(S)})
    rec("HQ can delete one", Q("select count(*) from announcements where id=:i", i=ann)[0][0] == 0)

    # f) branch activity trail
    rec("a branch admin can open their Activity page; HOD/media/finance cannot", ok_get(A, "/admin/activity") and not any(ok_get(x, "/admin/activity") for x in (HOD, MEDIA, FIN, PAST, RPA)))
    act_a = A.get("/admin/activity").text
    rec("it lists their own branch's people and no other branch's", "a@x.org" in act_a or "Admin A" in act_a and "Admin B" not in act_a and "HQ Super" not in act_a)

    # g) HQ programmes are visible (read-only) to branches
    for title, br in (("HQ Convention", hq_id), ("A Youth Night", A_id), ("B Youth Night", B_id)):
        Q("insert into events (title, starts_at, is_published, is_major, branch_id, created_at, updated_at) values (:t, :s, 1, 0, :b, :n, :n)", t=title, s=datetime.utcnow() + timedelta(days=3), b=br, n=datetime.utcnow())
    cal = A.get("/admin/events").text
    rec("branch A's calendar shows its own events and HQ's, never branch B's", "A Youth Night" in cal and "HQ Convention" in cal and "B Youth Night" not in cal)
    hq_ev = Q("select id from events where title='HQ Convention'")[0][0]
    rec("…but a branch cannot edit or delete an HQ event", not ok_get(A, f"/admin/events/{hq_ev}/edit"))
    A.post(f"/admin/events/{hq_ev}/destroy", data={"csrf_token": T(A)})
    A.post(f"/admin/events/{hq_ev}/delete", data={"csrf_token": T(A)})
    rec("…nor by direct POST", Q("select count(*) from events where id=:i", i=hq_ev)[0][0] == 1)
    rec("the dashboard's upcoming list follows the same rule", "HQ Convention" in A.get("/admin/dashboard").text and "B Youth Night" not in A.get("/admin/dashboard").text)

    # h) dashboard data stays inside the branch
    _m = A.get("/admin/metrics?mode=all", headers={"accept": "application/json"})
    ma = _m.json()
    ms = S.get("/admin/metrics?mode=all").json()
    rec("a branch admin's dashboard data names only their own branch; HQ's names them all", ma.get("branches") == ["Branch A"] and len(ms.get("branches", [])) >= 3, (ma.get("branches"), ms.get("branches")))
    rec("attendance figures are real: Branch A's submitted report (125) appears, nothing is invented",
        any(pt["branches"].get("Branch A") == 125 for pt in ma["attendanceTrend"]) and not ma["weeklyAttendance"], str(ma["attendanceTrend"][-2:]))

print(f"\n==== {len(res)} checks, {res.count(False)} failed ====")
sys.exit(1 if False in res else 0)
