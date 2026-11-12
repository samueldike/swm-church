"""Video Clipper jobs: each stage reads the project, does its work in a thread (ffmpeg, whisper and OpenCV block),
reports progress to the row every few seconds, and queues the next stage. Called from the Celery tasks in
app/tasks.py; a failure is recorded on the project (or the short) with a message fit for the person who started it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime

from sqlalchemy import select

from app.core.clipper import media, options
from app.core.clipper.media import ClipperError
from app.database import async_session
from app.models.clipper import ClipProject, ClipShort

logger = logging.getLogger(__name__)

STALE_MINUTES = 20


async def _update(project_id: int, **fields) -> None:
    async with async_session() as db:
        p = await db.get(ClipProject, project_id)
        if p is None:
            return
        for k, v in fields.items():
            setattr(p, k, v)
        p.heartbeat_at = datetime.utcnow()
        await db.commit()


async def _short_update(short_id: int, **fields) -> None:
    async with async_session() as db:
        s = await db.get(ClipShort, short_id)
        if s is None:
            return
        for k, v in fields.items():
            setattr(s, k, v)
        await db.commit()


async def _in_thread(project_id: int, fn, *args, stage: str | None = None, **kwargs):
    """Run ``fn(*args, progress=cb, **kwargs)`` in a thread; the latest progress is saved every 3 seconds."""
    state = {"p": None, "stage": None}

    def cb(fraction, label=None):
        state["p"] = max(0.0, min(1.0, float(fraction)))
        if label:
            state["stage"] = label

    task = asyncio.create_task(asyncio.to_thread(fn, *args, progress=cb, **kwargs))
    while not task.done():
        await asyncio.wait({task}, timeout=3)
        if state["p"] is not None:
            fields = {"progress": state["p"]}
            if state["stage"] or stage:
                fields["stage"] = state["stage"] or stage
            await _update(project_id, **fields)
    return task.result()


async def _fail(project_id: int, message: str) -> None:
    await _update(project_id, status="failed", error=message[:2000], stage=None)


async def _load(project_id: int) -> ClipProject | None:
    async with async_session() as db:
        return (await db.execute(select(ClipProject).where(ClipProject.id == project_id))).scalar_one_or_none()


def _settings(p: ClipProject) -> dict:
    return {**options.DEFAULTS, **(p.settings or {})}


def _info(project_id: int) -> dict:
    path = media.project_dir(project_id) / "info.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ─── 1. fetch ─────────────────────────────────────────────────────────

async def fetch(project_id: int) -> bool:
    p = await _load(project_id)
    if p is None:
        return False
    st = _settings(p)
    src = media.source_path(project_id)
    try:
        if not media.enough_disk():
            raise ClipperError("The server is short of disk space. Delete old projects, then try again.")
        await _update(project_id, status="fetching", stage="Getting the video", progress=0.0, error=None)
        if p.source_kind == "url":
            title = await _in_thread(project_id, media.download, p.source_url or "", src,
                                     start=st.get("range_start"), end=st.get("range_end"),
                                     max_height=1080, stage="Downloading the video")
            if title and not p.title:
                await _update(project_id, title=title[:255])
        else:
            uploads = sorted(media.project_dir(project_id).glob("upload.*"))
            if not uploads:
                raise ClipperError("The uploaded file is missing. Please upload it again.")
            up = uploads[0]
            await _update(project_id, stage="Preparing the uploaded video", progress=0.3)
            if st.get("range_start") is not None or st.get("range_end") is not None:
                await asyncio.to_thread(media.trim_upload, up, src, st.get("range_start"), st.get("range_end"))
                up.unlink(missing_ok=True)
            else:
                await asyncio.to_thread(media.remux, up, src)
        info = await asyncio.to_thread(media.probe, src)
        if not info["has_video"]:
            raise ClipperError("That file has no picture. The clipper needs a video.")
        if not info["has_audio"]:
            raise ClipperError("That video has no sound. The clipper works from what is said.")
        if info["duration"] < 60:
            raise ClipperError("That video is under a minute long; there is nothing to clip from it.")
        (media.project_dir(project_id) / "info.json").write_text(json.dumps(info), encoding="utf-8")
        await _update(project_id, duration=info["duration"], width=info["width"], height=info["height"],
                      size_bytes=info["size"], stage="Listening for pauses", progress=0.9)
        await asyncio.to_thread(media.extract_audio, src, media.audio_path(project_id))
        gaps = await asyncio.to_thread(media.silence_map, media.audio_path(project_id))
        (media.project_dir(project_id) / "gaps.json").write_text(json.dumps(gaps), encoding="utf-8")
        await _update(project_id, status="transcribing", stage="Waiting for the speech recogniser", progress=0.0)
        return True
    except ClipperError as exc:
        await _fail(project_id, str(exc))
    except Exception:
        logger.exception("Clipper fetch failed for project %s", project_id)
        await _fail(project_id, "Getting the video failed unexpectedly. Please try again.")
    return False


# ─── 2. transcribe (runs on the transcription queue: shares the loaded Whisper model) ─────

async def transcribe(project_id: int) -> bool:
    from app.core.clipper import speech

    p = await _load(project_id)
    if p is None:
        return False
    st = _settings(p)
    try:
        audio = media.audio_path(project_id)
        if not audio.exists():
            raise ClipperError("The audio of this video is missing. Retry the project.")
        await _update(project_id, status="transcribing", stage="Writing down every word", progress=0.0, error=None)
        hot = await _hotwords()
        words = await _in_thread(project_id, speech.transcribe_words, audio, language=st.get("language") or None,
                                 hotwords=hot, stage="Writing down every word")
        if len(words) < 40:
            raise ClipperError("Hardly any speech was heard in this video, so there is nothing to clip.")
        gaps_file = media.project_dir(project_id) / "gaps.json"
        gaps = json.loads(gaps_file.read_text(encoding="utf-8")) if gaps_file.exists() else []
        speech.save_words(media.words_path(project_id), words, gaps, {"language": st.get("language")})
        await _update(project_id, status="composing", stage="Waiting for the editor", progress=0.0)
        return True
    except ClipperError as exc:
        await _fail(project_id, str(exc))
    except Exception:
        logger.exception("Clipper transcription failed for project %s", project_id)
        await _fail(project_id, "Transcribing the video failed unexpectedly. Please try again.")
    return False


async def _hotwords() -> str:
    try:
        from app.core.settings_cache import get_settings

        async with async_session() as db:
            site = await get_settings(db)
        return ", ".join(x for x in (site.get("siteName"), "Jesus Christ", "Holy Spirit") if x)
    except Exception:
        return ""


# ─── 3. compose ───────────────────────────────────────────────────────

async def compose(project_id: int, *, more: int | None = None) -> bool:
    from app.core.clipper import editorial, speech

    p = await _load(project_id)
    if p is None:
        return False
    st = _settings(p)
    floor, target = options.length_window(st)
    try:
        words, gaps, _ = speech.load_words(media.words_path(project_id))
        units = speech.build_units(words, gaps)
        taken: set[int] = set()
        if more:
            for s in p.shorts:
                for piece in s.plan or []:
                    if not piece.get("spare"):
                        taken.update(piece.get("ids") or [])
        await _update(project_id, status="composing", stage="Reading the whole message", progress=0.02, error=None)

        async def say(label, fraction):
            await _update(project_id, stage=label, progress=fraction)

        def progress(label, fraction):
            asyncio.get_running_loop().create_task(say(label, fraction))

        structure = (p.brief or {}).get("structure") if more else None
        shorts, video, structure = await editorial.make_shorts(
            units, count=more or int(st["count"]), floor=floor, target=target, content_type=st["content_type"],
            title=p.title or "", style=st["style"], duration=float(p.duration or 0), taken=taken or None,
            structure=structure, progress=progress)
        async with async_session() as db:
            proj = await db.get(ClipProject, project_id)
            start = max([s.position for s in proj.shorts] + [-1]) + 1
            for n, s in enumerate(shorts):
                db.add(ClipShort(project_id=project_id, position=start + n, origin="ai", title=s["title"],
                                 caption=s["caption"], value=s["value"], score=s["score"], plan=s["plan"],
                                 length=s["length"], format=st["format"], quality=int(st["quality"]),
                                 framing=st["framing"], caption_style=st["caption_style"], status="queued"))
            brief = dict(proj.brief or {})
            if video:
                brief["video"] = video
            if structure:
                brief["structure"] = structure
            proj.brief = brief
            proj.status, proj.stage, proj.progress = "rendering", "Cutting the shorts", 0.0
            proj.heartbeat_at = datetime.utcnow()
            await db.commit()
        return True
    except (ClipperError, editorial.EditorialError) as exc:
        await _fail(project_id, str(exc))
    except Exception as exc:
        from app.core.ai import AIError

        if isinstance(exc, AIError):
            await _fail(project_id, str(exc))
        else:
            logger.exception("Clipper compose failed for project %s", project_id)
            await _fail(project_id, "Choosing the shorts failed unexpectedly. Please try again.")
    return False


async def manual_short(project_id: int, start: float, end: float) -> int | None:
    """A short the person marked by time: whole sentences from ``start`` to ``end``, with the dead air removed."""
    from app.core.clipper import speech

    p = await _load(project_id)
    if p is None:
        return None
    st = _settings(p)
    words, gaps, _ = speech.load_words(media.words_path(project_id))
    units = [u for u in speech.build_units(words, gaps) if u.end > start + 0.2 and u.start < end - 0.2]
    if not units:
        return None
    first, last = units[0], units[-1]
    text = " ".join(u.text for u in units)
    plan = [{"w0": first.w0, "w1": last.w1, "start": first.start, "end": last.end, "text": text, "role": "point",
             "ids": [u.i for u in units]}]
    async with async_session() as db:
        proj = await db.get(ClipProject, project_id)
        pos = max([s.position for s in proj.shorts] + [-1]) + 1
        short = ClipShort(project_id=project_id, position=pos, origin="manual",
                          title=f"Clip from {options.clock(first.start)}", caption="", value=None, score=None,
                          plan=plan, length=sum(u.speech for u in units), format=st["format"],
                          quality=int(st["quality"]), framing=st["framing"], caption_style=st["caption_style"],
                          status="queued")
        db.add(short)
        proj.status, proj.stage, proj.progress, proj.error = "rendering", "Cutting the shorts", 0.0, None
        await db.commit()
        return short.id


# ─── 4. render ────────────────────────────────────────────────────────

async def render_queued(project_id: int) -> None:
    """Render every queued short of the project, one at a time; then the project is ready."""
    from app.core.clipper import cut, render, speech

    p = await _load(project_id)
    if p is None:
        return
    st = _settings(p)
    floor, _target = options.length_window(st)
    try:
        words, gaps, _ = speech.load_words(media.words_path(project_id))
    except FileNotFoundError:
        await _fail(project_id, "The transcript of this video is missing. Retry the project.")
        return
    info = _info(project_id)
    queue = [s for s in p.shorts if s.status == "queued"]
    done_before = len([s for s in p.shorts if s.status in ("ready", "failed")])
    total = len(queue) + done_before
    for n, s in enumerate(queue):
        await _short_update(s.id, status="rendering", error=None)
        label = f"Cutting short {done_before + n + 1} of {total}"
        await _update(project_id, status="rendering", stage=label, progress=(done_before + n) / max(1, total))
        try:
            the_cut = cut.build_cut(s.plan or [], words, gaps, floor=floor if s.origin == "ai" else 0.0,
                                    nudge_start=s.nudge_start or 0.0, nudge_end=s.nudge_end or 0.0)
            out = media.short_file(project_id, s.id, "mp4")

            def work(progress):
                return render.render(src=media.source_path(project_id), info=info, segments=the_cut["segments"],
                                     words=the_cut["words"], out=out, fmt=s.format, quality=s.quality,
                                     framing=s.framing, caption_style=s.caption_style, progress=progress)

            base = (done_before + n) / max(1, total)

            def scaled(fn):
                def run_it(progress):
                    return fn(lambda f: progress(base + f / max(1, total), label))
                return run_it

            result = await _in_thread(project_id, scaled(work), stage=label)
            await _short_update(s.id, status="ready", segments=the_cut["segments"], length=result["length"],
                                rendered_at=datetime.utcnow(), error=None)
        except ClipperError as exc:
            await _short_update(s.id, status="failed", error=str(exc)[:1000])
        except Exception:
            logger.exception("Clipper render failed for short %s", s.id)
            await _short_update(s.id, status="failed", error="This short could not be cut. Try again.")
    await _update(project_id, status="ready", stage=None, progress=1.0, finished_at=datetime.utcnow())


# ─── housekeeping ─────────────────────────────────────────────────────

async def sweep_sources() -> int:
    """Delete source videos of projects finished more than CLIPPER_KEEP_SOURCE_DAYS ago (shorts stay)."""
    from datetime import timedelta

    from app.config import settings

    cutoff = datetime.utcnow() - timedelta(days=max(1, settings.CLIPPER_KEEP_SOURCE_DAYS))
    n = 0
    async with async_session() as db:
        rows = (await db.execute(select(ClipProject).where(
            ClipProject.source_deleted_at.is_(None), ClipProject.finished_at.is_not(None),
            ClipProject.finished_at < cutoff))).scalars().all()
        for p in rows:
            media.remove_source(p.id)
            p.source_deleted_at = datetime.utcnow()
            n += 1
        await db.commit()
    return n


def stale(p: ClipProject) -> bool:
    """A job that stopped reporting (worker restarted mid-way): show Retry instead of a frozen bar."""
    from datetime import timedelta

    if not p.busy or p.status == "queued" and not p.heartbeat_at:
        return False
    beat = p.heartbeat_at or p.updated_at or p.created_at
    limit = STALE_MINUTES * (6 if p.status == "transcribing" else 1)
    return beat is not None and datetime.utcnow() - beat > timedelta(minutes=limit)


def resume_stage(project_id: int) -> str:
    """Where a retry should start from, given the files already made."""
    if media.words_path(project_id).exists():
        return "compose"
    if media.audio_path(project_id).exists() and (media.project_dir(project_id) / "gaps.json").exists():
        return "transcribe"
    return "fetch"


def disk_summary() -> dict:
    total = used = 0
    try:
        u = shutil.disk_usage(media.root())
        total, used = u.total, u.used
    except OSError:
        pass
    return {"free": media.free_bytes(), "total": total, "used": used}
