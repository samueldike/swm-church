"""Reproduce the top audit findings against a LOCAL seeded server (audit/seed_local.py).

    py audit/verify_findings.py http://localhost:8010

Each check prints REPRODUCED (the defect is real) or NOT REPRODUCED. It writes to the local
seeded database only; never point it at production.
"""
import re
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8010"
assert "localhost" in BASE or "127.0.0.1" in BASE, "local servers only"
PW = "Launch-Audit-2026"


def login(email: str) -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=60)
    token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/admin/").text).group(1)
    r = c.post("/admin/login", data={"csrf_token": token, "email": email, "password": PW})
    assert r.status_code in (302, 303), (email, r.status_code)
    page = c.get("/admin/profile").text
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    return c, (m.group(1) if m else token)


def report(fid: str, reproduced: bool, detail: str) -> None:
    print(f"{fid:6} {'REPRODUCED' if reproduced else 'NOT REPRODUCED':15} {detail}")


# F-SEC-01: a department head deletes HQ's public event through /api/events
hod, _ = login("hod.choir@example.org")
events = httpx.get(BASE + "/api/events?per_page=50").json()
r = hod.delete("/api/events/5")
report("SEC-01", r.status_code == 200, f"HOD DELETE /api/events/5 -> {r.status_code} {r.text[:60]}")

# F-SEC-02: a branch admin uploads an HTML file as "audio"; it is served from the site's origin as HTML
ba, tok = login("abuja.admin@example.org")
r = ba.post("/admin/messages", data={"csrf_token": tok, "title": "Audit upload probe", "preacher": "Probe", "status": "draft"},
            files={"audio_file": ("probe.html", b"<script>document.title='pwned'</script>", "audio/mpeg")})
found = None
listing = ba.get("/admin/messages?search=Audit+upload+probe").text
for path in set(re.findall(r'/media/sermons/[0-9a-f]{32}\.html', listing)):
    found = path
if not found:
    import asyncio

    import asyncpg

    async def q():
        c = await asyncpg.connect("postgresql://swm_user:swm_password@localhost:5433/swm_launch")
        try:
            return await c.fetchval("select coalesce(media_url, audio_file_path) from sermons where title='Audit upload probe'")
        finally:
            await c.close()
    found = asyncio.run(q())
served = httpx.get(BASE + "/" + str(found).lstrip("/")) if found and "html" in str(found) else None
report("SEC-02", bool(served is not None and served.status_code == 200 and "text/html" in served.headers.get("content-type", "")),
       f"upload -> {r.status_code}; stored {found}; served as {served.headers.get('content-type') if served is not None else '-'}")

# F-SEC-03: X-Forwarded-For spoofing defeats the per-IP login limit
c = httpx.Client(base_url=BASE, timeout=30, follow_redirects=True)
tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/admin/").text).group(1)
codes = []
for i in range(14):
    r = c.post("/admin/login", data={"csrf_token": tok, "email": f"nobody{i}@example.org", "password": "x"},
               headers={"X-Forwarded-For": f"10.9.{i}.1"})
    codes.append("blocked" if "too many" in r.text.lower() else "tried")
report("SEC-03", "blocked" not in codes, f"14 failed logins from 14 spoofed IPs: {codes.count('blocked')} blocked")

# F-UX-01: unknown public URL gets raw JSON instead of a page
r = httpx.get(BASE + "/no-such-page")
report("UX-404", "application/json" in r.headers.get("content-type", ""), f"/no-such-page -> {r.status_code} {r.headers.get('content-type')} {r.text[:40]}")

# F-DATA-03: deleting a department deletes every church's reports for it
import asyncio

import asyncpg


async def count(sql):
    c = await asyncpg.connect("postgresql://swm_user:swm_password@localhost:5433/swm_launch")
    try:
        return await c.fetchval(sql)
    finally:
        await c.close()

sup, tok = login("admin@example.org")
dept_id = asyncio.run(count("select department_id from unit_reports order by id limit 1"))
before = asyncio.run(count(f"select count(*) from unit_reports where department_id={dept_id}"))
asyncio.run(count(f"update admins set department_id=null where department_id={dept_id}"))   # the accidental guard
r = sup.post(f"/admin/departments/{dept_id}/destroy", data={"csrf_token": tok})
after = asyncio.run(count(f"select count(*) from unit_reports where department_id={dept_id}"))
report("DATA-03", before > 0 and after == 0, f"department {dept_id}: {before} reports before delete, {after} after (HTTP {r.status_code})")
