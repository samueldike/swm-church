from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone

import httpx

from app.celery_app import celery_app
from app.database import async_session
from app.core.media import to_abspath
from app.core.telegram import TelegramDistributor, TelegramError

logger = logging.getLogger(__name__)


def _run(coro):
    """Run a task's coroutine on a fresh event loop with a fresh DB pool.

    Each Celery task gets a new loop from asyncio.run, but the engine's pooled asyncpg
    connections belong to the loop that opened them: reusing one in a later task fails with
    "... attached to a different loop". Disposing the pool when the task ends prevents that.
    """
    import asyncio

    from app.database import engine

    async def wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapped())


async def _load_sermon(db, sermon_id: int):
    from sqlalchemy import select
    from app.models.sermon import Sermon

    result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
    return result.scalar_one_or_none()


async def _set_distribution(sermon_id: int, platform: str, **fields) -> None:
    async with async_session() as db:
        sermon = await _load_sermon(db, sermon_id)
        if not sermon:
            return
        status = dict(sermon.distribution_status or {})
        entry = dict(status.get(platform) or {})
        entry.update(fields)
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        status[platform] = entry
        sermon.distribution_status = status
        await db.commit()


# ─── Distribution ──────────────────────────────────────────────────

# Transient failures (network, Telegram 429/5xx) retry on this schedule (seconds), ~2.5h in all.
_DISTRIBUTE_BACKOFF = (30, 120, 600, 1800, 3600, 3600)


@celery_app.task(bind=True, max_retries=len(_DISTRIBUTE_BACKOFF))
def distribute_audio_task(self, sermon_id: int, transcribe: bool = False):
    """Upload a sermon's audio to Telegram (and, only if ``transcribe``, queue a transcription after).

    Uploads queue their transcription themselves, straight away and independent of Telegram.

    Runs out-of-band because a multi-hundred-megabyte upload would otherwise
    hold the admin HTTP request open past nginx's proxy timeout. Failures that
    clear up by themselves are retried; the rest record Telegram's own reason.
    """
    import asyncio

    final = self.request.retries >= self.max_retries
    try:
        _run(_distribute(sermon_id, transcribe, final=final))
    except TelegramError as exc:
        if final or not exc.transient:
            return
        delay = max(exc.retry_after or 0, _DISTRIBUTE_BACKOFF[self.request.retries])
        raise self.retry(exc=exc, countdown=delay)


async def _distribute(sermon_id: int, transcribe: bool, final: bool = True) -> None:
    async with async_session() as db:
        sermon = await _load_sermon(db, sermon_id)
        if not sermon:
            logger.error("Sermon %d not found for distribution", sermon_id)
            return
        stored_path = sermon.audio_file_path
        audio_path = to_abspath(stored_path)
        filename = sermon.original_file_name or "sermon.mp3"
        title = sermon.title
        caption = sermon.caption or sermon.title
        branch_id = sermon.branch_id

    if not audio_path:
        logger.error("Sermon %d has no readable audio file on disk (%s)", sermon_id, stored_path)
        await _set_distribution(sermon_id, "telegram", state="failed",
                                error="the audio file is not on the server's disk; upload it again")
        return

    tg = TelegramDistributor(branch_id)      # the message's own branch bot; HQ's only for HQ messages
    if not await tg.configured():
        await _set_distribution(sermon_id, "telegram", state="skipped", error="this branch has no Telegram bot/channel saved")
        return

    # Telegram always gets the file exactly as uploaded: it is never re-encoded.
    size, limit = os.path.getsize(audio_path), await tg.upload_limit()
    if size > limit:
        logger.error("Sermon %d is %d bytes, over the %d-byte Telegram limit", sermon_id, size, limit)
        hint = ("the largest file Telegram accepts" if tg.is_local else
                "Telegram's standard limit. Turn on large files (up to 2 GB) in Settings → Audio distribution, then use Send again")
        await _set_distribution(sermon_id, "telegram", state="failed",
                                error=f"This file is {size / 1048576:.0f} MB; {limit // 1048576} MB is {hint}.")
        return

    await _set_distribution(sermon_id, "telegram", state="uploading", error=None)

    try:
        result = await tg.send_audio(
            file_path=audio_path,
            filename=filename,
            title=title,
            caption=caption,
        )
    except TelegramError as exc:
        retrying = exc.transient and not final
        logger.warning("Telegram upload for sermon %d failed (%s): %s", sermon_id,
                       "will retry" if retrying else "giving up", exc)
        await _set_distribution(sermon_id, "telegram", state="retrying" if retrying else "failed", error=str(exc))
        raise

    if not result:
        await _set_distribution(sermon_id, "telegram", state="skipped", error="this branch has no Telegram bot/channel saved")
        return

    async with async_session() as db:
        sermon = await _load_sermon(db, sermon_id)
        if sermon:
            sermon.telegram_file_id = result.get("file_id")
            sermon.telegram_message_id = str(result.get("message_id", ""))
            await db.commit()

    await _set_distribution(
        sermon_id, "telegram", state="sent", message_id=result.get("message_id"), error=None
    )
    logger.info("Sermon %d distributed to Telegram", sermon_id)

    if transcribe:
        transcribe_audio_task.delay(sermon_id)


