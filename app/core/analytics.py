"""Built-in, privacy-friendly traffic counts.

What is stored (table ``analytics_counters``): per day, how many times each public page was opened, which site the visitor
came from, what kind of device it was, and how many distinct visitors there were. What is NOT stored: IP addresses,
user agents, cookies, or anything that could follow a person from one day to the next. A "distinct visitor" is a hash of
(secret, day, IP, browser) that changes every day and cannot be reversed. Visitors who send Do-Not-Track / Global Privacy
Control are not counted at all. Admin pages, files, bots and API calls are ignored.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from app.config import settings as app_settings

logger = logging.getLogger(__name__)

_BOT = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram|curl|wget|python|httpx|okhttp|monitor|uptime|lighthouse|headless|scrapy|go-http", re.I)
_SKIP_PREFIXES = ("/admin", "/static", "/images", "/media", "/api", "/feed.xml", "/robots.txt", "/sitemap.xml", "/calendar/data",
                  "/unsubscribe", "/reminders", "/favicon", "/.well-known", "/stream", "/download", "/messages/")
_MSG_PAGE = re.compile(r"^/messages/[^/]+$")
_REFERRERS = {"google.": "Google", "bing.": "Bing", "duckduckgo.": "DuckDuckGo", "facebook.": "Facebook", "fb.": "Facebook", "instagram.": "Instagram",
              "t.co": "X (Twitter)", "twitter.": "X (Twitter)", "x.com": "X (Twitter)", "youtube.": "YouTube", "youtu.be": "YouTube",
              "wa.me": "WhatsApp", "whatsapp.": "WhatsApp", "t.me": "Telegram", "telegram.": "Telegram", "linkedin.": "LinkedIn"}

_buffer: dict[tuple[date, str, str], int] = defaultdict(int)
_lock = asyncio.Lock()


def device_of(ua: str) -> str:
    u = (ua or "").lower()
    if "ipad" in u or "tablet" in u or ("android" in u and "mobile" not in u):
        return "tablet"
    if "mobi" in u or "iphone" in u or "android" in u:
        return "mobile"
    return "desktop"


def referrer_of(referer: str, own_host: str) -> str:
    if not referer:
        return "direct"
    host = (urlparse(referer).hostname or "").lower()
    if not host or host == own_host.lower().split(":")[0]:
        return ""                                     # internal navigation: not a source
    for frag, label in _REFERRERS.items():
        if frag in host:
            return label
    return host[:120]


def countable(path: str, status: int, content_type: str, ua: str, headers) -> bool:
    if status != 200 or "text/html" not in (content_type or ""):
        return False
    if path.startswith(_SKIP_PREFIXES) and not _MSG_PAGE.match(path):
        return False
    if not ua or _BOT.search(ua):
        return False
    if headers.get("dnt") == "1" or headers.get("sec-gpc") == "1":
        return False
    return True


def visitor_hash(ip: str, ua: str, day: date) -> str:
    return hashlib.sha256(f"{app_settings.SECRET_KEY}|{day.isoformat()}|{ip}|{ua}".encode()).hexdigest()[:20]


def record(path: str, ua: str, ip: str, referer: str, host: str) -> None:
    """Count one page view in memory (flushed to the database by the scheduler)."""
    today = date.today()
    path = (path.rstrip("/") or "/")[:200]
    _buffer[(today, "page", path)] += 1
    _buffer[(today, "device", device_of(ua))] += 1
    _buffer[(today, "visitor", visitor_hash(ip, ua, today))] += 1
    ref = referrer_of(referer, host)
    if ref:
        _buffer[(today, "ref", ref)] += 1


async def flush() -> int:
    """Write buffered counts to the database (additive upsert). Safe to call at any time."""
    from sqlalchemy import insert

    from app.database import async_session, engine
    from app.models.engagement import AnalyticsCounter

    async with _lock:
        if not _buffer:
            return 0
        batch = dict(_buffer)
        _buffer.clear()
    try:
        if engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dial_insert
        else:
            from sqlalchemy.dialects.sqlite import insert as dial_insert
        rows = [{"day": d, "kind": k, "key": key, "count": n} for (d, k, key), n in batch.items()]
        async with async_session() as db:
            stmt = dial_insert(AnalyticsCounter).values(rows)
            stmt = stmt.on_conflict_do_update(index_elements=["day", "kind", "key"], set_={"count": AnalyticsCounter.count + stmt.excluded.count})
            await db.execute(stmt)
            await db.commit()
        return len(rows)
    except Exception:
        logger.exception("analytics flush failed")
        async with _lock:                                # put the counts back rather than lose them
            for k, n in batch.items():
                _buffer[k] += n
        return 0


class AnalyticsMiddleware:
    """Pure ASGI (no response buffering, so audio streaming and range requests are untouched)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "GET":
            return await self.app(scope, receive, send)
        info = {}

        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                info["status"] = message["status"]
                info["ctype"] = next((v.decode("latin-1") for k, v in message.get("headers", []) if k.lower() == b"content-type"), "")
            await send(message)

        await self.app(scope, receive, wrapped_send)
        try:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            path = scope.get("path", "/")
            ua = headers.get("user-agent", "")
            if countable(path, info.get("status", 0), info.get("ctype", ""), ua, headers):
                fwd = headers.get("x-forwarded-for", "").split(",")[0].strip()
                ip = fwd or (scope.get("client") or ("", 0))[0] or ""
                record(path, ua, ip, headers.get("referer", ""), headers.get("host", ""))
        except Exception:
            logger.exception("analytics record failed")


# ── reporting ─────────────────────────────────────────────────────────────
async def summary(db, days: int = 30) -> dict:
    from sqlalchemy import func, select

    from app.models.engagement import AnalyticsCounter as C

    since = date.today() - timedelta(days=days - 1)
    async def rows(kind, limit=15):
        q = select(C.key, func.sum(C.count)).where(C.kind == kind, C.day >= since).group_by(C.key).order_by(func.sum(C.count).desc()).limit(limit)
        return [(k, int(n)) for k, n in (await db.execute(q)).all()]

    views = int((await db.execute(select(func.coalesce(func.sum(C.count), 0)).where(C.kind == "page", C.day >= since))).scalar() or 0)
    visitors_by_day = {d: int(n) for d, n in (await db.execute(select(C.day, func.count()).where(C.kind == "visitor", C.day >= since).group_by(C.day))).all()}
    views_by_day = {d: int(n) for d, n in (await db.execute(select(C.day, func.sum(C.count)).where(C.kind == "page", C.day >= since).group_by(C.day))).all()}
    series = []
    for i in range(days):
        d = since + timedelta(days=i)
        series.append({"day": d, "views": views_by_day.get(d, 0), "visitors": visitors_by_day.get(d, 0)})
    return {
        "days": days, "views": views, "visitor_days": sum(visitors_by_day.values()),
        "pages": await rows("page", 20), "referrers": await rows("ref", 10), "devices": await rows("device", 5), "series": series,
        "since": since,
    }
