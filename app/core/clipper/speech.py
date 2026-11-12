"""Words and sentences for the Video Clipper.

``transcribe_words`` keeps every spoken word with its own start/end (faster-whisper word timestamps), so cuts land
on real word boundaries and captions are timed word by word. ``build_units`` turns the words into sentences (the
units the AI editor composes with), each tagged with how long it PLAYS once its pauses are removed.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# A pause inside speech at least this long is cut out (kept as a tiny breath on each side).
PAUSE = 0.42
PAD_IN = 0.06      # kept before a run's first word
PAD_OUT = 0.14     # kept after a run's last word (word ends are often early)
FILLERS = {"um", "uh", "er", "erm", "ah", "uhm", "umm", "hmm", "mm", "mhm", "eh"}
EDGE_CONNECTIVES = {"so", "and", "but", "now", "okay", "ok", "well", "alright", "right"}
MAX_UNIT_WORDS = 48


def norm(word: str) -> str:
    return re.sub(r"[^\w']+", "", (word or "").lower())


def transcribe_words(audio: Path, *, language: str | None = "en", hotwords: str | None = None, progress=None) -> list[list]:
    """[[start, end, word], ...] for the whole file, in order."""
    from app.core.transcription import _get_model

    model = _get_model()
    kwargs = {
        "beam_size": 1,
        "condition_on_previous_text": False,
        "word_timestamps": True,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 400},
    }
    if language and language != "pcm":
        kwargs["language"] = language
    elif language == "pcm":
        kwargs["language"] = "en"
    if hotwords:
        kwargs["hotwords"] = hotwords
    segments, info = model.transcribe(str(audio), **kwargs)
    total = float(info.duration or 0) or 1.0
    words: list[list] = []
    for seg in segments:
        for w in seg.words or []:
            text = (w.word or "").strip()
            if not text:
                continue
            start, end = float(w.start), float(w.end)
            if end <= start:
                end = start + 0.05
            words.append([round(start, 3), round(end, 3), text])
        if progress:
            progress(min(1.0, float(seg.end) / total))
    words.sort(key=lambda w: w[0])
    return words


def save_words(path: Path, words: list[list], gaps: list[list[float]], meta: dict | None = None) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"words": words, "gaps": gaps, "meta": meta or {}}, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load_words(path: Path) -> tuple[list[list], list[list[float]], dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("words") or [], data.get("gaps") or [], data.get("meta") or {}


@dataclass
class Unit:
    i: int
    start: float
    end: float
    text: str
    w0: int          # index of its first word
    w1: int          # index after its last word
    speech: float    # seconds it plays once pauses are cut


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.?!][\"'”’)]*$", text))


def runs(words: list[list], a: int, b: int, gaps: list[list[float]] | None = None, pause: float = PAUSE) -> list[list[float]]:
    """Spans of continuous speech for words[a:b], split at pauses (word gaps or measured silences)."""
    out: list[list[float]] = []
    gi = 0
    gaps = gaps or []
    for k in range(a, b):
        s, e = words[k][0], words[k][1]
        if out:
            prev_end = out[-1][1]
            split = s - prev_end >= pause
            if not split and gaps:
                # A measured silence between the two words (whisper often stretches a word across a pause).
                while gi < len(gaps) and gaps[gi][1] <= prev_end - 0.5:
                    gi += 1
                for g in gaps[gi:gi + 4]:
                    if g[0] >= s:
                        break
                    if g[1] - g[0] >= pause and g[0] >= words[k - 1][0] + 0.12 and g[1] <= s + 0.25:
                        split = True
                        out[-1][1] = min(out[-1][1], g[0])
                        s = max(s, g[1] - 0.05)
                        break
            if not split:
                out[-1][1] = max(prev_end, e)
                continue
        out.append([s, e])
    return out


def speech_seconds(words: list[list], a: int, b: int, gaps=None) -> float:
    spans = runs(words, a, b, gaps)
    return sum(e - s for s, e in spans) + len(spans) * (PAD_IN + PAD_OUT)


def build_units(words: list[list], gaps: list[list[float]] | None = None) -> list[Unit]:
    """Sentences on the source timeline. Ends at . ? ! ; a long pause or a very long run also closes one."""
    units: list[Unit] = []
    a = 0
    n = len(words)
    for k in range(n):
        text = words[k][2]
        last = k == n - 1
        long_pause = not last and words[k + 1][0] - words[k][1] >= 1.6 and k - a >= 3
        too_long = k - a + 1 >= MAX_UNIT_WORDS and (text.endswith((",", ";", ":")) or k - a + 1 >= MAX_UNIT_WORDS + 12)
        if last or _ends_sentence(text) or long_pause or too_long:
            body = " ".join(w[2] for w in words[a:k + 1]).strip()
            if body:
                units.append(Unit(len(units), words[a][0], words[k][1], body, a, k + 1, speech_seconds(words, a, k + 1, gaps)))
            a = k + 1
    return units


def unit_list(units: list[Unit]) -> str:
    from app.core.clipper.options import clock

    return "\n".join(f"[{u.i}] {clock(u.start)} {u.speech:.1f}s | {u.text}" for u in units)


def digest(units: list[Unit], bucket: float = 30.0, chars: int = 220) -> str:
    """One sample of what is being said every ``bucket`` seconds (the structure pass reads this)."""
    from app.core.clipper.options import clock

    lines, cur, buf = [], None, []
    for u in units:
        b = int(u.start // bucket)
        if cur is None:
            cur = b
        if b != cur:
            lines.append(f"{clock(cur * bucket)} {' '.join(buf)[:chars]}")
            cur, buf = b, []
        buf.append(u.text)
    if buf:
        lines.append(f"{clock((cur or 0) * bucket)} {' '.join(buf)[:chars]}")
    return "\n".join(lines)
