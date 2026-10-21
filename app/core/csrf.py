"""CSRF protection for the browser-facing routers (admin console and public forms).

Attached as a router dependency in app/main.py rather than as ASGI middleware:
the audio upload form can be gigabytes, and a dependency reuses the multipart
parse FastAPI already performs (spooled to disk) instead of buffering the body
a second time.

A request passes when its ``csrf_token`` form field (or ``X-CSRF-Token``
header) matches ``session["csrf_token"]``. Safe methods are never checked.
The token is minted by ``render_template`` when a page is first rendered.
"""

from __future__ import annotations

import secrets

from starlette.requests import Request

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


class CSRFError(Exception):
    """Raised when an unsafe request carries a missing or wrong CSRF token."""


async def csrf_protect(request: Request) -> None:
    if request.method in _SAFE_METHODS:
        return

    expected = request.session.get("csrf_token", "")
    supplied = request.headers.get("x-csrf-token", "")

    if not supplied and request.headers.get("content-type", "").startswith(_FORM_TYPES):
        form = await request.form()
        supplied = str(form.get("csrf_token") or "")

    if not expected or not supplied or not secrets.compare_digest(str(expected), str(supplied)):
        raise CSRFError()
