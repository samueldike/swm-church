"""Framing a short: a virtual camera that follows the speaker, the way a camera operator would.

For each source segment, frames are sampled (``SAMPLE_FPS``), faces found with OpenCV's YuNet detector, and one
subject chosen (the camera sticks with the person it is on; someone clearly bigger takes over only after a second).
The camera frames the zone the speaker paces in and holds still; it makes an eased move only after they stay out of
the dead zone for a moment; on a cut in the source video it cuts too. The camera path
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
DEAD_ZONE = 0.16          # of the crop width, either side of centre: the subject may move this far before a move
MAX_SPEED = 0.42          # fastest point of a move, in frame widths per second
DWELL = 0.8               # seconds the subject must stay outside the dead zone before the camera moves
LOOK = 3.0                # seconds ahead the camera looks to frame where the speaker paces
BAND = 0.55               # pacing inside this share of the crop width is framed as one zone (no move)
MIN_MOVE = 0.7            # every move eases in and out over at least this long
SWITCH_SAMPLES = 6        # samples in a row someone else must dominate before the camera changes person
SCENE_CUT = 38.0          # mean absolute grey difference between samples that marks a cut in the source
MAX_KEYS = 70
LINK = 0.08               # a face continues a track when its centre is this close (share of the frame) ...
LINK_GAP = 4              # ... to the track's last face, seen at most this many samples ago
PICTURE_SECONDS = 3.0     # a parked face is judged over at least this long
PICTURE_STILL = 0.12      # a face moving less than this share of its own width (std of its centre) is parked
PICTURE_ALIVE = 3.0       # a face moving this many times more than a parked one is the live person
LIP_SCALE = 2             # lips are read at this many times the detector's width (a mouth is ~12 px at 480)
TALK_WINDOW = 3.0         # seconds either side over which lips are compared with the voice
TALK_MIN_SAMPLES = 10     # fewest lip readings in that window to judge at all
TALK_MIN_RANGE = 6.0      # dB the voice must rise and fall by within the window (else there is nothing to follow)
TALK_MIN = 0.2            # a face is "talking" from this score up ...
TALK_MARGIN = 0.2         # ... and the camera moves to them only when they beat the person it is on by this much
TALK_SWITCH = 24          # samples in a row (4 s) someone else must be the one talking before the camera moves: an
                          # interpreter repeating each sentence never makes it bounce; the first speaker keeps the frame


@lru_cache(maxsize=1)
def _detector_factory():
    import cv2

    return cv2


def _mouth(frame, f, scale: float, cv2, np):
    """The mouth of a detected face (YuNet's two mouth-corner landmarks), as a small brightness-free patch."""
    fh, fw = frame.shape[:2]
    w, h = float(f[2]) * scale, float(f[3]) * scale
    rx, ry, lx, ly = (float(v) * scale for v in f[10:14])
    mx, my = (rx + lx) / 2, (ry + ly) / 2
    mw = max(abs(lx - rx), w * 0.3) * 1.5
    x0, x1 = int(max(0, mx - mw / 2)), int(min(fw, mx + mw / 2))
    y0, y1 = int(max(0, my - 0.14 * h)), int(min(fh, my + 0.16 * h))
    if x1 - x0 < 4 or y1 - y0 < 3:
        return None
    p = cv2.resize(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), (32, 16), interpolation=cv2.INTER_AREA)
    p = p.astype("float32")
    return (p - p.mean()) / (p.std() + 1e-6)


