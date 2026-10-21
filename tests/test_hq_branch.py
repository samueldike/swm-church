"""HQ + branches operating model: access policy, money arithmetic, schema evolution, end-to-end script."""
import asyncio
import pathlib
import subprocess
import sys

import pytest

from app.core import access
from app.core.finance import branch_position


# ─── role / permission policy ────────────────────────────────────────

@pytest.mark.parametrize("path,method,perm", [
    ("/admin/settings", "GET", "system"), ("/admin/security-logs/export", "GET", "system"), ("/admin/admins/3/edit", "GET", "system"),
    ("/admin/branches", "GET", "system"), ("/admin/branches/2", "GET", "branch.view"), ("/admin/branches/2/settings", "GET", "system"),
    ("/admin/branches/2/active", "POST", "system"), ("/admin/branch-settings", "POST", "branch_settings"),
    ("/admin/departments", "GET", "departments.view"), ("/admin/departments/create", "GET", "departments.manage"),
    ("/admin/departments/4/edit", "GET", "departments.manage"), ("/admin/departments", "POST", "departments.manage"),
    ("/admin/sliders", "GET", "media"), ("/admin/publications/books/1/editor", "GET", "media"), ("/admin/subscribers/export.csv", "GET", "media"),
    ("/admin/donations", "GET", "donations"), ("/admin/donations/record", "POST", "donations"), ("/admin/remittances", "GET", "remittances"),
    ("/admin/prayer-requests", "GET", "prayer"), ("/admin/inquiries/3", "GET", "inquiries"), ("/admin/members/import", "GET", "members"),
    ("/admin/messages/create", "GET", "audio"), ("/admin/transcriptions/9", "GET", "audio"), ("/admin/harvesttrack/export.csv", "GET", "harvest"),
    ("/admin/hods/invite", "GET", "hods"), ("/admin/unit-reports/2", "GET", "reports"), ("/admin/hod/reports", "GET", "hod_reports"),
    ("/admin/branch-reports", "GET", "branch_reports"), ("/admin/branch-reports/3/comment", "POST", "branch_reports"),
    ("/admin/dashboard", "GET", "dashboard"), ("/admin/metrics", "GET", "dashboard"), ("/admin/profile", "POST", "profile"),
])
def test_every_admin_url_maps_to_a_permission(path, method, perm):
    assert access.required_permission(method, path) == perm


def test_open_paths_and_non_admin_paths_need_no_permission():
    for p in ("/admin/", "/admin", "/admin/login", "/admin/invitation/abc", "/admin/hod/invitation/abc", "/messages", "/api/events"):
        assert access.path_allowed("hod", "GET", p)
    assert access.required_permission("GET", "/give") is None


def test_role_matrix():
    P = access.path_allowed
    assert all(P("super_admin", "GET", p) for p in ("/admin/settings", "/admin/donations", "/admin/hod/reports"))
    # HOD: only reports + profile
    assert P("hod", "GET", "/admin/hod/reports") and P("hod", "GET", "/admin/profile")
    assert not any(P("hod", "GET", p) for p in ("/admin/members", "/admin/dashboard", "/admin/messages", "/admin/hods", "/admin/departments"))
    # legacy strings behave as branch admin
    for legacy in ("admin", "branch_super_admin"):
        assert access.normalize_role(legacy) == "branch_admin"
        assert P(legacy, "GET", "/admin/members") and not P(legacy, "GET", "/admin/settings")
    ba = "branch_admin"
    assert P(ba, "GET", "/admin/donations") and P(ba, "GET", "/admin/branch-settings") and P(ba, "GET", "/admin/branches/5")
    assert not any(P(ba, "GET", p) for p in ("/admin/sliders", "/admin/subscribers", "/admin/publications/books", "/admin/branches", "/admin/admins", "/admin/security-logs"))
    assert not P(ba, "POST", "/admin/departments") and P(ba, "GET", "/admin/departments")
    assert P("hq_media", "GET", "/admin/sliders") and P("hq_media", "GET", "/admin/messages") and not P("hq_media", "GET", "/admin/donations") and not P("hq_media", "GET", "/admin/members")
    assert P("hq_finance", "GET", "/admin/remittances") and not P("hq_finance", "GET", "/admin/prayer-requests") and not P("hq_finance", "GET", "/admin/sliders")
    assert P("branch_admin", "GET", "/admin/branch-reports") and P("hq_pastoral", "GET", "/admin/branch-reports") and not P("hq_media", "GET", "/admin/branch-reports") and not P("hod", "GET", "/admin/branch-reports")
    assert P("hq_pastoral", "GET", "/admin/prayer-requests") and P("hq_pastoral", "GET", "/admin/members") and not P("hq_pastoral", "GET", "/admin/donations")
    assert not access.has_perm("ghost_role", "dashboard") and not access.has_perm(None, "dashboard")


def test_only_the_hq_administrator_holds_system_permissions():
    holders = [r for r in access.ROLE_PERMS if access.has_perm(r, "system")]
    assert holders == ["super_admin"]
    assert set(access.ASSIGNABLE_ROLES) == {"super_admin", "hq_media", "hq_finance", "hq_pastoral", "branch_admin", "resident_pastor"}
    assert "resident_pastor" not in access.FORM_ROLES


