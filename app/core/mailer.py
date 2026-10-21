"""Transactional and bulk email through Resend (https://resend.com).

* The API key, sender address and sender name come from Admin -> Settings -> Email delivery (key encrypted at rest);
  ``.env`` is never needed. With no key saved nothing is sent and the attempt is logged as "skipped".
* Every attempt is written to ``email_logs`` (recipient, subject, kind, outcome) so an administrator can see what left
  the system. Bodies are not stored.
* Sending never raises into a page: callers use :func:`fire` / :func:`send_email` and carry on.
* Reserved / test domains (example.com, *.test, *.invalid, *.local ...) are never emailed, so tests and crawlers cannot
  mail real people or burn the provider quota.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any, Awaitable

import httpx

from app.database import async_session

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
_EMAIL_RX = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]{2,}$")
_RESERVED_HOSTS = {"example.com", "example.org", "example.net", "localhost"}
_RESERVED_SUFFIXES = (".test", ".invalid", ".local", ".localhost", ".example")
_tasks: set[asyncio.Task] = set()


def valid_email(value: str | None) -> bool:
    v = (value or "").strip()
    return bool(v) and len(v) <= 255 and bool(_EMAIL_RX.match(v))


def is_reserved(address: str) -> bool:
    host = address.rsplit("@", 1)[-1].lower()
    return host in _RESERVED_HOSTS or host.endswith(_RESERVED_SUFFIXES)


class MailConfig:
    def __init__(self, api_key: str, from_email: str, from_name: str):
        self.api_key, self.from_email, self.from_name = api_key, from_email, from_name

    @property
    def sender(self) -> str:
        name = self.from_name.replace('"', "").replace("<", "").replace(">", "").strip()
        return f"{name} <{self.from_email}>" if name else self.from_email


async def get_config() -> MailConfig | None:
    from app.core.settings_cache import get_settings

    async with async_session() as db:
        s = await get_settings(db)
    key = (s.get("resend_api_key") or "").strip()
    frm = (s.get("resend_from_email") or "").strip()
    if not key or not valid_email(frm):
        return None
    return MailConfig(key, frm, (s.get("resend_from_name") or s.get("siteName") or "").strip())


async def _log(to: str, subject: str, kind: str, status: str, error: str | None = None, provider_id: str | None = None) -> None:
    from app.models.engagement import EmailLog

    try:
        async with async_session() as db:
            db.add(EmailLog(to_email=to[:255], subject=subject[:255], kind=kind[:30], status=status, error=(error or "")[:500] or None, provider_id=provider_id))
            await db.commit()
    except Exception:
        logger.exception("could not write email log")


async def send_email(to: str, subject: str, html: str, text: str | None = None, *, kind: str = "general",
                     reply_to: str | None = None, headers: dict[str, str] | None = None,
                     cfg: MailConfig | None = None, log: bool = True) -> tuple[bool, str]:
    """Send one email. Returns ``(ok, message)``; never raises."""
    to = (to or "").strip()
    subject = (subject or "").strip()[:255]
    try:
        if not valid_email(to):
            return False, "That is not a valid email address."
        if is_reserved(to) or os.environ.get("SWM_EMAIL_DRY_RUN") == "1":
            if log:
                await _log(to, subject, kind, "skipped", "reserved test address / dry run")
            return False, "Skipped (test address)."
        cfg = cfg or await get_config()
        if cfg is None:
            if log:
                await _log(to, subject, kind, "skipped", "email delivery is not configured")
            return False, "Email delivery is not configured (Admin → Settings → Email delivery)."
        payload: dict[str, Any] = {"from": cfg.sender, "to": [to], "subject": subject, "html": html}
        if text:
            payload["text"] = text
        if reply_to and valid_email(reply_to):
            payload["reply_to"] = reply_to
        if headers:
            payload["headers"] = headers
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(RESEND_URL, json=payload, headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"})
        data = {}
        try:
            data = r.json()
        except Exception:
            pass
        if r.status_code in (200, 201):
            if log:
                await _log(to, subject, kind, "sent", None, str(data.get("id") or "")[:100] or None)
            return True, "Sent."
        msg = str(data.get("message") or data.get("error") or r.text or f"HTTP {r.status_code}")[:300]
        if r.status_code in (401, 403):
            msg = "The provider rejected the API key or the sender address (is the domain verified in Resend?). " + msg
        if log:
            await _log(to, subject, kind, "failed", msg)
        return False, msg
    except Exception as exc:                                  # network, JSON, anything
        logger.exception("email send failed")
        if log:
            await _log(to, subject, kind, "failed", f"{type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"


def fire(coro: Awaitable) -> None:
    """Run ``coro`` in the background without ever letting it break the request that started it."""
    async def _guard():
        try:
            await asyncio.sleep(0.4)        # let the request that scheduled this commit its own transaction first
            await coro
        except Exception:
            logger.exception("background email task failed")

    try:
        task = asyncio.get_running_loop().create_task(_guard())
    except RuntimeError:                                      # no running loop (script / test): drop quietly
        if hasattr(coro, "close"):
            coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── rendering ─────────────────────────────────────────────────────────────
def render_email(template: str, **ctx: Any) -> tuple[str, str]:
    """Render ``emails/<template>.html`` and derive a plain-text alternative from it."""
    from app.core.template import _env

    html = _env.get_template(f"emails/{template}.html").render(**ctx)
    return html, html_to_text(html)


def html_to_text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style|head).*?</\1>", "", html)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</h[1-6]>|</tr>|</li>", "\n", t)
    t = re.sub(r"(?i)<a [^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", lambda m: f"{re.sub('<[^>]+>', '', m.group(2)).strip()} ({m.group(1)})" if m.group(2).strip() and not m.group(1).startswith("mailto:") else re.sub("<[^>]+>", "", m.group(2)), t)
    t = re.sub(r"<[^>]+>", "", t)
    import html as _h

    t = _h.unescape(t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip() + "\n"


async def site_context(base_url: str) -> dict[str, Any]:
    """Facts every email template needs (church name, address, contact, absolute base URL)."""
    from app.core.settings_cache import get_settings
    from app.core.site_content import clean_phone

    async with async_session() as db:
        s = await get_settings(db)
    return {
        "siteName": s.get("siteName") or "Scent of Water Ministry",
        "siteTagline": s.get("siteTagline") or "",
        "siteAddress": s.get("siteHeadquartersAddress") or "",
        "sitePhone": clean_phone(s.get("siteSupportPhone")),
        "siteEmail": s.get("siteContactEmail") or "",
        "siteUrl": base_url.rstrip("/"),
        "settings": s,
    }


async def send_notice(to: str, subject: str, *, base_url: str, kind: str, heading: str, paragraphs: list[str],
                      button: tuple[str, str] | None = None, details: list[tuple[str, str]] | None = None,
                      footnote: str = "", unsubscribe_url: str = "", reply_to: str | None = None) -> tuple[bool, str]:
    """A simple, consistent transactional email (heading, paragraphs, optional details table and button)."""
    ctx = await site_context(base_url)
    ctx.update(heading=heading, paragraphs=paragraphs, button=button, details=details or [], footnote=footnote, unsubscribe_url=unsubscribe_url, preheader=paragraphs[0] if paragraphs else "")
    html, text = render_email("notice", **ctx)
    headers = None
    if unsubscribe_url:
        headers = {"List-Unsubscribe": f"<{unsubscribe_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    return await send_email(to, subject, html, text, kind=kind, reply_to=reply_to, headers=headers)


async def notify_staff(branch_id: int | None, subject: str, *, base_url: str, kind: str, heading: str, paragraphs: list[str], link_path: str = "/admin") -> int:
    """Tell the people who handle this church's inbox that something is waiting. The email carries no personal
    content (a prayer request is never emailed to staff): it only says that something is waiting and links to the console."""
    from sqlalchemy import select

    from app.core.hq_branch import get_hq_branch_id
    from app.models.admin import Admin

    async with async_session() as db:
        hq_id = await get_hq_branch_id(db)
        church = branch_id or hq_id
        roles = ["branch_admin", "admin", "branch_super_admin"] + (["hq_pastoral", "super_admin"] if church == hq_id else [])
        rows = (await db.execute(select(Admin.email).where(Admin.is_active == True, Admin.branch_id == church, Admin.role.in_(roles)))).scalars().all()  # noqa: E712
    n = 0
    for email in sorted({e.strip().lower() for e in rows if e}):
        ok, _ = await send_notice(email, subject, base_url=base_url, kind=kind, heading=heading, paragraphs=paragraphs, button=("Open the console", base_url.rstrip("/") + link_path))
        n += 1 if ok else 0
    return n
