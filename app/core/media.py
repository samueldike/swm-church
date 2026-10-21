"""Filesystem helpers for uploaded media.

Large audio is handed to the Telegram bot-api server by absolute path rather
than streamed through this process, so the app, the Celery worker and the
telegram-bot-api container must all agree on where uploads live. Everything
resolves through ``settings.media_root`` to keep that single-sourced.
"""

import os

from app.config import settings


def media_root() -> str:
    return settings.media_root


def media_subdir(*parts: str) -> str:
    """Absolute path to a directory under the media root, created if missing."""
    path = os.path.join(settings.media_root, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def to_abspath(stored_path: str | None) -> str | None:
    """Resolve a DB-stored path like ``media/sermons/x.mp3`` to an absolute path.

    Returns ``None`` when the path is empty or no longer on disk.
    """
    if not stored_path:
        return None

    normalised = stored_path.replace("\\", "/")

    # "/media/x.mp3" (the media_url form) looks absolute but is really
    # media-root-relative, so strip that prefix before the isabs check.
    relative = normalised.lstrip("/")
    if relative.startswith("media/"):
        relative = relative[len("media/"):]
    elif os.path.isabs(stored_path):
        return stored_path if os.path.exists(stored_path) else None

    candidate = os.path.join(settings.media_root, *relative.split("/"))
    return candidate if os.path.exists(candidate) else None