def test_every_admin_route_is_covered_by_the_access_policy():
    """A new /admin route with no rule would be open to any signed-in role: fail loudly instead."""
    import re

    from app.routes.admin import router

    open_routes = {("GET", "/"), ("POST", "/login"), ("POST", "/logout"), ("GET", "/invitation/{token}"), ("POST", "/invitation/{token}")}
    uncovered = []
    for r in router.routes:
        for m in getattr(r, "methods", None) or []:
            if m in ("HEAD", "OPTIONS"):
                continue
            if access.required_permission(m, "/admin" + re.sub(r"\{[^}]+\}", "1", r.path)) is None and (m, r.path) not in open_routes:
                uncovered.append((m, r.path))
    assert not uncovered, uncovered


def test_new_roles_and_modules_are_wired():
    assert access.has_perm("super_admin", "announcements") and not access.has_perm("branch_admin", "announcements")
    assert access.has_perm("branch_admin", "announcements.view") and access.has_perm("resident_pastor", "announcements.view") and not access.has_perm("hod", "announcements.view")
    assert access.has_perm("branch_admin", "activity") and not access.has_perm("hq_media", "activity")
    assert access.required_permission("POST", "/admin/announcements/3/update") == "announcements"
    assert access.required_permission("GET", "/admin/announcements") == "announcements.view"
    assert access.required_permission("GET", "/admin/activity") == "activity"


# ─── money position ──────────────────────────────────────────────────

def test_branch_position_arithmetic():
    # HQ took 10,000 for the branch, branch took 60,000 itself; HQ share 10% -> entitlement 7,000; HQ already holds 10,000
    p = branch_position(10, 10_000, 60_000, 0, 0)
    assert (p["total"], p["entitlement"], p["balance"]) == (70_000, 7_000, -3_000)          # HQ owes branch 3,000
    assert branch_position(10, 10_000, 60_000, 0, 3_000)["balance"] == 0                      # HQ paid the branch: settled
    assert branch_position(10, 0, 100_000, 4_000, 0)["balance"] == 6_000                      # branch kept it all: owes HQ 6,000 after paying 4,000
    assert branch_position(0, 5_000, 0, 0, 0)["balance"] == -5_000                            # 0% share: HQ owes everything it holds
    assert branch_position(100, 0, 500, 0, 0)["balance"] == 500
    assert branch_position(10, 0, 0, 0, 0) == {"total": 0, "entitlement": 0, "balance": 0}


# ─── schema evolution on an old database ─────────────────────────────

def test_ensure_schema_upgrades_an_old_database(monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.database as dbm

    async def run():
        eng = create_async_engine("sqlite+aiosqlite://")
        async with eng.begin() as c:
            await c.execute(text("CREATE TABLE branches (id INTEGER PRIMARY KEY, name VARCHAR(255), slug VARCHAR(255), is_active BOOLEAN)"))
            await c.execute(text("CREATE TABLE givings (id INTEGER PRIMARY KEY, reference VARCHAR(255), amount NUMERIC)"))
            await c.execute(text("CREATE TABLE prayer_requests (id INTEGER PRIMARY KEY, full_name VARCHAR(255))"))
            await c.execute(text("CREATE TABLE admins (id INTEGER PRIMARY KEY, email VARCHAR(255), role VARCHAR(50))"))
            await c.execute(text("INSERT INTO branches (id, name, slug, is_active) VALUES (1, 'Scent of Water Ministry HQ', 'scent-of-water-hq', 1), (2, 'Abuja Branch', 'abuja-branch', 1)"))
            await c.execute(text("INSERT INTO givings (id, reference, amount) VALUES (1, 'r1', 100)"))
            await c.execute(text("INSERT INTO admins (id, email, role) VALUES (1, 'a', 'admin'), (2, 'b', 'branch_super_admin'), (3, 'c', 'super_admin'), (4, 'd', 'hod')"))
        monkeypatch.setattr(dbm, "engine", eng)
        await dbm.ensure_schema()
        await dbm.ensure_schema()                                   # idempotent
        async with eng.connect() as c:
            hq = (await c.execute(text("SELECT id FROM branches WHERE is_hq = 1"))).fetchall()
            roles = [r[0] for r in (await c.execute(text("SELECT role FROM admins ORDER BY id"))).fetchall()]
            g = (await c.execute(text("SELECT gateway_owner, method, branch_id FROM givings"))).fetchone()
            cols = [r[1] for r in (await c.execute(text("PRAGMA table_info(prayer_requests)"))).fetchall()]
        await eng.dispose()
        return hq, roles, g, cols

    hq, roles, g, cols = asyncio.run(run())
    assert hq == [(1,)]                                             # the existing HQ, not a newly invented one
    assert roles == ["branch_admin", "branch_admin", "super_admin", "hod"]
    assert tuple(g) == ("hq", "online", None) and "branch_id" in cols


def test_hq_branch_lookup_never_invents_a_second_hq(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.hq_branch import get_hq_branch_id
    from app.database import Base
    from app.models.branch import Branch

    async def run():
        eng = create_async_engine("sqlite+aiosqlite://")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, expire_on_commit=False)
        async with maker() as db:
            assert (await get_hq_branch_id(db)) > 0                  # empty database: creates one
            db.add(Branch(name="Other", slug="other"))
            await db.commit()
            first = await get_hq_branch_id(db)
            assert first == await get_hq_branch_id(db)
            from sqlalchemy import func, select

            n = (await db.execute(select(func.count()).select_from(Branch).where(Branch.is_hq == True))).scalar_one()  # noqa: E712
        await eng.dispose()
        return n

    assert asyncio.run(run()) == 1


# ─── the whole model, end to end ─────────────────────────────────────

def test_hq_branch_end_to_end_script():
    root = pathlib.Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, "scripts/hq_branch_checks.py"], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=590)
    failed = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAIL")]
    assert proc.returncode == 0 and not failed, "\n".join(failed) or proc.stderr[-2000:]
