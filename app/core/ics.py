"""iCalendar (.ics) files and "add to Google Calendar" links.

Event times are stored as naive local (church) time, so files carry ``TZID=Africa/Lagos`` (UTC+1, no daylight saving)
and open at the right hour in Apple, Google and Outlook calendars regardless of where the visitor is.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable
from urllib.parse import quote, urlencode

TZID = "Africa/Lagos"
_VTIMEZONE = (
    "BEGIN:VTIMEZONE\r\nTZID:Africa/Lagos\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\n"
    "TZOFFSETFROM:+0100\r\nTZOFFSETTO:+0100\r\nTZNAME:WAT\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
)


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


def _utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def event_ends(ev: Any) -> datetime:
    """An event with no end time is shown as two hours long."""
    start = ev.starts_at.replace(tzinfo=None)
    end = ev.ends_at.replace(tzinfo=None) if ev.ends_at else None
    return end if end and end > start else start + timedelta(hours=2)


def vevent(ev: Any, host: str, url: str = "") -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:event-{ev.id}@{host}",
        f"DTSTAMP:{_utc_stamp()}",
        f"DTSTART;TZID={TZID}:{_local(ev.starts_at)}",
        f"DTEND;TZID={TZID}:{_local(event_ends(ev))}",
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
    head = f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//{_esc(name)}//Website//EN\r\nCALSCALE:GREGORIAN\r\nMETHOD:PUBLISH\r\nX-WR-CALNAME:{_esc(name)}\r\nX-WR-TIMEZONE:{TZID}\r\n"
    return head + _VTIMEZONE + body + "END:VCALENDAR\r\n"


def events_ics(events: Iterable[Any], site_name: str, host: str, base_url: str) -> str:
    body = "".join(vevent(e, host, f"{base_url}/events/{e.id}") for e in events)
    return calendar(site_name, body)


def google_calendar_url(ev: Any) -> str:
    params = {
        "action": "TEMPLATE",
        "text": ev.title,
        "dates": f"{_local(ev.starts_at)}/{_local(event_ends(ev))}",
        "ctz": TZID,
    }
    if ev.description:
        params["details"] = ev.description[:1000]
    if ev.location:
        params["location"] = ev.location
    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)


_BYDAY = {"Monday": "MO", "Tuesday": "TU", "Wednesday": "WE", "Thursday": "TH", "Friday": "FR", "Saturday": "SA", "Sunday": "SU"}
_WEEKDAY_INDEX = {d: i for i, d in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}


def services_ics(services: list[dict], site_name: str, host: str, today: datetime) -> str:
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
            f"DTSTART;TZID={TZID}:{_local(start)}", f"DTEND;TZID={TZID}:{_local(end)}",
            f"RRULE:FREQ=WEEKLY;BYDAY={_BYDAY[slot['dayOfWeek']]}", f"SUMMARY:{_esc(s['name'] + ' — ' + site_name)}", "END:VEVENT",
        ]
        body.append("\r\n".join(_fold(x) for x in lines) + "\r\n")
    return calendar(f"{site_name} — weekly services", "".join(body))
