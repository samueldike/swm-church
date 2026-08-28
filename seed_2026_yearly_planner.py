"""Seed the 2026 annual programme from the official Yearly Planner PDF
(©SWM/ADMIN/PROG/2026) into the events table.

The source has no time-of-day column, so times are assigned by a fixed rule
rather than guessed per row:
  - "COMMUNION SERVICE (WED)" / any plain Wednesday service -> 5:00pm, matching
    the site's existing midweek Bible study time.
  - "ALL-NIGHT VIGIL" (always a Friday in the source) -> 9:00pm start,
    5:00am the next morning (Saturday) end.
  - Any entry spanning more than one calendar day -> 9:00am on the first day,
    9:00pm on the last day.
  - Everything else (single-day, non-Wednesday) -> 9:00am.

Two entries span across the month they're filed under in the source table:
"14 Days Pre-Convention Prayer & Fasting" is listed under JUNE but reads
"MON 25TH - SUN 7TH" — the 25th is only a Monday in May 2026, so it actually
runs May 25 - Jun 7. Same pattern for "14 Days Pre-Spirit Life Conference"
under OCTOBER, which actually runs Oct 26 - Nov 8. Both are encoded with
their true start/end month below.

Every date below is checked against the weekday the source printed (e.g.
"WED 7TH") before being inserted — a transcription slip fails loudly instead
of silently seeding a wrong date.
"""
import asyncio
from datetime import date, datetime, time

from sqlalchemy import select, delete

from app.database import async_session
from app.models.event import Event

YEAR = 2026

