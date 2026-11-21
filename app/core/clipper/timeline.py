"""The visual editor's timeline (Admin → Video Clipper → a short → Edit).

A person's edit is stored on ``ClipShort.edit``::

    {"clips": [{"start": s, "end": e}, ...],   # stretches of the source, in playing order (project time)
     "tighten": true,                         # remove pauses and "um"s inside the clips (as the AI cut does)
     "frame_x": 0.42 | null}                  # manual crop position (0 = left edge, 1 = right), null = framing

When ``edit`` is set it IS the cut: the AI plan stays for reference only and the speaker-opening rule
(opening.py) leaves it alone. Times are on the project's timeline (seconds from the start of the part that was
clipped; the source file is read at ``info.json`` offset). Pure functions; tested in tests/test_clipper.py.
"""
from __future__ import annotations

import bisect

from app.core.clipper.cut import MERGE_GAP, piece_spans, true_words
from app.core.clipper.speech import FILLERS, norm

MAX_CLIPS = 80
MAX_LENGTH = 600.0
MIN_CLIP = 0.2
JOIN = 1.2          # rendered segments closer than this (forward in the source) are one clip in the editor


class TimelineError(ValueError):
    pass


def clips_from_segments(segments: list[list[float]]) -> list[dict]:
    """The rendered cut as editor clips: pauses the cut removed are joined back, so a sentence is one block."""
    clips: list[dict] = []
    for s, e in segments or []:
        if clips and 0 <= s - clips[-1]["end"] < JOIN:
            clips[-1]["end"] = round(e, 3)
        else:
            clips.append({"start": round(s, 3), "end": round(e, 3)})
    return clips


def clean(raw, duration: float) -> list[dict]:
    """Validated clips from the browser (raises TimelineError with a message for the person)."""
    if not isinstance(raw, list) or not raw:
        raise TimelineError("The timeline is empty. Add at least one clip.")
    if len(raw) > MAX_CLIPS:
        raise TimelineError(f"Use at most {MAX_CLIPS} clips.")
    out, total = [], 0.0
    for c in raw:
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TimelineError("A clip on the timeline is not valid.") from exc
        s, e = max(0.0, min(s, duration)), max(0.0, min(e, duration))
        if e - s < MIN_CLIP:
            continue
        out.append({"start": round(s, 3), "end": round(e, 3)})
        total += e - s
    if not out:
        raise TimelineError("Every clip is too short. Make at least one longer.")
    if total > MAX_LENGTH:
        raise TimelineError(f"A short can be at most {int(MAX_LENGTH // 60)} minutes long.")
    return out


def words_on_timeline(segments: list[list[float]], words: list[list]) -> tuple[list[list], float]:
    """Words (fillers left out) on the short's own clock, for captions; and the short's length."""
    out: list[list] = []
    offset = 0.0
    starts = [w[0] for w in words]
    for s, e in segments:
        k = bisect.bisect_left(starts, s - 0.3)
        while k < len(words) and words[k][0] < e:
            w = words[k]
            mid = (w[0] + w[1]) / 2
            if s - 0.02 <= mid <= e + 0.02 and norm(w[2]) not in FILLERS:
                out.append([round(offset + max(0.0, w[0] - s), 3), round(offset + min(e, w[1]) - s, 3), w[2]])
            k += 1
        offset += e - s
    return out, round(offset, 2)


def cut_for(edit: dict, words: list[list], gaps: list[list[float]]) -> dict:
    """{segments, words, length} for a person's edit, the shape cut.build_cut returns."""
    clips = edit.get("clips") or []
    tighten = edit.get("tighten", True)
    tw = true_words(words, sorted(gaps or [])) if tighten else words
    starts = [w[0] for w in tw]
    segments: list[list[float]] = []
    for c in clips:
        s, e = float(c["start"]), float(c["end"])
        if tighten:
            k0 = bisect.bisect_left(starts, s - 0.05)
            idx = [k for k in range(k0, len(tw)) if tw[k][0] < e and (tw[k][0] + tw[k][1]) / 2 <= e + 0.02
                   and norm(tw[k][2]) not in FILLERS]
            spans = piece_spans(tw, idx) if idx else []
            # Keep the person's own edges: never start before or run past the clip they drew.
            spans = [[max(s, a), min(e, b)] for a, b in spans if min(e, b) - max(s, a) >= 0.08]
            if not idx:
                spans = [[s, e]]          # no words (music, a pause they want): keep it as drawn
        else:
            spans = [[s, e]]
        for a, b in spans:
            if segments and 0 <= a - segments[-1][1] < MERGE_GAP:
                segments[-1][1] = round(b, 3)
            else:
                segments.append([round(a, 3), round(b, 3)])
    if not segments:
        raise TimelineError("Nothing is left to play in this short.")
    if tighten and clips:                          # a tail after the last word, inside the clip the person drew
        from app.core.clipper.cut import land_ending

        segments = land_ending(segments, tw, limit=float(clips[-1]["end"]))
    out_words, length = words_on_timeline(segments, tw)
    return {"segments": segments, "words": out_words, "length": length}
