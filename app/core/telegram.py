"""Telegram audio distribution.

The public cloud Bot API caps ``sendAudio`` at 50MB and ``getFile`` at 20MB.
Both ceilings are server-side, so no client library lifts them. The only fix
is the official self-hosted ``telegram-bot-api`` server in ``--local`` mode,
which accepts uploads up to 2000MB. An administrator switches it on in
Settings once the church's api_id/api_hash are saved (``telegram_local``).
Against the local server files are handed over by absolute path rather than
streamed through this process, so a 2GB sermon never lands in RAM. Audio is
never re-encoded: Telegram always gets the file exactly as uploaded.
"""

import os
from typing import Any

import httpx

from app.config import settings as app_settings
from app.core import telegram_local
from app.database import async_session

CAPTION_LIMIT = 1024  # Bot API rejects longer audio captions outright


class TelegramError(Exception):
    """Telegram refused or could not be reached. ``transient`` errors are worth retrying."""

    def __init__(self, message: str, *, transient: bool, retry_after: int | None = None):
        self.transient = transient
        self.retry_after = retry_after
        super().__init__(message)


class TelegramUploadTooLarge(Exception):
    """Raised when a file exceeds the Bot API upload ceiling in use."""

    def __init__(self, size_bytes: int, limit_bytes: int):
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"{size_bytes / 1048576:.1f}MB exceeds the "
            f"{limit_bytes // 1048576}MB Telegram upload limit"
        )


