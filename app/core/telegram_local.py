"""Large-file Telegram uploads through the self-hosted Bot API server.

The public Bot API caps uploads at 50MB. The official ``telegram-bot-api`` server in
``--local`` mode accepts up to 2000MB, but it needs the church's Telegram ``api_id`` /
``api_hash`` (from my.telegram.org). An administrator saves those in Settings, where they
are encrypted like every other key. The container cannot read the database, so the app
writes them to a volume only the two containers share (``TELEGRAM_BOT_API_CONF_DIR``);
the container's entrypoint (``deploy/telegram-bot-api/entrypoint.sh``) starts the server
from them and restarts it whenever ``stamp`` changes.

The app never reads bot updates (no polling, no webhook), so Telegram's ``logOut`` step for
moving a bot between servers is unnecessary: switching large files on or off is instant and
reversible, and the same bot keeps working on both servers.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time

import httpx

from app.config import settings as app_settings

logger = logging.getLogger(__name__)

LARGE_FILES_SETTING = "telegram_large_files"   # "on" | ""
CLOUD_LIMIT_BYTES = 50 * 1024 * 1024

_API_ID = re.compile(r"^\d{3,12}$")
_API_HASH = re.compile(r"^[0-9a-f]{32}$")


def valid_api_id(value: str) -> bool:
    return bool(_API_ID.match(value or ""))


def valid_api_hash(value: str) -> bool:
    return bool(_API_HASH.match((value or "").lower()))


def credentials_saved(site: dict) -> bool:
    return valid_api_id(site.get("telegram_api_id", "")) and valid_api_hash(site.get("telegram_api_hash", ""))


def enabled(site: dict) -> bool:
    """Large files are on only when switched on *and* the credentials behind it are saved."""
    return site.get(LARGE_FILES_SETTING) == "on" and credentials_saved(site)


def _read(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _write(path: str, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as fh:
        fh.write(content)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def sync_credentials(site: dict) -> bool:
    """Hand the saved api_id/api_hash to the telegram-bot-api container (or withdraw them).

    Returns False when there is nowhere to write (outside Docker) or the write failed.
    """
    conf = app_settings.TELEGRAM_BOT_API_CONF_DIR
    if not conf or not os.path.isdir(conf):
        return False
    api_id, api_hash = site.get("telegram_api_id", ""), (site.get("telegram_api_hash", "") or "").lower()
    try:
        current = [_read(os.path.join(conf, n)) for n in ("api_id", "api_hash")]
        if credentials_saved(site):
            if current != [api_id, api_hash]:
                _write(os.path.join(conf, "api_id"), api_id)
                _write(os.path.join(conf, "api_hash"), api_hash)
                _write(os.path.join(conf, "stamp"), str(time.time()))   # the container restarts on this
        elif any(current):
            for n in ("api_id", "api_hash"):
                if os.path.exists(os.path.join(conf, n)):
                    os.unlink(os.path.join(conf, n))
            _write(os.path.join(conf, "stamp"), str(time.time()))
        return True
    except OSError:
        logger.exception("Could not write Telegram Bot API credentials to %s", conf)
        return False


async def server_check(bot_token: str, timeout: float = 4.0) -> tuple[str, str]:
    """``(state, message)`` for the large-file server. state: ok | down | rejected."""
    base = app_settings.TELEGRAM_LOCAL_API_BASE.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if not bot_token:
                await client.get(base + "/")
                return "ok", "The large-file server is running."
            r = await client.post(f"{base}/bot{bot_token}/getMe")
    except httpx.HTTPError:
        return "down", "The large-file server is not answering yet."
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    if r.status_code == 200 and payload.get("ok"):
        return "ok", f"Connected as @{payload['result'].get('username', 'bot')} with large files (up to 2 GB)."
    return "rejected", f"Telegram refused the login: {payload.get('description') or f'HTTP {r.status_code}'}"
