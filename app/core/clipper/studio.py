"""Caption Studio: a church's own caption designs (font, colours, outline, shadow, glow, box, the spoken word's effect,
how a line comes in and goes out, layout), next to the five built-in designs in captions.STYLES.

Per church (branch setting ``clipper_caption_designs``, JSON)::

    {"items": [{"id": "c-1a2b3c4d", "name": ..., <every key of BASE>}]}

A short stores the id in ``ClipShort.caption_style`` like a built-in one. ``ass`` writes the burned-in subtitles and
``static/js/clipper-captions.js`` (``customLine``) draws the same design live in the browser: keep the two in step.
The finished file's name carries ``cache_key`` (a hash of the design), so editing a design remakes its copies.
Fonts are OFL TTFs in ``static/fonts/clipper/`` (the browser loads them; libass reads the same folder).
Pure helpers are tested in tests/test_clipper.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from app.core.clipper import captions

ID_RE = re.compile(r"^c-[0-9a-f]{8}$")
MAX_DESIGNS = 40

# id -> label, file in static/fonts/clipper, the name libass finds it by, the kind (for grouping in the picker)
FONTS: dict[str, dict] = {
    "poppins-xb": {"label": "Poppins Extra Bold", "file": "Poppins-ExtraBold.ttf", "ass": "Poppins ExtraBold", "kind": "Clean"},
    "poppins-sb": {"label": "Poppins Semi Bold", "file": "Poppins-SemiBold.ttf", "ass": "Poppins SemiBold", "kind": "Clean"},
    "montserrat": {"label": "Montserrat Extra Bold", "file": "Montserrat-ExtraBold.ttf", "ass": "Montserrat ExtraBold", "kind": "Clean"},
    "inter": {"label": "Inter Extra Bold", "file": "Inter-ExtraBold.ttf", "ass": "Inter ExtraBold", "kind": "Clean"},
    "bricolage": {"label": "Bricolage Grotesque", "file": "BricolageGrotesque-ExtraBold.ttf", "ass": "Bricolage Grotesque ExtraBold", "kind": "Clean"},
    "anton": {"label": "Anton", "file": "Anton-Regular.ttf", "ass": "Anton", "kind": "Tall"},
    "bebas": {"label": "Bebas Neue", "file": "BebasNeue-Regular.ttf", "ass": "Bebas Neue", "kind": "Tall"},
    "oswald": {"label": "Oswald Bold", "file": "Oswald-Bold.ttf", "ass": "Oswald Bold", "kind": "Tall"},
    "archivo": {"label": "Archivo Black", "file": "ArchivoBlack-Regular.ttf", "ass": "Archivo Black", "kind": "Heavy"},
    "lilita": {"label": "Lilita One", "file": "LilitaOne-Regular.ttf", "ass": "Lilita One", "kind": "Heavy"},
    "luckiest": {"label": "Luckiest Guy", "file": "LuckiestGuy-Regular.ttf", "ass": "Luckiest Guy", "kind": "Fun"},
    "bangers": {"label": "Bangers", "file": "Bangers-Regular.ttf", "ass": "Bangers", "kind": "Fun"},
    "righteous": {"label": "Righteous", "file": "Righteous-Regular.ttf", "ass": "Righteous", "kind": "Fun"},
    "marker": {"label": "Permanent Marker", "file": "PermanentMarker-Regular.ttf", "ass": "Permanent Marker", "kind": "Handwritten"},
    "caveat": {"label": "Caveat Bold", "file": "Caveat-Bold.ttf", "ass": "Caveat Bold", "kind": "Handwritten"},
    "dancing": {"label": "Dancing Script", "file": "DancingScript-Bold.ttf", "ass": "Dancing Script Bold", "kind": "Script"},
    "playfair": {"label": "Playfair Display Black", "file": "PlayfairDisplay-Black.ttf", "ass": "Playfair Display Black", "kind": "Serif"},
    "merriweather": {"label": "Merriweather Black", "file": "Merriweather-Black.ttf", "ass": "Merriweather Black", "kind": "Serif"},
}

WORD_FX = {"none": "Colour only", "pop": "Pop", "bounce": "Bounce", "grow": "Stay bigger", "underline": "Underline"}
MODES = {"current": "Only the spoken word", "so_far": "Fill as it is spoken (karaoke)", "reveal": "Words appear as spoken"}
ENTERS = {"none": "None", "fade": "Fade", "pop": "Pop", "slide": "Slide up", "drop": "Drop down", "bounce": "Bounce",
          "zoom": "Zoom in", "blur": "Blur in", "swing": "Swing"}
EXITS = {"none": "None", "fade": "Fade out"}
BOXES = {"none": "No box", "word": "Box on the spoken word", "line": "Band behind the line"}

BASE_SIZE = 0.095            # share of the short's short side at size 100

BASE: dict = {
    "name": "My captions", "font": "poppins-xb", "size": 100, "upper": True, "italic": False, "spacing": 0, "tilt": 0,
    "pos": 26, "words": 3,
    "text": "#FFFFFF", "active": "#FFE03A",
    "outline": "#000000", "outline_w": 9, "shadow": "#000000", "shadow_d": 4, "shadow_a": 50,
    "glow": False, "glow_color": "#F0C837", "glow_s": 6,
    "box": "none", "box_color": "#F0C837", "box_a": 100,
    "word_fx": "pop", "mode": "current", "enter": "pop", "exit": "none",
}
# number keys: (lowest, highest)
RANGES = {"size": (50, 200), "spacing": (-5, 30), "tilt": (-12, 12), "pos": (4, 80), "words": (1, 8),
          "outline_w": (0, 25), "shadow_d": (0, 20), "shadow_a": (0, 100), "glow_s": (1, 14), "box_a": (10, 100)}
COLOURS = ("text", "active", "outline", "shadow", "glow_color", "box_color")
CHOICES = {"font": FONTS, "word_fx": WORD_FX, "mode": MODES, "enter": ENTERS, "exit": EXITS, "box": BOXES}

# Starting points shown in the studio (each is BASE plus these).
PRESETS: dict[str, dict] = {
    "pop": {"name": "Pop"},
    "karaoke": {"name": "Karaoke fill", "mode": "so_far", "word_fx": "none", "active": "#3CFF8A", "enter": "fade"},
    "typewriter": {"name": "Word by word", "mode": "reveal", "word_fx": "pop", "active": "#FFFFFF", "words": 5, "enter": "none"},
    "highlight": {"name": "Gold box", "box": "word", "box_color": "#F0C837", "active": "#000000", "word_fx": "pop", "words": 4},
    "one": {"name": "One big word", "words": 1, "size": 150, "pos": 42, "word_fx": "bounce", "active": "#FFFFFF", "enter": "none"},
    "glow": {"name": "Gold glow", "upper": False, "text": "#F0C837", "active": "#FFFFFF", "glow": True, "outline": "#3A2600",
             "outline_w": 3, "shadow_d": 0},
    "neon": {"name": "Neon", "font": "righteous", "upper": False, "text": "#FFFFFF", "active": "#FF4FD8", "glow": True,
             "glow_color": "#FF4FD8", "glow_s": 9, "outline": "#2A0030", "outline_w": 3, "shadow_d": 0, "enter": "blur"},
    "band": {"name": "Subtitle band", "font": "poppins-sb", "upper": False, "box": "line", "box_color": "#000000", "box_a": 60,
             "words": 7, "size": 70, "pos": 14, "word_fx": "none", "active": "#FFFFFF", "enter": "fade", "exit": "fade",
             "shadow_d": 0},
    "headline": {"name": "Headline", "font": "anton", "size": 125, "spacing": 3, "outline_w": 7, "active": "#FF5A36",
                 "word_fx": "grow", "enter": "slide"},
    "comic": {"name": "Comic", "font": "bangers", "spacing": 4, "tilt": -4, "active": "#FFD400", "outline_w": 12,
              "shadow_d": 8, "shadow_a": 90, "word_fx": "bounce", "enter": "swing"},
    "marker": {"name": "Marker", "font": "marker", "upper": False, "active": "#7CE7FF", "word_fx": "underline",
               "outline_w": 6, "enter": "drop"},
    "elegant": {"name": "Elegant", "font": "playfair", "upper": False, "italic": False, "text": "#FFF6E0",
                "active": "#F0C837", "outline_w": 0, "shadow_d": 5, "shadow_a": 70, "word_fx": "none", "enter": "fade",
                "exit": "fade", "words": 4},
    "script": {"name": "Script", "font": "dancing", "upper": False, "size": 120, "active": "#F0C837", "outline_w": 5,
               "word_fx": "none", "mode": "so_far", "enter": "zoom"},
}

SAMPLE = "Grace is not just enough, it is more than enough for you today."


def font_em(file: str) -> float:
    """How big the font's em is for a given ASS font size (pure, cached).

    libass sizes a font so that its OS/2 winAscent + winDescent fills the ASS font size; a browser's font-size is the
    em. The live preview multiplies its size by this, so it shows the same letter size as the burned-in video."""
    return _font_em(file)


@__import__("functools").lru_cache(maxsize=64)
def _font_em(file: str) -> float:
    import struct

    from app.core.clipper.captions import fonts_dir

    try:
        b = (__import__("pathlib").Path(fonts_dir()) / file).read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", b[4:6])[0]):
            tag, _cs, off, _ln = struct.unpack(">4sIII", b[12 + 16 * i:28 + 16 * i])
            tables[tag] = off
        upm = struct.unpack(">H", b[tables[b"head"] + 18:tables[b"head"] + 20])[0]
        win_a, win_d = struct.unpack(">HH", b[tables[b"OS/2"] + 74:tables[b"OS/2"] + 78])
        if win_a + win_d:
            return round(upm / (win_a + win_d), 4)
        asc, desc = struct.unpack(">hh", b[tables[b"hhea"] + 4:tables[b"hhea"] + 8])
        return round(upm / (asc - desc), 4) if asc - desc else 1.0
    except (OSError, KeyError, struct.error):
        return 1.0


def _hex(v, fallback: str) -> str:
    v = str(v or "").strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", v):
        return "#" + v.lstrip("#").upper()
    return fallback


def _num(v, lo: int, hi: int, fallback: int) -> int:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, n))


def clean(raw: dict | None, *, keep_id: str | None = None) -> dict:
    """A complete, safe design from anything posted (pure): unknown keys dropped, numbers clamped, colours #RRGGBB."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(BASE)
    out["name"] = re.sub(r"\s+", " ", str(raw.get("name") or BASE["name"])).strip()[:40] or BASE["name"]
    for k, table in CHOICES.items():
        out[k] = raw.get(k) if raw.get(k) in table else BASE[k]
    for k, (lo, hi) in RANGES.items():
        out[k] = _num(raw.get(k), lo, hi, BASE[k])
    for k in COLOURS:
        out[k] = _hex(raw.get(k), BASE[k])
    for k in ("upper", "italic", "glow"):
        v = raw.get(k, BASE[k])
        out[k] = v in (True, 1, "1", "true", "on") if not isinstance(v, bool) else v
    if keep_id and ID_RE.match(keep_id):
        out["id"] = keep_id
    return out


