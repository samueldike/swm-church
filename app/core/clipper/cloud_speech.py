"""Fast transcription for the Video Clipper through Groq's hosted Whisper (whisper-large-v3-turbo).

On this server's CPU, writing down every word takes about as long as the video. Groq runs the same Whisper model
on its own hardware, so an hour of speech comes back in well under a minute: the audio is cut at real pauses into
~10-minute pieces (each well under Groq's 25 MB upload limit as FLAC), the pieces are sent in parallel, and the
word timings are shifted back onto the video's timeline.

Groq's word timestamps carry no punctuation, but the sentence builder (``speech.build_units``) ends sentences at
. ? !, so ``punctuate`` copies each word's punctuation from the segment text it belongs to.

The key is the Groq key saved under Settings → AI drafting (``groq_api_key``). Without it, or when Groq cannot be
reached, the clipper falls back to the local model on the transcription worker.
"""
from __future__ import annotations

import logging
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from app.core.clipper.media import ClipperError, run

logger = logging.getLogger(__name__)

MODEL = "whisper-large-v3-turbo"
URL = "https://api.groq.com/openai/v1/audio/transcriptions"
CHUNK = 600.0        # seconds per piece (FLAC 16 kHz mono: ~10 MB)
SEARCH = 45.0        # look this far either side of a cut for the longest pause
PARALLEL = 4
ATTEMPTS = 4


class CloudUnavailable(Exception):
    """Groq could not do the job (no key, refused key, outage): use the local model instead."""


def _norm(word: str) -> str:
    return re.sub(r"[^\w']+", "", (word or "").lower())


def cut_points(duration: float, gaps: list[list[float]], chunk: float = CHUNK, search: float = SEARCH) -> list[float]:
    """Where to split the audio: about every ``chunk`` seconds, in the middle of the longest pause nearby."""
    points, at = [0.0], 0.0
    while duration - at > chunk + search:
        target = at + chunk
        near = [g for g in gaps or [] if target - search <= (g[0] + g[1]) / 2 <= target + search]
        cut = (lambda g: (g[0] + g[1]) / 2)(max(near, key=lambda g: g[1] - g[0])) if near else target
        points.append(round(cut, 3))
        at = cut
    points.append(duration)
    return points


def punctuate(words: list[dict], segments: list[dict]) -> list[list]:
    """[[start, end, text], ...]: Groq's timed words, spelled and punctuated as in its segment text."""
    tokens = [t for seg in segments or [] for t in (seg.get("text") or "").split()]
    out: list[list] = []
    j = 0
    for w in words or []:
        raw = (w.get("word") or "").strip()
        if not raw:
            continue
        key = _norm(raw)
        text = raw
        if key:
            for look in range(j, min(j + 4, len(tokens))):
                if _norm(tokens[look]) == key:
                    text, j = tokens[look], look + 1
                    break
        start, end = float(w.get("start") or 0), float(w.get("end") or 0)
        if end <= start:
            end = start + 0.05
        out.append([round(start, 3), round(end, 3), text])
    return out


def _post(key: str, piece: Path, language: str | None, prompt: str | None) -> dict:
    data = [("model", MODEL), ("response_format", "verbose_json"), ("temperature", "0"),
            ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
    if language:
        data.append(("language", language))
    if prompt:
        data.append(("prompt", prompt[:600]))
    delay = 2.0
    for attempt in range(ATTEMPTS):
        try:
            with piece.open("rb") as fh:
                r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, data=data,
                               files={"file": (piece.name, fh, "audio/flac")}, timeout=180)
        except httpx.HTTPError as exc:
            if attempt == ATTEMPTS - 1:
                raise CloudUnavailable(f"Groq could not be reached ({exc.__class__.__name__}).") from exc
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403):
            raise CloudUnavailable("Groq refused the saved key.")
        if r.status_code in (429, 500, 502, 503, 504) and attempt < ATTEMPTS - 1:
            try:
                wait = float(r.headers.get("retry-after") or delay)
            except ValueError:
                wait = delay
            time.sleep(min(60.0, max(1.0, wait)))
            delay *= 2
            continue
        raise CloudUnavailable(f"Groq answered {r.status_code}: {r.text[:200]}")
    raise CloudUnavailable("Groq did not answer.")


def transcribe_words(audio: Path, *, key: str, duration: float, gaps: list[list[float]],
                     language: str | None = "en", hotwords: str | None = None, progress=None) -> list[list]:
    """[[start, end, word], ...] for the whole file, in order (same shape as ``speech.transcribe_words``)."""
    if not key:
        raise CloudUnavailable("No Groq key is saved.")
    lang = None if not language else ("en" if language == "pcm" else language)
    points = cut_points(duration, gaps)
    spans = list(zip(points[:-1], points[1:]))
    work = Path(tempfile.mkdtemp(prefix="groq_", dir=str(audio.parent)))
    done = [0]
    try:
        pieces = []
        for n, (a, b) in enumerate(spans):
            piece = work / f"part{n:03d}.flac"
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(audio),
                 "-ac", "1", "-ar", "16000", "-c:a", "flac", str(piece)], timeout=600)
            if piece.stat().st_size > 24 * 1024 * 1024:
                raise CloudUnavailable("A piece of the audio is too large for Groq.")
            pieces.append((a, piece))

        def one(item):
            offset, piece = item
            data = _post(key, piece, lang, hotwords)
            words = punctuate(data.get("words") or [], data.get("segments") or [])
            done[0] += 1
            if progress:
                progress(done[0] / len(pieces))
            return [[round(s + offset, 3), round(e + offset, 3), t] for s, e, t in words]

        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            results = list(pool.map(one, pieces))
    except ClipperError:
        raise
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    words = [w for part in results for w in part]
    words.sort(key=lambda w: w[0])
    return words
