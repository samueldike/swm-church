"""Response headers for user-uploaded files (/media, /images, /static/images).

Uploads are served from the church's own domain, so a file that a browser renders as a
page (HTML, SVG) would run with the HQ Administrator's session if they opened its link.
``nosniff`` stops content-type guessing, and the sandboxing CSP means a document opened
directly can run no script. Neither header affects <img>/<audio> embedding.

Pure ASGI (not BaseHTTPMiddleware) so audio Range streaming passes through untouched.
"""

from __future__ import annotations

UPLOAD_PREFIXES = ("/media/", "/images/", "/static/images/")
UPLOAD_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"content-security-policy", b"default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; sandbox"),
)


class UploadHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(UPLOAD_PREFIXES):
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [h for h in UPLOAD_HEADERS if h[0] not in names]
            await send(message)

        await self.app(scope, receive, send_with_headers)
