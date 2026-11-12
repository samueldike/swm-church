"""Framing a short: a virtual camera that follows the speaker, the way a camera operator would.

For each source segment, frames are sampled (``SAMPLE_FPS``), faces found with OpenCV's YuNet detector, and one
subject chosen (the largest face, but the camera sticks with the person it is on unless someone clearly bigger
appears). The camera then holds still while the subject stays inside a dead zone in the middle of the frame and
pans smoothly (speed-limited) when they walk out of it; on a cut in the source video it cuts too. The camera path
becomes a piecewise-linear ffmpeg ``crop`` expression, so the render is one ffmpeg pass.

``camera_path`` and ``crop_expr`` are pure (tested); ``detect`` needs OpenCV and ffmpeg.
"""
from __future__ import annotations

import logging
import subprocess
from functools import lru_cache

from app.core.clipper.media import ASSETS

logger = logging.getLogger(__name__)

SAMPLE_FPS = 6.0
DETECT_WIDTH = 480
DEAD_ZONE = 0.22          # of the crop width, either side of centre: the subject may move this far before a pan
MAX_SPEED = 0.42          # pan speed, in frame widths per second
SCENE_CUT = 38.0          # mean absolute grey difference between samples that marks a cut in the source
MAX_KEYS = 70


@lru_cache(maxsize=1)
def _detector_factory():
    import cv2

    return cv2


def detect(src: str, start: float, end: float, src_w: int, src_h: int) -> list[dict]:
    """[{t, faces:[(cx, cy, w, h, score)] normalised 0..1, cut:bool}] for samples of source[start:end]."""
    import numpy as np

    cv2 = _detector_factory()
    if src_w <= 0 or src_h <= 0:
        return []
    dw = DETECT_WIDTH
    dh = max(2, int(round(src_h * dw / src_w / 2)) * 2)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{max(0.1, end - start):.3f}",
           "-vf", f"fps={SAMPLE_FPS},scale={dw}:{dh}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-an", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=600).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    frame_bytes = dw * dh * 3
    count = len(raw) // frame_bytes
    if not count:
        return []
    net = cv2.FaceDetectorYN.create(str(ASSETS / "face_detection_yunet_2023mar.onnx"), "", (dw, dh), 0.62, 0.3, 50)
    out = []
    prev_grey = None
    for n in range(count):
        frame = np.frombuffer(raw, dtype=np.uint8, count=frame_bytes, offset=n * frame_bytes).reshape(dh, dw, 3)
        grey = cv2.cvtColor(cv2.resize(frame, (96, max(2, int(96 * dh / dw)))), cv2.COLOR_BGR2GRAY).astype("int16")
        cut = prev_grey is not None and float(np.mean(np.abs(grey - prev_grey))) > SCENE_CUT
        prev_grey = grey
        _, faces = net.detect(frame)
        found = []
        if faces is not None:
            for f in faces:
                x, y, w, h, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
                if w < dw * 0.025:          # specks in a crowd shot
                    continue
                found.append(((x + w / 2) / dw, (y + h / 2) / dh, w / dw, h / dh, score))
        out.append({"t": n / SAMPLE_FPS, "faces": found, "cut": cut})
    return out


def choose_subject(samples: list[dict], start_x: float | None = None) -> list[tuple[float, float | None, bool]]:
    """[(t, subject centre x or None, cut)]: the person the camera is on, sample by sample."""
    out = []
    cur = start_x
    for s in samples:
        faces = s["faces"]
        if s["cut"]:
            cur = None if not faces else cur
        pick = None
        if faces:
            largest = max(faces, key=lambda f: f[2] * f[3])
            pick = largest
            if cur is not None:
                near = [f for f in faces if abs(f[0] - cur) < 0.12]
                if near:
                    stay = max(near, key=lambda f: f[2] * f[3])
                    if stay[2] * stay[3] * 1.8 >= largest[2] * largest[3]:
                        pick = stay
        if pick is not None:
            cur = pick[0]
        out.append((s["t"], pick[0] if pick is not None else None, s["cut"]))
    return out


