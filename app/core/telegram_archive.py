"""Telegram archive: back up the HQ channel's audio, including posts from before the bot joined.

Bots cannot read channel history (on the cloud or a self-hosted Bot API), so this signs in to
Telegram as a *user* through MTProto (Telethon) with the church's App api_id / App api_hash.
The login is a Telethon StringSession kept encrypted in ``telegram_user_session``; "Sign out"
logs it out at Telegram and deletes it.

Three background jobs (``telegram_archive_task``), each resumable and safe to re-run:
scan (list every audio post) → download (originals into TELEGRAM_ARCHIVE_DIR, which is never
served publicly) → import (one *draft* website message per file, with the post's date).
Progress lives in ``telegram_archive_items`` and the ``telegram_archive_job`` setting.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings as app_settings
from app.core.settings_cache import get_settings, update_settings
from app.database import async_session

logger = logging.getLogger(__name__)

SESSION_SETTING = "telegram_user_session"
JOB_SETTING = "telegram_archive_job"
DISK_MARGIN = 2 * 1024 ** 3          # always leave this much free for the site itself
_STALE_AFTER = 600                   # a "running" job with no heartbeat for 10 min is dead


class ArchiveError(Exception):
    """A problem to show the administrator as-is."""


# ─── Small pure helpers ────────────────────────────────────────────

def is_audio(message) -> bool:
    f = getattr(message, "file", None)
    if not f:
        return False
    return bool(getattr(message, "audio", None) or getattr(message, "voice", None)
                or (f.mime_type or "").startswith("audio/"))


def draft_title(audio_title: str | None, caption: str | None, file_name: str | None, posted_at: datetime) -> str:
    """Best human title: the audio's own title, else the caption's first line, else the file name."""
    for candidate in (audio_title, (caption or "").strip().splitlines()[0] if (caption or "").strip() else "",
                      os.path.splitext(file_name or "")[0].replace("_", " ")):
        candidate = re.sub(r"\s+", " ", candidate or "").strip(" -–—|#*")
        if len(candidate) >= 3:
            return candidate[:150]
    return f"Audio message {posted_at:%d %b %Y}"


def safe_name(name: str) -> str:
    stem, ext = os.path.splitext(name or "audio")
    stem = re.sub(r"[^\w\-]+", "_", stem, flags=re.ASCII).strip("_")[:80] or "audio"
    ext = re.sub(r"[^\w.]", "", ext)[:10] or ".mp3"
    return stem + ext


def post_link(channel_id: str, username: str | None, message_id: int) -> str:
    return f"https://t.me/{username}/{message_id}" if username else f"https://t.me/c/{channel_id}/{message_id}"


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text   # neutralise spreadsheet formulas


def manifest_csv(items, username: str | None) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["posted_at_utc", "message_id", "telegram_link", "title", "performer", "caption",
                "file", "size_bytes", "duration_seconds", "sha256", "status", "website_message_id"])
    for it in items:
        w.writerow([_cell(v) for v in (
            f"{it.posted_at:%Y-%m-%d %H:%M}", it.message_id, post_link(it.channel_id, username, it.message_id),
            draft_title(it.audio_title, it.caption, it.file_name, it.posted_at), it.performer, it.caption,
            it.stored_path or "", it.size, it.duration or "", it.sha256 or "", it.status, it.sermon_id or "")])
    return "﻿" + out.getvalue()


# ─── Job state ─────────────────────────────────────────────────────

async def get_job(db) -> dict:
    try:
        return json.loads((await get_settings(db)).get(JOB_SETTING) or "{}")
    except ValueError:
        return {}


def job_running(job: dict) -> bool:
    return job.get("state") == "running" and time.time() - float(job.get("heartbeat") or 0) < _STALE_AFTER


async def set_job(**fields) -> None:
    async with async_session() as db:
        job = await get_job(db)
        job.update(fields, heartbeat=time.time())
        await update_settings(db, {JOB_SETTING: json.dumps(job, default=str)})
        await db.commit()


async def claim_job(action: str) -> bool:
    """Mark ``action`` as running unless another archive job is alive. True when claimed."""
    async with async_session() as db:
        if job_running(await get_job(db)):
            return False
    await set_job(state="running", action=action, started_at=time.time(), error=None, message="Starting…",
                  done=0, total=0, bytes_done=0, bytes_total=0)
    return True


# ─── Sign-in (a Telegram user, not the bot) ────────────────────────

async def _login_state(db=None) -> dict:
    if db is None:
        async with async_session() as db:
            return await _login_state(db)
    try:
        return json.loads((await get_settings(db)).get(SESSION_SETTING) or "{}")
    except ValueError:
        return {}


async def _save_login(state: dict) -> None:
    async with async_session() as db:
        await update_settings(db, {SESSION_SETTING: json.dumps(state) if state else ""})
        await db.commit()


async def login_state() -> dict:
    """What the page may show: stage (none|code|password|ready), the account name, the masked phone."""
    s = await _login_state()
    phone = s.get("phone") or ""
    return {"stage": s.get("stage") or "none", "name": s.get("name") or "",
            "phone": (phone[:4] + "•" * max(0, len(phone) - 7) + phone[-3:]) if phone else ""}


async def _client(session: str = ""):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    from app.core import telegram_local

    async with async_session() as db:
        site = await get_settings(db)
    if not telegram_local.credentials_saved(site):
        raise ArchiveError("Save the App api_id and App api_hash in Settings → Audio distribution first.")
    client = TelegramClient(StringSession(session), int(site["telegram_api_id"]), site["telegram_api_hash"].lower(),
                            device_model="Church website archive", app_version="1.0", system_version="server")
    await client.connect()
    return client


def _explain(exc: Exception) -> ArchiveError:
    from telethon import errors

    if isinstance(exc, errors.FloodWaitError):
        mins = max(1, round(exc.seconds / 60))
        return ArchiveError(f"Telegram asks us to wait about {mins} minute{'s' if mins != 1 else ''} before trying again.")
    if isinstance(exc, (errors.ApiIdInvalidError, errors.ApiIdPublishedFloodError)):
        return ArchiveError("Telegram rejected the App api_id / App api_hash. Check them in Settings → Audio distribution.")
    if isinstance(exc, errors.PhoneNumberInvalidError):
        return ArchiveError("That phone number is not valid. Use international format, e.g. +234 803 123 4567.")
    if isinstance(exc, errors.PhoneNumberBannedError):
        return ArchiveError("Telegram says this phone number is banned.")
    if isinstance(exc, errors.PhoneCodeInvalidError):
        return ArchiveError("That code is not right. Check the latest message from “Telegram” in the app and try again.")
    if isinstance(exc, errors.PhoneCodeExpiredError):
        return ArchiveError("That code has expired. Start again to get a new one.")
    if isinstance(exc, errors.PasswordHashInvalidError):
        return ArchiveError("That two-step verification password is not right.")
    if isinstance(exc, ArchiveError):
        return exc
    return ArchiveError(f"Telegram said: {exc}")


async def start_login(phone: str) -> None:
    phone = re.sub(r"[^\d+]", "", phone or "")
    if not re.fullmatch(r"\+\d{7,15}", phone):
        raise ArchiveError("Enter the phone number in international format, starting with + (e.g. +234 803 123 4567).")
    client = await _client()
    try:
        sent = await client.send_code_request(phone)
        await _save_login({"stage": "code", "phone": phone, "hash": sent.phone_code_hash, "session": client.session.save()})
    except Exception as exc:
        raise _explain(exc) from exc
    finally:
        await client.disconnect()


async def _finish(client, state: dict) -> None:
    me = await client.get_me()
    name = " ".join(p for p in (me.first_name, me.last_name) if p) or (me.username or "Telegram user")
    await _save_login({"stage": "ready", "phone": state.get("phone"), "name": name, "session": client.session.save()})


async def submit_code(code: str) -> str:
    from telethon import errors

    state = await _login_state()
    if state.get("stage") != "code":
        raise ArchiveError("Start the sign-in again: no code is pending.")
    client = await _client(state.get("session", ""))
    try:
        try:
            await client.sign_in(state["phone"], re.sub(r"\D", "", code or ""), phone_code_hash=state["hash"])
        except errors.SessionPasswordNeededError:
            await _save_login({**state, "stage": "password", "session": client.session.save()})
            return "password"
        await _finish(client, state)
        return "ready"
    except Exception as exc:
        raise _explain(exc) from exc
    finally:
        await client.disconnect()


async def submit_password(password: str) -> None:
    state = await _login_state()
    if state.get("stage") != "password":
        raise ArchiveError("Start the sign-in again: no password is pending.")
    client = await _client(state.get("session", ""))
    try:
        await client.sign_in(password=password)
        await _finish(client, state)
    except Exception as exc:
        raise _explain(exc) from exc
    finally:
        await client.disconnect()


async def sign_out() -> None:
    """End the session at Telegram (it disappears from the account's Devices) and delete it here."""
    state = await _login_state()
    if state.get("session") and state.get("stage") == "ready":
        try:
            client = await _client(state["session"])
            try:
                await client.log_out()
            finally:
                await client.disconnect()
        except Exception:
            logger.warning("Telegram log_out failed; deleting the stored session anyway", exc_info=True)
    await _save_login({})


async def _ready_client():
    state = await _login_state()
    if state.get("stage") != "ready":
        raise ArchiveError("Sign in to Telegram first (step 1).")
    client = await _client(state["session"])
    if not await client.is_user_authorized():
        await client.disconnect()
        await _save_login({})
        raise ArchiveError("The Telegram sign-in has ended (it was removed from the account's devices). Sign in again.")
    return client


async def _channel(client):
    async with async_session() as db:
        chat = ((await get_settings(db)).get("telegram_audio_chat_id") or "").strip()
    if not chat:
        raise ArchiveError("No HQ channel is saved in Settings → Audio distribution.")
    target = int(chat) if re.fullmatch(r"-?\d+", chat) else chat
    try:
        return await client.get_entity(target)
    except ValueError:
        await client.get_dialogs()              # numeric ids resolve once the account's chats are known
        try:
            return await client.get_entity(target)
        except ValueError as exc:
            raise ArchiveError("This Telegram account cannot see the HQ channel. Sign in with an account that is "
                               "a member or admin of it.") from exc


# ─── Jobs ──────────────────────────────────────────────────────────

async def run_scan() -> None:
    from app.models.sermon import Sermon
    from app.models.telegram_archive import TelegramArchiveItem

    client = await _ready_client()
    try:
        entity = await _channel(client)
        channel_id, username = str(entity.id), getattr(entity, "username", None)
        await set_job(channel_id=channel_id, channel_title=entity.title, channel_username=username,
                      message="Reading the channel from its first post…")
        async with async_session() as db:
            known = {m for (m,) in (await db.execute(select(TelegramArchiveItem.message_id)
                                                   .where(TelegramArchiveItem.channel_id == channel_id))).all()}
            on_site = {str(t) for (t,) in (await db.execute(select(Sermon.telegram_message_id)
                                                          .where(Sermon.telegram_message_id.isnot(None)))).all()}
        seen = found = 0
        batch = []
        async for m in client.iter_messages(entity, reverse=True):
            seen += 1
            if is_audio(m) and m.id not in known:
                f = m.file
                batch.append(TelegramArchiveItem(
                    channel_id=channel_id, channel_title=entity.title, message_id=m.id,
                    posted_at=m.date.replace(tzinfo=None), caption=(m.message or None),
                    audio_title=(f.title or None), performer=(f.performer or None), file_name=f.name,
                    mime=f.mime_type, size=f.size or 0, duration=int(f.duration) if f.duration else None,
                    status="on_website" if str(m.id) in on_site else "found"))
                found += 1
            if len(batch) >= 200 or (seen % 500 == 0):
                if batch:
                    async with async_session() as db:
                        db.add_all(batch)
                        await db.commit()
                    batch = []
                await set_job(message=f"Read {seen:,} posts, found {found:,} new audio files…")
        if batch:
            async with async_session() as db:
                db.add_all(batch)
                await db.commit()
        await set_job(state="done", message=f"Read all {seen:,} posts: {found:,} new audio files found.")
    finally:
        await client.disconnect()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def run_download() -> None:
    from telethon import errors

    from app.models.telegram_archive import TelegramArchiveItem

    root = app_settings.telegram_archive_dir
    os.makedirs(root, exist_ok=True)
    async with async_session() as db:
        todo = list((await db.execute(select(TelegramArchiveItem).where(TelegramArchiveItem.status.in_(("found", "failed")))
                                      .order_by(TelegramArchiveItem.posted_at))).scalars().all())
    need = sum(i.size or 0 for i in todo)
    free = shutil.disk_usage(root).free
    if need > free - DISK_MARGIN:
        raise ArchiveError(f"Not enough disk space: the files need {need / 1024 ** 3:.1f} GB but only "
                           f"{max(0, free - DISK_MARGIN) / 1024 ** 3:.1f} GB can be used safely.")
    await set_job(total=len(todo), bytes_total=need, message=f"Downloading {len(todo):,} files…")

    client = await _ready_client()
    try:
        entity = await _channel(client)
        bytes_before = 0
        for n, item in enumerate(todo, 1):
            rel = f"{item.channel_id}/{item.posted_at:%Y}/{item.posted_at:%Y-%m-%d}_{item.message_id}_{safe_name(item.file_name or '')}"
            dest = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            label = draft_title(item.audio_title, item.caption, item.file_name, item.posted_at)
            last = [0.0]

            async def progress(current, _total, base=bytes_before, label=label, n=n):
                if time.time() - last[0] > 5:
                    last[0] = time.time()
                    await set_job(done=n - 1, bytes_done=base + current, message=f"{n:,}/{len(todo):,}: {label}")

            status, error, digest = "failed", "Telegram kept asking us to wait; run Download again later.", None
            for attempt in range(3):
                try:
                    msg = await client.get_messages(entity, ids=item.message_id)
                    if not msg or not is_audio(msg):
                        status, error = "failed", "The post was deleted from the channel."
                        break
                    await client.download_media(msg, file=dest + ".part", progress_callback=progress)
                    os.replace(dest + ".part", dest)
                    digest = await asyncio.to_thread(_sha256, dest)
                    status, error = "saved", None
                    break
                except errors.FloodWaitError as exc:
                    await set_job(message=f"Telegram asked us to pause for {exc.seconds}s; continuing after that…")
                    await asyncio.sleep(exc.seconds + 1)
                except Exception as exc:          # network drop, file reference expiry: try again, then record it
                    status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
                    await asyncio.sleep(5)
            if os.path.exists(dest + ".part"):
                os.unlink(dest + ".part")
            async with async_session() as db:
                row = await db.get(TelegramArchiveItem, item.id)
                row.status, row.error = status, error
                if status == "saved":
                    row.stored_path, row.sha256 = rel, digest
                await db.commit()
            bytes_before += item.size or 0
            await set_job(done=n, bytes_done=bytes_before, message=f"{n:,}/{len(todo):,}: {label}")
        await write_manifest()
        async with async_session() as db:
            failed = (await db.execute(select(TelegramArchiveItem.id).where(TelegramArchiveItem.status == "failed"))).all()
        tail = f" {len(failed)} could not be downloaded; run Download again to retry them." if failed else ""
        await set_job(state="done", message=f"Downloaded {len(todo) - len(failed):,} of {len(todo):,} files.{tail}")
    finally:
        await client.disconnect()


async def write_manifest() -> None:
    """index.csv next to the files, so the archive explains itself even without this website."""
    from app.models.telegram_archive import TelegramArchiveItem

    async with async_session() as db:
        items = list((await db.execute(select(TelegramArchiveItem).order_by(TelegramArchiveItem.posted_at))).scalars().all())
        job = await get_job(db)
    if items:
        path = os.path.join(app_settings.telegram_archive_dir, "index.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(manifest_csv(items, job.get("channel_username")))


async def run_import(admin_id: int | None) -> None:
    from slugify import slugify

    from app.core.hq_branch import get_hq_branch_id
    from app.core.media import ARCHIVE_PREFIX
    from app.models.sermon import Sermon
    from app.models.telegram_archive import TelegramArchiveItem

    async with async_session() as db:
        hq = await get_hq_branch_id(db)
        items = list((await db.execute(select(TelegramArchiveItem).where(
            TelegramArchiveItem.status == "saved", TelegramArchiveItem.sermon_id.is_(None))
            .order_by(TelegramArchiveItem.posted_at))).scalars().all())
        await set_job(total=len(items), message=f"Creating {len(items):,} draft messages…")
        for n, it in enumerate(items, 1):
            title = draft_title(it.audio_title, it.caption, it.file_name, it.posted_at)
            base = slugify(title)[:200] or "message"
            slug, k = base, 1
            while (await db.execute(select(Sermon.id).where(Sermon.slug == slug))).first():
                k += 1
                slug = f"{base}-{k}"
            sermon = Sermon(title=title, slug=slug, caption=it.caption, preacher=it.performer, type="audio",
                            # posted_at is naive UTC; published_at is timestamptz, so say it is UTC
                            status="draft", published_at=it.posted_at.replace(tzinfo=timezone.utc), branch_id=hq, created_by=admin_id,
                            audio_file_path=ARCHIVE_PREFIX + it.stored_path, media_url=None,
                            original_file_name=it.file_name, original_file_size=min(it.size or 0, 2147483647),
                            telegram_message_id=str(it.message_id), transcription_status="none")
            db.add(sermon)
            await db.flush()
            it.sermon_id = sermon.id
            if n % 50 == 0:
                await db.commit()
                await set_job(done=n)
        await db.commit()
    await write_manifest()
    await set_job(state="done", done=len(items),
                  message=f"Created {len(items):,} draft messages. Review them under Audio Messages (filter: Draft) and publish the ones you want.")


async def run(action: str, admin_id: int | None = None) -> None:
    """Entry point for the background job; records any failure on the job for the page to show."""
    try:
        if action == "scan":
            await run_scan()
        elif action == "download":
            await run_download()
        elif action == "import":
            await run_import(admin_id)
        else:
            raise ArchiveError(f"Unknown archive step: {action}")
    except Exception as exc:
        err = exc if isinstance(exc, ArchiveError) else None
        if err is None:
            try:
                err = _explain(exc)
            except Exception:
                err = ArchiveError(str(exc))
            logger.exception("Telegram archive %s failed", action)
        await set_job(state="failed", error=str(err), message=str(err))


# ─── Download everything as one ZIP ────────────────────────────────

class _Sink:
    """Write-only buffer for zipfile on an unseekable stream (it then writes data descriptors)."""

    def __init__(self):
        self.buf = bytearray()

    def write(self, b) -> int:
        self.buf += b
        return len(b)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        out, self.buf = bytes(self.buf), bytearray()
        return out


def zip_stream(files: list[tuple[str, str, datetime]], manifest: str):
    """Yield a ZIP (stored, not compressed: audio is already compressed) of ``(path, name, when)`` files."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        zf.writestr("index.csv", manifest.encode("utf-8"))
        yield sink.drain()
        for path, name, when in files:
            info = zipfile.ZipInfo(name, date_time=(max(when.year, 1980), when.month, when.day, when.hour, when.minute, when.second))
            info.compress_type = zipfile.ZIP_STORED
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)
                    if len(sink.buf) >= 1024 * 1024:
                        yield sink.drain()
            yield sink.drain()
    yield sink.drain()
