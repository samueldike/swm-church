from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy.exc import IntegrityError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import init_db

    await init_db()
    try:
        from app.database import sync_sequences

        fixed = await sync_sequences()
        if fixed:
            import logging

            logging.getLogger(__name__).warning("Resynced %d out-of-date id sequences", fixed)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Could not resync id sequences")
    await _seed_defaults()
    try:
        from app.core import geo_migration

        await geo_migration.run()       # idempotent: fills empty country/currency/time-zone codes
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Global data backfill failed; the review page lists what is missing")
    from app.core import scheduler

    job_loop = scheduler.start()
    if not settings.is_development and settings.SECRET_KEY in ("", "change-me"):
        import logging

        logging.getLogger(__name__).critical(
            "SECRET_KEY is the default value: admin sessions can be forged and saved API keys are trivially decryptable. "
            "Set a long random SECRET_KEY in .env."
        )
    yield
    await scheduler.stop(job_loop)


app = FastAPI(
    title=settings.APP_NAME,
    docs_url=None if settings.is_development is False else "/docs",
    redoc_url=None if settings.is_development is False else "/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="swm_session",
    max_age=60 * 60 * 24,
    same_site="lax",
    https_only=not settings.is_development,
)


from app.core.analytics import AnalyticsMiddleware

app.add_middleware(AnalyticsMiddleware)

from app.core.upload_headers import UploadHeadersMiddleware

app.add_middleware(UploadHeadersMiddleware)

_base_dir = os.path.dirname(os.path.abspath(__file__))
_static_dir = os.path.join(_base_dir, "static")
# Single-sourced so the app, the Celery worker and the telegram-bot-api
# container all resolve uploads to the same absolute path (see MEDIA_ROOT).
from app.config import settings as _app_settings

_media_dir = _app_settings.media_root

os.makedirs(_static_dir, exist_ok=True)
os.makedirs(_media_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=_static_dir), name="static")
app.mount(
    "/images", StaticFiles(directory=os.path.join(_static_dir, "images")), name="images"
)
app.mount("/media", StaticFiles(directory=_media_dir), name="media")

from app.core.csrf import CSRFError, csrf_protect
from app.core.policy import AccessDenied, PasswordChangeRequired, admin_policy
from app.routes.public import router as public_router

# Every browser form POST/PUT/DELETE must carry the session's csrf_token
# (see app/core/csrf.py). The token-authenticated /api router is exempt.
app.include_router(public_router, dependencies=[Depends(csrf_protect)])

from app.routes.public_extra import open_router as public_open_router, router as public_extra_router

app.include_router(public_extra_router, dependencies=[Depends(csrf_protect)])
app.include_router(public_open_router)      # token-authorised POSTs (unsubscribe) — mail clients cannot send a CSRF token

try:
    from app.routes.admin import router as admin_router

    app.include_router(admin_router, prefix="/admin", dependencies=[Depends(csrf_protect), Depends(admin_policy)])
except ImportError as e:
    import logging

    logging.error("Failed to load admin routes: %s", e)

try:
    from app.routes.api import router as api_router

    app.include_router(api_router)
except ImportError as e:
    import logging

    logging.error("Failed to load API routes: %s", e)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, exc):
    """Safety net: a unique/foreign-key violation we did not pre-check becomes a message, not a 500 page."""
    import logging

    from fastapi.responses import JSONResponse, RedirectResponse

    logging.getLogger(__name__).warning("IntegrityError on %s %s: %s", request.method, request.url.path, str(exc.orig)[:200])
    message = "That change conflicts with an existing record (for example a duplicate name or email). Nothing was saved."
    accept = request.headers.get("accept", "")
    if request.url.path.startswith("/api/") or ("application/json" in accept and "text/html" not in accept):
        return JSONResponse({"error": message}, status_code=409)
    try:
        request.session["flash_messages"] = [{"type": "error", "message": message}]
        request.session["error"] = message
    except Exception:
        pass
    return RedirectResponse(url=request.headers.get("referer") or "/", status_code=303)


@app.exception_handler(AccessDenied)
async def access_denied_handler(request, exc):
    """A signed-in admin asked for something their role does not include."""
    from fastapi.responses import JSONResponse, RedirectResponse

    from app.core.access import home_for

    message = "You do not have access to that page."
    accept = request.headers.get("accept", "")
    if request.url.path.endswith(("/metrics", "/status")) or ("application/json" in accept and "text/html" not in accept):
        return JSONResponse({"error": message}, status_code=403)
    request.session["flash_messages"] = [{"type": "error", "message": message}]
    request.session["error"] = message
    return RedirectResponse(url=home_for(exc.role), status_code=303)


@app.exception_handler(PasswordChangeRequired)
async def password_change_required_handler(request, exc):
    from fastapi.responses import JSONResponse, RedirectResponse

    message = "Set your own password before you continue."
    accept = request.headers.get("accept", "")
    if request.url.path.endswith(("/metrics", "/status")) or ("application/json" in accept and "text/html" not in accept):
        return JSONResponse({"error": message}, status_code=403)
    request.session["flash_messages"] = [{"type": "error", "message": message}]
    return RedirectResponse(url="/admin/profile", status_code=303)


@app.exception_handler(CSRFError)
async def csrf_error_handler(request, exc):
    """Bounce a form with a missing/stale csrf_token back to where it came from."""
    from fastapi.responses import JSONResponse, RedirectResponse

    if "application/json" in request.headers.get("accept", "") and "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"error": "Invalid or missing CSRF token"}, status_code=403)

    message = "Your session expired or the form was invalid. Please try again."
    request.session["flash_messages"] = [{"type": "error", "message": message}]
    request.session["error"] = message
    target = request.headers.get("referer") or "/admin"
    return RedirectResponse(url=target, status_code=303)


async def _seed_defaults():
    from app.database import async_session

    try:
        from app.models.site_settings import SiteSetting
        from sqlalchemy import select, func

        async with async_session() as db:
            from app.core.settings_cache import encrypt_plaintext_secrets

            if await encrypt_plaintext_secrets(db):
                await db.commit()
            # The telegram-bot-api container reads the api_id/api_hash saved in Settings from a shared volume.
            from app.core import telegram_local
            from app.core.settings_cache import get_settings

            telegram_local.sync_credentials(await get_settings(db))
            count_result = await db.execute(
                select(func.count()).select_from(SiteSetting)
            )
            count = count_result.scalar_one() or 0

            if count == 0:
                defaults = {
                    "siteName": "Scent of Water Ministry",
                    "siteTagline": "Repositioning men for God's best",
                    "siteHeadquartersAddress": "#1 Scent Close off Barr. Otta Ewa Road, Beside the Remand Home, Mile 50, Abakaliki, Ebonyi State, Nigeria",
                    "siteSupportPhone": "07037736698",
                    "siteContactEmail": "",
                    "privacy_policy": "",
                    "terms_of_service": "",
                    "telegram_bot_token": "",
                    "telegram_audio_chat_id": "",
                }
                for key, value in defaults.items():
                    db.add(SiteSetting(key=key, value=value))
                await db.commit()
    except Exception as e:
        import logging

        logging.warning("Seed failed: %s", e)
