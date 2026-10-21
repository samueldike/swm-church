"""Router-level access policy for the admin console.

The sidebar only *hides* links; this makes the server enforce the same rules for every admin URL, so
a department head cannot open the member list by typing its address and an HQ finance user cannot
reach Settings. Object-level ownership (this record belongs to another branch) is enforced in the
routes with ``app.core.scope``.
"""
from __future__ import annotations

import logging

from starlette.requests import Request

from app.core.access import OPEN_PREFIXES, has_perm, required_permission
from app.core.auth import get_current_admin

logger = logging.getLogger(__name__)


class AccessDenied(Exception):
    def __init__(self, role: str | None, permission: str):
        self.role, self.permission = role, permission


class PasswordChangeRequired(Exception):
    """The signed-in account is using a temporary/default password and must set its own first."""


_PASSWORD_PAGES = ("/admin/profile", "/admin/profile/password")


async def admin_policy(request: Request) -> None:
    path = request.url.path
    if path.startswith(OPEN_PREFIXES) or path.rstrip("/") == "/admin":
        return
    if path.rstrip("/") not in _PASSWORD_PAGES:
        who = await get_current_admin(request)
        if who is not None and getattr(who, "must_change_password", False):
            raise PasswordChangeRequired()
    perm = required_permission(request.method, path)
    if perm is None:
        return
    admin = await get_current_admin(request)
    if admin is None:
        return  # the route itself redirects unauthenticated users to the sign-in page
    if not has_perm(admin.role, perm):
        logger.warning("Access denied: admin=%s role=%s %s %s needs %s", admin.id, admin.role, request.method, path, perm)
        raise AccessDenied(admin.role, perm)
