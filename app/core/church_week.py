"""The church's week as dates: what is on a given day, and when the next gathering starts.

Pure functions over the Settings service list (`site_content.get_services`) and published events,
so the landing page's countdown and calendar can never disagree with the times the church saved.
Everything here is naive wall-clock church time, like the rest of the event code.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)


def start_time(text: str | None) -> tuple[int, int] | None:
    """Start of a free-text time such as "7:00 — 9:30 am", "from 9:00 pm" or "5pm".

    The first am/pm found applies when the start has none of its own ("4:40 — 6:00 pm").
    Unreadable text gives None, and that service simply gets no countdown.
    """
    hits = list(_CLOCK.finditer(text or ""))
    if not hits:
        return None
    hour, minute = int(hits[0].group(1)), int(hits[0].group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    meridian = next((h.group(3).lower() for h in hits if h.group(3)), None)
    if meridian and hour <= 12:
        hour = hour % 12 + (12 if meridian == "pm" else 0)
    return hour, minute


def occurs_on(day_label: str | None, day: date) -> bool:
    """Whether a service labelled "Sunday" or "Last Friday" happens on this date."""
    label = (day_label or "").strip().lower()
    if label.startswith("last "):
        name = label[5:].strip()
        if name not in _WEEKDAYS or day.weekday() != _WEEKDAYS.index(name):
            return False
        return day + timedelta(days=7) > date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
    return label in _WEEKDAYS and day.weekday() == _WEEKDAYS.index(label)


def next_gathering(services: list[dict[str, Any]], now: datetime, horizon: int = 40) -> dict[str, Any] | None:
    """The soonest service that has not started yet."""
    for offset in range(horizon):
        day = (now + timedelta(days=offset)).date()
        found = []
        for svc in services:
            clock = start_time(svc.get("time"))
            if clock and occurs_on(svc.get("day"), day):
                at = datetime(day.year, day.month, day.day, *clock)
                if at > now:
                    found.append((at, svc))
        if found:
            at, svc = min(found, key=lambda row: row[0])
            return {"at": at, "name": svc.get("name"), "day": svc.get("day"), "time": svc.get("time")}
    return None


def month_view(services: list[dict[str, Any]], events: list[Any], first: date, today: date) -> dict[str, Any]:
    """One month as weeks (Sunday first) of {date, in_month, today, items}; events sort before services."""
    by_day: dict[date, list[Any]] = {}
    for ev in events:
        by_day.setdefault(ev.starts_at.date(), []).append(ev)
    weeks = []
    for week in calendar.Calendar(firstweekday=6).monthdatescalendar(first.year, first.month):
        cells = []
        for day in week:
            items = [{"title": ev.title, "time": ev.starts_at.strftime("%I:%M %p").lstrip("0"), "event_id": ev.id, "major": True}
                     for ev in sorted(by_day.get(day, []), key=lambda e: e.starts_at)]
            items += [{"title": s.get("name"), "time": s.get("time"), "event_id": None, "major": False}
                      for s in services if occurs_on(s.get("day"), day)]
            cells.append({"date": day, "in_month": day.month == first.month, "today": day == today, "items": items})
        weeks.append(cells)
    return {"key": first.strftime("%Y-%m"), "label": first.strftime("%B %Y"), "weeks": weeks}
