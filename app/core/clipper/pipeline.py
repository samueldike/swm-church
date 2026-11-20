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

from sqlalchemy import func, select, update

from app.core.clipper import captions, media, options
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
        # Always beat while the work is alive (even before the first progress), so a job that died
        # shows "Stopped" after STALE_MINUTES instead of looking busy for hours.
        fields = {}
        if state["p"] is not None:
            fields["progress"] = state["p"]
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
        if p.source_kind == "url" and media.is_youtube(p.source_url or ""):
            # Sound first (seconds), so listening starts now; the picture downloads alongside (fetch_video).
            sound = media.audio_src_path(project_id)
            title = await _in_thread(project_id, media.download, p.source_url or "", sound, sound_only=True,
                                     cookies=(await youtube_cookies()) or None, stage="Getting the sound")
            if title and not p.title:
                await _update(project_id, title=title[:255])
            await _update(project_id, stage="Listening for pauses", progress=0.9)
            await asyncio.to_thread(media.extract_audio, sound, media.audio_path(project_id),
                                    st.get("range_start"), st.get("range_end"))
            sound.unlink(missing_ok=True)
            length = float((await asyncio.to_thread(media.probe, media.audio_path(project_id))).get("duration") or 0)
            if length < 60:
                raise ClipperError("That video is under a minute long; there is nothing to clip from it.")
            gaps = await asyncio.to_thread(media.silence_map, media.audio_path(project_id))
            (media.project_dir(project_id) / "gaps.json").write_text(json.dumps(gaps), encoding="utf-8")
            await _update(project_id, duration=length, status="transcribing", stage="Writing down every word",
                          progress=0.0)
            return True
        if p.source_kind == "url":
            cookies = await youtube_cookies() if media.is_youtube(p.source_url or "") else None
            title = await _in_thread(project_id, media.download, p.source_url or "", src,
                                     start=st.get("range_start"), end=st.get("range_end"),
                                     max_height=1080, cookies=cookies or None, stage="Downloading the video")
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
        await _update(project_id, status="transcribing", stage="Writing down every word", progress=0.0)
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
        gaps_file = media.project_dir(project_id) / "gaps.json"
        gaps = json.loads(gaps_file.read_text(encoding="utf-8")) if gaps_file.exists() else []
        partial = media.project_dir(project_id) / "words.partial.json"
        words = await _in_thread(project_id, speech.transcribe_words, audio, language=st.get("language") or None,
                                 hotwords=hot, duration=float(p.duration or _info(project_id).get("duration") or 0),
                                 gaps=gaps, partial=partial, stage="Writing down every word")
        if len(words) < 40:
            raise ClipperError("Hardly any speech was heard in this video, so there is nothing to clip.")
        speech.save_words(media.words_path(project_id), words, gaps, {"language": st.get("language")})
        partial.unlink(missing_ok=True)
        await _update(project_id, status="composing", stage="Waiting for the editor", progress=0.0)
        return True
    except ClipperError as exc:
        await _fail(project_id, str(exc))
    except Exception:
        logger.exception("Clipper transcription failed for project %s", project_id)
        await _fail(project_id, "Transcribing the video failed unexpectedly. Please try again.")
    return False


async def groq_key() -> str:
    from app.core import ai

    try:
        return (await ai.get_config("groq")).api_key or ""
    except Exception:
        return ""


