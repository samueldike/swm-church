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


PIECE = 600.0      # local transcription works through the audio in pieces this long (cut at pauses)


def _model_kwargs(language: str | None, hotwords: str | None) -> dict:
    kwargs = {
        "beam_size": 1,
        "condition_on_previous_text": False,
        "word_timestamps": True,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 400},
    }
    if language:
        kwargs["language"] = "en" if language == "pcm" else language
    if hotwords:
        kwargs["hotwords"] = hotwords
    return kwargs


BATCH = 16         # speech stretches decoded together (faster-whisper's batched pipeline)


def _engine():
    """The local model wrapped in faster-whisper's batched pipeline.

    Measured on the VPS with a real sermon (5 min): 10-30% faster than one stretch at a time, and the words come
    back punctuated (the plain pipeline with condition_on_previous_text=False drops most full stops, which
    build_units needs to find sentences). Same model, same words otherwise (2.5% of tokens differ, nearly all
    punctuation and capitals)."""
    from faster_whisper import BatchedInferencePipeline

    from app.core.transcription import _get_model

    global _batched
    if _batched is None:
        _batched = BatchedInferencePipeline(_get_model())
    return _batched


_batched = None


def _words_of(model, path: Path, kwargs: dict, offset: float = 0.0, on_seg=None) -> list[list]:
    segments, info = model.transcribe(str(path), batch_size=BATCH, **kwargs)
    words: list[list] = []
    for seg in segments:
        for w in seg.words or []:
            text = (w.word or "").strip()
            if not text:
                continue
            start, end = float(w.start), float(w.end)
            if end <= start:
                end = start + 0.05
            words.append([round(start + offset, 3), round(end + offset, 3), text])
        if on_seg:
            on_seg(float(seg.end), float(info.duration or 0))
    return words


def transcribe_words(audio: Path, *, language: str | None = "en", hotwords: str | None = None, progress=None,
                     duration: float | None = None, gaps: list[list[float]] | None = None,
                     partial: Path | None = None) -> list[list]:
    """[[start, end, word], ...] for the whole file, in order.

    With ``duration`` known the audio is worked through in ~10-minute pieces cut at real pauses, so memory stays
    flat however long the video is (a 2 3/4-hour service in one go was killed for memory), and each finished piece
    is saved to ``partial`` so a retry carries on from the piece it stopped in.
    """
    import shutil
    import tempfile

    from app.core.clipper.cloud_speech import cut_points
    from app.core.clipper.media import run
    model = _engine()
    kwargs = _model_kwargs(language, hotwords)
    total = float(duration or 0)
    if total <= PIECE * 1.5:
        words = _words_of(model, audio, kwargs, on_seg=(lambda end, dur: progress and progress(
            min(1.0, end / (total or dur or 1.0)))))
        words.sort(key=lambda w: w[0])
        return words

    points = cut_points(total, gaps or [], chunk=PIECE)
    spans = list(zip(points[:-1], points[1:]))
    stamp = f"{audio.stat().st_size}:{language}:{len(spans)}"
    done: dict[str, list] = {}
    if partial and partial.exists():
        try:
            saved = json.loads(partial.read_text(encoding="utf-8"))
            if saved.get("stamp") == stamp:
                done = saved.get("pieces") or {}
        except (OSError, ValueError):
            done = {}
    work = Path(tempfile.mkdtemp(prefix="listen_", dir=str(audio.parent)))
    try:
        for a, b in spans:
            key = f"{a:.3f}"
            if key in done:
                continue
            piece = work / "piece.wav"
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(audio),
                 "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(piece)], timeout=600)
            before = sum(y - x for x, y in spans if f"{x:.3f}" in done)
            done[key] = _words_of(model, piece, kwargs, offset=a, on_seg=(
                lambda end, dur, before=before: progress and progress(min(1.0, (before + end) / total))))
            if partial:
                partial.write_text(json.dumps({"stamp": stamp, "pieces": done}), encoding="utf-8")
            if progress:
                progress(min(1.0, (before + b - a) / total))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    words = [w for a, _ in spans for w in done.get(f"{a:.3f}", [])]
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
