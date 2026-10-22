"""The platform's currency and home country: always HQ's.

Every gift, total, remittance and amount on the website is in HQ's currency (set on HQ's branch
page). Branches record where they are (country / region / city / time zone) for global management,
but money has one currency. Each gift still stores its currency, so if HQ ever changes it, older
gifts stay correct and are reported separately rather than mixed.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core import geo


async def _hq(db):
    from app.models.branch import Branch

    return (await db.execute(select(Branch).where(Branch.is_hq == True))).scalar_one_or_none()  # noqa: E712


async def platform_currency(db) -> str:
    hq = await _hq(db)
    return (hq.currency if hq and hq.currency else None) or (geo.currency_for(hq.country_code) if hq and hq.country_code else geo.DEFAULT_CURRENCY)


async def platform_country(db) -> str:
    hq = await _hq(db)
    return (hq.country_code if hq and hq.country_code else geo.DEFAULT_COUNTRY)


async def church_country(db, branch_id: int | None) -> str:
    """A church's country (for reading its local phone numbers), or the platform's (HQ's)."""
    from app.models.branch import Branch

    if branch_id:
        cc = (await db.execute(select(Branch.country_code).where(Branch.id == branch_id))).scalar_one_or_none()
        if cc:
            return cc
    return await platform_country(db)


async def platform_timezone(db) -> str:
    """HQ's time zone: for HQ-wide times (weekly services) and events with no church."""
    hq = await _hq(db)
    return (hq.timezone if hq and hq.timezone else None) or (geo.timezone_for(hq.country_code) if hq and hq.country_code else geo.DEFAULT_TIMEZONE)
