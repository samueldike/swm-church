"""Video Clipper choices, their validation, and who may use the clipper."""
from __future__ import annotations

import re

from app.core.access import BRANCH_ADMIN, HOD, HQ_MEDIA, SUPER, normalize_role

CONTENT_TYPES = {
    "sermon": "Sermon / church message",
    "teaching": "Bible study / teaching",
    "podcast": "Podcast / interview / panel",
    "other": "Other talk",
}
# What the editor should treat as wrapping, per content type (the structure pass reads it).
CONTENT_LABEL = {
    "sermon": "church service or sermon",
    "teaching": "Bible study or teaching",
    "podcast": "podcast, interview or panel discussion",
    "other": "talk",
}
STYLES = {
    "passage": "One passage each (most natural)",
    "assembled": "Edited from across the video",
}
FORMATS = {
    "vertical": {"label": "Vertical 9:16 (Reels, TikTok, Shorts, Status)", "ratio": (9, 16)},
    "square": {"label": "Square 1:1 (feed posts)", "ratio": (1, 1)},
    "landscape": {"label": "Landscape 16:9 (YouTube, Facebook)", "ratio": (16, 9)},
}
QUALITIES = {720: "720p (smaller files)", 1080: "1080p (best)"}
FRAMINGS = {
    "speaker": "Follow the speaker",
    "centre": "Keep the centre",
    "fit": "Whole picture on a blurred background",
}
CAPTION_STYLES = {
    "bold": "Bold, word by word",
    "clean": "Clean subtitles",
    "none": "No captions",
}
LENGTHS = {
    "15-30": (15, 30),
    "30-60": (30, 60),
    "60-90": (60, 90),
    "90-180": (90, 180),
}
LANGUAGES = {"en": "English", "": "Detect automatically", "yo": "Yoruba", "ig": "Igbo", "ha": "Hausa", "fr": "French", "pcm": "Nigerian Pidgin (as English)"}

DEFAULTS = {
    "content_type": "sermon",
    "count": 6,
    "length": "30-60",
    "style": "passage",
    "format": "vertical",
    "quality": 1080,
    "framing": "speaker",
    "caption_style": "bold",
    "language": "en",
    "range_start": None,
    "range_end": None,
}

_CLOCK = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.\d+)?\s*$")


def parse_clock(value) -> float | None:
    """'1:02:03', '62:03' or '3723' -> seconds; blank or invalid -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    m = _CLOCK.match(text)
    if not m:
        return None
    h, mnt, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    if s >= 60 or (m.group(1) is not None and mnt >= 60):
        return None
    return float(h * 3600 + mnt * 60 + s)


def clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(round(max(0.0, float(seconds))))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def clean_settings(form) -> tuple[dict, list[str]]:
    """Validated project settings from a submitted form, and the problems found."""
    get = (lambda k, d=None: form.get(k, d)) if hasattr(form, "get") else (lambda k, d=None: d)
    out = dict(DEFAULTS)
    problems: list[str] = []

    ct = get("content_type") or DEFAULTS["content_type"]
    out["content_type"] = ct if ct in CONTENT_TYPES else DEFAULTS["content_type"]
    try:
        out["count"] = max(1, min(15, int(get("count") or DEFAULTS["count"])))
    except (TypeError, ValueError):
        out["count"] = DEFAULTS["count"]
    length = get("length") or DEFAULTS["length"]
    out["length"] = length if length in LENGTHS else DEFAULTS["length"]
    for key, table in (("style", STYLES), ("format", FORMATS), ("framing", FRAMINGS),
                       ("caption_style", CAPTION_STYLES), ("language", LANGUAGES)):
        val = get(key)
        out[key] = val if val in table else DEFAULTS[key]
    try:
        q = int(get("quality") or DEFAULTS["quality"])
    except (TypeError, ValueError):
        q = DEFAULTS["quality"]
    out["quality"] = q if q in QUALITIES else DEFAULTS["quality"]

    raw_start, raw_end = (get("range_start") or "").strip(), (get("range_end") or "").strip()
    start, end = parse_clock(raw_start), parse_clock(raw_end)
    if raw_start and start is None:
        problems.append("The start time should look like 45:00 or 1:05:30.")
    if raw_end and end is None:
        problems.append("The end time should look like 45:00 or 1:05:30.")
    if start is not None and end is not None and end - start < 120:
        problems.append("Choose a part at least two minutes long.")
    out["range_start"], out["range_end"] = start, end
    return out, problems


def length_window(settings: dict) -> tuple[float, float]:
    """(floor, target) in seconds: shorts aim for the target and never fall under the floor."""
    lo, hi = LENGTHS.get((settings or {}).get("length") or DEFAULTS["length"], LENGTHS[DEFAULTS["length"]])
    return float(lo), float(hi)


# ─── Access ───────────────────────────────────────────────────────────

def is_media_department(name: str | None) -> bool:
    return bool(name) and re.search(r"\bmedia\b", name, re.I) is not None


def allowed(role: str | None, department_name: str | None) -> bool:
    """HQ Administrator, branch administrators, HQ Media, and the HOD of a Media department (pure; tested)."""
    role = normalize_role(role)
    if role in (SUPER, BRANCH_ADMIN, HQ_MEDIA):
        return True
    return role == HOD and is_media_department(department_name)


async def can_use(db, admin) -> bool:
    if admin is None:
        return False
    from app.core.whatsapp import department_name

    return allowed(admin.role, await department_name(db, admin))


def sees_every_church(admin) -> bool:
    return normalize_role(getattr(admin, "role", None)) == SUPER
