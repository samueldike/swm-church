"""A short must open on the person speaking, not on a scripture slide or a wide shot of the room.

Church recordings often cut to a full-screen Bible verse while the preacher reads it. A short that opens there
looks like a slideshow and loses the scroller in the first second, so before an AI short is rendered its first
seconds are looked at (the camera's own face detector) and, when nobody is clearly on screen:

1. a 2-8 second line from inside the same short where the speaker IS on screen becomes the cold open (it plays
   first and again in its place, so nothing he said is lost), or
2. the short starts up to ``MAX_SKIP`` sentences later, at the first sentence that opens on the speaker, if it is
   still long enough.

Otherwise the short is left as it is. Everything here is pure except the ``look`` callback, which returns the share
of sampled frames in a source window that show a speaker-sized face (tested in tests/test_clipper.py).
"""
from __future__ import annotations

OPENING = 2.0          # seconds of the short that must show the speaker
SHOW = 0.6             # share of those frames with a speaker-sized face
MIN_FACE_W = 0.03      # a face narrower than this share of the frame is the congregation, not the speaker
COLD_MIN, COLD_MAX = 2.0, 8.0
MAX_SKIP = 2


def speaker_share(samples: list[dict], min_w: float = MIN_FACE_W) -> float:
    """Share of samples showing at least one speaker-sized face (``reframe.detect`` samples)."""
    if not samples:
        return 0.0
    hit = sum(1 for s in samples if any(f[2] >= min_w and f[4] >= 0.7 for f in s.get("faces") or []))
    return hit / len(samples)


def opening_window(segments: list[list[float]], seconds: float = OPENING) -> list[tuple[float, float]]:
    """The source ranges that play during the short's first ``seconds``."""
    out, left = [], seconds
    for s, e in segments:
        if left <= 0:
            break
        take = min(e - s, left)
        out.append((s, s + take))
        left -= take
    return out


def _units_in(units, piece: dict) -> list:
    return [u for u in units if u.w0 >= piece["w0"] and u.w1 <= piece["w1"]]


def choose_opening(plan: list[dict], units, segments: list[list[float]], look, *, length_after_skip=None,
                   floor: float = 0.0) -> tuple[list[dict], str]:
    """(plan, what was done): "kept" | "cold_open" | "skipped". ``look(start, end) -> share``."""
    window = opening_window(segments)
    if not window:
        return plan, "kept"
    total = sum(b - a for a, b in window)
    seen = sum(look(a, b) * (b - a) for a, b in window) / (total or 1.0)
    if seen >= SHOW:
        return plan, "kept"

    body = [p for p in plan if not p.get("spare") and not p.get("cold_open")]
    if not body:
        return plan, "kept"
    # 1. A cold open from inside the short, where the speaker is on screen.
    for piece in body:
        for u in _units_in(units, piece):
            if COLD_MIN <= u.end - u.start <= COLD_MAX and look(u.start, min(u.end, u.start + OPENING)) >= SHOW:
                hook = {"w0": u.w0, "w1": u.w1, "start": u.start, "end": u.end, "text": u.text, "role": "hook",
                        "cold_open": True}
                return [hook] + [p for p in plan if not p.get("cold_open")], "cold_open"
    # 2. Start a sentence or two later.
    first = body[0]
    inside = _units_in(units, first)
    for skip in range(1, min(MAX_SKIP, len(inside) - 1) + 1):
        u = inside[skip]
        if look(u.start, u.start + OPENING) < SHOW:
            continue
        trimmed = [dict(p) for p in plan if not p.get("cold_open")]
        k = next(i for i, p in enumerate(trimmed) if p is not None and p["w0"] == first["w0"] and not p.get("spare"))
        trimmed[k] = {**trimmed[k], "w0": u.w0, "start": u.start}
        if length_after_skip is not None and length_after_skip(trimmed) < floor:
            break
        return trimmed, "skipped"
    return plan, "kept"
