"""Captions for a short, timed word by word on the short's own timeline (app/core/clipper/cut.py words).

* ``bold``: two or three words at a time, upper case, heavy outline, the word being spoken in gold (the look of
  most short-form video); burned in with libass.
* ``clean``: sentence-case subtitles on a soft dark box, a short line at a time.
* Every short also gets an ``.srt`` for platforms that take caption files.
"""
from __future__ import annotations

import re

from app.core.clipper.media import ASSETS

GOLD = "&H0037C8F0"      # ASS colours are &HAABBGGRR: this is #F0C837
WHITE = "&H00FFFFFF"
BLACK = "&H00000000"


def fonts_dir() -> str:
    return str(ASSETS)


def _group(words: list[list], max_words: int, max_chars: int, max_gap: float = 0.55) -> list[list[list]]:
    groups: list[list[list]] = []
    cur: list[list] = []
    for w in words:
        if cur:
            text_len = sum(len(x[2]) + 1 for x in cur) + len(w[2])
            ends = re.search(r"[.?!,;:]$", cur[-1][2]) is not None
            if len(cur) >= max_words or text_len > max_chars or w[0] - cur[-1][1] > max_gap or (ends and len(cur) >= 2):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _clean_word(w: str, upper: bool) -> str:
    w = w.strip()
    if upper:
        w = re.sub(r"[,;:]$", "", w).upper()
    return w


def ass(words: list[list], *, style: str, width: int, height: int) -> str:
    vertical = height > width
    base = min(width, height)
    if style == "bold":
        size = round(base * (0.082 if vertical else 0.072))
        margin_v = round(height * (0.24 if vertical else 0.12))
        styles = (f"Style: Main,Poppins ExtraBold,{size},{WHITE},{WHITE},{BLACK},&H96000000,0,0,0,0,100,100,0,0,1,"
                  f"{max(3, round(size * 0.075))},{max(1, round(size * 0.04))},2,60,60,{margin_v},1")
        groups = _group(words, 3, 18)
    else:
        size = round(base * (0.052 if vertical else 0.05))
        margin_v = round(height * (0.16 if vertical else 0.08))
        styles = (f"Style: Main,Poppins SemiBold,{size},{WHITE},{WHITE},&H8C000000,&H8C000000,0,0,0,0,100,100,0,0,3,"
                  f"{max(4, round(size * 0.22))},0,2,80,80,{margin_v},1")
        groups = _group(words, 8, 34 if vertical else 48, 0.8)

    events = []
    for n, g in enumerate(groups):
        start = g[0][0]
        end = g[-1][1]
        nxt = groups[n + 1][0][0] if n + 1 < len(groups) else None
        if nxt is not None and nxt - end < 0.35:
            end = nxt                       # no flicker between groups that follow each other
        else:
            end += 0.25
        if style == "bold":
            upper = True
            texts = [_esc(_clean_word(w[2], upper)) for w in g]
            for k, w in enumerate(g):
                ws = start if k == 0 else w[0]
                we = g[k + 1][0] if k + 1 < len(g) else end
                if we - ws < 0.02:
                    continue
                line = " ".join(f"{{\\c{GOLD}}}{t}{{\\c{WHITE}}}" if j == k else t for j, t in enumerate(texts))
                events.append(f"Dialogue: 0,{_ts(ws)},{_ts(we)},Main,,0,0,0,,{line}")
        else:
            text = _esc(" ".join(w[2].strip() for w in g))
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Main,,0,0,0,,{text}")

    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        styles, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, "",
    ])


def srt(words: list[list]) -> str:
    def ts(t: float) -> str:
        t = max(0.0, t)
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for n, g in enumerate(_group(words, 8, 42, 0.8), 1):
        out.append(f"{n}\n{ts(g[0][0])} --> {ts(g[-1][1] + 0.2)}\n{' '.join(w[2].strip() for w in g)}\n")
    return "\n".join(out)
