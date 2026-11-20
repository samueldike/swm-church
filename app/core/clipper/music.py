"""Background music under the shorts, mixed so it never competes with the speaker.

Per church (branch setting ``clipper_music``, JSON)::

    {"items": [{"id", "name", "file", "seconds", "start"}], "default": id | "random" | null}

``start`` (seconds) is where the track begins under a short, e.g. after a slow intro or at the chorus; when the
short outlasts the rest of the track, it carries on from the top.

Files live in ``settings.clipper_dir/music/b<branch>/`` (private, served only by ``admin_clipper_music_file``).
``ClipShort.music`` is ``"default"``, ``"none"``, ``"random"`` or a track id; "random" picks a track from the short's
own id, so the same short always gets the same track (and its finished copy stays cached).

How the level is set (``mix_filters``, one pass inside render.finish):

1. every track is brought to the same quiet loudness (``BED_LUFS``, ~18 LU under the -14 LUFS speech), whatever
   its own mastering, and limited so a loud drum hit cannot poke out;
2. it is side-chain compressed by the speech: whenever the speaker talks it dips a further ~10-15 dB, and comes back
   up gently in the pauses;
3. it fades in over 1 s and out over 1.5 s, and loops if the short is longer than the track;
4. the speech itself is untouched: it is mixed on top at full level (``normalize=0``).

Tested in tests/test_clipper.py (including a measured check that the music sits 20 dB+ under speech while speaking).
"""
from __future__ import annotations

import json
import uuid
import zlib
from pathlib import Path

TYPES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
MAX_BYTES = 25 * 1024 * 1024
MAX_SECONDS = 15 * 60
BED_LUFS = -32          # the music bed's loudness before ducking (speech is -14)


class MusicError(ValueError):
    pass


def empty() -> dict:
    return {"items": [], "default": None}


def parse(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    out = empty()
    out["items"] = [i for i in data.get("items") or [] if isinstance(i, dict) and i.get("id") and i.get("file")]
    ids = {i["id"] for i in out["items"]}
    d = data.get("default")
    out["default"] = d if d in ids or (d == "random" and ids) else None
    return out


def resolve(choice: str | None, cfg: dict, short_id: int) -> dict | None:
    """The track under a short (pure): its own choice, else the church default; "random" is stable per short."""
    items = cfg.get("items") or []
    if not items:
        return None

    def pick(key: str | None) -> dict | None:
        if key == "random":
            return items[zlib.crc32(str(short_id).encode()) % len(items)]
        return next((i for i in items if i["id"] == key), None)

    choice = choice or "default"
    if choice == "none":
        return None
    if choice == "default":
        return pick(cfg.get("default"))
    return pick(choice) or pick(cfg.get("default"))      # a deleted track falls back to the default


def start_of(item: dict | None) -> float:
    """Where a track starts playing, kept inside the track (pure)."""
    if not item:
        return 0.0
    try:
        s = float(item.get("start") or 0)
    except (TypeError, ValueError):
        s = 0.0
    return max(0.0, min(s, max(0.0, float(item.get("seconds") or 0) - 5.0)))


def cache_key(item: dict) -> str:
    """Identifies the track AND its start, so moving the start makes new finished copies."""
    return f"{item['id']}s{int(round(start_of(item)))}"


def mix_filters(speech: str, music: str, length: float, out: str) -> list[str]:
    """Filter-graph lines that lay ``music`` under ``speech`` (labels without brackets) as ``out`` (pure; tested)."""
    fade_out = max(0.0, length - 1.5)
    return [
        f"[{speech}]aresample=48000,aformat=channel_layouts=stereo,asplit=2[mxsp][mxsc]",
        f"[{music}]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={length:.3f},asetpts=PTS-STARTPTS,"
        f"loudnorm=I={BED_LUFS}:TP=-12:LRA=7,alimiter=limit=0.25,"
        f"afade=t=in:st=0:d=1,afade=t=out:st={fade_out:.3f}:d=1.5[mxbed]",
        "[mxbed][mxsc]sidechaincompress=threshold=0.012:ratio=12:attack=15:release=450:knee=4[mxduck]",
        f"[mxsp][mxduck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[{out}]",
    ]


def folder(branch_id: int) -> Path:
    from app.config import settings

    d = Path(settings.clipper_dir) / "music" / f"b{int(branch_id)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_of(branch_id: int, item: dict) -> Path:
    return folder(branch_id) / Path(item["file"]).name


def add(cfg: dict, branch_id: int, name: str, filename: str, data: bytes) -> dict:
    from app.core.clipper.media import ClipperError, probe

    ext = Path(filename or "").suffix.lower()
    if ext not in TYPES:
        raise MusicError("Use an MP3, M4A, WAV, OGG or FLAC file.")
    if len(data) > MAX_BYTES:
        raise MusicError("A music file can be at most 25 MB.")
    fid = uuid.uuid4().hex[:12]
    dest = folder(branch_id) / f"{fid}{ext}"
    dest.write_bytes(data)
    try:
        info = probe(dest)
    except ClipperError as exc:
        dest.unlink(missing_ok=True)
        raise MusicError("That file could not be read as music.") from exc
    seconds = float(info.get("duration") or 0)
    if not info.get("has_audio") or seconds < 5:
        dest.unlink(missing_ok=True)
        raise MusicError("That file has no music (or is under 5 seconds).")
    if seconds > MAX_SECONDS:
        dest.unlink(missing_ok=True)
        raise MusicError("Use a track under 15 minutes.")
    item = {"id": fid, "name": (name or "Track").strip()[:60] or "Track", "file": dest.name, "seconds": round(seconds, 1),
            "start": 0.0}
    cfg["items"].append(item)
    return item


def remove(cfg: dict, branch_id: int, fid: str) -> bool:
    item = next((i for i in cfg["items"] if i["id"] == fid), None)
    if not item:
        return False
    cfg["items"] = [i for i in cfg["items"] if i["id"] != fid]
    if cfg.get("default") == fid or (cfg.get("default") == "random" and not cfg["items"]):
        cfg["default"] = None
    path_of(branch_id, item).unlink(missing_ok=True)
    return True


async def load(db, branch_id: int) -> dict:
    from app.core.branch_settings import get_branch_settings

    return parse((await get_branch_settings(db, branch_id)).get("clipper_music"))


async def save(db, branch_id: int, cfg: dict) -> None:
    from app.core.branch_settings import update_branch_settings

    await update_branch_settings(db, branch_id, {"clipper_music": json.dumps(cfg)})
