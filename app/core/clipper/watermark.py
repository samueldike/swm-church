"""Watermark: the ministry's logo in a corner of every short (or the church's own mark, or none).

Per church (branch setting ``clipper_watermark``, JSON)::

    {"items": [{"id", "name", "file", "w", "h"}],          # the church's own uploads
     "default": "ministry" | id | null,                     # null = no watermark by default
     "position": "top-right", "size": 14, "opacity": 80, "tone": "original" | "white"}

The ministry logo (``MINISTRY``, the site's own ``static/images/swm-logo.png``) is always in the list and is the
default until the church chooses another or none, so a new church gets the ministry's mark with no setup. Uploads live
in ``settings.clipper_dir/watermarks/b<branch>/`` (private, served only by ``admin_clipper_watermark_file``).
``ClipShort.watermark`` is ``"default"``, ``"none"`` or an id (``"ministry"`` included). Like frames it never re-cuts a
short: the grid and editor show it live, and render.finish puts it on the download/post copy (over the frame, under
the captions). Pure helpers tested in tests/test_clipper.py.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

MINISTRY = "ministry"
MINISTRY_NAME = "Ministry logo"
POSITIONS = {"top-left": "Top left", "top-right": "Top right", "bottom-left": "Bottom left", "bottom-right": "Bottom right"}
SIZES = (8, 30)              # percent of the short's width
OPACITY = (20, 100)          # percent
TONES = {"original": "As uploaded", "white": "All white"}
MARGIN = 0.04                # of the short's width, from the edges
MAX_BYTES = 5 * 1024 * 1024
DEFAULTS = {"position": "top-right", "size": 14, "opacity": 80, "tone": "original"}


class WatermarkError(ValueError):
    pass


def ministry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "static" / "images" / "swm-logo.png"


def _clamp(value, lo: int, hi: int, fallback: int) -> int:
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, v))


def empty() -> dict:
    return {"items": [], "default": MINISTRY, **DEFAULTS}


def parse(raw: str | None) -> dict:
    """Saved settings, cleaned (pure). Nothing saved yet = the ministry logo, top right."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = empty()
    out["items"] = [i for i in data.get("items") or [] if isinstance(i, dict) and i.get("id") and i.get("file")
                    and i["id"] != MINISTRY]
    ids = {i["id"] for i in out["items"]} | {MINISTRY}
    if "default" in data:
        out["default"] = data["default"] if data["default"] in ids else None
    out["position"] = data.get("position") if data.get("position") in POSITIONS else DEFAULTS["position"]
    out["size"] = _clamp(data.get("size"), *SIZES, DEFAULTS["size"])
    out["opacity"] = _clamp(data.get("opacity"), *OPACITY, DEFAULTS["opacity"])
    out["tone"] = data.get("tone") if data.get("tone") in TONES else DEFAULTS["tone"]
    return out


def choices(cfg: dict) -> list[dict]:
    """Every mark a short can use, the ministry logo first (pure)."""
    return [{"id": MINISTRY, "name": MINISTRY_NAME, "builtin": True}] + [
        {"id": i["id"], "name": i.get("name") or "Watermark", "builtin": False} for i in cfg.get("items") or []]


def resolve(choice: str | None, cfg: dict) -> dict | None:
    """The mark a short shows: its own choice, else the church default; None for "none" (pure)."""
    choice = choice or "default"
    if choice == "none":
        return None
    marks = {MINISTRY: {"id": MINISTRY, "name": MINISTRY_NAME, "builtin": True},
             **{i["id"]: i for i in cfg.get("items") or []}}
    if choice in marks:
        return marks[choice]
    return marks.get(cfg.get("default"))          # "default" or a deleted mark: the church default (None = none)