# (title, is_major, kind, start_month, start_day, start_wd, end_month, end_day, end_wd)
# kind: 'normal' | 'wed' | 'vigil' | 'multi'
# end_month/end_day/end_wd are None for single-day 'normal'/'wed' entries.
EVENTS = [
    ("40 Days of Prayer & Fasting", True, "multi", 1, 7, "Wed", 2, 15, "Sun"),
    ("Anointing Service", False, "normal", 1, 4, "Sun", None, None, None),
    ("Communion Service (Midweek)", False, "wed", 1, 7, "Wed", None, None, None),
    ("Special Service", False, "normal", 1, 11, "Sun", None, None, None),
    ("Communion Service", False, "normal", 1, 18, "Sun", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 1, 25, "Sun", None, None, None),
    ("All-Night Vigil", False, "vigil", 1, 30, "Fri", None, None, None),

    ("Prophetic Service", False, "normal", 2, 1, "Sun", None, None, None),
    ("Communion Service (Midweek)", False, "wed", 2, 4, "Wed", None, None, None),
    ("Anointing Service", False, "normal", 2, 8, "Sun", None, None, None),
    ("Valentine Special", False, "normal", 2, 14, "Sat", None, None, None),
    ("Communion Service", False, "normal", 2, 15, "Sun", None, None, None),
    ("14th Anniversary", True, "multi", 2, 20, "Fri", 2, 22, "Sun"),
    ("All-Night Vigil", False, "vigil", 2, 27, "Fri", None, None, None),

    ("Prophetic Service", False, "normal", 3, 1, "Sun", None, None, None),
    ("3 Days Monthly Prayer & Fasting", False, "multi", 3, 2, "Mon", 3, 4, "Wed"),
    ("Communion Service (Midweek)", False, "wed", 3, 4, "Wed", None, None, None),
    ("Anointing Service", False, "normal", 3, 8, "Sun", None, None, None),
    ("Singles Point Blank", True, "multi", 3, 13, "Fri", 3, 15, "Sun"),
    ("Communion Service", False, "normal", 3, 22, "Sun", None, None, None),
    ("3rd Odogwu Conference", False, "multi", 3, 27, "Fri", 3, 29, "Sun"),

    ("Cultural/Easter Service", False, "normal", 4, 5, "Sun", None, None, None),
    ("3 Days Monthly Prayer & Fasting", False, "multi", 4, 6, "Mon", 4, 8, "Wed"),
    ("Communion Service (Midweek)", False, "wed", 4, 8, "Wed", None, None, None),
    ("Annual Prison's Outreach", False, "normal", 4, 10, "Fri", None, None, None),
    ("Pastor's Appreciation Day (PAD)", False, "normal", 4, 12, "Sun", None, None, None),
    ("Anointing Service", False, "normal", 4, 12, "Sun", None, None, None),
    ("Communion Service", False, "normal", 4, 19, "Sun", None, None, None),
    ("All-Night Vigil", False, "vigil", 4, 24, "Fri", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 4, 26, "Sun", None, None, None),

    ("Prophetic Service", False, "normal", 5, 3, "Sun", None, None, None),
    ("3 Days Monthly Prayer & Fasting", False, "multi", 5, 4, "Mon", 5, 6, "Wed"),
    ("Anointing Service", False, "normal", 5, 10, "Sun", None, None, None),
    ("Leaders/Ministers Retreat", False, "multi", 5, 14, "Thu", 5, 17, "Sun"),
    ("Healing/Communion Service", False, "normal", 5, 17, "Sun", None, None, None),
    ("Children's Day Special Service", False, "normal", 5, 24, "Sun", None, None, None),
    ("All-Night Vigil", False, "vigil", 5, 29, "Fri", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 5, 31, "Sun", None, None, None),

    ("14 Days Pre-Convention Prayer & Fasting/Prophetic Service", True, "multi", 5, 25, "Mon", 6, 7, "Sun"),
    ("Communion Service (Midweek)", False, "wed", 6, 3, "Wed", None, None, None),
    ("Prophetic Service", False, "normal", 6, 7, "Sun", None, None, None),
    ("14th Annual Word Convention/Anointing Service", True, "multi", 6, 10, "Wed", 6, 14, "Sun"),
    ("Special Worship Service/Communion Service", False, "normal", 6, 21, "Sun", None, None, None),
    ("All-Night Vigil", False, "vigil", 6, 26, "Fri", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 6, 28, "Sun", None, None, None),

    ("Prophetic Service", False, "normal", 7, 5, "Sun", None, None, None),
    ("3 Days Monthly Prayer & Fasting", False, "multi", 7, 6, "Mon", 7, 8, "Wed"),
    ("Communion Service (Midweek)", False, "wed", 7, 8, "Wed", None, None, None),
    ("Friendship/Anointing Service", False, "normal", 7, 12, "Sun", None, None, None),
    ("Singles Point Blank", True, "multi", 7, 17, "Fri", 7, 19, "Sun"),
    ("Communion Service", False, "normal", 7, 19, "Sun", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 7, 26, "Sun", None, None, None),
    ("All-Night Vigil", False, "vigil", 7, 31, "Fri", None, None, None),

    ("Prophetic Service", False, "normal", 8, 2, "Sun", None, None, None),
    ("Annual Camp Meeting", True, "multi", 8, 3, "Mon", 8, 5, "Wed"),
    ("Fruitful Praise", False, "normal", 8, 7, "Fri", None, None, None),
    ("Annual Picnic", False, "normal", 8, 8, "Sat", None, None, None),
    ("Anointing Service", False, "normal", 8, 9, "Sun", None, None, None),
    ("Couples Dinner", True, "normal", 8, 14, "Fri", None, None, None),
    ("Communion Service/Love Feast — Exchange of Gifts", False, "normal", 8, 16, "Sun", None, None, None),
    ("Special Service", False, "normal", 8, 23, "Sun", None, None, None),
    ("All-Night Vigil", False, "vigil", 8, 28, "Fri", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 8, 30, "Sun", None, None, None),

    ("Communion Service (Midweek)", False, "wed", 9, 2, "Wed", None, None, None),
    ("Youth Convention", True, "multi", 9, 4, "Fri", 9, 6, "Sun"),
    ("3 Days Monthly Prayer & Fasting", False, "multi", 9, 7, "Mon", 9, 9, "Wed"),
    ("Special Service", False, "normal", 9, 13, "Sun", None, None, None),
    ("Communion Service", False, "normal", 9, 20, "Sun", None, None, None),
    ("Mama's Birthday", False, "normal", 9, 24, "Thu", None, None, None),
    ("All-Night Vigil", False, "vigil", 9, 25, "Fri", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 9, 27, "Sun", None, None, None),

    ("Celebrating Nationhood/Communion", True, "normal", 10, 1, "Thu", None, None, None),
    ("Prophetic Service", False, "normal", 10, 4, "Sun", None, None, None),
    ("Anointing Service", False, "normal", 10, 11, "Sun", None, None, None),
    ("Communion Service", False, "normal", 10, 18, "Sun", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 10, 25, "Sun", None, None, None),
    ("14 Days Pre-Spirit Life Conference/Annual P&F Programme", False, "multi", 10, 26, "Mon", 11, 8, "Sun"),
    ("All-Night Vigil", False, "vigil", 10, 30, "Fri", None, None, None),

    ("Prophetic Service", False, "normal", 11, 1, "Sun", None, None, None),
    # Source prints "WED 5TH NOV", but Nov 5 2026 is a Thursday (Nov 1 is
    # corroborated as a Sunday by "Prophetic Service, Sun 1st Nov" directly
    # above it, and every other month's midweek Communion Service falls on
    # the first Wednesday after the month opens) — the weekday label is
    # trusted over the day-of-month digit, so this seeds as the 4th.
    ("Communion Service (Midweek)", False, "wed", 11, 4, "Wed", None, None, None),
    ("Anointing Service", False, "normal", 11, 8, "Sun", None, None, None),
    ("5th Annual Spirit Life Conference", True, "multi", 11, 11, "Wed", 11, 15, "Sun"),
    ("Special Worship Service (Annual Hospital Outreach)", False, "normal", 11, 22, "Sun", None, None, None),
    ("All-Night Vigil", False, "vigil", 11, 27, "Fri", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 11, 29, "Sun", None, None, None),

    ("Communion Service (Midweek)", False, "wed", 12, 2, "Wed", None, None, None),
    ("Annual Singles Dinner", True, "normal", 12, 4, "Fri", None, None, None),
    ("Medical Mission/Crusade Project", False, "normal", 12, 5, "Sat", None, None, None),
    ("Prophetic Service", False, "normal", 12, 6, "Sun", None, None, None),
    ("Carol Service", True, "normal", 12, 11, "Fri", None, None, None),
    ("Anointing Service", False, "normal", 12, 13, "Sun", None, None, None),
    ("10th Annual Mara-Tongue", False, "normal", 12, 19, "Sat", None, None, None),
    ("Communion Service", False, "normal", 12, 20, "Sun", None, None, None),
    ("Christmas Service", False, "normal", 12, 25, "Fri", None, None, None),
    ("Thanksgiving/Testimony Service", False, "normal", 12, 27, "Sun", None, None, None),
    ("Cross Over Service", False, "normal", 12, 31, "Thu", None, None, None),
]