async def transcribe_fast(project_id: int) -> str:
    """Hosted transcription (Groq): "done", "failed", or "local" when the local model has to do it."""
    from app.core.clipper import cloud_speech, speech

    key = await groq_key()
    if not key:
        await _update(project_id, status="transcribing", stage="Waiting for the speech recogniser", progress=0.0)
        return "local"
    p = await _load(project_id)
    if p is None:
        return "failed"
    st = _settings(p)
    try:
        audio = media.audio_path(project_id)
        if not audio.exists():
            raise ClipperError("The audio of this video is missing. Retry the project.")
        gaps_file = media.project_dir(project_id) / "gaps.json"
        gaps = json.loads(gaps_file.read_text(encoding="utf-8")) if gaps_file.exists() else []
        await _update(project_id, status="transcribing", stage="Writing down every word", progress=0.0, error=None)
        words = await _in_thread(project_id, cloud_speech.transcribe_words, audio, key=key,
                                 duration=float(p.duration or _info(project_id).get("duration") or 0), gaps=gaps,
                                 language=st.get("language") or None, hotwords=await _hotwords(),
                                 stage="Writing down every word")
        if len(words) < 40:
            raise ClipperError("Hardly any speech was heard in this video, so there is nothing to clip.")
        speech.save_words(media.words_path(project_id), words, gaps, {"language": st.get("language"), "engine": "groq"})
        await _update(project_id, status="composing", stage="Waiting for the editor", progress=0.0)
        return "done"
    except cloud_speech.CloudUnavailable as exc:
        logger.warning("Clipper project %s: hosted transcription unavailable (%s); using the local model", project_id, exc)
        await _update(project_id, status="transcribing", stage="Waiting for the speech recogniser", progress=0.0)
        return "local"
    except ClipperError as exc:
        await _fail(project_id, str(exc))
    except Exception:
        logger.exception("Clipper hosted transcription failed for project %s", project_id)
        await _update(project_id, status="transcribing", stage="Waiting for the speech recogniser", progress=0.0)
        return "local"
    return "failed"


async def youtube_cookies() -> str:
    from app.core.settings_cache import get_setting

    async with async_session() as db:
        return (await get_setting(db, "clipper_youtube_cookies", "")).strip()


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
        if not more and st["content_type"] == "sermon":
            structure = await editorial.map_structure(units, title=p.title or "", content_type="sermon",
                                                      duration=float(p.duration or 0))
            structure = await _only_the_senior_pastor(project_id, p, st, structure)
            if structure is False:
                return False
        shorts, video, structure = await editorial.make_shorts(
            units, count=more or int(st["count"]), floor=floor, target=target, content_type=st["content_type"],
            title=p.title or "", style=st["style"], duration=float(p.duration or 0), taken=taken or None,
            structure=structure, progress=progress,
            min_score=int(st.get("min_score") or 0) or None, refills=int(st.get("refills") or editorial.REFILLS))
        from app.core import social

        ministry = await social.ministry_tags()      # the ministry's own hashtags go on every caption
        async with async_session() as db:
            proj = await db.get(ClipProject, project_id)
            start = max([s.position for s in proj.shorts] + [-1]) + 1
            for n, s in enumerate(shorts):
                s["caption"] = social.with_ministry(s["caption"], ministry)
                db.add(ClipShort(project_id=project_id, position=start + n, origin="ai", title=s["title"],
                                 caption=s["caption"], value=s["value"], score=s["score"], plan=s["plan"],
                                 length=s["length"], format=st["format"], quality=int(st["quality"]),
                                 framing=st["framing"], caption_style=captions.style_id(st["caption_style"]) or captions.DEFAULT, denoise=st.get("denoise") or "natural",
                                 captions_on=captions.style_id(st["caption_style"]) is not None, status="queued"))
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


