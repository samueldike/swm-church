"""The Automatic Clipper: watch a church's YouTube channel and clip its services on its own.

Per church (branch settings, JSON):

* ``clipper_watch``: {"enabled", "url" (the channel), "every": "hours"|"days"|"weekdays", "n" (every N hours/days),
  "days" ([0..6], Monday = 0, for "weekdays"), "at" ("HH:MM" church time, for days/weekdays), "format", "length",
  "captions" (a design id or "none"), "min_score" (0-100: moments below it are not cut), "source" (``SOURCES``: the
  Live tab, the Videos tab or both), "start" (``STARTS``: from now on / from the very first video / from a chosen
  video, "start_url"), "speaker" ("all" or a voice-library person id: only that preacher's parts are clipped)}.
* ``clipper_watch_state``: {"last_check", "next_check" (UTC ISO), "seen" (video ids already handled), "baseline",
  "backlog" (older video ids still to clip, oldest first), "last_result", "projects" ([{id, video, title}])}.

Older videos (``start`` = all / from) are a backlog: a few per check, newest lives first, and only while fewer than
``BACKLOG_BUSY`` automatic videos are still being worked on; while a backlog is left the next look comes sooner.

A check lists the channel's Live tab with yt-dlp (``/streams``, the saved YouTube cookies), skips what it has seen,
and for each new video asks YouTube for its own state: ``was_live`` = the stream ended and the recording is ready;
``is_live`` (still on), ``is_upcoming`` (scheduled) and ``post_live`` (ended, still being processed) are left for a
later check. Each ready one becomes a normal Video Clipper project (``auto`` in its settings) with a large count, the
value bar (``min_score``) and extra search rounds, so it keeps every moment worth posting and drops the rest.
The scheduler (app/core/scheduler.py) calls ``dispatch_due`` each tick; the check itself runs on the clipper queue.
Pure helpers are tested in tests/test_clipper.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

EVERY = {"hours": "Every few hours", "days": "Every few days", "weekdays": "On chosen days of the week"}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
STARTS = {"now": "From now on", "all": "From the very first video", "from": "From a video I choose"}
SOURCES = {"streams": "Live services (the Live tab)", "videos": "Uploaded videos (the Videos tab)", "both": "Both"}
DEFAULTS = {"enabled": False, "url": "", "every": "hours", "n": 6, "days": [6], "at": "15:00", "format": "vertical",
            "length": "30-60", "captions": "none", "min_score": 70, "source": "streams", "start": "now",
            "start_url": "", "speaker": "all"}
BACKLOG_BUSY = 2          # older videos are started only while fewer automatic videos than this are still working
BACKLOG_EVERY = timedelta(minutes=45)   # how soon the next look comes while older videos are waiting
LIST_RECENT = 30          # videos read from a tab on an ordinary look
LIST_ALL = 1000           # ... and when starting from the first or a chosen video
AUTO_COUNT = 30           # "as many as possible": the editor stops earlier when the message runs out
AUTO_REFILLS = 4
MAX_NEW_PER_CHECK = 3     # finished lives turned into projects per check (the rest wait for the next one)
GIVE_UP_AFTER = timedelta(days=3)   # a live that never becomes ready (a broken stream) stops being looked at
SEEN_KEEP = 400


# ─── pure ─────────────────────────────────────────────────────────────

def streams_url(url: str) -> str | None:
    """The Live tab of a YouTube channel link (@handle, /channel/UC..., /c/name, /user/name), or None."""
    u = str(url or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[\w.\-]+|channel/UC[\w-]{20,}|c/[\w.\-]+|user/[\w.\-]+)", u)
    if not m:
        return None
    return f"https://www.youtube.com/{m.group(1)}/streams"


def video_id(url: str) -> str | None:
    """The id of a YouTube video link (watch?v=, youtu.be/, /live/, /shorts/), or None (pure; tested)."""
    u = str(url or "").strip()
    m = re.search(r"(?:[?&]v=|youtu\.be/|/live/|/shorts/|/embed/)([\w-]{11})(?![\w-])", u)
    return m.group(1) if m else None


def tab_urls(url: str, source: str) -> list[str]:
    """The channel tabs to read: Live, Videos or both (pure; tested)."""
    live = streams_url(url)
    if not live:
        return []
    videos = live[: -len("/streams")] + "/videos"
    return {"streams": [live], "videos": [videos]}.get(source, [live, videos])


def clean(form, current: dict | None = None, people: list[str] | None = None) -> tuple[dict, list[str]]:
    """Validated watch settings from the form, and the problems found. ``people``: the voice library's ids."""
    from app.core.clipper import captions, options

    get = form.get
    out = {**DEFAULTS, **(current or {})}
    problems = []
    out["enabled"] = get("enabled") == "1"
    out["url"] = str(get("url") or "").strip()[:300]
    if out["url"] and not streams_url(out["url"]):
        problems.append("Paste the channel's link, like https://www.youtube.com/@yourchurch.")
    if out["enabled"] and not out["url"]:
        problems.append("Paste the channel's link before switching the watch on.")
    out["every"] = get("every") if get("every") in EVERY else "hours"
    try:
        out["n"] = max(1, min(168 if out["every"] == "hours" else 30, int(get("n") or out["n"])))
    except (TypeError, ValueError):
        out["n"] = DEFAULTS["n"]
    days = sorted({int(d) for d in form.getlist("days") if str(d).isdigit() and 0 <= int(d) <= 6}) if hasattr(form, "getlist") else []
    out["days"] = days or DEFAULTS["days"]
    at = str(get("at") or "").strip()
    out["at"] = at if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at) else DEFAULTS["at"]
    out["format"] = get("format") if get("format") in options.FORMATS else DEFAULTS["format"]
    out["length"] = get("length") if get("length") in options.LENGTHS else DEFAULTS["length"]
    cap = get("captions")
    out["captions"] = cap if captions.style_id(cap) else "none"
    try:
        out["min_score"] = max(0, min(95, int(get("min_score") or out["min_score"])))
    except (TypeError, ValueError):
        out["min_score"] = DEFAULTS["min_score"]
    out.pop("latest", None)
    out["source"] = get("source") if get("source") in SOURCES else DEFAULTS["source"]
    out["start"] = get("start") if get("start") in STARTS else DEFAULTS["start"]
    out["start_url"] = str(get("start_url") or "").strip()[:300] if out["start"] == "from" else ""
    if out["start"] == "from" and not video_id(out["start_url"]):
        problems.append("Paste the link of the video to start from, like https://www.youtube.com/watch?v=....")
    sp = str(get("speaker") or "all")
    out["speaker"] = sp if sp == "all" or sp in (people or []) else "all"
    return out, problems


