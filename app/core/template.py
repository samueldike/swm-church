from __future__ import annotations

import os
from typing import Any

from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request
from starlette.responses import HTMLResponse

_template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "views")
_env = Environment(
    loader=FileSystemLoader(_template_dir),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
    cache_size=0,
    auto_reload=True,
)


def _nl2br(value: str) -> str:
    return value.replace("\n", "<br>\n") if value else ""


def _timesince(value) -> str:
    if not value:
        return ""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    if hasattr(value, "tzinfo") and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    diff = now - value
    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    months = days / 30
    if months < 12:
        return f"{int(months)}mo ago"
    years = months / 12
    return f"{int(years)}y ago"


_env.filters["nl2br"] = _nl2br
_env.filters["timesince"] = _timesince


class TemplateRequest:
    def __init__(self, request: Request, form_data: dict | None = None):
        self._request = request
        self.session = request.session
        self.client = request.client
        self.headers = request.headers
        self.url = request.url
        self.query_params = request.query_params
        self.method = request.method
        self.path = request.url.path
        self._form_data = _FormProxy(form_data or {})

    @property
    def form(self):
        return self._form_data


class _FormProxy:
    def __init__(self, data: dict):
        self._data: dict = data

    def get(self, key: str, default: Any = "") -> Any:
        return self._data.get(key, default)

    def getlist(self, key: str) -> list:
        val = self._data.get(key, [])
        if isinstance(val, list):
            return val
        return [val] if val else []

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


