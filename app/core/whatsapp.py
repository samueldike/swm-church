"""WhatsApp: who may use it, which church's account they reach, the gateway client and scheduled posts.

Each church (HQ or a branch) links its own WhatsApp account; the session lives in the gateway container
(whatsapp-gateway/, WPPConnect) under the tenant id ``b<branch id>`` and stays connected until someone presses
Disconnect.

Access
------
* HQ Administrator: every church's WhatsApp (picks the church).
* The HOD of a Media department: their own church's WhatsApp, by default.
* Any HOD or Resident Pastor: when granted (``Admin.whatsapp_access = "grant"``); ``"deny"`` removes the
  Media HOD's default. Grants are set by the HQ Administrator, or by a branch admin for their own branch.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select, update

from app.core.access import HOD, RESIDENT_PASTOR, SUPER, BRANCH_ADMIN, normalize_role

logger = logging.getLogger(__name__)

GRANTABLE_ROLES = (HOD, RESIDENT_PASTOR)
WAIT_FOR_CONNECTION = timedelta(hours=6)      # a due post waits this long for a disconnected account

# ─── anti-ban rules for Post to many (the gateway adds hard per-account limits: whatsapp-gateway/src/guard.js) ───
MAX_TARGETS = 50              # recipients in one post
MAX_PEOPLE = 25               # of which individual people (groups and channels reach many with one message)
PER_TICK = 2                  # recipients sent per scheduler round (every ~20 s) ...
GAP_SECONDS = (8, 20)         # ... with a random human-like pause between them
NAME_TOKEN = "{name}"


def is_media_department(name: str | None) -> bool:
    return bool(name) and re.search(r"\bmedia\b", name, re.I) is not None


def default_access(role: str | None, department_name: str | None) -> bool:
    role = normalize_role(role)
    return role == SUPER or (role == HOD and is_media_department(department_name))


def allowed(role: str | None, override: str | None, department_name: str | None) -> bool:
    """Pure rule (tested in tests/test_whatsapp.py)."""
    role = normalize_role(role)
    if role == SUPER:
        return True
    if role not in GRANTABLE_ROLES:
        return False
    if override == "grant":
        return True
    if override == "deny":
        return False
    return default_access(role, department_name)


async def department_name(db, admin) -> str | None:
    if not getattr(admin, "department_id", None):
        return None
    from app.models.department import Department

    return (await db.execute(select(Department.name).where(Department.id == admin.department_id))).scalar_one_or_none()


async def can_use(db, admin) -> bool:
    if admin is None:
        return False
    return allowed(admin.role, getattr(admin, "whatsapp_access", None), await department_name(db, admin))


def can_grant(granter, target) -> bool:
    """May ``granter`` switch WhatsApp access on/off for ``target``?"""
    if granter is None or target is None or normalize_role(target.role) not in GRANTABLE_ROLES:
        return False
    role = normalize_role(granter.role)
    return role == SUPER or (role == BRANCH_ADMIN and granter.branch_id is not None and granter.branch_id == target.branch_id)


async def church_for(db, admin, requested: int | None = None) -> int:
    """The church whose WhatsApp this admin works with. Only the HQ Administrator may choose another."""
    from app.core.hq_branch import get_hq_branch_id

    if normalize_role(admin.role) == SUPER and requested:
        from app.models.branch import Branch

        if (await db.execute(select(Branch.id).where(Branch.id == requested))).scalar_one_or_none():
            return requested
    if normalize_role(admin.role) != SUPER and admin.branch_id:
        return admin.branch_id
    return await get_hq_branch_id(db)


def tenant(branch_id: int) -> str:
    return f"b{int(branch_id)}"


# ─── gateway client ───
class GatewayError(Exception):
    def __init__(self, message: str, status: int = 502, retry_at: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_at = retry_at


def _base() -> str:
    return (os.environ.get("WA_GATEWAY_URL") or "http://whatsapp:4100").rstrip("/")


def configured() -> bool:
    return bool(os.environ.get("WA_GATEWAY_TOKEN"))


async def call(method: str, path: str, *, json: dict | None = None, params: dict | None = None, timeout: float = 30.0, raw: bool = False):
    if not configured():
        raise GatewayError("The WhatsApp service is not set up on this server yet (WA_GATEWAY_TOKEN is missing).", 503)
    headers = {"Authorization": f"Bearer {os.environ['WA_GATEWAY_TOKEN']}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, _base() + path, json=json, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise GatewayError("The WhatsApp service is not reachable right now. Try again in a minute.", 503) from e
    if raw:
        if r.status_code >= 400:
            raise GatewayError(_err(r), r.status_code)
        return r
    if r.status_code >= 400:
        retry = None
        try:
            retry = r.json().get("retryAt")
        except Exception:
            pass
        raise GatewayError(_err(r), r.status_code, retry)
    return r.json()


def _err(r) -> str:
    try:
        return r.json().get("error") or f"WhatsApp service error ({r.status_code})"
    except Exception:
        return f"WhatsApp service error ({r.status_code})"


# ─── scheduled posts ───
def personalise(text: str, name: str | None) -> str:
    """{name} becomes the recipient's first name (or nothing): every person gets a slightly different message."""
    if NAME_TOKEN not in (text or ""):
        return text or ""
    clean = (name or "").strip()
    first = "" if not clean or re.fullmatch(r"[+\d\s()-]+", clean) else clean.lstrip("~+ ").split(" ")[0]
    out = (text or "").replace(NAME_TOKEN, first)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"[ \t]+([,!.?])", r"\1", out)