async def _only_the_senior_pastor(project_id: int, p, st: dict, structure: dict | None):
    """With the church's "Only the senior pastor" on: keep only the parts of the sermon(s) he preaches, judged by
    voice (speakers.py). Returns the structure to use, or False when he does not preach (the project is failed
    with a message and a "Clip anyway" button)."""
    from app.core.clipper import speakers

    if not structure or not structure.get("body") or st.get("ignore_speaker") or not p.branch_id:
        return structure
    async with async_session() as db:
        cfg = await speakers.load(db, p.branch_id)
    him = speakers.senior(cfg)
    cal = speakers.calibrate(cfg)
    if cfg["mode"] != "senior" or not him or cal["accept"] is None:
        return structure
    await _update(project_id, stage=f"Listening for {him['name'] or 'the senior pastor'}", progress=0.08)
    try:
        windows = await asyncio.to_thread(speakers.windows_for, media.audio_path(project_id), structure["body"])
    except FileNotFoundError:
        logger.warning("Voice model missing: speaker check skipped for project %s", project_id)
        return structure
    verdicts = speakers.judge(windows, cfg, cal["accept"])
    ranges = speakers.his_ranges(verdicts, structure["body"])
    found = round(speakers.share(verdicts) * 100)
    check = {"name": him["name"], "share": found, "ranges": ranges, "refused": not ranges}
    async with async_session() as db:
        proj = await db.get(ClipProject, project_id)
        proj.brief = {**(proj.brief or {}), "speaker_check": check}
        await db.commit()
    if not ranges:
        await _fail(project_id, f"{him['name'] or 'The senior pastor'} does not preach in this video (his voice matched "
                                f"{found}% of the sermon). Only his messages are clipped; use “Clip anyway” to clip it.")
        return False
    logger.info("Project %s: %s preaches %s%% (%s)", project_id, him["name"], found, ranges)
    return {**structure, "body": ranges, "speaker": him["name"] or structure.get("speaker", "")}


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
                          quality=int(st["quality"]), framing=st["framing"], caption_style=captions.style_id(st["caption_style"]) or captions.DEFAULT, denoise=st.get("denoise") or "natural",
                                 captions_on=captions.style_id(st["caption_style"]) is not None,
                          status="queued")
        db.add(short)
        proj.status, proj.stage, proj.progress, proj.error = "rendering", "Cutting the shorts", 0.0, None
        await db.commit()
        return short.id


# ─── 4. render ────────────────────────────────────────────────────────

RENDER_PARALLEL = 2   # shorts cut at the same time (the clipper container may use 5 cores)


VIDEO_LOCK = "video.lock"


def _video_lock_fresh(project_id: int, seconds: float = 90.0) -> bool:
    import time

    lock = media.project_dir(project_id) / VIDEO_LOCK
    try:
        return time.time() - lock.stat().st_mtime < seconds
    except FileNotFoundError:
        return False


async def video_needed(project_id: int) -> bool:
    """A YouTube project whose picture is not on disk and is not being downloaded right now."""
    p = await _load(project_id)
    if p is None or p.source_kind != "url" or not media.is_youtube(p.source_url or ""):
        return False
    return not media.video_ready(project_id) and not _video_lock_fresh(project_id)


async def fetch_video(project_id: int) -> bool:
    """Download the picture while the words are being written down. True when shorts are waiting to be cut.

    Runs next to the speech/editor steps, so it never writes the project's status or stage (only its heartbeat and
    the picture's size); a failure fails the project."""
    p = await _load(project_id)
    if p is None or p.status == "failed":
        return False
    if media.video_ready(project_id):
        return await _shorts_waiting(project_id)
    if _video_lock_fresh(project_id, 30.0):
        return False
    lock = media.project_dir(project_id) / VIDEO_LOCK
    lock.touch()
    st = _settings(p)
    src = media.source_path(project_id)
    try:
        task = asyncio.create_task(asyncio.to_thread(
            media.download, p.source_url or "", src, start=st.get("range_start"), end=st.get("range_end"),
            keep_whole=True, max_height=1080, cookies=(await youtube_cookies()) or None))
        while not task.done():
            await asyncio.wait({task}, timeout=15)
            lock.touch()
            await _update(project_id)
        task.result()
        info = await asyncio.to_thread(media.probe, src)
        if not info["has_video"]:
            raise ClipperError("That link has no picture. The clipper needs a video.")
        # The words are timed from range_start (the sound was cut there); the source is kept whole and read at
        # this offset, so there is no keyframe drift between picture and words.
        info["offset"] = float(st.get("range_start") or 0)
        info["duration"] = float(p.duration or info["duration"])
        (media.project_dir(project_id) / "info.json").write_text(json.dumps(info), encoding="utf-8")
        await _update(project_id, width=info["width"], height=info["height"], size_bytes=info["size"])
        return await _shorts_waiting(project_id)
    except ClipperError as exc:
        await _fail(project_id, str(exc))
    except Exception:
        logger.exception("Clipper video download failed for project %s", project_id)
        await _fail(project_id, "Downloading the video failed unexpectedly. Please try again.")
    finally:
        lock.unlink(missing_ok=True)
    return False


