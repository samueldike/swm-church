from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_settings(db: AsyncSession) -> dict[str, str]:
    from app.models.site_settings import SiteSetting

    result = await db.execute(select(SiteSetting))
    rows = result.scalars().all()
    return {row.key: row.value for row in rows}


async def get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    settings = await get_settings(db)
    return settings.get(key, default)


async def update_settings(db: AsyncSession, settings_dict: dict[str, str]) -> None:
    from app.models.site_settings import SiteSetting

    for key, value in settings_dict.items():
        result = await db.execute(
            select(SiteSetting).where(SiteSetting.key == key)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = value
        else:
            db.add(SiteSetting(key=key, value=value))

    await db.flush()
