from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone

import httpx

from app.celery_app import celery_app
from app.database import async_session
from app.core.telegram import TelegramDistributor

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def transcribe_audio_task(self, sermon_id: int, telegram_file_id: str):
    import asyncio

    asyncio.run(_transcribe(sermon_id, telegram_file_id))


async def _transcribe(sermon_id: int, telegram_file_id: str):
    from sqlalchemy import select
    from app.models.sermon import Sermon

    async with async_session() as db:
        result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
        sermon = result.scalar_one_or_none()
        if not sermon:
            logger.error("Sermon %d not found for transcription", sermon_id)
            return

        sermon.transcription_status = "processing"
        sermon.transcription_started_at = datetime.now(timezone.utc)
        await db.commit()

    tg = TelegramDistributor()
    file_url = await tg.file_download_url(telegram_file_id)
    if not file_url:
        async with async_session() as db:
            result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
            sermon = result.scalar_one_or_none()
            if sermon:
                sermon.transcription_status = "failed"
                await db.commit()
        logger.error("Could not get Telegram download URL for sermon %d", sermon_id)
        return

    tmp_path = None
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("GET", file_url) as resp:
                resp.raise_for_status()
                suffix = ".mp3"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                    tmp_path = f.name

        from app.core.transcription import transcribe_audio

        transcript = await asyncio.get_event_loop().run_in_executor(
            None, transcribe_audio, tmp_path
        )

        async with async_session() as db:
            result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
            sermon = result.scalar_one_or_none()
            if sermon:
                sermon.transcript = transcript
                sermon.transcription_status = "completed"
                sermon.transcription_completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.info("Transcription completed for sermon %d (%d chars)", sermon_id, len(transcript))

    except Exception as exc:
        logger.exception("Transcription failed for sermon %d", sermon_id)
        try:
            async with async_session() as db:
                result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
                sermon = result.scalar_one_or_none()
                if sermon:
                    sermon.transcription_status = "failed"
                    await db.commit()
        except Exception:
            pass
        raise exc

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
