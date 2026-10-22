"""iCalendar (.ics) files and "add to Google Calendar" links.

Event times are stored as naive wall-clock time IN THE EVENT'S CHURCH'S TIME ZONE (``Branch.timezone``;
HQ's for HQ-wide items). Files and links carry them converted to UTC (``…Z``), daylight saving included,
so they open at the right hour in Apple, Google and Outlook calendars wherever the visitor is.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable
from urllib.parse import quote, urlencode

from app.core import geo


def event_tz(ev: Any, default: str | None = None) -> str:
    """The time zone an event's stored times are in: its church's, else ``default`` (HQ's)."""
    branch = getattr(ev, "branch", None)
    return (branch.timezone if branch is not None and getattr(branch, "timezone", None) else None) or default or geo.DEFAULT_TIMEZONE


def _esc(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines are at most 75 octets; continuation lines start with one space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def _local(dt: datetime) -> str:
    return dt.replace(tzinfo=None).strftime("%Y%m%dT%H%M%S")


def _utc(dt: datetime, tz: str) -> str:
    return geo.to_utc(dt.replace(tzinfo=None), tz).strftime("%Y%m%dT%H%M%SZ")


def _utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def event_ends(ev: Any) -> datetime:
    """An event with no end time is shown as two hours long."""
    start = ev.starts_at.replace(tzinfo=None)
    end = ev.ends_at.replace(tzinfo=None) if ev.ends_at else None
    return end if end and end > start else start + timedelta(hours=2)


def vevent(ev: Any, host: str, url: str = "", default_tz: str | None = None) -> str:
    tz = event_tz(ev, default_tz)
    lines = [
        "BEGIN:VEVENT",
        f"UID:event-{ev.id}@{host}",
        f"DTSTAMP:{_utc_stamp()}",
        f"DTSTART:{_utc(ev.starts_at, tz)}",
        f"DTEND:{_utc(event_ends(ev), tz)}",
        f"SUMMARY:{_esc(ev.title)}",
    ]
    if ev.description:
        lines.append(f"DESCRIPTION:{_esc(ev.description[:1500])}")
    if ev.location:
        lines.append(f"LOCATION:{_esc(ev.location)}")
    if url:
        lines.append(f"URL:{url}")
    lines.append("END:VEVENT")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def calendar(name: str, body: str) -> str:
    head = f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//{_esc(name)}//Website//EN\r\nCALSCALE:GREGORIAN\r\nMETHOD:PUBLISH\r\nX-WR-CALNAME:{_esc(name)}\r\n"
    return head + body + "END:VCALENDAR\r\n"


def events_ics(events: Iterable[Any], site_name: str, host: str, base_url: str, default_tz: str | None = None) -> str:
    body = "".join(vevent(e, host, f"{base_url}/events/{e.id}", default_tz) for e in events)
    return calendar(site_name, body)


def google_calendar_url(ev: Any, default_tz: str | None = None) -> str:
    tz = event_tz(ev, default_tz)
    params = {
        "action": "TEMPLATE",
        "text": ev.title,
        "dates": f"{_utc(ev.starts_at, tz)}/{_utc(event_ends(ev), tz)}",
    }
    if ev.description:
        params["details"] = ev.description[:1000]
    if ev.location:
        params["location"] = ev.location
    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)


_BYDAY = {"Monday": "MO", "Tuesday": "TU", "Wednesday": "WE", "Thursday": "TH", "Friday": "FR", "Saturday": "SA", "Sunday": "SU"}
_WEEKDAY_INDEX = {d: i for i, d in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}


def services_ics(services: list[dict], site_name: str, host: str, today: datetime, tz: str | None = None) -> str:
    """The weekly services as repeating calendar entries (only the ones whose time can be read reliably)."""
    from app.core.site_content import parse_slot

    body = []
    for i, s in enumerate(services):
        slot = parse_slot(s.get("day", ""), s.get("time", ""))
        if not slot:
            continue
        idx = _WEEKDAY_INDEX[slot["dayOfWeek"]]
        first = today.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=(idx - today.weekday()) % 7)
        sh, sm = map(int, slot["opens"].split(":"))
        eh, em = map(int, slot["closes"].split(":"))
        start, end = first.replace(hour=sh, minute=sm), first.replace(hour=eh, minute=em)
        lines = [
            "BEGIN:VEVENT", f"UID:service-{i}-{slot['dayOfWeek'].lower()}@{host}", f"DTSTAMP:{_utc_stamp()}",
            f"DTSTART:{_utc(start, tz or geo.DEFAULT_TIMEZONE)}", f"DTEND:{_utc(end, tz or geo.DEFAULT_TIMEZONE)}",
            f"RRULE:FREQ=WEEKLY;BYDAY={_BYDAY[slot['dayOfWeek']]}", f"SUMMARY:{_esc(s['name'] + ' — ' + site_name)}", "END:VEVENT",
        ]
        body.append("\r\n".join(_fold(x) for x in lines) + "\r\n")
    return calendar(f"{site_name} — weekly services", "".join(body))