def camera_path(track: list[tuple[float, float | None, bool]], crop_w: float, *, start_x: float | None = None,
                duration: float | None = None) -> list[tuple[float, float]]:
    """Keyframes (t, centre x normalised) of a camera that holds, pans smoothly, and cuts on source cuts."""
    if not track:
        c = 0.5 if start_x is None else start_x
        return [(0.0, c), (duration or 0.0, c)]
    # Fill gaps (no face) with the last known position; smooth jitter with a short median.
    xs: list[float | None] = [x for _, x, _ in track]
    known = [x for x in xs if x is not None]
    first = known[0] if known else (0.5 if start_x is None else start_x)
    filled, last = [], (start_x if start_x is not None else first)
    for x in xs:
        last = x if x is not None else last
        filled.append(last)
    smooth = []
    for k in range(len(filled)):
        win = sorted(filled[max(0, k - 2):k + 3])
        smooth.append(win[len(win) // 2])

    half = crop_w / 2
    lo, hi = half, 1 - half
    dz = crop_w * DEAD_ZONE
    keys: list[tuple[float, float]] = []
    c = min(hi, max(lo, smooth[0]))
    prev_t = track[0][0]
    keys.append((prev_t, c))
    for (t, _x, cut), want in zip(track, smooth):
        dt = max(0.0, t - prev_t)
        want = min(hi, max(lo, want))
        if cut and abs(want - c) > dz:
            keys.append((max(prev_t, t - 0.001), c))
            c = want
        elif abs(want - c) > dz:
            step = MAX_SPEED * dt
            goal = want - dz * 0.35 if want > c else want + dz * 0.35
            c = c + max(-step, min(step, goal - c))
        keys.append((t, c))
        prev_t = t
    if duration is not None and keys[-1][0] < duration:
        keys.append((duration, c))
    return simplify(keys)


def simplify(keys: list[tuple[float, float]], tol: float = 0.004) -> list[tuple[float, float]]:
    """Drop keyframes that lie on the line between their neighbours (and cap how many remain)."""
    while True:
        out = [keys[0]]
        for k in range(1, len(keys) - 1):
            (t0, x0), (t1, x1), (t2, x2) = out[-1], keys[k], keys[k + 1]
            if t2 - t0 <= 1e-6:
                out.append(keys[k])
                continue
            interp = x0 + (x2 - x0) * (t1 - t0) / (t2 - t0)
            if abs(interp - x1) > tol:
                out.append(keys[k])
        out.append(keys[-1])
        if len(out) <= MAX_KEYS:
            return out
        keys, tol = out, tol * 1.6


def crop_expr(keys: list[tuple[float, float]], src_w: int, crop_px: int) -> str:
    """ffmpeg expression (in t, seconds from the segment start) for the crop's left edge in pixels."""
    span = src_w - crop_px

    def px(c: float) -> float:
        return round(min(span, max(0.0, c * src_w - crop_px / 2)), 1)

    if len(keys) == 1 or all(abs(px(k[1]) - px(keys[0][1])) < 0.6 for k in keys):
        return f"{px(keys[0][1])}"
    expr = f"{px(keys[-1][1])}"
    for (t0, c0), (t1, c1) in reversed(list(zip(keys, keys[1:]))):
        x0, x1 = px(c0), px(c1)
        if t1 - t0 <= 1e-4:
            piece = f"{x1}"
        elif abs(x1 - x0) < 0.3:
            piece = f"{x0}"
        else:
            piece = f"{x0}+({x1 - x0:.1f})*(t-{t0:.3f})/{t1 - t0:.3f}"
        expr = f"if(lt(t,{t1:.3f}),{piece},{expr})"
    return expr


def output_size(fmt: str, quality: int) -> tuple[int, int]:
    q = 1080 if quality >= 1080 else 720
    if fmt == "square":
        return q, q
    if fmt == "landscape":
        return (1920, 1080) if q == 1080 else (1280, 720)
    return (q, round(q * 16 / 9))


def video_filter(*, fmt: str, quality: int, framing: str, src_w: int, src_h: int, keys=None) -> str:
    """The -vf chain for one segment: crop (following the camera keys) or fit on a blurred background, then scale."""
    ow, oh = output_size(fmt, quality)
    target = ow / oh
    src = src_w / src_h if src_h else target
    if framing == "fit" or (fmt == "landscape" and src < target - 0.01):
        return (f"split[a][b];[a]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
                f"boxblur=24:3,eq=brightness=-0.06[bg];[b]scale={ow}:{oh}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if abs(src - target) < 0.01:
        return f"scale={ow}:{oh},setsar=1"
    if src > target:                                   # wider than the output: crop the sides
        cw = int(src_h * target) // 2 * 2
        if framing == "speaker" and keys:
            x = crop_expr(keys, src_w, cw)
        else:
            x = f"{(src_w - cw) / 2:.1f}"
        return f"crop=w={cw}:h={src_h}:x='{x}':y=0,scale={ow}:{oh},setsar=1"
    ch = int(src_w / target) // 2 * 2                  # taller than the output: crop top and bottom
    y = max(0, min(src_h - ch, int((src_h - ch) * 0.4)))
    return f"crop=w={src_w}:h={ch}:x=0:y={y},scale={ow}:{oh},setsar=1"


def needs_tracking(fmt: str, framing: str, src_w: int, src_h: int) -> bool:
    if framing != "speaker" or not src_h:
        return False
    ow, oh = output_size(fmt, 1080)
    return src_w / src_h > ow / oh + 0.01