def voice_levels(src: str, start: float, end: float, count: int) -> list[float] | None:
    """Loudness (dB) of the sound for each sample, or None when the source has no sound."""
    import numpy as np

    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{max(0.1, end - start):.3f}",
           "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    a = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
    step = int(8000 / SAMPLE_FPS)
    n = min(count, len(a) // step)
    if n < 2:
        return None
    rms = np.sqrt(np.mean(a[: n * step].reshape(n, step) ** 2, axis=1) + 1e-10)
    levels = [float(v) for v in 20 * np.log10(rms)]
    return levels + [levels[-1]] * (count - n)


def detect(src: str, start: float, end: float, src_w: int, src_h: int) -> list[dict]:
    """[{t, faces:[(cx, cy, w, h, score, talk)] normalised 0..1, cut:bool}] for samples of source[start:end].

    ``talk`` (-1..1, or None when it cannot be told) says how closely that face's lips follow the voice: see
    ``talk_scores``. Pictures of faces (screens, posters) are left out: see ``drop_pictures``."""
    import numpy as np

    cv2 = _detector_factory()
    if src_w <= 0 or src_h <= 0:
        return []
    dw = DETECT_WIDTH
    dh = max(2, int(round(src_h * dw / src_w / 2)) * 2)
    mw = min(src_w, dw * LIP_SCALE) // 2 * 2         # lips are read from a sharper copy than faces are found in
    mh = max(2, int(round(src_h * mw / src_w / 2)) * 2)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{max(0.1, end - start):.3f}",
           "-vf", f"fps={SAMPLE_FPS},scale={mw}:{mh}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-an", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=900).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    frame_bytes = mw * mh * 3
    count = len(raw) // frame_bytes
    if not count:
        return []
    net = cv2.FaceDetectorYN.create(str(ASSETS / "face_detection_yunet_2023mar.onnx"), "", (dw, dh), 0.62, 0.3, 50)
    out, mouths = [], []
    prev_grey = None
    for n in range(count):
        big = np.frombuffer(raw, dtype=np.uint8, count=frame_bytes, offset=n * frame_bytes).reshape(mh, mw, 3)
        frame = cv2.resize(big, (dw, dh), interpolation=cv2.INTER_AREA) if mw != dw else big
        grey = cv2.cvtColor(cv2.resize(frame, (96, max(2, int(96 * dh / dw)))), cv2.COLOR_BGR2GRAY).astype("int16")
        cut = prev_grey is not None and float(np.mean(np.abs(grey - prev_grey))) > SCENE_CUT
        prev_grey = grey
        _, faces = net.detect(frame)
        found, ms = [], []
        if faces is not None:
            for f in faces:
                x, y, w, h, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
                if w < dw * 0.025:          # specks in a crowd shot
                    continue
                found.append(((x + w / 2) / dw, (y + h / 2) / dh, w / dw, h / dh, score))
                ms.append(_mouth(big, f, mw / dw, cv2, np))
        out.append({"t": n / SAMPLE_FPS, "faces": found, "cut": cut})
        mouths.append(ms)
    lips: dict[tuple[int, int], float] = {}
    for tr in tracks(out):
        for (n1, i1), (n2, i2) in zip(tr, tr[1:]):
            a, b = mouths[n1][i1], mouths[n2][i2]
            if n2 - n1 == 1 and a is not None and b is not None:
                lips[(n2, i2)] = float(np.mean(np.abs(b - a)))
    talk = talk_scores(out, lips, voice_levels(src, start, end, count))
    out = [{**s, "faces": [(*f, talk.get((n, k))) for k, f in enumerate(s["faces"])]} for n, s in enumerate(out)]
    return drop_pictures(out)