async def _shorts_waiting(project_id: int, statuses: tuple[str, ...] = ("queued",)) -> bool:
    async with async_session() as db:
        n = (await db.execute(select(func.count()).select_from(ClipShort).where(
            ClipShort.project_id == project_id, ClipShort.status.in_(statuses)))).scalar_one()
    return bool(n)


async def _claim_short(short_id: int) -> bool:
    """queued -> rendering, atomically: a second render job never cuts the same short twice."""
    async with async_session() as db:
        res = await db.execute(update(ClipShort).where(ClipShort.id == short_id, ClipShort.status == "queued")
                               .values(status="rendering", error=None))
        await db.commit()
        return res.rowcount == 1


async def final_copy(project_id: int, short, branch_id: int | None):
    """The file a download or a post uses: the master, with the short's frame and captions when it has them."""
    from app.core.clipper import frames, render

    master = media.short_file(project_id, short.id, "mp4")
    style = short.caption_style if short.captions_on else None
    fpath = fid = None
    if branch_id:
        async with async_session() as db:
            cfg = await frames.load(db, branch_id)
        item = frames.resolve(getattr(short, "frame", None), short.format, cfg)
        if item:
            fpath, fid = frames.path_of(branch_id, item), item["id"]
    opath = oitem = None
    if branch_id:
        from app.core.clipper import outros

        async with async_session() as db:
            ocfg = await outros.load(db, branch_id)
        oitem = outros.resolve(getattr(short, "outro", None), ocfg)
        opath = outros.path_of(branch_id, oitem) if oitem else None
    mpath = mitem = None
    if branch_id:
        from app.core.clipper import music

        async with async_session() as db:
            mcfg = await music.load(db, branch_id)
        mitem = music.resolve(getattr(short, "music", None), mcfg, short.id)
        mpath = music.path_of(branch_id, mitem) if mitem else None
    return await asyncio.to_thread(render.finish, master, style, fpath, fid, opath, oitem, mpath, mitem)


async def premake_captions(project_id: int, short_id: int) -> None:
    """Make the finished copy (frame + captions) ahead of its download/post."""
    async with async_session() as db:
        s = await db.get(ClipShort, short_id)
        p = await db.get(ClipProject, project_id)
        if s is None or p is None or s.project_id != project_id:
            return
    if media.short_file(project_id, short_id, "mp4").exists():
        try:
            await final_copy(project_id, s, p.branch_id)
        except Exception:
            logger.exception("The finished copy of short %s could not be made ahead", short_id)


