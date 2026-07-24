import asyncio
from app.database import async_session
from app.models.admin import Admin
from app.core.security import hash_password
from sqlalchemy import select

async def seed_admin():
    async with async_session() as db:
        result = await db.execute(select(Admin).where(Admin.email == "admin@scentofwater.org"))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Admin already exists: {existing.email} (id={existing.id})")
            return

        admin = Admin(
            email="admin@scentofwater.org",
            full_name="Super Admin",
            password_hash=hash_password("admin123"),
            role="super_admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        print(f"Created super admin: {admin.email} / admin123 (id={admin.id})")

asyncio.run(seed_admin())
