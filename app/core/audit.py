from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request


async def log_admin_action(
    db: AsyncSession,
    admin_id: int,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    if not admin_id:
        return

    from app.models.admin import AdminAuditLog

    ip_address = None
    user_agent = None
    if request:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

    log_entry = AdminAuditLog(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        extra_data=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(log_entry)
    await db.flush()
