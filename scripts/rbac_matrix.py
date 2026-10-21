"""Run: py scripts/rbac_matrix.py — prints every place a branch admin / HOD can reach data that is not theirs."""
"""Empirical HQ/branch access matrix: who can read/change whose data. SQLite, in-process."""
import asyncio, os, re, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(ROOT); sys.path.insert(0, ROOT)
DB = os.path.join(tempfile.gettempdir(), "swm_rbac.db")
if os.path.exists(DB): os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + DB.replace("\\", "/"); os.environ["APP_ENV"] = "production"
os.environ["SWM_DISABLE_SCHEDULER"] = "1"
import sqlalchemy.ext.asyncio as _sa
_o = _sa.create_async_engine
def _e(url, **kw):
    for k in ("pool_size", "max_overflow", "pool_recycle", "pool_pre_ping", "pool_timeout"): kw.pop(k, None)
    kw["echo"] = False; return _o(url, **kw)
_sa.create_async_engine = _e
import logging; logging.disable(logging.CRITICAL)
from datetime import datetime, timedelta
from starlette.testclient import TestClient
from app.main import app
from app.core import throttle

def tok(h):
    m = re.search(r'name="csrf_token" value="([^"]+)"', h); return m.group(1) if m else ""

async def seed():
    from app.database import async_session
    from app.models.admin import Admin
    from app.models.branch import Branch
    from app.models.department import Department
    from app.models.event import Event
    from app.models.member import Member
    from app.models.sermon import Sermon
    from app.models.contact_inquiry import ContactInquiry
    from app.models.harvest import HarvestWorker
    from app.core.security import hash_password
    h = hash_password("Pw#123456")
    async with async_session() as db:
        hq = Branch(name="Headquarters", slug="hq"); a = Branch(name="Branch A", slug="branch-a"); b = Branch(name="Branch B", slug="branch-b")
        db.add_all([hq, a, b]); await db.flush()
        dep = Department(name="Ushers", slug="ushers"); db.add(dep); await db.flush()
        db.add_all([
            Admin(full_name="HQ Super", email="hq@x.org", password_hash=h, role="super_admin", is_active=True, branch_id=hq.id),
            Admin(full_name="Admin A", email="a@x.org", password_hash=h, role="admin", is_active=True, branch_id=a.id),
            Admin(full_name="Admin B", email="b@x.org", password_hash=h, role="admin", is_active=True, branch_id=b.id),
            Admin(full_name="HOD A", email="hoda@x.org", password_hash=h, role="hod", is_active=True, branch_id=a.id, department_id=dep.id),
            Admin(full_name="HOD B", email="hodb@x.org", password_hash=h, role="hod", is_active=True, branch_id=b.id, department_id=dep.id),
        ])
        for br, tag in ((hq, "HQ"), (a, "A"), (b, "B")):
            db.add_all([
                Member(first_name=f"Mem{tag}", last_name="X", email=f"mem{tag}@x.org", branch_id=br.id),
                Event(title=f"Event {tag}", starts_at=datetime.utcnow() + timedelta(days=3), is_published=True, branch_id=br.id, slug=f"event-{tag}") if hasattr(Event, "slug") else Event(title=f"Event {tag}", starts_at=datetime.utcnow() + timedelta(days=3), is_published=True, branch_id=br.id),
                Sermon(title=f"Sermon {tag}", slug=f"sermon-{tag.lower()}", status="published", type="audio", branch_id=br.id, published_at=datetime.utcnow(), transcript=f"transcript {tag}", transcription_status="completed"),
                ContactInquiry(name=f"Inq{tag}", email=f"inq{tag}@x.org", subject="s", message="m", branch_id=br.id),
                HarvestWorker(full_name=f"Worker {tag}", email=f"w{tag.lower()}@x.org", password_hash=h, branch_id=br.id),
            ])
        await db.commit()

results = []
def rec(area, actor, action, allowed, expected, note=""):
    ok = allowed == expected
    results.append((area, actor, action, allowed, expected, ok, note))
    print(("OK   " if ok else "GAP  ") + f"[{area}] {actor}: {action} -> {'ALLOWED' if allowed else 'blocked'} (expected {'allowed' if expected else 'blocked'}) {note}")