def _corr(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (vx * vy) ** 0.5


def talk_scores(samples: list[dict], lips: dict[tuple[int, int], float],
                levels: list[float] | None) -> dict[tuple[int, int], float]:
    """{(sample, face): talk} — how closely a face's lip movement follows the loudness of the voice around that
    moment (Pearson correlation over ``TALK_WINDOW`` seconds either side, within the face's own track).

    The person speaking opens and closes their mouth as the sound rises and falls; a listener, a choir member or a
    face on a screen (which runs a little late) does not follow it. Measured on a real service: the preacher +0.2 to
    +0.3, the same face on the LED wall -0.1 to -0.2. Faces with too little to go on get no score."""
    if not levels:
        return {}
    half = int(TALK_WINDOW * SAMPLE_FPS)
    out: dict[tuple[int, int], float] = {}
    for tr in tracks(samples):
        series = [(n, k, lips[(n, k)]) for n, k in tr if (n, k) in lips and n < len(levels)]
        if len(series) < TALK_MIN_SAMPLES:
            continue
        for j, (n, k, _) in enumerate(series):
            win = [(m, v) for m, _, v in series[max(0, j - half):j + half + 1] if abs(m - n) <= half]
            if len(win) < TALK_MIN_SAMPLES:
                continue
            lv = [levels[m] for m, _ in win]
            if max(lv) - min(lv) < TALK_MIN_RANGE:       # steady sound (music, silence): nothing to follow
                continue
            c = _corr([v for _, v in win], lv)
            if c is not None:
                out[(n, k)] = round(c, 3)
    return out


def tracks(samples: list[dict]) -> list[list[tuple[int, int]]]:
    """Faces linked across samples into one track per person: [[(sample index, face index), ...], ...].

    A face continues the track whose last face is nearest (centre within ``LINK`` of a frame and a similar size),
    seen at most ``LINK_GAP`` samples ago; a source cut ends every track."""
    done: list[list[tuple[int, int]]] = []
    live: list[list[tuple[int, int]]] = []
    for n, s in enumerate(samples):
        if s.get("cut"):
            done += live
            live = []
        live_next, taken = [], set()
        for k, f in enumerate(s["faces"]):
            best, gap = None, None
            for j, tr in enumerate(live):
                if j in taken:
                    continue
                m, i = tr[-1]
                g = samples[m]["faces"][i]
                d = max(abs(f[0] - g[0]), abs(f[1] - g[1]))
                ratio = max(f[3], g[3]) / max(1e-6, min(f[3], g[3]))
                if n - m <= LINK_GAP and d < LINK and ratio < 1.6 and (gap is None or d < gap):
                    best, gap = j, d
            if best is None:
                live_next.append([(n, k)])
            else:
                taken.add(best)
                live[best].append((n, k))
        done += [tr for tr in live if n - tr[-1][0] >= LINK_GAP]
        live = [tr for tr in live if n - tr[-1][0] < LINK_GAP] + live_next
    return done + live


def drop_pictures(samples: list[dict]) -> list[dict]:
    """Samples without the faces that are pictures, while a live person is on screen.

    A face on an LED wall, a screen, a poster or a banner behind the speaker is often far bigger than the speaker's
    own face, so "the largest face" would frame the wall. Such a face stays parked in one place: over a track of at
    least ``PICTURE_SECONDS`` its centre moves less than ``PICTURE_STILL`` of its own width. When a face like that
    shares the frame with a smaller face that is alive (it moves at least ``PICTURE_ALIVE`` times as much, measured
    the same way), the parked one is a picture and is left out. Alone, or beside another still face (two people
    seated for an interview), nothing is dropped."""
    import statistics

    trs = tracks(samples)

    def spread(tr):
        fs = [samples[n]["faces"][i] for n, i in tr]
        if len(fs) < 2:
            return 0.0, fs
        w = statistics.median(f[2] for f in fs) or 1e-6
        return max(statistics.pstdev(f[0] for f in fs), statistics.pstdev(f[1] for f in fs)) / w, fs

    need = int(PICTURE_SECONDS * SAMPLE_FPS)
    info = []
    for tr in trs:
        sp, fs = spread(tr)
        info.append({"tr": tr, "spread": sp, "area": statistics.median(f[2] * f[3] for f in fs),
                     "span": (tr[0][0], tr[-1][0]), "long": len(tr) >= need})
    drop: set[tuple[int, int]] = set()
    for a in info:
        if not a["long"] or a["spread"] >= PICTURE_STILL:
            continue
        for b in info:
            if b is a or len(b["tr"]) < need or b["area"] >= a["area"]:
                continue
            overlap = min(a["span"][1], b["span"][1]) - max(a["span"][0], b["span"][0])
            if overlap >= need and b["spread"] >= max(PICTURE_STILL, a["spread"] * PICTURE_ALIVE):
                drop.update(a["tr"])
                break
    if not drop:
        return samples
    return [{**s, "faces": [f for k, f in enumerate(s["faces"]) if (n, k) not in drop]} for n, s in enumerate(samples)]


def choose_subject(samples: list[dict], start_x: float | None = None) -> list[tuple[float, float | None, bool]]:
    """[(t, subject centre x or None, cut)]: the person the camera is on, sample by sample.

    With several people on screen, the one talking leads: a face whose lips follow the voice (``talk``, see
    ``talk_scores``) beats the person the camera is on by ``TALK_MARGIN`` for ``TALK_SWITCH`` samples (4 s) and the
    camera moves to them; it never moves away from the person talking just because someone else is bigger. When
    talking cannot be told (no sound, one face, steady music), the biggest face leads as before: someone else takes
    over only after being clearly the main face for ``SWITCH_SAMPLES`` samples in a row (a second), so a head
    turning or a passer-by never yanks the frame across."""
    def talk(f) -> float | None:
        return f[5] if len(f) > 5 else None

    def talker(faces, largest):
        """The face clearly talking among the speaker-sized ones, or None."""
        sized = [f for f in faces if f[2] >= largest[2] * 0.35 and talk(f) is not None]
        if not sized:
            return None
        best = max(sized, key=talk)
        if talk(best) < TALK_MIN:
            return None
        others = [talk(f) for f in sized if f is not best]
        return best if not others or talk(best) - max(others) >= TALK_MARGIN else None

    def opener(k: int) -> float | None:
        """Where the person talking stands in the first seconds of a shot (talk is only known after a moment)."""
        seen: list[float] = []
        for j in range(k, len(samples)):
            if j > k and (samples[j]["cut"] or samples[j]["t"] - samples[k]["t"] > TALK_WINDOW * 2):
                break
            fs = samples[j]["faces"]
            if fs:
                sp = talker(fs, max(fs, key=lambda f: f[2] * f[3]))
                if sp is not None:
                    seen.append(sp[0])
        if len(seen) < TALK_SWITCH:
            return None
        seen.sort()
        return seen[len(seen) // 2]

    out = []
    cur = start_x
    rival, streak = None, 0
    for n, s in enumerate(samples):
        faces = s["faces"]
        if s["cut"]:
            cur = None if not faces else cur
            rival, streak = None, 0
        if cur is None and faces:
            cur = opener(n)
        pick = None
        if faces:
            largest = max(faces, key=lambda f: f[2] * f[3])
            speaking = talker(faces, largest)
            pick = speaking or largest
            if cur is not None:
                near = [f for f in faces if abs(f[0] - cur) < 0.12]
                if near:
                    stay = max(near, key=lambda f: f[2] * f[3])
                    ts = talk(stay)
                    if speaking is not None and abs(speaking[0] - stay[0]) >= 0.12 and \
                            (ts is None or talk(speaking) - ts >= TALK_MARGIN):
                        contender, need = speaking, TALK_SWITCH          # someone else is the one talking
                    elif speaking is not None and abs(speaking[0] - stay[0]) < 0.12:
                        contender, need = None, 0                        # the camera is on the one talking
                    elif stay[2] * stay[3] * 1.8 < largest[2] * largest[3]:
                        contender, need = largest, SWITCH_SAMPLES        # someone clearly bigger
                    else:
                        contender, need = None, 0
                    if contender is None:
                        pick, rival, streak = stay, None, 0
                    else:
                        streak = streak + 1 if rival is not None and abs(rival - contender[0]) < 0.12 else 1
                        rival = contender[0]
                        pick = contender if streak >= need else stay
        if pick is not None:
            cur = pick[0]
        out.append((s["t"], pick[0] if pick is not None else None, s["cut"]))
    return out


def camera_path(track: list[tuple[float, float | None, bool]], crop_w: float, *, start_x: float | None = None,
                duration: float | None = None) -> list[tuple[float, float]]:
    """Keyframes (t, centre x normalised) of a calm camera operator: holds, then makes eased moves; cuts on cuts.

    * Zone framing: when the speaker paces inside a band that fits in the frame over the next ``LOOK`` seconds,
      the camera frames the middle of that band and does not move at all.
    * Hold, then move: it moves only after the target has been outside the dead zone for ``DWELL`` seconds (at
      once if the speaker is about to leave the frame), and each move eases in and out (smoothstep) over at least
      ``MIN_MOVE`` seconds, never faster than ``MAX_SPEED``. No chasing, no jitter.
    * On a cut in the source video the camera cuts too.
    """
    if not track:
        c = 0.5 if start_x is None else start_x
        return [(0.0, c), (duration or 0.0, c)]
    xs: list[float | None] = [x for _, x, _ in track]
    known = [x for x in xs if x is not None]
    first = known[0] if known else (0.5 if start_x is None else start_x)
    filled, last = [], (start_x if start_x is not None else first)
    for x in xs:
        last = x if x is not None else last
        filled.append(last)
    smooth = []
    for k in range(len(filled)):                        # ~1.5 s median: detector noise and gestures vanish
        win = sorted(filled[max(0, k - 4):k + 5])
        smooth.append(win[len(win) // 2])

    ts = [t for t, _, _ in track]
    cuts = [bool(c) for _, _, c in track]
    n = len(track)
    half = crop_w / 2
    lo, hi = half, 1 - half
    dz = crop_w * DEAD_ZONE

    def clamp(v: float) -> float:
        return min(hi, max(lo, v))

    def desired(k: int) -> float:
        # The zone the speaker paces in around this moment: LOOK seconds ahead and behind, within this shot.
        j, a, b = k, smooth[k], smooth[k]
        while j + 1 < n and ts[j + 1] - ts[k] <= LOOK and not cuts[j + 1]:
            j += 1
            a, b = min(a, smooth[j]), max(b, smooth[j])
        j = k
        while j - 1 >= 0 and ts[k] - ts[j - 1] <= LOOK and not cuts[j]:
            j -= 1
            a, b = min(a, smooth[j]), max(b, smooth[j])
        return clamp((a + b) / 2 if b - a <= crop_w * BAND else smooth[k])

    c = clamp(desired(0))
    if start_x is not None and abs(clamp(start_x) - c) <= dz:
        c = clamp(start_x)
    keys: list[tuple[float, float]] = [(ts[0], c)]
    away = None
    k = 1
    while k < n:
        t = ts[k]
        want = desired(k)
        if cuts[k]:
            if abs(want - c) > dz:
                keys.append((max(keys[-1][0], t - 0.001), c))
                c = want
                keys.append((t, c))
            away, k = None, k + 1
            continue
        if abs(want - c) <= dz:
            away, k = None, k + 1
            continue
        away = t if away is None else away
        leaving = abs(smooth[k] - c) > half * 0.85        # the speaker is nearly out of the frame
        if t - away < DWELL and not leaving:
            k += 1
            continue
        span = max(MIN_MOVE, 1.5 * abs(want - c) / MAX_SPEED)
        keys.append((t, c))
        start, frm = t, c
        for i in range(1, 9):
            u = i / 8
            keys.append((start + span * u, frm + (want - frm) * u * u * (3 - 2 * u)))
        c, away = want, None
        k += 1
        while k < n and ts[k] < start + span and not cuts[k]:
            k += 1
        if k < n and cuts[k] and ts[k] < start + span:   # a source cut interrupts the move: stop it there
            keys = [x for x in keys if x[0] < ts[k] - 0.001]
            u = (ts[k] - 0.001 - start) / span
            c = frm + (want - frm) * u * u * (3 - 2 * u)
            keys.append((ts[k] - 0.001, c))
    end = duration if duration is not None else ts[-1]
    if keys[-1][0] < end:
        keys.append((end, c))
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