async def render_queued(project_id: int) -> None:
    """Render every queued short of the project, two at a time; each appears as soon as it is ready."""
    from app.core.clipper import cut, opening, render, speech, timeline

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
    if not media.video_ready(project_id):
        if p.source_kind == "url" and media.is_youtube(p.source_url or ""):
            # The picture is still downloading: fetch_video starts this job again when it lands.
            await _update(project_id, status="rendering", stage="Getting the picture (almost there)", progress=0.0)
            if await video_needed(project_id):
                from app.tasks import clip_video_task

                clip_video_task.apply_async((project_id,))
            return
        await _fail(project_id, "The source video is missing. Retry the project.")
        return
    info = _info(project_id)
    queue = [s for s in p.shorts if s.status == "queued"]
    if not queue:
        if not any(s.status == "rendering" for s in p.shorts):
            await _update(project_id, status="ready", stage=None, progress=1.0, finished_at=datetime.utcnow())
        return
    done_before = len([s for s in p.shorts if s.status in ("ready", "failed")])
    total = max(1, len(queue) + done_before)
    share: dict[int, float] = {s.id: 0.0 for s in queue}
    finished = [done_before]
    await _update(project_id, status="rendering", progress=done_before / total,
                  stage=f"Cutting {len(queue)} short{'s' if len(queue) != 1 else ''}")

    units = None
    src_w, src_h = int(info.get("width") or 0), int(info.get("height") or 0)
    offset = float(info.get("offset") or 0.0)

    def look(a: float, b: float) -> float:
        from app.core.clipper import reframe

        return opening.speaker_share(reframe.detect(str(media.source_path(project_id)), a + offset, b + offset,
                                                    src_w, src_h))

    def one(s, progress):
        nonlocal units
        plan = s.plan or []
        if s.edit and s.edit.get("clips"):          # a person's own timeline (visual editor) is the cut
            the_cut = timeline.cut_for(s.edit, words, gaps)
        else:
            the_cut = cut.build_cut(plan, words, gaps, floor=floor if s.origin == "ai" else 0.0,
                                    nudge_start=s.nudge_start or 0.0, nudge_end=s.nudge_end or 0.0)
        # AI shorts open on the speaker, not on a scripture slide (a person's own edits are left alone).
        if (s.origin == "ai" and not s.edit and not (s.nudge_start or s.nudge_end) and src_w
                and the_cut["segments"]):
            if units is None:
                units = speech.build_units(words, gaps)
            new_plan, how = opening.choose_opening(
                plan, units, the_cut["segments"], look, floor=floor,
                length_after_skip=lambda pl: cut.build_cut(pl, words, gaps, floor=floor)["length"])
            if how != "kept":
                logger.info("Short %s: opening on the speaker (%s)", s.id, how)
                plan = new_plan
                the_cut = cut.build_cut(plan, words, gaps, floor=floor)
        the_cut["plan"] = plan
        result = render.render(src=media.source_path(project_id), info=info, segments=the_cut["segments"],
                               words=the_cut["words"], out=media.short_file(project_id, s.id, "mp4"), fmt=s.format,
                               quality=s.quality, framing=s.framing, caption_style=s.caption_style, progress=progress,
                               frame_x=(s.edit or {}).get("frame_x"), denoise=getattr(s, "denoise", None),
                               flashes=bool(getattr(s, "flashes", True)), zooms=bool(getattr(s, "zooms", True)),
                               emphasis=[] if s.edit else [(x["start"], x["end"]) for x in the_cut["plan"]
                                                           if x.get("cold_open") or x.get("role") in ("payoff", "hook")],
                               touches=bool(getattr(s, "touches", True)))
        return the_cut, result

    sem = asyncio.Semaphore(RENDER_PARALLEL)

    async def run_short(s):
        async with sem:
            if not await _claim_short(s.id):
                share[s.id] = 1.0
                return
            try:
                the_cut, result = await asyncio.to_thread(one, s, lambda f, sid=s.id: share.__setitem__(sid, float(f)))
                await _short_update(s.id, status="ready", segments=the_cut["segments"], length=result["length"],
                                    plan=the_cut["plan"], effects=result.get("effects") or [],
                                    rendered_at=datetime.utcnow(), error=None)
                try:                       # the finished copy (frame, captions) is ready before anyone asks
                    await final_copy(project_id, s, p.branch_id)
                except Exception:
                    logger.exception("The finished copy of short %s could not be made ahead", s.id)
            except ClipperError as exc:
                await _short_update(s.id, status="failed", error=str(exc)[:1000])
            except Exception:
                logger.exception("Clipper render failed for short %s", s.id)
                await _short_update(s.id, status="failed", error="This short could not be cut. Try again.")
            finally:
                share[s.id] = 1.0
                finished[0] += 1

    jobs = asyncio.gather(*(run_short(s) for s in queue))
    task = asyncio.ensure_future(jobs)
    while not task.done():
        await asyncio.wait({task}, timeout=3)
        if not task.done():
            ready = finished[0]
            await _update(project_id, progress=min(0.99, (done_before + sum(share.values())) / total),
                          stage=f"Cutting the shorts: {ready} of {total} ready")
    task.result()
    if not await _shorts_waiting(project_id, ("queued", "rendering")):
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
    # Waiting in the speech queue behind another long job is not a stop.
    limit = STALE_MINUTES * (6 if p.stage == "Waiting for the speech recogniser" else 1)
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