def restart_needed(old: dict, new: dict) -> bool:
    """A new channel, tab or starting point means looking at the channel afresh (pure; tested)."""
    return any(old.get(k) != new.get(k) for k in ("url", "source", "start", "start_url"))


def next_due(cfg: dict, after_utc: datetime, tz: str) -> datetime:
    """The next check time (naive UTC) after ``after_utc`` (pure; tested)."""
    from app.core import geo

    if cfg.get("every") == "hours":
        return after_utc + timedelta(hours=max(1, int(cfg.get("n") or 1)))
    hh, mm = (int(x) for x in (cfg.get("at") or "15:00").split(":"))
    local = after_utc.replace(tzinfo=timezone.utc).astimezone(geo.zone(tz)).replace(tzinfo=None)
    if cfg.get("every") == "days":
        step = max(1, int(cfg.get("n") or 1))
        cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        cand = cand if cand > local else cand + timedelta(days=1)
        return geo.to_utc(cand + timedelta(days=step - 1), tz)
    days = set(cfg.get("days") or [6])
    cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    for _ in range(8):
        if cand > local and cand.weekday() in days:
            return geo.to_utc(cand, tz)
        cand += timedelta(days=1)
    return after_utc + timedelta(days=7)


def first_look(entries: list[dict], start: str, start_id: str | None) -> tuple[list[str], set[str], str | None]:
    """The first look at the channel: (backlog oldest first, ids to leave alone, problem). ``entries`` are
    newest first, as YouTube lists them (pure; tested).

    * now: everything already there is left alone; only videos that appear from now on are clipped.
    * all: every video already there is clipped, oldest first, then every new one.
    * from: the chosen video and every one after it, then every new one; older ones are left alone."""
    ids = list(dict.fromkeys(str(e.get("id")) for e in entries if e.get("id")))
    if start == "all":
        return list(reversed(ids)), set(), None
    if start == "from":
        if start_id not in ids:
            return [], set(), "The video to start from is not on the channel's list (check the link and the tab chosen)."
        k = ids.index(start_id)
        return list(reversed(ids[: k + 1])), set(ids[k + 1:]), None
    return [], set(ids), None