def placement(cfg: dict, width: int, height: int, mark_w: int, mark_h: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the mark on a width x height short (pure): ``size`` % of the width, kept inside the short,
    ``MARGIN`` from the chosen corner, never taller than a quarter of the short."""
    w = max(2, int(width * cfg.get("size", DEFAULTS["size"]) / 100))
    h = max(2, int(round(w * mark_h / max(1, mark_w))))
    if h > height // 4:
        h = max(2, height // 4)
        w = max(2, int(round(h * mark_w / max(1, mark_h))))
    w, h = w - w % 2, h - h % 2
    m = int(width * MARGIN)
    pos = cfg.get("position") or DEFAULTS["position"]
    x = m if pos.endswith("left") else width - w - m
    y = m if pos.startswith("top") else height - h - m
    return max(0, x), max(0, y), w, h


def overlay_filters(cfg: dict, src: str, base: str, out: str, width: int, height: int, mark_w: int, mark_h: int) -> list[str]:
    """ffmpeg filter lines laying the mark ``src`` over ``base`` (pure; tested)."""
    x, y, w, h = placement(cfg, width, height, mark_w, mark_h)
    alpha = max(0.0, min(1.0, cfg.get("opacity", DEFAULTS["opacity"]) / 100))
    white = ",lutrgb=r=255:g=255:b=255" if cfg.get("tone") == "white" else ""
    return [f"[{src}]scale={w}:{h}:flags=lanczos,format=rgba{white},colorchannelmixer=aa={alpha:.2f}[wm]",
            f"[{base}][wm]overlay={x}:{y}:format=auto[{out}]"]


def cache_key(cfg: dict, mark: dict | None) -> str | None:
    """Short stable id of the mark and how it is placed, for the finished file's name (pure)."""
    if not mark:
        return None
    raw = f"{mark['id']}|{cfg.get('position')}|{cfg.get('size')}|{cfg.get('opacity')}|{cfg.get('tone')}"
    return "w" + hashlib.sha1(raw.encode()).hexdigest()[:8]


def folder(branch_id: int) -> Path:
    from app.config import settings

    d = Path(settings.clipper_dir) / "watermarks" / f"b{int(branch_id)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_of(branch_id: int, mark: dict | None) -> Path | None:
    if not mark:
        return None
    if mark["id"] == MINISTRY:
        return ministry_path()
    return folder(branch_id) / Path(mark["file"]).name if mark.get("file") else None


def item(cfg: dict, mark_id: str) -> dict | None:
    """The stored entry (with its file) for an uploaded mark."""
    return next((i for i in cfg.get("items") or [] if i["id"] == mark_id), None)


def size_of(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as img:
        return img.size


def check_image(data: bytes) -> tuple[int, int]:
    """(w, h) of an uploaded mark, or WatermarkError with a message for the person."""
    import io

    from PIL import Image

    if len(data) > MAX_BYTES:
        raise WatermarkError("A watermark can be at most 5 MB.")
    if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data[8:12] == b"WEBP"):
        raise WatermarkError("Upload a PNG (or WebP) logo with a see-through background.")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise WatermarkError("That picture could not be read.") from exc
    w, h = img.size
    if w < 64 or h < 64:
        raise WatermarkError(f"That logo is only {w}×{h}. Use one at least 300 pixels wide so it stays sharp.")
    return w, h


def add(cfg: dict, branch_id: int, name: str, data: bytes) -> dict:
    w, h = check_image(data)
    mid = uuid.uuid4().hex[:12]
    fname = f"{mid}.png"
    if data[8:12] == b"WEBP":                    # keep one format on disk: PNG
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.open(io.BytesIO(data)).convert("RGBA").save(buf, "PNG")
        data = buf.getvalue()
    (folder(branch_id) / fname).write_bytes(data)
    entry = {"id": mid, "name": (name or "Watermark").strip()[:60] or "Watermark", "file": fname, "w": w, "h": h}
    cfg["items"].append(entry)
    return entry


def remove(cfg: dict, branch_id: int, mark_id: str) -> bool:
    entry = item(cfg, mark_id)
    if not entry:
        return False
    cfg["items"] = [i for i in cfg["items"] if i["id"] != mark_id]
    if cfg.get("default") == mark_id:
        cfg["default"] = MINISTRY
    try:
        (folder(branch_id) / Path(entry["file"]).name).unlink()
    except FileNotFoundError:
        pass
    return True


async def load(db, branch_id: int) -> dict:
    from app.core.branch_settings import get_branch_settings

    return parse((await get_branch_settings(db, branch_id)).get("clipper_watermark"))


async def save(db, branch_id: int, cfg: dict) -> None:
    from app.core.branch_settings import update_branch_settings

    await update_branch_settings(db, branch_id, {"clipper_watermark": json.dumps(cfg)})
