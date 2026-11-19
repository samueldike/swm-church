"""Watch a church's YouTube channel and clip every finished live service automatically.

Per church (branch settings, JSON):

* ``clipper_watch``: {"enabled", "url" (the channel), "every": "hours"|"days"|"weekdays", "n" (every N hours/days),
  "days" ([0..6], Monday = 0, for "weekdays"), "at" ("HH:MM" church time, for days/weekdays), "format", "length",
  "captions" (a design id or "none"), "min_score" (0-100: moments below it are not cut), "latest" (on the first check,
  also clip the most recent finished live)}.
* ``clipper_watch_state``: {"last_check", "next_check" (UTC ISO), "seen" (video ids already handled), "baseline",
  "last_result", "projects" ([{id, video, title}] the newest auto projects)}.

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
DEFAULTS = {"enabled": False, "url": "", "every": "hours", "n": 6, "days": [6], "at": "15:00", "format": "vertical",
            "length": "30-60", "captions": "none", "min_score": 70, "latest": True}
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


def clean(form, current: dict | None = None) -> tuple[dict, list[str]]:
    """Validated watch settings from the form, and the problems found."""
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
    out["latest"] = get("latest") == "1"
    return out, problems


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


def pick_new(entries: list[dict], seen: set[str], *, baseline: bool, latest: bool) -> tuple[list[str], set[str]]:
    """(video ids to look at now, ids to mark seen without clipping). On the first check (``baseline``) everything
    already on the channel is marked seen, except the most recent when ``latest`` (pure; tested)."""
    ids = [str(e.get("id")) for e in entries if e.get("id")]
    fresh = [i for i in ids if i not in seen]
    if baseline:
        keep = fresh[:1] if latest else []
        return keep, set(fresh[len(keep):])
    return fresh[:MAX_NEW_PER_CHECK * 3], set()


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


def list_streams(url: str, cookies: str | None) -> list[dict]:
    data = _ytdlp(["--flat-playlist", "--playlist-end", "15", url], cookies)
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


async def check(branch_id: int) -> str:
    """List the Live tab, start projects for finished lives. Returns a one-line summary (also saved)."""
    from app.core.clipper import captions, media, options, pipeline
    from app.database import async_session
    from app.models.clipper import ClipProject

    async with async_session() as db:
        cfg, state = await load(db, branch_id)
    url = streams_url(cfg.get("url"))
    now = datetime.utcnow()
    summary = ""
    started: list[dict] = []
    try:
        if not url:
            raise RuntimeError("No channel link is saved.")
        if not media.enough_disk():
            raise RuntimeError("The server is short of disk space; nothing new was started.")
        cookies = await pipeline.youtube_cookies() or None
        entries = await asyncio.to_thread(list_streams, url, cookies)
        seen = set(state.get("seen") or [])
        baseline = not state.get("baseline")
        look, mark = pick_new(entries, seen, baseline=baseline, latest=bool(cfg.get("latest")))
        seen |= mark
        waiting = 0
        titles = {str(e.get("id")): e.get("title") or "" for e in entries}
        for vid in look:
            if len(started) >= MAX_NEW_PER_CHECK:
                break
            try:
                info = await asyncio.to_thread(video_info, vid, cookies)
            except RuntimeError as exc:
                logger.info("Watch b%s: %s not readable yet (%s)", branch_id, vid, exc)
                waiting += 1
                continue
            state_ = readiness(info)
            if state_ == LATER:
                first = (state.setdefault("waiting", {})).setdefault(vid, now.isoformat())
                if now - datetime.fromisoformat(first) > GIVE_UP_AFTER:
                    seen.add(vid)
                    state["waiting"].pop(vid, None)
                    continue
                waiting += 1
                continue
            (state.get("waiting") or {}).pop(vid, None)
            seen.add(vid)
            if state_ == SKIP:
                continue
            settings = {**options.DEFAULTS, "content_type": "sermon", "count": AUTO_COUNT, "length": cfg["length"],
                        "format": cfg["format"], "caption_style": cfg["captions"] if captions.style_id(cfg["captions"]) else "none",
                        "min_score": int(cfg.get("min_score") or 0), "refills": AUTO_REFILLS, "auto": True}
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
        if started:
            summary = f"Started {len(started)} new video{'s' if len(started) != 1 else ''}: " + "; ".join(s["title"] for s in started)[:300]
        elif waiting:
            summary = f"{waiting} live{'s' if waiting != 1 else ''} not finished or still processing on YouTube; checking again later."
        elif baseline:
            summary = "Watching from now on: earlier videos are left alone."
        else:
            summary = "No new finished live."
    except RuntimeError as exc:
        summary = f"Could not read the channel: {exc}"
    except Exception:
        logger.exception("YouTube watch failed for branch %s", branch_id)
        summary = "The check failed unexpectedly; it will try again at the next time."
    async with async_session() as db:
        cfg, fresh = await load(db, branch_id)
        fresh.update({k: v for k, v in state.items() if k in ("seen", "baseline", "waiting")})
        fresh["last_check"] = now.isoformat()
        fresh["last_result"] = summary
        fresh["projects"] = list(fresh.get("projects") or []) + started
        if not fresh.get("next_check") or datetime.fromisoformat(fresh["next_check"]) <= now:
            fresh["next_check"] = next_due(cfg, now, await _tz(db, branch_id)).isoformat()
        await save(db, branch_id, state=fresh)
        await db.commit()
    return summary