def preset(key: str) -> dict:
    return clean({**BASE, **PRESETS.get(key, {})})


def new_id() -> str:
    return "c-" + uuid.uuid4().hex[:8]


def is_custom(style: str | None) -> bool:
    return bool(style and ID_RE.match(style))


def cache_key(design: dict) -> str:
    body = {k: design.get(k) for k in BASE if k != "name"}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:8]


def chars_for(words: int) -> int:
    return 6 + 6 * words


def public(design: dict) -> dict:
    """The design as the browser's caption code reads it (same grouping numbers as ``ass``)."""
    d = clean(design, keep_id=design.get("id"))
    return {**d, "custom": True, "label": d["name"], "hint": f"Your design: {FONTS[d['font']]['label']}",
            "chars": chars_for(d["words"]), "gap": 0.6, "size_share": BASE_SIZE * d["size"] / 100, "pos_share": d["pos"] / 100,
            "css_font": "SWM " + FONTS[d["font"]]["label"], "em": font_em(FONTS[d["font"]]["file"])}


def parse(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    items = []
    for i in (data.get("items") if isinstance(data, dict) else None) or []:
        if isinstance(i, dict) and is_custom(i.get("id")):
            items.append(clean(i, keep_id=i["id"]))
    return {"items": items[:MAX_DESIGNS]}


def find(cfg: dict | None, style: str | None) -> dict | None:
    return next((i for i in (cfg or {}).get("items") or [] if i["id"] == style), None)


async def load(db, branch_id: int) -> dict:
    from app.core.branch_settings import get_branch_settings

    return parse((await get_branch_settings(db, branch_id)).get("clipper_caption_designs"))


async def save(db, branch_id: int, cfg: dict) -> None:
    from app.core.branch_settings import update_branch_settings

    await update_branch_settings(db, branch_id, {"clipper_caption_designs": json.dumps(cfg)})


# ─── The burned-in captions (ASS) ─────────────────────────────────────

def _alpha(opacity_pct: int) -> str:
    return f"&H{round(255 * (1 - max(0, min(100, opacity_pct)) / 100)):02X}&"


def ass(words: list[list], design: dict, *, width: int, height: int) -> str:
    """ASS subtitles for a custom design (pure; tested). One event per spoken word (two with glow: halo + letters)."""
    d = clean(design)
    font = FONTS[d["font"]]
    c = captions._c
    vertical = height > width
    size = round(min(width, height) * BASE_SIZE * d["size"] / 100 * (1.0 if vertical else 0.8))
    margin_v = round(height * d["pos"] / 100 * (1.0 if vertical else 0.55))
    ow = round(size * d["outline_w"] / 100)
    sd = round(size * d["shadow_d"] / 100)
    spacing = round(size * d["spacing"] / 100)
    band = d["box"] == "line"
    pad = max(2, round(size * 0.16))
    if band:
        style = (f"Style: Main,{font['ass']},{size},{c(d['text'])},{c(d['text'])},{c(d['box_color'])},{c(d['box_color'])},"
                 f"0,{-1 if d['italic'] else 0},0,0,100,100,{spacing},{-d['tilt']},3,{pad},0,2,60,60,{margin_v},1")
    else:
        style = (f"Style: Main,{font['ass']},{size},{c(d['text'])},{c(d['text'])},{c(d['outline'])},{c(d['shadow'])},"
                 f"0,{-1 if d['italic'] else 0},0,0,100,100,{spacing},{-d['tilt']},1,{ow},{sd},2,60,60,{margin_v},1")
    shadow_a = _alpha(d["shadow_a"])
    box_a = _alpha(d["box_a"])
    x, y = width // 2, height - margin_v
    glow = d["glow"] and not band

    def run(text: str, j: int, k: int, first: bool, halo: bool) -> str:
        spoken = j == k
        lit = spoken or (d["mode"] == "so_far" and j < k)
        hidden = d["mode"] == "reveal" and j > k
        tags = []
        if hidden:
            tags.append("\\alpha&HFF&")
        elif halo:
            tags.append(f"\\1a&HFF&\\3c{c(d['glow_color'])}\\3a&H40&\\4a&HFF&\\bord{max(2, round(ow * 1.6 + size * 0.08))}"
                        f"\\blur{d['glow_s'] * 1.5:g}")
        else:
            tags.append(f"\\1a&H00&\\c{c(d['active'] if lit else d['text'])}")
            if not band:
                if d["box"] == "word" and spoken:
                    tags.append(f"\\3c{c(d['box_color'])}\\3a{box_a}\\bord{max(ow, pad)}\\4a&HFF&")
                else:
                    tags.append(f"\\3c{c(d['outline'])}\\3a&H00&\\bord{ow}\\4a{shadow_a}")
                if glow:
                    tags.append("\\blur0")
        tags.append("\\u1" if spoken and d["word_fx"] == "underline" and not halo else "\\u0")
        # sizes: the same on both glow layers so they stay on top of each other
        if spoken and d["word_fx"] == "pop":
            tags.append("\\fscx120\\fscy120\\t(0,110,\\fscx100\\fscy100)")
        elif spoken and d["word_fx"] == "bounce":
            tags.append("\\fscx135\\fscy135\\t(0,80,\\fscx92\\fscy92)\\t(80,160,\\fscx100\\fscy100)")
        elif spoken and d["word_fx"] == "grow":
            tags.append("\\fscx112\\fscy112")
        elif first and d["enter"] == "pop":
            tags.append("\\fscx70\\fscy70\\t(0,140,\\fscx100\\fscy100)")
        elif first and d["enter"] == "bounce":
            tags.append("\\fscx125\\fscy125\\t(0,100,\\fscx92\\fscy92)\\t(100,180,\\fscx100\\fscy100)")
        elif first and d["enter"] == "zoom":
            tags.append("\\fscx150\\fscy150\\t(0,170,\\fscx100\\fscy100)")
        else:
            tags.append("\\fscx100\\fscy100")
        if first and d["enter"] == "blur" and not halo:
            tags.append("\\blur10\\t(0,180,\\blur0)")
        return "{" + "".join(tags) + "}" + text

    groups = captions.group(words, d["words"], chars_for(d["words"]) if vertical else chars_for(d["words"]) + 10, 0.6)
    events = []
    for n, g in enumerate(groups):
        start, end = g[0][0], g[-1][1]
        nxt = groups[n + 1][0][0] if n + 1 < len(groups) else None
        end = nxt if nxt is not None and nxt - end < 0.35 else end + 0.25
        texts = [captions._esc(captions.word_text(w[2], d["upper"])) for w in g]
        for k, w in enumerate(g):
            ws = start if k == 0 else w[0]
            we = g[k + 1][0] if k + 1 < len(g) else end
            if we - ws < 0.02:
                continue
            first = k == 0
            head = []
            fin = 150 if first and d["enter"] in ("fade", "blur", "zoom", "slide", "drop", "swing") else 0
            fout = 140 if k == len(g) - 1 and d["exit"] == "fade" else 0
            if fin or fout:
                head.append(f"\\fad({fin},{fout})")
            if first and d["enter"] in ("slide", "drop"):
                off = round(height * 0.05) * (1 if d["enter"] == "slide" else -1)
                head.append(f"\\move({x},{y + off},{x},{y},0,170)")
            if first and d["enter"] == "swing":
                head.append(f"\\frz{-d['tilt'] + 14}\\t(0,200,\\frz{-d['tilt']})")
            lead = "{" + "".join(head) + "}" if head else ""
            for layer, halo in ((0, True), (1, False)) if glow else ((0, False),):
                line = " ".join(run(t, j, k, first, halo) for j, t in enumerate(texts))
                events.append(f"Dialogue: {layer},{captions._ts(ws)},{captions._ts(we)},Main,,0,0,0,,{lead}{line}")

    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        style, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, "",
    ])


def sample_words(text: str = SAMPLE, start: float = 0.3) -> list[list]:
    """Evenly timed words for previews (pure)."""
    out, t = [], start
    for w in (text or SAMPLE).split()[:40]:
        dur = 0.18 + 0.045 * len(w)
        out.append([round(t, 3), round(t + dur, 3), w])
        t += dur + (0.32 if re.search(r"[.?!,]$", w) else 0.06)
    return out
