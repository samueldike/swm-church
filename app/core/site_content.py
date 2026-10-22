"""Editable public-site content: service times, visit information, what the church teaches, contact channels.

Everything here is stored as plain rows in ``site_settings`` (Admin -> Settings -> Website content) so an administrator
can change it without a deploy. Defaults are drawn only from what the ministry has already published on its own pages;
anything the church has not told us (parking, dress, children's ministry, history...) is left empty and simply not shown.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

# ── service times ─────────────────────────────────────────────────────────
DEFAULT_SERVICES: list[dict[str, Any]] = [
    {"day": "Sunday", "name": "First Service", "time": "7:00 — 9:30 am", "lead": True},
    {"day": "Sunday", "name": "Second Service", "time": "9:30 am — 12:00 pm", "lead": True},
    {"day": "Monday", "name": "Prayers", "time": "4:40 — 6:00 pm", "lead": False},
    {"day": "Wednesday", "name": "Midweek Bible Study", "time": "5:00 pm", "lead": False},
    {"day": "Saturday", "name": "Preservice Prayer", "time": "5:00 — 6:00 pm", "lead": False},
    {"day": "Last Friday", "name": "Monthly Vigil", "time": "from 9:00 pm", "lead": False},
]

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _loads(raw: str | None, kind: type) -> Any:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, kind) else None


def get_services(site: dict[str, str]) -> list[dict[str, Any]]:
    """The weekly rhythm, from Settings when saved, else the published defaults."""
    rows = _loads(site.get("serviceTimes"), list)
    if rows is None:
        return [dict(s) for s in DEFAULT_SERVICES]
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        day, name, time = (str(r.get(k) or "").strip()[:60] for k in ("day", "name", "time"))
        if day and name:
            out.append({"day": day, "name": name, "time": time, "lead": bool(r.get("lead"))})
    return out


def services_to_form(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return services or [{"day": "", "name": "", "time": "", "lead": False}]


def services_from_form(days: list[str], names: list[str], times: list[str], leads: list[str]) -> str:
    """Rebuild the JSON list from the parallel form arrays (blank rows dropped)."""
    rows = []
    for i, (d, n, t) in enumerate(zip(days, names, times)):
        d, n, t = d.strip()[:60], n.strip()[:60], t.strip()[:60]
        if d and n:
            rows.append({"day": d, "name": n, "time": t, "lead": (leads[i] if i < len(leads) else "0") == "1"})
    return json.dumps(rows)


def _to_24h(h: int, m: int, ampm: str | None) -> tuple[int, int]:
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return h, m


_TIME_RANGE = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[—–\-]\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*$", re.I)


def parse_slot(day: str, time: str) -> dict[str, str] | None:
    """``("Sunday", "7:00 — 9:30 am")`` -> schema.org dayOfWeek / opens / closes, or None when it cannot be read
    reliably (a single start time, "from 9:00 pm", a monthly slot). Never guessed."""
    if day.strip().title() not in _WEEKDAYS:
        return None
    m = _TIME_RANGE.match(time or "")
    if not m:
        return None
    h1, m1, ap1, h2, m2, ap2 = m.groups()
    ap2 = ap2.lower()
    ap1 = (ap1 or "").lower() or None
    eh, em = _to_24h(int(h2), int(m2 or 0), ap2)
    sh, sm = _to_24h(int(h1), int(m1 or 0), ap1 or ap2)
    if not ap1 and (sh, sm) > (eh, em):            # "11:00 — 1:00 pm": the start is in the morning
        sh, sm = _to_24h(int(h1), int(m1 or 0), "am")
    if (sh, sm) >= (eh, em) or not (0 <= sh < 24 and 0 <= eh < 24):
        return None
    return {"dayOfWeek": day.strip().title(), "opens": f"{sh:02d}:{sm:02d}", "closes": f"{eh:02d}:{em:02d}"}


# ── visit information ─────────────────────────────────────────────────────
DEFAULT_EXPECT = [
    ("A warm welcome", "You will be greeted at the door and shown to a seat. Nobody is singled out because it is their first time."),
    ("Worship, prayer and the Word", "Services bring together worship, prayer and teaching from God’s Word. Come as you are."),
    ("Afterwards", "Stay and speak to someone from the team, ask for prayer, or tell us you were here so we can follow up."),
]

VISIT_KEYS = ("visitParking", "visitDress", "visitChildren", "visitAccessibility", "visitNote")


def get_expect(site: dict[str, str]) -> list[tuple[str, str]]:
    rows = _loads(site.get("visitExpect"), list)
    if rows is None:
        return list(DEFAULT_EXPECT)
    return [(str(r.get("title") or "").strip(), str(r.get("text") or "").strip()) for r in rows if isinstance(r, dict) and (r.get("title") or r.get("text"))]


# ── what the ministry teaches ─────────────────────────────────────────────
# Taken from the ministry’s own published wording (About page, pastor’s profile). A full statement of faith is the
# church’s to write: Settings -> Website content lets the administrator replace this list.
DEFAULT_TEACHING = [
    ("The Word of God", "We teach the Word of Faith and demonstrate the Spirit and power, that men may be repositioned for God’s very best."),
    ("The grace of God", "We teach and focus only on the grace of God."),
    ("Hope and restoration", "Every dead dream can live again. At the scent of water it will bud — through God’s Word and His Spirit (Job 14:7-9)."),
    ("The Kingdom of God", "We are here to enforce God’s Kingdom on earth and to equip men to enjoy their inheritance in Christ Jesus."),
    ("Destiny and excellence", "We align men with destiny and raise models of excellence."),
]


def get_teaching(site: dict[str, str]) -> list[tuple[str, str]]:
    rows = _loads(site.get("beliefs"), list)
    if rows is None:
        return list(DEFAULT_TEACHING)
    return [(str(r.get("title") or "").strip(), str(r.get("text") or "").strip()) for r in rows if isinstance(r, dict) and (r.get("title") or r.get("text"))]


def pairs_from_form(titles: list[str], texts: list[str], limit: int = 20) -> str:
    rows = [{"title": t.strip()[:120], "text": x.strip()[:1200]} for t, x in zip(titles, texts) if t.strip() or x.strip()]
    return json.dumps(rows[:limit])


# ── contact channels ──────────────────────────────────────────────────────
_PLACEHOLDER_PHONE = re.compile(r"\(\s*555\s*\)|x{3,}", re.I)          # "(555)" area codes are fiction; Nigerian formats never use brackets


def clean_phone(value: str | None) -> str:
    """A phone number safe to publish: empty when it is obviously a placeholder (e.g. ``+234 (555) 123-4567``)."""
    v = (value or "").strip()
    digits = re.sub(r"\D", "", v)
    local = digits[3:] if digits.startswith("234") else digits
    if len(digits) < 7 or _PLACEHOLDER_PHONE.search(v) or re.fullmatch(r"(\d)\1+", local.lstrip("0") or "0") or local.count("0") >= 8:
        return ""
    return v


def tel_href(value: str) -> str:
    return "tel:" + re.sub(r"[^\d+]", "", value or "")


def whatsapp_chat_url(number: str | None, message: str = "", country: str | None = None) -> str:
    """wa.me link from a phone number. A local number (0803…, 07911…) is read in ``country`` (the
    church's own; HQ's by default); "+44…"/"00 44…" work anywhere. No link for a non-number."""
    from app.core import geo

    raw = (number or "").strip()
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    digits = geo.whatsapp_digits(raw, country or geo.DEFAULT_COUNTRY)
    if not digits:
        return ""
    return f"https://wa.me/{digits}" + (f"?text={quote(message)}" if message else "")


def safe_https(url: str | None) -> str:
    u = (url or "").strip()
    return u if re.match(r"^https?://[^\s<>\"']+$", u, re.I) else ""


def channels(site: dict[str, str], greeting: str = "", country: str | None = None) -> dict[str, str]:
    return {
        "whatsappChat": whatsapp_chat_url(site.get("whatsappNumber"), greeting or "Hello, I found you on your website.", country),
        "whatsappChannel": safe_https(site.get("whatsappChannelUrl")),
        "telegramJoin": safe_https(site.get("telegramJoinUrl")) or safe_https(site.get("socialTelegram")),
    }


# ── structured data ───────────────────────────────────────────────────────
def church_jsonld(*, name: str, url: str, description: str, address: str, phone: str, email: str,
                  services: list[dict[str, Any]], same_as: list[str], image: str, country: str = "NG") -> dict:
    data: dict[str, Any] = {"@context": "https://schema.org", "@type": "Church", "name": name, "url": url, "description": description}
    if image:
        data["image"] = image
        data["logo"] = image
    if address:
        data["address"] = {"@type": "PostalAddress", "streetAddress": address, "addressCountry": country}
    if phone:
        data["telephone"] = phone
    if email:
        data["email"] = email
    hours = []
    for s in services:
        slot = parse_slot(s.get("day", ""), s.get("time", ""))
        if slot:
            hours.append({"@type": "OpeningHoursSpecification", **slot})
    if hours:
        data["openingHoursSpecification"] = hours
    if same_as:
        data["sameAs"] = same_as
    return data


def jsonld_script(data: dict | list) -> str:
    """Safe to embed in a <script type="application/ld+json"> block (``</`` and ``<!--`` are escaped)."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


# ── prayer ────────────────────────────────────────────────────────────────
DEFAULT_PRAYER_RESPONSE = "three working days"   # shown as "…you can expect to hear from us within <this>"; editable in Settings
