"""Run: py scripts/repair_hod_departments.py [--apply] — HOD accounts created from an invitation before SEC-04 have
no department, so their reports count for no department. Lists each one with the department of the invitation it
accepted; --apply sets it. Only fills empty department_id from an exact (case-insensitive) email match; never
changes an account that already has a department. Uses DATABASE_URL: run it against a copy first."""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402

FIND = text("""
    SELECT a.id, a.email, a.branch_id, inv.department_id, d.name
    FROM admins a
    JOIN LATERAL (
        SELECT i.department_id FROM hod_invitations i
        WHERE lower(i.email) = lower(a.email) AND i.branch_id IS NOT DISTINCT FROM a.branch_id
          AND i.accepted_at IS NOT NULL AND i.department_id IS NOT NULL
        ORDER BY i.accepted_at DESC LIMIT 1
    ) inv ON TRUE
    JOIN departments d ON d.id = inv.department_id
    WHERE a.role = 'hod' AND a.department_id IS NULL
    ORDER BY a.id
""")
UNMATCHED = text("SELECT count(*) FROM admins WHERE role = 'hod' AND department_id IS NULL")


async def main(apply: bool) -> None:
    async with engine.begin() as conn:
        rows = (await conn.execute(FIND)).all()
        for admin_id, email, branch_id, dept_id, dept in rows:
            print(f"HOD #{admin_id} {email} (branch {branch_id}) -> {dept} (#{dept_id})")
            if apply:
                await conn.execute(text("UPDATE admins SET department_id = :d WHERE id = :i AND department_id IS NULL"),
                                   {"d": dept_id, "i": admin_id})
        left = (await conn.execute(UNMATCHED)).scalar()
    print(f"{len(rows)} matched, {'updated' if apply else 'not changed (dry run; add --apply)'}. "
          f"HOD accounts without a department right now: {left} (any not matched above: set them on the HODs page).")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
