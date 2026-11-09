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

import logging
import os
import re
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select, update

from app.core.access import HOD, RESIDENT_PASTOR, SUPER, BRANCH_ADMIN, normalize_role

logger = logging.getLogger(__name__)

GRANTABLE_ROLES = (HOD, RESIDENT_PASTOR)
WAIT_FOR_CONNECTION = timedelta(hours=6)      # a due post waits this long for a disconnected account


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
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


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
        raise GatewayError(_err(r), r.status_code)
    return r.json()


def _err(r) -> str:
    try:
        return r.json().get("error") or f"WhatsApp service error ({r.status_code})"
    except Exception:
        return f"WhatsApp service error ({r.status_code})"


# ─── scheduled posts ───
async def deliver(post) -> tuple[str, dict, str | None]:
    """Send one post now. Returns (status, per-target result, error)."""
    sid = tenant(post.branch_id)
    if post.kind == "status":
        kind = post.media_kind if post.media_kind in ("image", "video") else "text"
        await call("POST", f"/sessions/{sid}/status", json={"kind": kind, "text": post.text or "", "file": post.media_path, "caption": post.text or ""}, timeout=240)
        return "sent", {"status": "ok"}, None
    results, failed = {}, 0
    for t in post.targets or []:
        jid = t.get("jid") if isinstance(t, dict) else str(t)
        try:
            await call("POST", f"/sessions/{sid}/send", json={"to": jid, "text": post.text or "", "file": post.media_path,
                                                             "kind": post.media_kind, "filename": post.media_name}, timeout=180)
            results[jid] = "ok"
        except GatewayError as e:
            if e.status in (409, 503):
                raise
            results[jid] = str(e)[:300]
            failed += 1
    if not results:
        return "failed", results, "No recipients were chosen."
    if failed == 0:
        return "sent", results, None
    return ("partial" if failed < len(results) else "failed"), results, f"{failed} of {len(results)} could not be sent."


async def process_due(now: datetime | None = None) -> int:
    """Send every post whose time has come (called by app/core/scheduler.py). Rows are claimed atomically."""
    from app.database import async_session
    from app.models.whatsapp import WhatsAppPost

    if not configured():
        return 0
    now = now or datetime.utcnow()
    done = 0
    async with async_session() as db:
        ids = [i for (i,) in (await db.execute(select(WhatsAppPost.id).where(WhatsAppPost.status == "pending", WhatsAppPost.send_at <= now).order_by(WhatsAppPost.send_at).limit(20))).all()]
    for pid in ids:
        async with async_session() as db:
            claimed = await db.execute(update(WhatsAppPost).where(WhatsAppPost.id == pid, WhatsAppPost.status == "pending").values(status="sending"))
            await db.commit()
            if claimed.rowcount != 1:
                continue
            post = (await db.execute(select(WhatsAppPost).where(WhatsAppPost.id == pid))).scalar_one()
            try:
                status, result, error = await deliver(post)
            except GatewayError as e:
                if e.status in (409, 503) and now - post.send_at < WAIT_FOR_CONNECTION:
                    post.status, post.error = "pending", f"Waiting: {e}"           # retried on the next tick
                else:
                    post.status, post.error = "failed", str(e)[:500]
                await db.commit()
                continue
            except Exception as e:  # never let one post stop the loop
                logger.exception("whatsapp post %s failed", pid)
                status, result, error = "failed", {}, str(e)[:500]
            post.status, post.result, post.error = status, result, error
            post.sent_at = datetime.utcnow()
            await db.commit()
            done += 1
    return done
