"""Outros: what plays after a short ends (a church logo card for a few seconds, or a short branded video).

Per church (branch setting ``clipper_outros``, JSON)::

    {"items": [{"id", "name", "kind": "image"|"video", "file", "seconds", "w", "h", "audio": bool}],
     "default": id | null}

Files live in ``settings.clipper_dir/outros/b<branch>/`` (private, served only by ``admin_clipper_outro_file``). An image
plays for ``seconds`` (1-4, chosen when it is added); a video plays whole (at most ``MAX_VIDEO_SECONDS``). Either is
fitted into the short's own shape over a blurred copy of itself, so one outro suits vertical, square and landscape
shorts. ``ClipShort.outro`` is ``"default"``, ``"none"`` or an outro id. Like frames and captions it never re-cuts the
short: render.finish adds it to the download/post copy. Pure helpers tested in tests/test_clipper.py.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

IMAGE_TYPES = {".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image"}
VIDEO_TYPES = {".mp4": "video", ".mov": "video", ".m4v": "video", ".webm": "video"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_VIDEO_BYTES = 60 * 1024 * 1024
MAX_VIDEO_SECONDS = 15.0
IMAGE_SECONDS = (1.0, 4.0)


class OutroError(ValueError):
    pass


def empty() -> dict:
    return {"items": [], "default": None}


def parse(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    out = empty()
    out["items"] = [i for i in data.get("items") or [] if isinstance(i, dict) and i.get("id") and i.get("kind") in ("image", "video")]
    ids = {i["id"] for i in out["items"]}
    out["default"] = data.get("default") if data.get("default") in ids else None
    return out


def resolve(choice: str | None, cfg: dict) -> dict | None:
    """The outro a short ends with: its own choice, else the church default; None for "none" (pure)."""
    choice = choice or "default"
    if choice == "none":
        return None
    items = {i["id"]: i for i in cfg.get("items") or []}
    return items.get(choice) or items.get(cfg.get("default"))


def seconds_of(item: dict | None) -> float:
    return float(item.get("seconds") or 0) if item else 0.0


def image_seconds(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 3.0
    return max(IMAGE_SECONDS[0], min(IMAGE_SECONDS[1], round(v, 1)))


def folder(branch_id: int) -> Path:
    from app.config import settings

    d = Path(settings.clipper_dir) / "outros" / f"b{int(branch_id)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_of(branch_id: int, item: dict) -> Path:
    return folder(branch_id) / Path(item["file"]).name


def add(cfg: dict, branch_id: int, name: str, filename: str, data: bytes, seconds) -> dict:
    """Check and store an uploaded outro (raises OutroError with a message for the person)."""
    ext = Path(filename or "").suffix.lower()
    kind = IMAGE_TYPES.get(ext) or VIDEO_TYPES.get(ext)
    if not kind:
        raise OutroError("Use a PNG, JPG or WEBP picture, or an MP4, MOV or WEBM video.")
    fid = uuid.uuid4().hex[:12]
    dest = folder(branch_id) / f"{fid}{ext}"
    item = {"id": fid, "name": (name or "Outro").strip()[:60] or "Outro", "kind": kind, "file": dest.name}
    if kind == "image":
        import io

        from PIL import Image

        if len(data) > MAX_IMAGE_BYTES:
            raise OutroError("An outro picture can be at most 5 MB.")
        try:
            img = Image.open(io.BytesIO(data))
            img.verify()
            w, h = Image.open(io.BytesIO(data)).size
        except Exception as exc:
            raise OutroError("That picture could not be read.") from exc
        dest.write_bytes(data)
        item.update(seconds=image_seconds(seconds), w=w, h=h, audio=False)
    else:
        from app.core.clipper.media import ClipperError, probe

        if len(data) > MAX_VIDEO_BYTES:
            raise OutroError("An outro video can be at most 60 MB.")
        dest.write_bytes(data)
        try:
            info = probe(dest)
        except ClipperError as exc:
            dest.unlink(missing_ok=True)
            raise OutroError("That video could not be read.") from exc
        if not info.get("has_video"):
            dest.unlink(missing_ok=True)
            raise OutroError("That file has no picture.")
        if float(info.get("duration") or 0) > MAX_VIDEO_SECONDS + 0.5:
            dest.unlink(missing_ok=True)
            raise OutroError(f"An outro video can be at most {int(MAX_VIDEO_SECONDS)} seconds long.")
        item.update(seconds=round(float(info.get("duration") or 0), 2), w=int(info.get("width") or 0),
                    h=int(info.get("height") or 0), audio=bool(info.get("has_audio")))
    cfg["items"].append(item)
    if not cfg.get("default"):
        cfg["default"] = fid               # the first outro becomes the default
    return item


def remove(cfg: dict, branch_id: int, fid: str) -> bool:
    item = next((i for i in cfg["items"] if i["id"] == fid), None)
    if not item:
        return False
    cfg["items"] = [i for i in cfg["items"] if i["id"] != fid]
    if cfg.get("default") == fid:
        cfg["default"] = None
    path_of(branch_id, item).unlink(missing_ok=True)
    return True


def fit_filter(width: int, height: int, label_in: str, label_out: str) -> str:
    """Fit a picture into width x height over a blurred, darkened copy of itself (pure; tested)."""
    a, b, bg, fg = f"{label_out}a", f"{label_out}b", f"{label_out}bg", f"{label_out}fg"
    return (f"[{label_in}]split[{a}][{b}];"
            f"[{a}]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=24:3,"
            f"eq=brightness=-0.08[{bg}];"
            f"[{b}]scale={width}:{height}:force_original_aspect_ratio=decrease[{fg}];"
            f"[{bg}][{fg}]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[{label_out}]")


async def load(db, branch_id: int) -> dict:
    from app.core.branch_settings import get_branch_settings

    return parse((await get_branch_settings(db, branch_id)).get("clipper_outros"))


async def save(db, branch_id: int, cfg: dict) -> None:
    from app.core.branch_settings import update_branch_settings

    await update_branch_settings(db, branch_id, {"clipper_outros": json.dumps(cfg)})