class TelegramDistributor:
    """Sends audio through *one church's* bot.

    ``branch_id=None`` (or the HQ branch) uses the site-wide Telegram settings. Any other branch
    uses only the bot and channel that branch saved for itself, and **never falls back to HQ's**:
    HQ's channel promotes the General Overseer and must not carry branch pastors' messages.
    Large files (the self-hosted server) are one switch for every church's bot.
    """

    def __init__(self, branch_id: int | None = None):
        self.branch_id = branch_id
        self.is_local = False   # set by _get_credentials from Settings → large files

    async def _get_credentials(self) -> tuple[str, str]:
        from sqlalchemy import select

        from app.core.branch_settings import get_branch_settings
        from app.core.settings_cache import get_settings
        from app.models.branch import Branch

        async with async_session() as db:
            settings = await get_settings(db)
            self.is_local = telegram_local.enabled(settings)
            if self.branch_id:
                branch = (await db.execute(select(Branch).where(Branch.id == self.branch_id))).scalar_one_or_none()
                if branch is not None and not branch.is_hq:
                    own = await get_branch_settings(db, self.branch_id)
                    return own.get("telegram_bot_token", ""), own.get("telegram_audio_chat_id", "")

        return (
            settings.get("telegram_bot_token", ""),
            settings.get("telegram_audio_chat_id", ""),
        )

    async def check(self) -> tuple[bool, str]:
        """Verify the saved bot token and that the bot can see the channel. Returns ``(ok, message)``."""
        token, chat_id = await self._get_credentials()
        if not token:
            return False, "No bot token is saved."
        if not chat_id:
            return False, "No channel / chat ID is saved."
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                me = await client.post(self._method_url(token, "getMe"))
                if me.status_code != 200 or not me.json().get("ok"):
                    return False, "Telegram rejected the bot token."
                chat = await client.post(self._method_url(token, "getChat"), json={"chat_id": chat_id})
        except httpx.HTTPError as exc:
            where = "the large-file server" if self.is_local else "Telegram"
            return False, f"Could not reach {where}: {exc}"
        if chat.status_code != 200 or not chat.json().get("ok"):
            return False, "The bot cannot see that channel. Add the bot to the channel as an administrator and check the ID."
        title = chat.json()["result"].get("title") or chat.json()["result"].get("username") or chat_id
        size = "up to 2 GB per file" if self.is_local else "up to 50 MB per file"
        return True, f"Connected as @{me.json()['result'].get('username', 'bot')} → {title} ({size})"

    async def upload_limit(self) -> int:
        """Ceiling for a single upload: 2000MB with large files on, 50MB on the cloud API."""
        await self._get_credentials()
        return self._limit()

    def _limit(self) -> int:
        if self.is_local:
            return app_settings.telegram_max_upload_bytes
        return min(app_settings.telegram_max_upload_bytes, telegram_local.CLOUD_LIMIT_BYTES)

    def _method_url(self, token: str, method: str, cloud: bool = False) -> str:
        base = app_settings.telegram_api_base if cloud or not self.is_local else app_settings.TELEGRAM_LOCAL_API_BASE.rstrip("/")
        return f"{base}/bot{token}/{method}"

    async def configured(self) -> bool:
        token, chat_id = await self._get_credentials()
        return bool(token and chat_id)

    async def send_audio(
        self,
        file_path: str,
        filename: str,
        title: str,
        caption: str = "",
        timeout: float = 1800.0,
    ) -> dict[str, Any] | None:
        """Send an on-disk audio file. Returns ``None`` only when no bot/channel is saved.

        Raises ``TelegramError`` (with Telegram's own reason) when the upload fails.
        ``file_path`` must be absolute and, in local mode, readable by the
        telegram-bot-api container at the exact same path.
        """
        token, chat_id = await self._get_credentials()
        if not token or not chat_id:
            return None

        size = os.path.getsize(file_path)
        if size > self._limit():
            raise TelegramUploadTooLarge(size, self._limit())

        url = self._method_url(token, "sendAudio")
        if len(caption) > CAPTION_LIMIT:
            caption = caption[: CAPTION_LIMIT - 1].rstrip() + "…"
        data = {"chat_id": chat_id, "title": title, "caption": caption}

        # A --local server reads the file off disk itself, so we pass a URI and
        # never stream the payload. Otherwise httpx streams it from the handle.
        try:
            if self.is_local:
                data["audio"] = "file://" + file_path.replace("\\", "/")
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, data=data)
            else:
                with open(file_path, "rb") as fh:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.post(
                            url,
                            data=data,
                            files={"audio": (filename, fh, "audio/mpeg")},
                        )
        except httpx.HTTPError as exc:
            raise TelegramError(f"could not reach Telegram ({type(exc).__name__})", transient=True) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code != 200 or not payload.get("ok"):
            reason = payload.get("description") or f"HTTP {response.status_code}"
            retry_after = (payload.get("parameters") or {}).get("retry_after")
            # 429 = flood control, 5xx = Telegram's side: both clear up by themselves.
            transient = response.status_code == 429 or response.status_code >= 500
            raise TelegramError(f"Telegram: {reason}", transient=transient, retry_after=retry_after)

        message = payload["result"]
        audio = message.get("audio", {}) or message.get("document", {})

        return {
            "message_id": message.get("message_id"),
            "file_id": audio.get("file_id"),
        }

    async def resolve_file(self, file_id: str) -> tuple[str | None, str | None]:
        """Locate a stored file as ``(local_path, download_url)``.

        Always asks the cloud API: the bot is never logged out of it, and a --local server
        answers with paths on its own disk, which this container cannot read. The cloud
        returns a relative path we turn into a download URL (capped at 20MB by Telegram).
        Uploads made through this site are served from the media volume instead.
        """
        token, _ = await self._get_credentials()
        if not token:
            return None, None

        url = self._method_url(token, "getFile", cloud=True)

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json={"file_id": file_id})

        if response.status_code != 200:
            return None, None

        payload = response.json()
        if not payload.get("ok"):
            return None, None

        file_path = payload["result"].get("file_path")
        if not file_path:
            return None, None

        if os.path.isabs(file_path) and os.path.exists(file_path):
            return file_path, None

        return None, f"{app_settings.telegram_api_base}/file/bot{token}/{file_path}"

    async def file_download_url(self, file_id: str) -> str | None:
        """Back-compat shim. Prefer :meth:`resolve_file`, which also handles paths."""
        _, url = await self.resolve_file(file_id)
        return url
