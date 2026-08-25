from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import init_db

    await init_db()
    await _seed_defaults()
    yield


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


_base_dir = os.path.dirname(os.path.abspath(__file__))
_static_dir = os.path.join(_base_dir, "static")
_media_dir = os.path.join(_base_dir, "media")

os.makedirs(_static_dir, exist_ok=True)
os.makedirs(_media_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=_static_dir), name="static")
app.mount(
    "/images", StaticFiles(directory=os.path.join(_static_dir, "images")), name="images"
)
app.mount("/media", StaticFiles(directory=_media_dir), name="media")

from app.routes.public import router as public_router

app.include_router(public_router)

try:
    from app.routes.admin import router as admin_router

    app.include_router(admin_router, prefix="/admin")
except ImportError as e:
    import logging

    logging.error("Failed to load admin routes: %s", e)

try:
    from app.routes.api import router as api_router

    app.include_router(api_router)
except ImportError as e:
    import logging

    logging.error("Failed to load API routes: %s", e)


async def _seed_defaults():
    from app.database import async_session

    try:
        from app.models.site_settings import SiteSetting
        from sqlalchemy import select, func

        async with async_session() as db:
            count_result = await db.execute(
                select(func.count()).select_from(SiteSetting)
            )
            count = count_result.scalar_one() or 0

            if count == 0:
                defaults = {
                    "siteName": "Scent of Water Ministry",
                    "siteTagline": "Dominion By Knowledge",
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
