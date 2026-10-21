from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch

HQ_BRANCH_SLUG = "hq"


async def get_hq_branch_id(db: AsyncSession) -> int:
    """Id of the HQ branch (``Branch.is_hq``). Created only when the database has no branches at all.

    ``ensure_schema`` flags exactly one existing branch as HQ, so a deployment whose HQ is called
    "Scent of Water Ministry HQ" never gets a second, empty "Headquarters" branch invented for it.
    """
    row = (await db.execute(select(Branch.id).where(Branch.is_hq == True).order_by(Branch.id).limit(1))).scalar_one_or_none()  # noqa: E712
    if row:
        return row
    any_branch = (await db.execute(select(Branch.id).order_by(Branch.id).limit(1))).scalar_one_or_none()
    if any_branch:
        (await db.execute(select(Branch).where(Branch.id == any_branch))).scalar_one().is_hq = True
        await db.flush()
        return any_branch
    hq = Branch(name="Headquarters", slug=HQ_BRANCH_SLUG, is_hq=True)
    db.add(hq)
    await db.flush()
    return hq.id


async def sync_super_admin_branches(db: AsyncSession) -> None:
    """Every HQ administrator belongs to the HQ branch."""
    from sqlalchemy import update

    from app.core.access import HQ_ROLES
    from app.models.admin import Admin

    hq_id = await get_hq_branch_id(db)
    await db.execute(update(Admin).where(Admin.role.in_(HQ_ROLES), Admin.branch_id.is_(None)).values(branch_id=hq_id))
