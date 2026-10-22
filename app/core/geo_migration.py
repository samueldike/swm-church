"""One-time, idempotent backfill of the global (country/currency/time zone) and list fields.

Runs at startup. It only fills EMPTY code columns, and only from an exact match
(``geo.match_country`` / ``geo.match_region`` / ``lists.canonical``); anything it cannot match is
left as typed and reported by ``review_items()`` (Admin → Data check), never guessed.
"""
from __future__ import annotations

import logging

from sqlalchemy import or_, select

from app.core import geo, lists
from app.database import async_session

logger = logging.getLogger(__name__)


async def run() -> dict[str, int]:
    from app.core.settings_cache import get_settings
    from app.models.branch import Branch
    from app.models.branch_setting import Remittance
    from app.models.giving import Giving
    from app.models.member import Member
    from app.models.minister import Minister
    from app.models.story import Story

    done = {"branches": 0, "givings": 0, "remittances": 0, "ministers": 0, "members": 0, "purposes": 0, "stories": 0}
    async with async_session() as db:
        site = await get_settings(db)
        branches = list((await db.execute(select(Branch))).scalars().all())
        for b in branches:
            changed = False
            if not b.country_code and (code := geo.match_country(b.country)):
                b.country_code, b.country, changed = code, geo.country_name(code), True
            if b.country_code and not b.region_code and (rc := geo.match_region(b.country_code, b.state)):
                b.region_code, b.state, changed = rc, geo.region_name(rc), True
            # One platform currency: HQ's. Branches have none of their own.
            if b.is_hq and b.country_code and not b.currency:
                b.currency, changed = geo.currency_for(b.country_code), True
            elif not b.is_hq and b.currency:
                b.currency, changed = None, True
            if b.country_code and not b.timezone:
                b.timezone, changed = geo.timezone_for(b.country_code), True
            done["branches"] += changed
        by_id = {b.id: b for b in branches}
        hq = next((b for b in branches if b.is_hq), None)
        hq_currency = (hq.currency if hq and hq.currency else geo.DEFAULT_CURRENCY)

        # Money: the currency of the account that took the gift (HQ's, or the branch's own gateway).
        for g in (await db.execute(select(Giving).where(or_(Giving.currency.is_(None), Giving.currency == "")))).scalars():
            g.currency = hq_currency
            done["givings"] += 1
        for r in (await db.execute(select(Remittance).where(or_(Remittance.currency.is_(None), Remittance.currency == "")))).scalars():
            r.currency = hq_currency
            done["remittances"] += 1

        # Ministers: state of origin, matched within their church's country.
        for m in (await db.execute(select(Minister).where(Minister.origin_region_code.is_(None)))).scalars():
            b = by_id.get(m.branch_id)
            country = m.origin_country_code or (b.country_code if b else None)
            if m.state_of_origin and country and (rc := geo.match_region(country, m.state_of_origin)):
                m.origin_country_code, m.origin_region_code, m.state_of_origin = country, rc, geo.region_name(rc)
                done["ministers"] += 1
            if m.title and (t := lists.canonical(m.title, lists.items(site, "minister_titles"))) and t != m.title:
                m.title = t

        # Categories: the list's own spelling when it matches exactly (case/spaces ignored).
        purposes = lists.items(site, "giving_purposes") + [lists.OTHERS]
        for g in (await db.execute(select(Giving))).scalars():
            if (p := lists.canonical(g.giving_type, purposes)) and p != g.giving_type:
                g.giving_type = p
                done["purposes"] += 1
        cats = lists.items(site, "story_categories")
        for s in (await db.execute(select(Story).where(Story.category.isnot(None)))).scalars():
            if (c := lists.canonical(s.category, cats)) and c != s.category:
                s.category = c
                done["stories"] += 1
        for mem in (await db.execute(select(Member).where(Member.gender.isnot(None), Member.gender != ""))).scalars():
            if (gnd := lists.gender(mem.gender)) and gnd != mem.gender:
                mem.gender = gnd
                done["members"] += 1
        await db.commit()
    if any(done.values()):
        logger.info("Global data backfill: %s", done)
    return done


async def review_items(db) -> list[dict]:
    """Records the backfill could not match exactly: shown on Admin → Data check to fix by hand."""
    from app.core.settings_cache import get_settings
    from app.models.branch import Branch
    from app.models.giving import Giving
    from app.models.member import Member
    from app.models.minister import Minister

    out: list[dict] = []
    for b in (await db.execute(select(Branch).order_by(Branch.name))).scalars():
        if not b.country_code:
            out.append({"kind": "Branch", "id": b.id, "name": b.name, "field": "Country", "value": b.country or "(empty)",
                        "fix": "Choose the country"})
        elif not b.region_code:
            out.append({"kind": "Branch", "id": b.id, "name": b.name, "field": "State / region", "value": b.state or "(empty)",
                        "fix": f"Choose from {geo.country_name(b.country_code)}'s official list"})
    for m in (await db.execute(select(Minister).where(Minister.state_of_origin.isnot(None), Minister.state_of_origin != "",
                                                      Minister.origin_region_code.is_(None)))).scalars():
        out.append({"kind": "Minister", "id": m.id, "name": m.full_name, "field": "State of origin", "value": m.state_of_origin,
                    "fix": "Choose the country and state/region of origin"})
    site = await get_settings(db)
    purposes = {p.lower() for p in lists.items(site, "giving_purposes") + [lists.OTHERS]}
    odd = (await db.execute(select(Giving.giving_type).distinct())).scalars().all()
    for p in odd:
        if p and p.lower() not in purposes:
            out.append({"kind": "Giving purpose", "id": None, "name": p, "field": "Purpose", "value": p,
                        "fix": "Add it to Settings → Lists → Giving purposes, or leave it as history"})
    bad_gender = (await db.execute(select(Member.gender).where(Member.gender.isnot(None), Member.gender != "",
                                                               Member.gender.notin_(("male", "female"))).distinct())).scalars().all()
    for g in bad_gender:
        out.append({"kind": "Member gender", "id": None, "name": g, "field": "Gender", "value": g,
                    "fix": "Correct these members' gender"})
    return out