WD_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def _check_weekday(label: str, d: date, expected: str) -> None:
    if d.weekday() != WD_INDEX[expected]:
        raise ValueError(
            f"{label}: {d.isoformat()} is a {d.strftime('%A')}, expected {expected} — "
            "check the source transcription before seeding."
        )


def build_events() -> list[Event]:
    built = []
    for title, is_major, kind, sm, sd, swd, em, ed, ewd in EVENTS:
        start_date = date(YEAR, sm, sd)
        _check_weekday(title, start_date, swd)

        if kind == "normal":
            starts_at = datetime.combine(start_date, time(9, 0))
            ends_at = None
        elif kind == "wed":
            starts_at = datetime.combine(start_date, time(17, 0))
            ends_at = None
        elif kind == "vigil":
            starts_at = datetime.combine(start_date, time(21, 0))
            ends_at = datetime.combine(date.fromordinal(start_date.toordinal() + 1), time(5, 0))
        elif kind == "multi":
            end_date = date(YEAR, em, ed)
            _check_weekday(title, end_date, ewd)
            starts_at = datetime.combine(start_date, time(9, 0))
            ends_at = datetime.combine(end_date, time(21, 0))
        else:
            raise ValueError(f"unknown kind {kind!r} for {title!r}")

        built.append(Event(
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            is_major=is_major,
            is_published=True,
        ))
    return built


async def seed():
    events = build_events()
    print(f"Built {len(events)} events from the 2026 Yearly Planner (all weekdays verified).")

    async with async_session() as db:
        existing = (await db.execute(select(Event))).scalars().all()
        if existing:
            print(f"Clearing {len(existing)} existing (placeholder) events...")
            await db.execute(delete(Event))
            await db.flush()

        for ev in events:
            db.add(ev)
        await db.commit()
        print(f"Seeded {len(events)} events for {YEAR}.")


if __name__ == "__main__":
    asyncio.run(seed())
