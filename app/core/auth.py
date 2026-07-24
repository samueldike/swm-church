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
            select(Admin).where(Admin.id == admin_id).options(
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
        if (datetime.now(timezone.utc) - last_activity).total_seconds() > 1800:
            logout_admin(request)
            return None

    admin = await _load_admin(admin_id)
    if not admin:
        logout_admin(request)
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


def login_admin(request: Request, admin_id: int) -> None:
    request.session["admin_id"] = admin_id
    request.session["admin_authenticated"] = True
    request.session["admin_last_activity_at"] = datetime.now(timezone.utc).isoformat()


def logout_admin(request: Request) -> None:
    request.session.pop("admin_id", None)
    request.session.pop("admin_authenticated", None)
    request.session.pop("admin_last_activity_at", None)