def is_person(jid: str) -> bool:
    return jid.endswith("@c.us") or jid.endswith("@s.whatsapp.net") or jid.endswith("@lid")


async def _send_status(post) -> tuple[str, dict, str | None]:
    sid = tenant(post.branch_id)
    kind = post.media_kind if post.media_kind in ("image", "video") else "text"
    await call("POST", f"/sessions/{sid}/status", json={"kind": kind, "text": post.text or "", "file": post.media_path, "caption": post.text or ""}, timeout=240)
    return "sent", {"status": "ok"}, None


def _jid(t) -> str:
    return t.get("jid") if isinstance(t, dict) else str(t)


def _final(results: dict, failed: int) -> tuple[str, dict, str | None]:
    if not results:
        return "failed", results, "No recipients were chosen."
    if failed == 0:
        return "sent", results, None
    return ("partial" if failed < len(results) else "failed"), results, f"{failed} of {len(results)} could not be sent."


async def process_due(now: datetime | None = None) -> int:
    """Send due posts gradually (called by app/core/scheduler.py every ~20 s).

    Anti-ban: a message post sends at most PER_TICK recipients per round with a random pause between them,
    then waits (next_at) before the next ones; it goes out whenever the sender chose (day or night), and when
    the gateway says a safety limit is reached it waits until that limit frees up. Recipients already done are never sent
    again. Rows are locked (locked_until) so several workers never send the same post twice.
    """
    from sqlalchemy import or_

    from app.database import async_session
    from app.models.whatsapp import WhatsAppPost

    if not configured():
        return 0
    now = now or datetime.utcnow()
    done = 0
    async with async_session() as db:
        ids = [i for (i,) in (await db.execute(
            select(WhatsAppPost.id).where(
                or_(
                    (WhatsAppPost.status == "pending") & (WhatsAppPost.send_at <= now),
                    (WhatsAppPost.status == "sending") & or_(WhatsAppPost.next_at.is_(None), WhatsAppPost.next_at <= now),
                ),
                or_(WhatsAppPost.locked_until.is_(None), WhatsAppPost.locked_until < now),
            ).order_by(WhatsAppPost.send_at).limit(10))).all()]
    for pid in ids:
        async with async_session() as db:
            claimed = await db.execute(update(WhatsAppPost).where(
                WhatsAppPost.id == pid, WhatsAppPost.status.in_(("pending", "sending")),
                or_(WhatsAppPost.locked_until.is_(None), WhatsAppPost.locked_until < now),
            ).values(status="sending", locked_until=now + timedelta(seconds=120)))
            await db.commit()
            if claimed.rowcount != 1:
                continue
            post = (await db.execute(select(WhatsAppPost).where(WhatsAppPost.id == pid))).scalar_one()
            try:
                finished = await _advance(post, now)
            except Exception as e:  # never let one post stop the loop
                logger.exception("whatsapp post %s failed", pid)
                post.status, post.error, finished = "failed", str(e)[:500], True
            post.locked_until = None
            with db.no_autoflush:      # someone pressed Stop while this round was sending: keep it stopped
                current = (await db.execute(select(WhatsAppPost.status).where(WhatsAppPost.id == pid).execution_options(populate_existing=False))).scalar()
            if current == "cancelled":
                post.status, post.next_at = "cancelled", None
                finished = False
            if finished:
                post.sent_at = datetime.utcnow()
                done += 1
            await db.commit()
    return done


