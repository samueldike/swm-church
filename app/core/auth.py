from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.database import async_session


async def _load_admin(admin_id: int):
    from app.models.admin import Admin

    async with async_session() as db:
        result = await db.execute(
            select(Admin)
            .where(Admin.id == admin_id)
            .options(
                selectinload(Admin.branch),
                selectinload(Admin.department),
            )
        )
        return result.scalar_one_or_none()


async def get_current_admin(request: Request):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return None
    if not request.session.get("admin_authenticated"):
        return None

    last_activity = request.session.get("admin_last_activity_at")
    if last_activity:
        if isinstance(last_activity, str):
            last_activity = datetime.fromisoformat(last_activity)
        try:
            from app.core.settings_cache import get_setting
            from app.database import async_session as _sess

            timeout_min = 30
            async with _sess() as _db:
                val = await get_setting(_db, "admin_session_timeout_minutes", "30")
                timeout_min = int(val) if str(val).isdigit() else 30
        except Exception:
            timeout_min = 30
        if (
            datetime.now(timezone.utc) - last_activity
        ).total_seconds() > timeout_min * 60:
            logout_admin(request)
            return None
        request.session["admin_last_activity_at"] = datetime.now(
            timezone.utc
        ).isoformat()

    admin = await _load_admin(admin_id)
    if not admin or not admin.is_active:
        logout_admin(request)                 # deleted or switched off: an open session must not survive it
        return None
    if (admin.session_version or 0) != request.session.get("admin_sv", 0):
        logout_admin(request)                 # password reset / access changed since this session began
        return None
    if admin.branch is not None and not admin.branch.is_active and not admin.branch.is_hq:
        logout_admin(request)                 # a closed branch's people cannot use the console
        return None

    return admin


async def require_admin(request: Request):
    admin = await get_current_admin(request)
    if admin is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Authentication required")
    return admin


async def require_super_admin(request: Request):
    admin = await require_admin(request)
    if admin.role != "super_admin":
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Super admin access required")
    return admin


def login_admin(request: Request, admin_id: int, session_version: int = 0) -> None:
    request.session["admin_id"] = admin_id
    request.session["admin_sv"] = session_version or 0
    request.session["admin_authenticated"] = True
    request.session["admin_last_activity_at"] = datetime.now(timezone.utc).isoformat()


def logout_admin(request: Request) -> None:
    request.session.clear()
