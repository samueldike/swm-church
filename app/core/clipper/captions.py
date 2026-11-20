"""Captions for a short, timed word by word on the short's own timeline (app/core/clipper/cut.py words).

Shorts are rendered once WITHOUT captions (the clean master). Captions are made ahead and kept apart:

* the short's words go to ``shorts/<id>.words.json``; the shorts grid and the editor draw the chosen design live
  over the video from them (static/js/clipper-captions.js mirrors ``STYLES`` and ``group`` exactly), so ticking
  "Captions" or picking another design shows at once;
* the download and the social post burn the chosen design into a copy (``burn`` -> ``<id>.cap-<style>.mp4``, made in
  the background as soon as captions are ticked, or on the spot).

Designs (the look and motion of the CapCut caption presets people know): ``pop`` (3 words, the spoken word pops
up in yellow), ``highlight`` (4 words, the spoken word on a gold box), ``one`` (one big word at a time, bouncing in),
``glow`` (soft gold glow, spoken word white), ``clean`` (sentence subtitles on a dark band).
Every short also gets an ``.srt`` for platforms that take caption files.
"""
from __future__ import annotations

import re

from app.core.clipper.media import ASSETS

# Colours as #RRGGBB (ASS wants &HAABBGGRR, see _c).
YELLOW, GOLD, WHITE, BLACK, GREEN = "#FFE03A", "#F0C837", "#FFFFFF", "#000000", "#3CFF8A"
EDGE = "#3A2600"              # the thin dark edge that keeps glow letters sharp

# One entry per design: grouping (max_words, max_chars, max_gap), case, font, size (share of the short side),
# position (share of the height above the bottom edge), colours. clipper-captions.js holds the same table.
STYLES: dict[str, dict] = {
    "pop": {"label": "Pop", "hint": "Three words, the spoken one pops in yellow", "words": 3, "chars": 18, "gap": 0.55,
            "upper": True, "font": "Poppins ExtraBold", "size": 0.105, "pos": 0.26, "color": WHITE, "active": YELLOW,
            "outline": BLACK, "border": 0.085},
    "highlight": {"label": "Highlight", "hint": "The spoken word sits on a gold box", "words": 4, "chars": 22, "gap": 0.6,
                  "upper": True, "font": "Poppins ExtraBold", "size": 0.088, "pos": 0.26, "color": WHITE, "active": BLACK,
                  "box": GOLD, "outline": BLACK, "border": 0.07},
    "one": {"label": "One word", "hint": "One big word at a time, bouncing in", "words": 1, "chars": 14, "gap": 0.5,
            "upper": True, "font": "Poppins ExtraBold", "size": 0.15, "pos": 0.42, "color": WHITE, "active": YELLOW,
            "outline": BLACK, "border": 0.09},
    "glow": {"label": "Glow", "hint": "Gold words with a soft glow, the spoken word in white", "words": 3, "chars": 20, "gap": 0.55,
             "upper": False, "font": "Poppins ExtraBold", "size": 0.098, "pos": 0.26, "color": GOLD, "active": WHITE,
             "outline": GOLD, "border": 0.06, "blur": 6},
    "clean": {"label": "Clean subtitles", "hint": "A sentence at a time on a dark band", "words": 8, "chars": 34,
              "gap": 0.8, "upper": False, "font": "Poppins SemiBold", "size": 0.062, "pos": 0.16, "color": WHITE,
              "active": WHITE, "band": "#000000", "border": 0.22},
}
DEFAULT = "pop"
LEGACY = {"bold": "pop"}      # earlier shorts saved "bold" (the first design)


def style_id(name: str | None) -> str | None:
    """A known design id (built in, or a Caption Studio id ``c-xxxxxxxx``), or None for no captions."""
    from app.core.clipper.studio import is_custom

    name = LEGACY.get(name or "", name or "")
    return name if name in STYLES or is_custom(name) else None


def fonts_dir() -> str:
    """Every caption font (Poppins and the Caption Studio fonts): the same files the browser preview loads."""
    from pathlib import Path

    return str(Path(__file__).resolve().parents[2] / "static" / "fonts" / "clipper")