# ─── publishing (Zernio) ──────────────────────────────────────────────

async def publish(post_id: int) -> None:
    """Send a short to its chosen places: Zernio accounts (upload + post) and the church's own WhatsApp (a small
    WhatsApp copy queued as WhatsApp posts, which the scheduler sends with its anti-ban pacing)."""
    import re

    from app.core import social
    from app.core.clipper import render
    from app.models.clipper import ClipPost

    async with async_session() as db:
        post = await db.get(ClipPost, post_id)
        if post is None or post.status not in ("queued", "uploading"):
            return
        short = await db.get(ClipShort, post.short_id)
        if short is None:
            return
        post.status = "uploading"
        await db.commit()
        project_id, captions_on, style = short.project_id, bool(short.captions_on), short.caption_style
        caption, title, targets, when = post.caption or "", post.title or short.title, post.targets or [], post.scheduled_for
        branch_id, author, short_id = post.branch_id, post.created_by, short.id
    zernio = [t for t in targets if t.get("platform") != "whatsapp"]
    whats = [t for t in targets if t.get("platform") == "whatsapp"]
    rows: list[dict] = []
    pid = None
    try:
        master = media.short_file(project_id, short_id, "mp4")
        if not master.exists():
            raise social.SocialError("The short's video file is missing. Re-cut it, then publish again.")
        async with async_session() as db:
            short = await db.get(ClipShort, short_id)
        path = await final_copy(project_id, short, branch_id)
        if zernio:
            try:
                url = await social.upload(path)
                body = social.build_post(caption=caption, title=title, media_url=url, targets=zernio,
                                         when_utc=when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None, tz="UTC",
                                         tags=re.findall(r"#(\w+)", caption))
                data = await social.create_post(body)
                pid = str((data.get("post") or data).get("_id") or "") or None
                _status, zrows = social.summarize(data)
                rows += zrows or [{"platform": t["platform"], "status": "scheduled" if when else "published", "url": "",
                                   "error": ""} for t in zernio]
            except social.SocialError as exc:
                rows += [{"platform": t["platform"], "status": "failed", "url": "", "error": str(exc)[:300]} for t in zernio]
        if whats:
            try:
                rows += await _queue_whatsapp(path, whats, branch_id, author, when)
            except Exception as exc:
                logger.exception("WhatsApp copy of post %s failed", post_id)
                rows += [{"platform": "whatsapp", "status": "failed", "url": "", "error": f"WhatsApp: {exc}"[:300]}]
        status = social.overall(rows)
        async with async_session() as db:
            post = await db.get(ClipPost, post_id)
            post.status, post.results, post.zernio_post_id = status, rows, pid
            post.error = "; ".join(f"{social.LABELS.get(r['platform'], r['platform'])}: {r['error']}"
                                   for r in rows if r.get("error"))[:1000] or None
            await db.commit()
    except social.SocialError as exc:
        await _post_failed(post_id, str(exc))
    except Exception:
        logger.exception("Publishing post %s failed", post_id)
        await _post_failed(post_id, "Publishing failed unexpectedly. Try again.")


def _whatsapp_copy(src, dest) -> None:
    """A WhatsApp-sized copy: 720 px on the short side, modest bitrate (a minute is ~10 MB), fast start."""
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
               "-vf", "scale=w='if(gt(iw,ih),-2,720)':h='if(gt(iw,ih),720,-2)'",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "1500k", "-bufsize", "3000k",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dest)], timeout=900)