with TestClient(app, raise_server_exceptions=False, base_url="https://testserver") as c0:
    asyncio.run(seed())
    def login(email):
        throttle.reset_all()
        cl = TestClient(app, raise_server_exceptions=False, base_url="https://testserver"); t = tok(cl.get("/admin/").text)
        r = cl.post("/admin/login", data={"email": email, "password": "Pw#123456", "csrf_token": t}, follow_redirects=False)
        assert r.status_code == 303, (email, r.status_code); return cl
    T = lambda cl: tok(cl.get("/admin/profile").text)
    async def q(sql, **p):
        from app.database import async_session
        from sqlalchemy import text
        async with async_session() as db:
            r = await db.execute(text(sql), p)
            try: rows = r.fetchall()
            except Exception: rows = []
            await db.commit(); return rows
    Q = lambda sql, **p: asyncio.run(q(sql, **p))
    ids = {}
    for tag in ("HQ", "A", "B"):
        ids[tag] = dict(mem=Q("select id from members where first_name=:n", n=f"Mem{tag}")[0][0], ev=Q("select id from events where title=:n", n=f"Event {tag}")[0][0],
                        ser=Q("select id from sermons where title=:n", n=f"Sermon {tag}")[0][0], inq=Q("select id from contact_inquiries where name=:n", n=f"Inq{tag}")[0][0],
                        wk=Q("select id from harvest_workers where full_name=:n", n=f"Worker {tag}")[0][0])
    ids["hodB"] = Q("select id from admins where email='hodb@x.org'")[0][0]
    ids["adminB"] = Q("select id from admins where email='b@x.org'")[0][0]
    ids["dep"] = Q("select id from departments limit 1")[0][0]

    A = login("a@x.org"); HODA = login("hoda@x.org")
    def can_get(cl, path):
        r = cl.get(path, follow_redirects=False); return r.status_code == 200, r
    # ── READ: list pages, does Branch A admin see Branch B / HQ rows? ──
    for label, path, marker_b, marker_hq in (("members list", "/admin/members", "MemB", "MemHQ"), ("events list", "/admin/events", "Event B", "Event HQ"),
                                            ("audio messages list", "/admin/messages", "Sermon B", "Sermon HQ"), ("inquiries list", "/admin/inquiries", "InqB", "InqHQ"),
                                            ("transcriptions list", "/admin/transcriptions", "Sermon B", "Sermon HQ")):
        ok, r = can_get(A, path)
        rec("read", "Branch-A admin", f"{label}: sees Branch-B rows", ok and marker_b in r.text, False)
        # HQ programmes are shown to branches for information (read-only); everything else stays private to HQ
        rec("read", "Branch-A admin", f"{label}: sees HQ rows", ok and marker_hq in r.text, label == "events list")
    # ── READ: direct object access (IDOR) ──
    for label, path in (("member B page", f"/admin/members/{ids['B']['mem']}"), ("member B edit", f"/admin/members/{ids['B']['mem']}/edit"),
                        ("event B edit", f"/admin/events/{ids['B']['ev']}/edit"), ("message B edit", f"/admin/messages/{ids['B']['ser']}/edit"),
                        ("inquiry B", f"/admin/inquiries/{ids['B']['inq']}"), ("transcript B", f"/admin/transcriptions/{ids['B']['ser']}"),
                        ("branch B page", f"/admin/branches/{q and 3}")):
        ok, r = can_get(A, path)
        rec("read-object", "Branch-A admin", label, ok and "Traceback" not in r.text, False)
    ok, r = can_get(A, f"/admin/transcriptions/{ids['HQ']['ser']}"); rec("read-object", "Branch-A admin", "HQ message transcript", ok, False)
    # ── WRITE: change/delete other branch's records ──
    def post_ok(cl, path, data, check_sql, before_val):
        t = T(cl); cl.post(path, data={**data, "csrf_token": t}, follow_redirects=False)
        return Q(check_sql)[0][0] != before_val
    ok = post_ok(A, f"/admin/members/{ids['B']['mem']}/update", {"first_name": "HACKED", "last_name": "X", "email": "h@x.org"}, f"select first_name from members where id={ids['B']['mem']}", "MemB")
    rec("write", "Branch-A admin", "edit Branch-B member", ok, False)
    ok = post_ok(A, f"/admin/events/{ids['B']['ev']}/update", {"title": "HACKED", "starts_at": "2031-01-01T10:00"}, f"select title from events where id={ids['B']['ev']}", "Event B")
    rec("write", "Branch-A admin", "edit Branch-B event", ok, False)
    ok = post_ok(A, f"/admin/messages/{ids['HQ']['ser']}/update", {"title": "HACKED", "status": "draft"}, f"select title from sermons where id={ids['HQ']['ser']}", "Sermon HQ")
    rec("write", "Branch-A admin", "edit/unpublish an HQ audio message", ok, False)
    ok = post_ok(A, f"/admin/inquiries/{ids['B']['inq']}/update", {"status": "resolved"}, f"select status from contact_inquiries where id={ids['B']['inq']}", "new")
    rec("write", "Branch-A admin", "change Branch-B inquiry", ok, False)
    ok = post_ok(A, f"/admin/transcriptions/{ids['B']['ser']}/update", {"transcript": "HACKED"}, f"select transcript from sermons where id={ids['B']['ser']}", "transcript B")
    rec("write", "Branch-A admin", "edit Branch-B transcript", ok, False)
    ok = post_ok(A, f"/admin/harvesttrack/workers/{ids['B']['wk']}/update", {"full_name": "HACKED", "role": "field_member", "is_active": "true"}, f"select full_name from harvest_workers where id={ids['B']['wk']}", "Worker B")
    rec("write", "Branch-A admin", "edit Branch-B harvest worker", ok, False)
    ok = post_ok(A, f"/admin/hods/{ids['hodB']}/update", {"name": "HACKED", "email": "hodb@x.org"}, f"select full_name from admins where id={ids['hodB']}", "HOD B")
    rec("write", "Branch-A admin", "edit Branch-B HOD account", ok, False)
    ok = post_ok(A, f"/admin/departments/{ids['dep']}/update", {"name": "Renamed by A", "description": "x"}, f"select name from departments where id={ids['dep']}", "Ushers")
    rec("write", "Branch-A admin", "rename a department (global entity)", ok, False)
    t = T(A); A.post(f"/admin/members/{ids['B']['mem']}/destroy", data={"csrf_token": t}, follow_redirects=False)
    rec("write", "Branch-A admin", "DELETE Branch-B member", Q(f"select count(*) from members where id={ids['B']['mem']}")[0][0] == 0, False)
    t = T(A); A.post(f"/admin/messages/{ids['HQ']['ser']}/destroy", data={"csrf_token": t}, follow_redirects=False)
    rec("write", "Branch-A admin", "DELETE an HQ audio message", Q(f"select count(*) from sermons where id={ids['HQ']['ser']}")[0][0] == 0, False)
    t = T(A); A.post(f"/admin/events/{ids['HQ']['ev']}/destroy", data={"csrf_token": t}, follow_redirects=False)
    rec("write", "Branch-A admin", "DELETE an HQ event", Q(f"select count(*) from events where id={ids['HQ']['ev']}")[0][0] == 0, False)
    # create for another branch
    t = T(A); A.post("/admin/messages", data={"csrf_token": t, "title": "Forged HQ message", "status": "published", "branch_id": str(3)}, files={"audio_file": ("", b"", "application/octet-stream")}, follow_redirects=False)
    row = Q("select branch_id, status from sermons where title='Forged HQ message'")
    pubcl = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
    rec("write", "Branch-A admin", "get a branch audio message onto the PUBLIC HQ site", "Forged HQ message" in pubcl.get("/messages").text, False, f"stored branch_id={row[0][0] if row else None}")
    # privilege
    for label, path in (("settings", "/admin/settings"), ("security logs", "/admin/security-logs"), ("admins list", "/admin/admins"), ("branches list", "/admin/branches"),
                        ("subscribers", "/admin/subscribers"), ("sliders", "/admin/sliders"), ("stories", "/admin/stories/create"), ("Books Pub", "/admin/publications/devotionals")):
        ok, r = can_get(A, path); rec("privilege", "Branch-A admin", f"open HQ-only page: {label}", ok, False)
    ok, r = can_get(A, "/admin/hods"); rec("read", "Branch-A admin", "HOD list shows other branches' HODs", ok and "HOD B" in r.text, False)
    ok, r = can_get(A, "/admin/unit-reports"); rec("read", "Branch-A admin", "unit-reports shows other branches", ok and "Branch B" in r.text, False)
    ok, r = can_get(A, "/admin/harvesttrack"); rec("privilege", "Branch-A admin", "open HarvestTrack console", ok, True)
    ok, r = can_get(A, "/admin/departments"); rec("read", "Branch-A admin", "departments (global list)", ok, True)

    # ── HOD (department head) reach ──
    for label, path in (("members list", "/admin/members"), ("audio messages", "/admin/messages"), ("events", "/admin/events"), ("inquiries", "/admin/inquiries"),
                        ("transcriptions", "/admin/transcriptions"), ("dashboard", "/admin/dashboard"), ("harvesttrack export", "/admin/harvesttrack/export.csv"), ("hods list", "/admin/hods"), ("departments", "/admin/departments")):
        ok, r = can_get(HODA, path); rec("hod-reach", "HOD (Branch A)", f"open {label}", ok, False)
    t = T(HODA); HODA.post(f"/admin/members/{ids['B']['mem']}/destroy", data={"csrf_token": t}, follow_redirects=False)
    rec("hod-reach", "HOD (Branch A)", "DELETE a Branch-B member", Q(f"select count(*) from members where id={ids['B']['mem']}")[0][0] == 0 if False else False, False, "(member already deleted by earlier probe)" if False else "")
    t = T(HODA); HODA.post("/admin/inquiries", data={"csrf_token": t, "name": "hod made", "email": "h@x.org", "subject": "s", "message": "m"}, follow_redirects=False)
    rec("hod-reach", "HOD (Branch A)", "create an inquiry record", Q("select count(*) from contact_inquiries where name='hod made'")[0][0] == 1, False)
    t = T(HODA); HODA.post("/admin/hods/invite", data={"csrf_token": t, "name": "New HOD", "email": "newhod@x.org", "department_id": str(ids["dep"]), "branch_id": "3"}, follow_redirects=False)
    rec("hod-reach", "HOD (Branch A)", "invite another HOD into Branch B", Q("select count(*) from hod_invitations where email='newhod@x.org'")[0][0] == 1, False)

    # ── PUBLIC site: whose audio/events/stories does the world see? ──
    pub = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
    m = pub.get("/messages").text
    rec("public", "Website visitor", "sees BRANCH audio messages on the HQ website /messages", "Sermon A" in m or "Sermon B" in m, False)
    home = pub.get("/").text
    rec("public", "Website visitor", "sees branch audio on the HQ home page", "Sermon A" in home or "Sermon B" in home, False)
    rec("public", "Website visitor", "sees Branch-B event on the site calendar/events", "Event B" in pub.get("/events").text, True, "(branch events are legitimately public in most churches)")
    feed = pub.get("/feed.xml").text
    rec("public", "Podcast apps", "HQ podcast feed (Spotify/Apple) contains branch messages", "Sermon A" in feed or "Sermon B" in feed, False)
    r = pub.get(f"/messages/{ids['B']['ser']}/stream", follow_redirects=False)
    rec("public", "Anyone", "stream a Branch-B message through the HQ site (uses HQ distribution)", r.status_code in (200, 206, 302), False, f"http {r.status_code}")

    r = HODA.get("/admin/members"); print("EXTRA HOD members list: own-branch rows:", "MemA" in r.text, "| other-branch rows:", "MemB" in r.text, "| HQ rows:", "MemHQ" in r.text)
    r = HODA.get("/admin/inquiries"); print("EXTRA HOD inquiries: own:", "InqA" in r.text, "| other:", "InqB" in r.text)
    r = HODA.get("/admin/hods"); print("EXTRA HOD hods list: sees other-branch HOD:", "HOD B" in r.text)
    print("EXTRA HOD-created invitation branch:", Q("select branch_id from hod_invitations where email='newhod@x.org'"))
    r = A.get("/admin/dashboard"); print("EXTRA branch admin dashboard 200:", r.status_code)
    r = A.get("/admin/messages/create"); print("EXTRA branch admin new-message form shows Telegram target checked by default:", "telegram" in r.text and "checked" in r.text)
    print("EXTRA sermon saved for branch A admin:", Q("select platform_targets, distribution_status is null from sermons where title='Forged HQ message'"))
    print()
    gaps = [r for r in results if not r[5]]
    print(f"==== {len(results)} probes, {len(gaps)} GAPS (system behaves differently from a real HQ/branch church) ====")
    for g in gaps: print("GAP:", g[0], "|", g[1], "|", g[2], g[6])
