from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.admin import Admin

HQ_BRANCH_SLUG = "hq"


async def get_hq_branch_id(db: AsyncSession) -> int:
    result = await db.execute(select(Branch.id).where(Branch.slug == HQ_BRANCH_SLUG))
    row = result.scalar_one_or_none()
    if row:
        return row

    hq = Branch(name="Headquarters", slug=HQ_BRANCH_SLUG)
    db.add(hq)
    await db.flush()
    return hq.id


async def sync_super_admin_branches(db: AsyncSession) -> None:
    from app.models.admin import admin_branches

    hq_branch_id = await get_hq_branch_id(db)

    result = await db.execute(
        select(Admin.id).where(Admin.role == "super_admin")
    )
    admin_ids = [row[0] for row in result.all()]

    for admin_id in admin_ids:
        existing = await db.execute(
            select(admin_branches.c.branch_id).where(
                (admin_branches.c.admin_id == admin_id)
                & (admin_branches.c.branch_id == hq_branch_id)
            )
        )
        if not existing.scalar_one_or_none():
            await db.execute(
                admin_branches.insert().values(
                    admin_id=admin_id, branch_id=hq_branch_id
                )
            )