async def render_template(
    request: Request,
    template_name: str,
    context: dict[str, Any] | None = None,
) -> HTMLResponse:
    ctx: dict[str, Any] = dict(context) if context else {}

    db = ctx.pop("_db", None)

    from app.core.settings_cache import get_settings

    if db is not None:
        site = await get_settings(db)
    else:
        site = {}

    ctx.setdefault("siteName", site.get("siteName", "Scent of Water Ministry"))
    ctx.setdefault("siteTagline", site.get("siteTagline", "Repositioning men for God's best"))
    ctx.setdefault("siteHeadquartersAddress", site.get("siteHeadquartersAddress", ""))
    ctx.setdefault("siteSupportPhone", site.get("siteSupportPhone", ""))
    ctx.setdefault("siteContactEmail", site.get("siteContactEmail", ""))
    ctx.setdefault("defaultPageHero", site.get("defaultPageHero", "/images/general-page-header.jpg"))
    _map_embed_url = (site.get("mapEmbedUrl") or "").strip()
    if not _map_embed_url and ctx.get("siteHeadquartersAddress"):
        from urllib.parse import quote

        _map_embed_url = f"https://www.google.com/maps?q={quote(ctx['siteHeadquartersAddress'])}&output=embed"
    ctx.setdefault("mapEmbedUrl", _map_embed_url)
    if "settings" not in ctx:
        ctx["settings"] = site
    # `or default`, not `.get(key, default)`: these keys exist in the settings
    # table with empty values, so a plain .get() returns "" and the page renders
    # a blank heading/paragraph instead of falling back.
    for key, default in (
        ("pastorName", "Pastor Samuel Daniel Inyeneabasi"),
        ("pastorTitle", "Senior Pastor"),
        ("pastorPhoto", "images/pastor-samuel-inyeneabasi.png"),
        (
            "pastorBio",
            "Pastor Samuel Daniel Inyeneabasi is a dynamic teacher of God's word, a revivalist, and a visionary "
            "leader. He carries a specific mandate to reposition men for God's very best through the teaching of "
            "the Word of Faith and the demonstration of the Spirit and Power.",
        ),
    ):
        ctx.setdefault(key, (site.get(key) or "").strip() or default)

    # Social + counselling: editable under Admin → Settings, with the
    # ministry's published handles as defaults so nothing renders empty.
    for key, default in (
        ("socialFacebook", "https://www.facebook.com/swmnigeria"),
        ("socialInstagram", "https://www.instagram.com/swm_nigeria/"),
        ("socialX", "https://x.com/swm_nigeria"),
        ("socialYouTube", ""),
        ("socialTelegram", ""),
        ("counsellingPhone", site.get("siteSupportPhone", "") or ""),
        ("counsellingHours", "Monday to Friday, 9:00 am — 5:00 pm"),
    ):
        ctx.setdefault(key, (site.get(key) or default))

    def url_for(route_name: str, *args, **path_params: Any) -> str:
        if route_name == "static":
            path = path_params.get("path", "")
            return f"/static/{path}" if path else "/static"

        normalized = route_name.replace(".", "_").replace("-", "_")

        # Positional args map to path params in declared order — resolved by
        # walking app.routes just for the placeholder names, not to build the
        # final URL (that part is delegated below).
        if args:
            import re

            for route in request.app.routes:
                route_name_norm = str(getattr(route, "name", "")).replace(".", "_").replace("-", "_")
                if route_name_norm == normalized:
                    route_params = re.findall(r"\{(\w+)\}", getattr(route, "path", ""))
                    for i, val in enumerate(args):
                        if i < len(route_params):
                            path_params.setdefault(route_params[i], val)
                    break

        # Delegate to Starlette's own reverse-routing. A hand-rolled walk of
        # request.app.router.routes previously lived here; it broke outright
        # when a Starlette upgrade started wrapping included routers behind
        # an opaque proxy object instead of a flat list, silently degrading
        # every internal link on the page to "/". url_path_for is the stable
        # public API and stays correct regardless of that internal structure.
        for name in (route_name, normalized) if route_name != normalized else (route_name,):
            try:
                return str(request.app.url_path_for(name, **path_params))
            except Exception:
                continue

        import logging

        logging.getLogger(__name__).warning(
            "url_for: no route named %r (params=%s) — link will point to \"/\"",
            route_name, path_params,
        )
        return "/"

    ctx["url_for"] = url_for

    # ── absolute URLs for social cards ────────────────────────────────────
    # Crawlers reject relative og:image/og:url, so everything shared has to be
    # fully qualified. Honour X-Forwarded-Proto so links behind the VPS's TLS
    # terminator advertise https rather than the http the app itself sees.
    _base = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") == "https" and _base.startswith("http://"):
        _base = "https://" + _base[len("http://"):]

    def abs_url(path: str | None) -> str:
        if not path:
            return _base
        path = str(path)
        if path.startswith(("http://", "https://")):
            return path
        return f"{_base}/{path.lstrip('/')}"

    ctx["siteUrl"] = _base
    ctx["absUrl"] = abs_url
    ctx.setdefault("canonicalUrl", abs_url(request.url.path))

    form_data: dict = {}
    if request.method == "POST":
        try:
            body = await request.body()
            if body:
                from urllib.parse import parse_qs

                form_data = {
                    k: v[0] if len(v) == 1 else v
                    for k, v in parse_qs(body.decode()).items()
                }
        except Exception:
            pass
    ctx["request"] = TemplateRequest(request, form_data)
    ctx["session"] = request.session
    ctx["csrf_token"] = request.session.get("csrf_token", "")
    if not ctx["csrf_token"]:
        import secrets

        ctx["csrf_token"] = secrets.token_hex(32)
        request.session["csrf_token"] = ctx["csrf_token"]

    flash_messages = request.session.get("flash_messages", [])
    if "flash_messages" not in ctx:
        ctx["flash_messages"] = list(flash_messages)
    else:
        ctx["flash_messages"] = list(flash_messages) + list(ctx["flash_messages"])
    for _k in ("success", "error", "status", "info"):
        if _k in request.session:
            if not any(
                m.get("message") == request.session[_k] for m in ctx["flash_messages"]
            ):
                ctx["flash_messages"].append(
                    {"type": _k, "message": request.session[_k]}
                )

    ctx["current_page"] = request.url.path
    ctx.setdefault("error_messages", {})
    ctx.setdefault("errors", {})
    ctx.setdefault("now", lambda: __import__("datetime").datetime.utcnow())

    template = _env.get_template(template_name)
    html = template.render(ctx)
    request.session.pop("flash_messages", None)
    for _k in ("success", "error", "status", "info"):
        request.session.pop(_k, None)
    response = HTMLResponse(content=html)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response