async def _advance(post, now: datetime) -> bool:
    """One round of a post. Returns True when the post is finished (sent / partial / failed)."""
    if post.kind == "status":
        try:
            post.status, post.result, post.error = await _send_status(post)
        except GatewayError as e:
            return _wait_or_fail(post, e, now)
        return True
    sid = tenant(post.branch_id)
    results = dict(post.result or {})
    todo = [t for t in (post.targets or []) if _jid(t) not in results]
    sent_now = 0
    for t in todo[:PER_TICK]:
        jid = _jid(t)
        if sent_now:
            await asyncio.sleep(random.uniform(*GAP_SECONDS))
        try:
            await call("POST", f"/sessions/{sid}/send", json={
                "to": jid, "text": personalise(post.text, t.get("name") if isinstance(t, dict) else ""),
                "file": post.media_path, "kind": post.media_kind, "filename": post.media_name, "bulk": True}, timeout=180)
            results[jid] = "ok"
            sent_now += 1
        except GatewayError as e:
            post.result = results
            return _wait_or_fail(post, e, now)
        except Exception as e:  # one bad recipient never stops the others
            results[jid] = str(e)[:300]
    post.result = results
    if len(results) < len(post.targets or []):
        post.status = "sending"
        post.next_at = now + timedelta(seconds=random.uniform(*GAP_SECONDS))
        post.error = None
        return False
    failed = sum(1 for v in results.values() if v != "ok")
    post.status, post.result, post.error = _final(results, failed)
    return True


def _wait_or_fail(post, e: GatewayError, now: datetime) -> bool:
    """A gateway refusal: wait (safety limit, disconnected) or give up. Returns True if the post is finished."""
    if e.status == 429:                          # anti-ban limit or brake: wait until it frees up
        retry = None
        if e.retry_at:
            try:
                retry = datetime.fromisoformat(str(e.retry_at).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                retry = None
        post.status = "sending"
        post.next_at = max(retry or now, now) + timedelta(seconds=random.uniform(30, 90))
        post.error = f"Paused for safety: {e}"
        return False
    if e.status in (409, 503) and now - post.send_at < WAIT_FOR_CONNECTION:
        post.status = "sending"
        post.next_at = now + timedelta(minutes=2)
        post.error = f"Waiting: {e}"
        return False
    if e.status in (409, 503) or post.kind != "message":
        post.status, post.error = "failed", str(e)[:500]
        return True
    # a refusal for this one recipient (e.g. not on WhatsApp): note it and carry on next round
    results = dict(post.result or {})
    todo = [t for t in (post.targets or []) if _jid(t) not in results]
    if todo:
        results[_jid(todo[0])] = str(e)[:300]
    post.result = results
    if len(results) < len(post.targets or []):
        post.status, post.next_at = "sending", now + timedelta(seconds=random.uniform(*GAP_SECONDS))
        return False
    failed = sum(1 for v in results.values() if v != "ok")
    post.status, post.result, post.error = _final(results, failed)
    return True
