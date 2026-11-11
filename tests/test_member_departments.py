"""A member can serve in several departments (member_departments): saving, filtering, counting and cleanup."""
import asyncio
from types import SimpleNamespace as NS

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.branch import Branch
from app.models.department import Department
from app.models.member import Member, member_departments
from app.routes import admin as admin_routes


def _run(fn):
    async def go():
        eng = create_async_engine("sqlite+aiosqlite://")
        async with eng.begin() as c:
            await c.exec_driver_sql("PRAGMA foreign_keys=ON")
            await c.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[
                Branch.__table__, Department.__table__, Member.__table__, member_departments]))
        Session = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with Session() as db:
                return await fn(db)
        finally:
            await eng.dispose()
    return asyncio.run(go())


async def _seed(db):
    db.add_all([Branch(id=1, name="HQ", slug="hq", is_active=True, is_hq=True), Branch(id=2, name="Abuja", slug="abuja", is_active=True)])
    choir, ushers, media = Department(name="Choir", slug="choir"), Department(name="Ushering", slug="ushering"), Department(name="Media", slug="media")
    old = Department(name="Old Unit", slug="old-unit", is_active=False)
    db.add_all([choir, ushers, media, old])
    await db.flush()
    return choir, ushers, media, old


def test_a_member_keeps_several_departments_and_the_list_filters_by_them():
    async def body(db):
        choir, ushers, media, old = await _seed(db)
        ada = Member(first_name="Ada", last_name="Obi", branch_id=1, departments=[])
        ben = Member(first_name="Ben", last_name="Eze", branch_id=2, departments=[])
        cy = Member(first_name="Cy", last_name="Uche", branch_id=1, departments=[])
        db.add_all([ada, ben, cy])
        await db.flush()
        # the form sends ids as text; unknown ids and duplicates are ignored, a retired unit cannot be newly picked
        await admin_routes._set_member_departments(db, ada, [str(choir.id), str(media.id), str(media.id), "999", str(old.id)])
        await admin_routes._set_member_departments(db, ben, [str(choir.id)])
        await db.commit()
        assert [d.name for d in ada.departments] == ["Choir", "Media"]

        super_admin, hq_admin = NS(role="super_admin", branch_id=None), NS(role="branch_admin", branch_id=1)
        names = lambda q: sorted(m.first_name for m in q)                      # noqa: E731
        q, _ = admin_routes._members_query(super_admin, "", "", "", str(choir.id))
        everyone_in_choir = names((await db.execute(q)).scalars().all())
        q, _ = admin_routes._members_query(hq_admin, "", "", "", str(choir.id))
        hq_choir = names((await db.execute(q)).scalars().all())
        q, _ = admin_routes._members_query(hq_admin, "", "", "", "none")
        hq_no_dept = names((await db.execute(q)).scalars().all())
        return everyone_in_choir, hq_choir, hq_no_dept

    everyone_in_choir, hq_choir, hq_no_dept = _run(body)
    assert everyone_in_choir == ["Ada", "Ben"]          # HQ Administrator: every church
    assert hq_choir == ["Ada"]                          # a branch admin: own church only
    assert hq_no_dept == ["Cy"]


def test_a_retired_department_already_held_is_kept_and_deleting_a_department_only_removes_the_link():
    async def body(db):
        choir, ushers, media, old = await _seed(db)
        ada = Member(first_name="Ada", last_name="Obi", branch_id=1, departments=[choir, old])
        db.add(ada)
        await db.commit()
        await admin_routes._set_member_departments(db, ada, [str(choir.id), str(old.id)])   # editing keeps the retired one
        await db.commit()
        kept = [d.name for d in ada.departments]
        await db.delete(choir)
        await db.commit()
        links = (await db.execute(select(func.count()).select_from(member_departments))).scalar_one()
        still_there = (await db.execute(select(func.count()).select_from(Member))).scalar_one()
        return kept, links, still_there

    kept, links, still_there = _run(body)
    assert kept == ["Choir", "Old Unit"]
    assert links == 1 and still_there == 1              # Ada stays, only her Choir link went with the department