def pick_new(entries: list[dict], seen: set[str], backlog: list[str]) -> list[str]:
    """Videos that appeared since the last look, newest first (pure; tested)."""
    queued = set(backlog)
    return [i for i in dict.fromkeys(str(e.get("id")) for e in entries if e.get("id"))
            if i not in seen and i not in queued][: MAX_NEW_PER_CHECK * 3]


READY, LATER, SKIP = "ready", "later", "skip"


def readiness(info: dict) -> str:
    """Is this video a finished live whose recording can be downloaded? (pure; tested)"""
    status = info.get("live_status")
    if status in ("is_live", "is_upcoming", "post_live"):
        return LATER
    if info.get("availability") in ("private", "premium_only", "subscriber_only", "needs_auth") and status != "was_live":
        return SKIP
    length = float(info.get("duration") or 0)
    if status == "was_live" and not length:
        return LATER          # ended, but YouTube has not finished the recording yet (no length shown)
    if status == "was_live" and length >= 120:
        return READY
    if status == "not_live" and float(info.get("duration") or 0) >= 600:
        return READY          # an uploaded full service on the Live tab
    return SKIP if status else LATER


# ─── YouTube (yt-dlp, with the saved cookies) ─────────────────────────

def _ytdlp(args: list[str], cookies: str | None, timeout: int = 180) -> dict:
    cookie_file = None
    try:
        cmd = ["yt-dlp", "--no-warnings", "-J", *args]
        if cookies:
            fd, cookie_file = tempfile.mkstemp(suffix=".txt")
            os.write(fd, cookies.encode("utf-8"))
            os.close(fd)
            os.chmod(cookie_file, 0o600)
            cmd[1:1] = ["--cookies", cookie_file]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            tail = (r.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
            raise RuntimeError(tail[0][:300])
        return json.loads(r.stdout or "{}")
    finally:
        if cookie_file:
            try:
                os.unlink(cookie_file)
            except OSError:
                pass


def list_streams(url: str, cookies: str | None, limit: int = LIST_RECENT) -> list[dict]:
    try:
        data = _ytdlp(["--flat-playlist", "--playlist-end", str(limit), url], cookies, timeout=600)
    except RuntimeError as exc:
        if "does not have a" in str(exc).lower() or "404" in str(exc):
            return []                          # the channel has no such tab (no lives, or no uploads)
        raise
    return [e for e in data.get("entries") or [] if isinstance(e, dict)]


def video_info(video_id: str, cookies: str | None) -> dict:
    return _ytdlp(["--skip-download", "--no-playlist", f"https://www.youtube.com/watch?v={video_id}"], cookies, timeout=120)


# ─── settings storage ─────────────────────────────────────────────────

async def load(db, branch_id: int) -> tuple[dict, dict]:
    from app.core.branch_settings import get_branch_settings

    bs = await get_branch_settings(db, branch_id)
    try:
        cfg = {**DEFAULTS, **json.loads(bs.get("clipper_watch") or "{}")}
    except ValueError:
        cfg = dict(DEFAULTS)
    try:
        state = json.loads(bs.get("clipper_watch_state") or "{}")
    except ValueError:
        state = {}
    return cfg, state


async def save(db, branch_id: int, cfg: dict | None = None, state: dict | None = None) -> None:
    from app.core.branch_settings import update_branch_settings

    values = {}
    if cfg is not None:
        values["clipper_watch"] = json.dumps(cfg)
    if state is not None:
        state["seen"] = list(state.get("seen") or [])[-SEEN_KEEP:]
        state["projects"] = list(state.get("projects") or [])[-10:]
        state["backlog"] = list(state.get("backlog") or [])[:LIST_ALL]
        values["clipper_watch_state"] = json.dumps(state)
    await update_branch_settings(db, branch_id, values)


async def _tz(db, branch_id: int) -> str:
    from app.core.platform import platform_timezone
    from app.models.branch import Branch

    b = await db.get(Branch, branch_id)
    return (getattr(b, "timezone", None) or await platform_timezone(db)) or "Africa/Lagos"


# ─── scheduling and the check ─────────────────────────────────────────

async def dispatch_due(now: datetime | None = None) -> int:
    """Queue a check for every church whose watch is due (scheduler tick)."""
    from sqlalchemy import select

    from app.database import async_session
    from app.models.branch_setting import BranchSetting

    now = now or datetime.utcnow()
    n = 0
    async with async_session() as db:
        rows = (await db.execute(select(BranchSetting.branch_id).where(BranchSetting.key == "clipper_watch"))).scalars().all()
        for branch_id in rows:
            cfg, state = await load(db, branch_id)
            if not cfg.get("enabled") or not streams_url(cfg.get("url")):
                continue
            due = state.get("next_check")
            if due and datetime.fromisoformat(due) > now:
                continue
            # Claim it: the next check time moves on before the job is queued, so it is queued once.
            state["next_check"] = next_due(cfg, now, await _tz(db, branch_id)).isoformat()
            state["queued_at"] = now.isoformat()
            await save(db, branch_id, state=state)
            await db.commit()
            from app.core.jobs import enqueue
            from app.tasks import clip_watch_task

            await enqueue(clip_watch_task, branch_id)
            n += 1
    return n


async def _busy(branch_id: int) -> int:
    """Automatic videos of this church still being worked on (started in the last 12 hours)."""
    from sqlalchemy import select

    from app.database import async_session
    from app.models.clipper import ClipProject

    since = datetime.utcnow() - timedelta(hours=12)
    async with async_session() as db:
        rows = (await db.execute(select(ClipProject.settings).where(
            ClipProject.branch_id == branch_id, ClipProject.created_at >= since,
            ClipProject.status.notin_(("ready", "failed", "stopped"))))).scalars().all()
    return sum(1 for st in rows if (st or {}).get("auto"))


async def clipped_ids(branch_id: int) -> set[str]:
    """YouTube ids of every video this church has already clipped (by hand or automatically, whatever became of
    it): the Automatic Clipper never starts one of them again, whatever its starting point."""
    from sqlalchemy import select

    from app.database import async_session
    from app.models.clipper import ClipProject

    async with async_session() as db:
        urls = (await db.execute(select(ClipProject.source_url).where(
            ClipProject.branch_id == branch_id, ClipProject.source_url.isnot(None)))).scalars().all()
    return {v for v in (video_id(u) for u in urls) if v}


async def check(branch_id: int) -> str:
    """Read the channel, start projects for new finished services and, while the clipper is free, for older ones
    still waiting (the backlog). Returns a one-line summary (also saved)."""
    from app.core.clipper import captions, media, options, pipeline
    from app.database import async_session
    from app.models.clipper import ClipProject

    async with async_session() as db:
        cfg, state = await load(db, branch_id)
    epoch = state.get("epoch")
    tabs = tab_urls(cfg.get("url"), cfg.get("source") or "streams")
    now = datetime.utcnow()
    summary = ""
    started: list[dict] = []
    backlog: list[str] = list(state.get("backlog") or [])
    try:
        if not tabs:
            raise RuntimeError("No channel link is saved.")
        if not media.enough_disk():
            raise RuntimeError("The server is short of disk space; nothing new was started.")
        cookies = await pipeline.youtube_cookies() or None
        baseline = not state.get("baseline")
        limit = LIST_ALL if baseline and cfg.get("start") in ("all", "from") else LIST_RECENT
        entries: list[dict] = []
        for tab in tabs:
            entries += await asyncio.to_thread(list_streams, tab, cookies, limit)
        seen = set(state.get("seen") or [])
        if baseline:
            backlog, mark, problem = first_look(entries, cfg.get("start") or "now", video_id(cfg.get("start_url")))
            if problem:
                raise RuntimeError(problem)
            seen |= mark
            new_ids: list[str] = []
        else:
            new_ids = pick_new(entries, seen, backlog)
        done = await clipped_ids(branch_id)          # never clip a video of this church twice
        seen |= done & {str(e.get("id")) for e in entries}
        backlog = [v for v in backlog if v not in done]
        new_ids = [v for v in new_ids if v not in done]
        titles = {str(e.get("id")): e.get("title") or "" for e in entries}
        busy = await _busy(branch_id)
        # New services first; older ones only while the clipper is not already busy with automatic videos.
        queue = [(v, False) for v in new_ids] + ([(v, True) for v in backlog] if busy < BACKLOG_BUSY else [])
        waiting = 0
        for vid, old in queue:
            if len(started) >= MAX_NEW_PER_CHECK or (old and busy + len(started) >= BACKLOG_BUSY):
                break
            try:
                info = await asyncio.to_thread(video_info, vid, cookies)
            except RuntimeError as exc:
                logger.info("Automatic Clipper b%s: %s not readable yet (%s)", branch_id, vid, exc)
                waiting += 1
                continue
            state_ = readiness(info)
            if state_ == LATER:
                first = (state.setdefault("waiting", {})).setdefault(vid, now.isoformat())
                if now - datetime.fromisoformat(first) > GIVE_UP_AFTER:
                    seen.add(vid)
                    state["waiting"].pop(vid, None)
                    backlog = [x for x in backlog if x != vid]
                    continue
                waiting += 1
                continue
            (state.get("waiting") or {}).pop(vid, None)
            seen.add(vid)
            backlog = [x for x in backlog if x != vid]
            if state_ == SKIP:
                continue
            if vid in await clipped_ids(branch_id):  # started meanwhile (a "Look now" running alongside, or by hand)
                continue
            settings = {**options.DEFAULTS, "content_type": "sermon", "count": AUTO_COUNT, "length": cfg["length"],
                        "format": cfg["format"], "caption_style": cfg["captions"] if captions.style_id(cfg["captions"]) else "none",
                        "min_score": int(cfg.get("min_score") or 0), "refills": AUTO_REFILLS, "auto": True,
                        "speaker": cfg.get("speaker") or "all"}
            async with async_session() as db:
                p = ClipProject(branch_id=branch_id, created_by=None, title=(info.get("title") or titles.get(vid) or "")[:255],
                                source_kind="url", source_url=f"https://www.youtube.com/watch?v={vid}", settings=settings,
                                status="queued", stage="Waiting to start")
                db.add(p)
                await db.commit()
                started.append({"id": p.id, "video": vid, "title": p.title})
            from app.tasks import clip_fetch_task

            clip_fetch_task.apply_async((started[-1]["id"],))
        state["seen"] = list(seen)
        state["baseline"] = True
        left = f" {len(backlog)} older video{'s' if len(backlog) != 1 else ''} still to clip." if backlog else ""
        if started:
            summary = (f"Started {len(started)} video{'s' if len(started) != 1 else ''}: "
                       + "; ".join(x["title"] for x in started)[:300] + "." + left)
        elif waiting:
            summary = (f"{waiting} video{'s' if waiting != 1 else ''} not finished or still processing on YouTube; "
                       "looking again later." + left)
        elif backlog:
            summary = "Waiting for the videos being clipped to finish." + left
        elif baseline and cfg.get("start") == "now":
            summary = "Watching from now on: earlier videos are left alone."
        else:
            summary = "Nothing new to clip."
    except RuntimeError as exc:
        summary = f"Could not read the channel: {exc}"
    except Exception:
        logger.exception("Automatic Clipper failed for branch %s", branch_id)
        summary = "The look failed unexpectedly; it will try again at the next time."
    async with async_session() as db:
        cfg, fresh = await load(db, branch_id)
        if fresh.get("epoch") != epoch:          # the settings were changed (a new start) while this look ran
            return summary
        fresh.update({k: v for k, v in state.items() if k in ("seen", "baseline", "waiting")})
        fresh["backlog"] = backlog
        fresh["last_check"] = now.isoformat()
        fresh["last_result"] = summary
        fresh["projects"] = list(fresh.get("projects") or []) + started
        due = next_due(cfg, now, await _tz(db, branch_id))
        if backlog:
            due = min(due, now + BACKLOG_EVERY)
        if not fresh.get("next_check") or datetime.fromisoformat(fresh["next_check"]) <= now or backlog:
            fresh["next_check"] = due.isoformat()
        await save(db, branch_id, state=fresh)
        await db.commit()
    return summary