# ─── Telegram archive ──────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=0)
def telegram_archive_task(self, action: str, admin_id: int | None = None, limit: int | None = None):
    """Scan / download / import for the Telegram archive. Hours-long downloads are fine here;
    progress and errors are written to the ``telegram_archive_job`` setting. ``limit`` = a
    download batch of that many files; None = continuous until paused."""
    import asyncio

    from app.core.telegram_archive import run

    _run(run(action, admin_id, limit))


# ─── Transcription ─────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def transcribe_audio_task(self, sermon_id: int, telegram_file_id: str | None = None):
    import asyncio

    _run(_transcribe(sermon_id, telegram_file_id))


async def _resolve_audio(sermon_id: int, stored_path: str | None, telegram_file_id: str | None, branch_id: int | None = None):
    """Return ``(path, is_temp)`` for the audio to transcribe.

    The local copy is preferred: it avoids a network round trip and sidesteps
    the cloud Bot API's 20MB getFile ceiling entirely.
    """
    local = to_abspath(stored_path)
    if local:
        return local, False

    if not telegram_file_id:
        return None, False

    tg = TelegramDistributor(branch_id)
    remote_path, file_url = await tg.resolve_file(telegram_file_id)

    if remote_path:
        return remote_path, False

    if not file_url:
        return None, False

    tmp_path = None
    async with httpx.AsyncClient(timeout=1800) as client:
        async with client.stream("GET", file_url) as resp:
            resp.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                tmp_path = f.name
    return tmp_path, True


async def _mark_failed(sermon_id: int, reason: str) -> None:
    try:
        async with async_session() as db:
            sermon = await _load_sermon(db, sermon_id)
            if sermon:
                sermon.transcription_status = "failed"
                sermon.transcription_error = reason[:1000]
                await db.commit()
    except Exception:
        logger.exception("Could not mark sermon %d transcription failed", sermon_id)


async def _transcribe(sermon_id: int, telegram_file_id: str | None):
    import asyncio

    async with async_session() as db:
        sermon = await _load_sermon(db, sermon_id)
        if not sermon:
            logger.error("Sermon %d not found for transcription", sermon_id)
            return

        if sermon.transcription_status == "processing" and sermon.transcription_started_at and                 datetime.utcnow() - sermon.transcription_started_at < timedelta(hours=6):
            logger.info("Sermon %d is already being transcribed; skipping the duplicate", sermon_id)
            return
        stored_path = sermon.audio_file_path
        telegram_file_id = telegram_file_id or sermon.telegram_file_id
        branch_id = sermon.branch_id

        sermon.transcription_status = "processing"
        sermon.transcription_error = None
        # Naive UTC: these are TIMESTAMP WITHOUT TIME ZONE columns. An aware value made asyncpg
        # reject this write, so every transcription died here and stayed "Not started".
        sermon.transcription_started_at = datetime.utcnow()
        await db.commit()

    audio_path = None
    is_temp = False
    try:
        audio_path, is_temp = await _resolve_audio(sermon_id, stored_path, telegram_file_id, branch_id)
    except Exception:
        logger.exception("Could not fetch audio for sermon %d", sermon_id)

    if not audio_path:
        await _mark_failed(sermon_id, "The audio file is not on the server and could not be fetched from Telegram.")
        logger.error("No audio source available for sermon %d", sermon_id)
        return

    try:
        from app.core.transcription import transcribe_audio

        transcript = await asyncio.get_event_loop().run_in_executor(
            None, transcribe_audio, audio_path
        )

        async with async_session() as db:
            sermon = await _load_sermon(db, sermon_id)
            if sermon:
                sermon.transcript = transcript
                sermon.transcription_status = "completed"
                sermon.transcription_completed_at = datetime.utcnow()
                await db.commit()
                logger.info(
                    "Transcription completed for sermon %d (%d chars)",
                    sermon_id,
                    len(transcript),
                )

    except Exception as exc:
        logger.exception("Transcription failed for sermon %d", sermon_id)
        await _mark_failed(sermon_id, f"{type(exc).__name__}: {exc}")
        raise exc

    finally:
        if is_temp and audio_path and os.path.exists(audio_path):
            try:
                os.unlink(audio_path)
            except OSError:
                pass


# ─── Books Pub (AI drafting) ───────────────────────────────────────

@celery_app.task(bind=True, max_retries=0)
def generate_topics_task(self, publication_id: int):
    import asyncio

    from app.core.publications import run_topics

    _run(run_topics(publication_id))


@celery_app.task(bind=True, max_retries=0)
def generate_contents_task(self, publication_id: int):
    import asyncio

    from app.core.publications import run_contents

    _run(run_contents(publication_id))
