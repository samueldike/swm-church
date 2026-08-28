"""Shared month-grid math for the public and admin calendar views.

Both routes need the same thing: a Sunday-first grid of dates for a given
month, with events attached to every date they actually cover. Kept in one
place so the "does a multi-day event show up on every day it spans" behaviour
can't drift between the two.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta
from typing import Any, Iterable


def month_bounds(year: int, month: int) -> tuple[date, date, date]:
    """(currentMonth, prevMonth, nextMonth) as the 1st of each, for nav links."""
    current = date(year, month, 1)
    prev_month = (current - timedelta(days=1)).replace(day=1)
    next_month = (current + timedelta(days=32)).replace(day=1)
    return current, prev_month, next_month


def month_grid_days(year: int, month: int) -> list[date]:
    """Full Sunday-first weeks covering the month (padded with adjacent-month
    days), matching what a real wall calendar shows."""
    cal = _calendar.Calendar(firstweekday=6)
    return [d for week in cal.monthdatescalendar(year, month) for d in week]


def events_by_date(events: Iterable[Any]) -> dict[str, list[Any]]:
    """Map ISO date -> events occurring on that date.

    The key fix this exists for: a multi-day event (starts_at != ends_at's
    date) previously only appeared on its start date, so day 3 of a 6-day
    anniversary or day 20 of a 40-day fast showed nothing. Every event is now
    attached to EVERY date from starts_at through ends_at inclusive.
    """
    by_date: dict[str, list[Any]] = {}
    for ev in events:
        if not ev.starts_at:
            continue
        start = ev.starts_at.date()
        end = ev.ends_at.date() if ev.ends_at else start
        if end < start:  # defensive: malformed data shouldn't wedge the loop
            end = start
        day = start
        while day <= end:
            by_date.setdefault(day.isoformat(), []).append(ev)
            day += timedelta(days=1)
    return by_date


def year_months(year: int, selected_month: int) -> list[dict[str, Any]]:
    """The 12-month rail for jumping around within a year."""
    return [
        {
            "label": date(year, mo, 1).strftime("%b"),
            "ym": f"{year}-{mo:02d}",
            "current": mo == selected_month,
        }
        for mo in range(1, 13)
    ]


def serialize_event(ev: Any) -> dict[str, Any]:
    return {
        "id": ev.id,
        "title": ev.title,
        "is_major": bool(ev.is_major),
        "starts_at": ev.starts_at.isoformat() if ev.starts_at else None,
        "ends_at": ev.ends_at.isoformat() if ev.ends_at else None,
        "time_label": ev.starts_at.strftime("%I:%M %p").lstrip("0") if ev.starts_at else "",
        "location": ev.location or None,
    }
