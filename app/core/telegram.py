import io
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session


class TelegramDistributor:

    async def _get_credentials(self) -> tuple[str, str]:
        from app.core.settings_cache import get_settings

        async with async_session() as db:
            settings = await get_settings(db)

        return (
            settings.get("telegram_bot_token", ""),
            settings.get("telegram_audio_chat_id", ""),
        )

    async def configured(self) -> bool:
        token, chat_id = await self._get_credentials()
        return bool(token and chat_id)

    async def send_audio(
        self,
        file_bytes: bytes,
        filename: str,
        title: str,
        caption: str = "",
    ) -> dict[str, Any] | None:
        token, chat_id = await self._get_credentials()
        if not token or not chat_id:
            return None

        url = f"https://api.telegram.org/bot{token}/sendAudio"

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                data={
                    "chat_id": chat_id,
                    "title": title,
                    "caption": caption,
                },
                files={
                    "audio": (filename, io.BytesIO(file_bytes), "audio/mpeg"),
                },
            )

        if response.status_code != 200:
            return None

        data = response.json()
        if not data.get("ok"):
            return None

        message = data["result"]
        audio = message.get("audio", {})
        file_id = audio.get("file_id")

        return {
            "message_id": message.get("message_id"),
            "file_id": file_id,
        }

    async def file_download_url(self, file_id: str) -> str | None:
        token, _ = await self._get_credentials()
        if not token:
            return None

        url = f"https://api.telegram.org/bot{token}/getFile"

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                json={"file_id": file_id},
            )

        if response.status_code != 200:
            return None

        data = response.json()
        if not data.get("ok"):
            return None

        file_path = data["result"].get("file_path")
        if not file_path:
            return None

        return f"https://api.telegram.org/file/bot{token}/{file_path}"