async def _queue_whatsapp(path, whats: list[dict], branch_id: int, author: int | None, when) -> list[dict]:
    """WhatsAppPost rows for the Status and the groups/channels (sent by app/core/whatsapp.py:process_due)."""
    import uuid
    from pathlib import Path

    from app.core.media import media_root
    from app.models.whatsapp import WhatsAppPost

    rel = f"whatsapp/b{int(branch_id)}/clip-{uuid.uuid4().hex}.mp4"
    dest = Path(media_root()) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_whatsapp_copy, path, dest)
    rows = []
    async with async_session() as db:
        for t in whats:
            opts = t.get("options") or {}
            is_status = t.get("kind") == "status"
            wp = WhatsAppPost(branch_id=branch_id, created_by=author, kind="status" if is_status else "message",
                              targets=[] if is_status else (opts.get("chats") or []), text=opts.get("text") or "",
                              media_path=rel, media_kind="video", media_name="short.mp4",
                              send_at=when or datetime.utcnow(), status="pending")
            db.add(wp)
            await db.flush()
            rows.append({"platform": "whatsapp", "status": "scheduled", "url": "", "error": "", "wa_post": wp.id,
                         "name": t.get("name") or ""})
        await db.commit()
    if not when:
        from app.core import whatsapp as wa

        try:
            await wa.process_due()            # start now; the scheduler carries on with its pacing
        except Exception:
            logger.exception("WhatsApp send did not start at once; the scheduler will pick it up")
    return rows


async def _post_failed(post_id: int, message: str) -> None:
    from app.models.clipper import ClipPost

    async with async_session() as db:
        post = await db.get(ClipPost, post_id)
        if post is not None:
            post.status, post.error = "failed", message[:1000]
            await db.commit()


async def refresh_posts(short_ids: list[int]) -> None:
    """Ask Zernio how scheduled/just-sent posts went (at most every 2 minutes per post)."""
    from datetime import timedelta

    from app.core import social
    from app.models.clipper import ClipPost

    if not short_ids:
        return
    cutoff = datetime.utcnow() - timedelta(minutes=2)
    async with async_session() as db:
        from app.models.whatsapp import WhatsAppPost

        rows = (await db.execute(select(ClipPost).where(
            ClipPost.short_id.in_(short_ids), ClipPost.status.in_(("scheduled", "partial"))))).scalars().all()
        wa_state = {"sent": "published", "partial": "partial", "failed": "failed", "cancelled": "failed"}
        for post in rows[:10]:
            results = [dict(r) for r in (post.results or [])]
            touched = False
            # The church's WhatsApp: read straight from its posts (cheap, no rate limit).
            for r in results:
                if r.get("wa_post"):
                    wp = await db.get(WhatsAppPost, int(r["wa_post"]))
                    st = wa_state.get(wp.status, "scheduled") if wp else "failed"
                    err = (wp.error or "") if wp else "The WhatsApp post was removed."
                    if st != r.get("status") or err != r.get("error"):
                        r.update(status=st, error=err[:300])
                        touched = True
            # Zernio: at most every 2 minutes, and only once the post is due.
            due = not (post.scheduled_for and post.scheduled_for > datetime.utcnow())
            if post.zernio_post_id and due and (post.updated_at or cutoff) <= cutoff:
                try:
                    _s, zrows = social.summarize(await social.get_post(post.zernio_post_id))
                    results = [r for r in results if r.get("platform") == "whatsapp"] + zrows
                    touched = True
                except social.SocialError:
                    pass
                post.updated_at = datetime.utcnow()
            if touched:
                post.results, post.status = results, social.overall(results)
                failed = [r for r in results if r.get("error")]
                post.error = "; ".join(f"{social.LABELS.get(r['platform'], r['platform'])}: {r['error']}" for r in failed)[:1000] or None
        await db.commit()
