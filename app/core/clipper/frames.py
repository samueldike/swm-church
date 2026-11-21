"""Video frames: a church's PNG overlays (logo bar, border, lower third) laid over its shorts.

Per church (branch setting ``clipper_frames``, JSON)::

    {"items": [{"id", "name", "format": "vertical"|"square"|"landscape", "file", "w", "h"}],
     "default": {"vertical": id | null, "square": ..., "landscape": ...}}

Files live in ``settings.clipper_dir/frames/b<branch>/`` (private, served only by ``admin_clipper_frame_file``). A
frame's shape comes from its own size (9:16, 1:1 or 16:9), and it is stretched to the short's exact size, so it always
fits the short it is laid on. ``ClipShort.frame`` is ``"default"`` (the church's default for that shape, if any),
``"none"``, or a frame id. Like captions, frames never re-cut a short: the grid and the editor show them live over
the video, and the download/post copy gets them in one pass (render.finish). Pure helpers tested in test_clipper.py.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

FORMATS = ("vertical", "square", "landscape")
MAX_BYTES = 5 * 1024 * 1024
RATIOS = {"vertical": 9 / 16, "square": 1.0, "landscape": 16 / 9}
TOLERANCE = 0.06


class FrameError(ValueError):
    pass


def shape_of(w: int, h: int) -> str | None:
    """The short shape a w x h picture fits (pure)."""
    if w <= 0 or h <= 0:
        return None
    r = w / h
    for fmt, want in RATIOS.items():
        if abs(r - want) / want <= TOLERANCE:
            return fmt
    return None


def empty() -> dict:
    return {"items": [], "default": {f: None for f in FORMATS}}


def parse(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    out = empty()
    out["items"] = [i for i in data.get("items") or [] if isinstance(i, dict) and i.get("id") and i.get("format") in FORMATS]
    ids = {i["id"] for i in out["items"]}
    for f in FORMATS:
        d = (data.get("default") or {}).get(f)
        out["default"][f] = d if d in ids else None
    return out


def resolve(choice: str | None, fmt: str, cfg: dict) -> dict | None:
    """The frame a short shows: its own choice, the church default for its shape, or None (pure)."""
    choice = choice or "default"
    if choice == "none":
        return None
    items = {i["id"]: i for i in cfg.get("items") or []}
    item = items.get(choice)
    if item is None or item["format"] != fmt:        # "default", a deleted frame, or one made for another shape
        item = items.get((cfg.get("default") or {}).get(fmt))
    return item if item and item["format"] == fmt else None


def folder(branch_id: int) -> Path:
    from app.config import settings

    d = Path(settings.clipper_dir) / "frames" / f"b{int(branch_id)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_of(branch_id: int, item: dict) -> Path:
    return folder(branch_id) / Path(item["file"]).name


KEY_DEFAULT = "#00FF00"
KEY_TOLERANCE = 40          # 1-100: how far from the picked colour still counts as "the screen"


def _hex_rgb(value: str | None) -> tuple[int, int, int]:
    v = str(value or KEY_DEFAULT).strip().lstrip("#")
    if len(v) != 6 or any(c not in "0123456789abcdefABCDEF" for c in v):
        v = KEY_DEFAULT[1:]
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _chroma(rgb):
    """(Cb, Cr) of RGB values 0-255 (numpy arrays or numbers): the colour's tint, without its brightness."""
    r, g, b = rgb
    return (128 - 0.168736 * r - 0.331264 * g + 0.5 * b, 128 + 0.5 * r - 0.418688 * g - 0.081312 * b)