def _c(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def group(words: list[list], max_words: int, max_chars: int, max_gap: float = 0.55) -> list[list[list]]:
    """Words into caption lines (pure; mirrored in clipper-captions.js)."""
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


_group = group   # older name


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def word_text(w: str, upper: bool) -> str:
    w = w.strip()
    if upper:
        w = re.sub(r"[,;:]$", "", w).upper()
    return w


def ass(words: list[list], *, style: str, width: int, height: int) -> str:
    sid = style_id(style) or DEFAULT
    st = STYLES[sid]
    base = min(width, height)
    vertical = height > width
    size = round(base * st["size"] * (1.0 if vertical else 0.8))
    margin_v = round(height * st["pos"] * (1.0 if vertical else 0.55))
    bord = max(2, round(size * st["border"]))
    if sid == "clean":
        line_style = (f"Style: Main,{st['font']},{size},{_c(WHITE)},{_c(WHITE)},{_c(BLACK, 0x73)},{_c(BLACK, 0x73)},"
                      f"0,0,0,0,100,100,0,0,3,{bord},0,2,80,80,{margin_v},1")
    else:
        line_style = (f"Style: Main,{st['font']},{size},{_c(st['color'])},{_c(st['color'])},{_c(st['outline'])},"
                      f"{_c(BLACK, 0x80)},0,0,0,0,100,100,0,0,1,{bord},{max(1, round(size * 0.035))},2,60,60,{margin_v},1")

    groups = group(words, st["words"], st["chars"] if vertical else st["chars"] + 10, st["gap"])
    events = []
    for n, g in enumerate(groups):
        start, end = g[0][0], g[-1][1]
        nxt = groups[n + 1][0][0] if n + 1 < len(groups) else None
        end = nxt if nxt is not None and nxt - end < 0.35 else end + 0.25   # no flicker between lines
        if sid == "clean":
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Main,,0,0,0,,{{\\fad(90,60)}}"
                          f"{_esc(' '.join(w[2].strip() for w in g))}")
            continue
        texts = [_esc(word_text(w[2], st["upper"])) for w in g]
        for k, w in enumerate(g):
            ws = start if k == 0 else w[0]
            we = g[k + 1][0] if k + 1 < len(g) else end
            if we - ws < 0.02:
                continue
            # The line slides up a little and fades in when it appears; the spoken word pops.
            enter = "{\\fad(70,0)\\fscx92\\fscy92\\t(0,110,\\fscx100\\fscy100)}" if k == 0 else ""
            if sid == "highlight":
                on = (f"{{\\c{_c(st['active'])}\\3c{_c(st['box'])}\\bord{round(bord * 2.4)}\\shad0"
                      f"\\fscx108\\fscy108\\t(0,90,\\fscx100\\fscy100)}}")
            elif sid == "one":
                on = (f"{{\\c{_c(st['active'] if n % 2 else st['color'])}\\fscx135\\fscy135"
                      f"\\t(0,80,\\fscx94\\fscy94)\\t(80,150,\\fscx100\\fscy100)}}")
            else:
                on = f"{{\\c{_c(st['active'])}\\fscx116\\fscy116\\t(0,110,\\fscx100\\fscy100)}}"
            if st.get("blur"):
                # Glow: layer 0 is only the halo (blurred gold edge, no fill); layer 1 is the text, sharp, with a thin
                # dark edge so every letter stays crisp against the halo. Both carry the same size changes, so they
                # stay exactly on top of each other.
                halo = (f"\\1a&HFF&\\3c{_c(st['outline'])}\\3a&H40&\\bord{round(bord * 2.2)}"
                        f"\\blur{st['blur'] * 1.6:g}\\shad0")
                sharp = f"\\blur0\\bord{max(1, round(size * 0.03))}\\3c{_c(EDGE)}\\3a&H30&\\shad0"
                for layer, look, word_on in ((0, halo, on.replace(f"\\c{_c(st['active'])}", "")), (1, sharp, on)):
                    line = " ".join(f"{word_on}{t}{{\\r{look}}}" if j == k else t for j, t in enumerate(texts))
                    events.append(f"Dialogue: {layer},{_ts(ws)},{_ts(we)},Main,,0,0,0,,{enter}{{{look}}}{line}")
                continue
            off = "{\\r}"                              # back to the line's own look after the spoken word
            line = " ".join(f"{on}{t}{off}" if j == k else t for j, t in enumerate(texts))
            events.append(f"Dialogue: 0,{_ts(ws)},{_ts(we)},Main,,0,0,0,,{enter}{line}")

    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        line_style, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, "",
    ])


def public_styles() -> dict:
    """The design table for the browser (clipper-captions.js)."""
    return {k: {kk: v for kk, v in st.items()} for k, st in STYLES.items()}


def srt(words: list[list]) -> str:
    def ts(t: float) -> str:
        t = max(0.0, t)
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for n, g in enumerate(group(words, 8, 42, 0.8), 1):
        out.append(f"{n}\n{ts(g[0][0])} --> {ts(g[-1][1] + 0.2)}\n{' '.join(w[2].strip() for w in g)}\n")
    return "\n".join(out)