def chroma_key(data: bytes, color: str | None = None, tolerance: int | None = None) -> tuple[bytes, float]:
    """A frame drawn on a green (or any one-colour) screen made see-through where that colour is. Returns
    (PNG bytes, share of the picture that became see-through). The colour is compared by tint (Cb/Cr), so white,
    black and gold lettering survive however bright or dark they are; edges fade over a soft band, and the screen's
    colour spilling onto the edges is taken out. The same rule draws the upload preview (clipper-keyer.js)."""
    import io

    import numpy as np
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as exc:
        raise FrameError("That picture could not be read.") from exc
    px = np.asarray(img).astype(np.float32)
    rgb, alpha = px[..., :3], px[..., 3]
    key = _hex_rgb(color)
    kcb, kcr = _chroma(key)
    cb, cr = _chroma((rgb[..., 0], rgb[..., 1], rgb[..., 2]))
    d = np.sqrt((cb - kcb) ** 2 + (cr - kcr) ** 2) / 255.0
    tol = max(1, min(100, int(tolerance or KEY_TOLERANCE)))
    t1 = tol / 100 * 0.4
    t2 = t1 * 1.6
    keep = np.clip((d - t1) / max(1e-6, t2 - t1), 0.0, 1.0)
    # Spill: near the screen, the screen's main channel may not exceed the other two (no green fringe).
    k = int(np.argmax(key))
    others = [c for c in range(3) if c != k]
    near = d < t2 * 1.8
    cap = np.maximum(rgb[..., others[0]], rgb[..., others[1]])
    rgb[..., k] = np.where(near, np.minimum(rgb[..., k], cap), rgb[..., k])
    out = np.dstack([rgb, alpha * keep]).clip(0, 255).astype(np.uint8)
    cleared = float((keep < 0.5).mean())
    buf = io.BytesIO()
    Image.fromarray(out, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue(), cleared


def check_png(data: bytes) -> tuple[int, int, str]:
    """(w, h, shape) of an uploaded frame, or FrameError with a message for the person."""
    import io

    from PIL import Image

    if len(data) > MAX_BYTES:
        raise FrameError("A frame can be at most 5 MB.")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise FrameError("Upload a PNG with a see-through middle, so the video shows, or tick \"Remove a green screen\" "
                         "if the picture has a green (or other one-colour) screen where the video goes.")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise FrameError("That PNG could not be read.") from exc
    w, h = img.size
    shape = shape_of(w, h)
    if shape is None:
        raise FrameError(f"That frame is {w}×{h}. Use 9:16 (e.g. 1080×1920), 1:1 (1080×1080) or 16:9 (1920×1080).")
    if img.mode not in ("RGBA", "LA", "P") or (img.mode == "P" and "transparency" not in img.info):
        raise FrameError("That picture has no see-through area, so it would cover the video. If it has a green (or other "
                         "one-colour) screen where the video goes, tick \"Remove a green screen\".")
    return w, h, shape


def add(cfg: dict, branch_id: int, name: str, data: bytes, *, key: str | None = None,
        tolerance: int | None = None) -> dict:
    if len(data) > MAX_BYTES:
        raise FrameError("A frame can be at most 5 MB.")
    if key:                                       # a green-screen frame: the screen becomes see-through first
        data, cleared = chroma_key(data, key, tolerance)
        if cleared < 0.02:
            raise FrameError("That colour was not found in the picture. Click the screen colour in the preview to pick "
                             "it, or raise how much of it is removed.")
    w, h, shape = check_png(data)
    fid = uuid.uuid4().hex[:12]
    fname = f"{fid}.png"
    (folder(branch_id) / fname).write_bytes(data)
    item = {"id": fid, "name": (name or "Frame").strip()[:60] or "Frame", "format": shape, "file": fname, "w": w, "h": h}
    cfg["items"].append(item)
    if not cfg["default"].get(shape):
        cfg["default"][shape] = fid          # the first frame of a shape becomes its default
    return item


def remove(cfg: dict, branch_id: int, fid: str) -> bool:
    item = next((i for i in cfg["items"] if i["id"] == fid), None)
    if not item:
        return False
    cfg["items"] = [i for i in cfg["items"] if i["id"] != fid]
    for f in FORMATS:
        if cfg["default"].get(f) == fid:
            cfg["default"][f] = None
    try:
        path_of(branch_id, item).unlink()
    except FileNotFoundError:
        pass
    return True


async def load(db, branch_id: int) -> dict:
    from app.core.branch_settings import get_branch_settings

    return parse((await get_branch_settings(db, branch_id)).get("clipper_frames"))


async def save(db, branch_id: int, cfg: dict) -> None:
    from app.core.branch_settings import update_branch_settings

    await update_branch_settings(db, branch_id, {"clipper_frames": json.dumps(cfg)})
