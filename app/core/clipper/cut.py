"""The exact cut of a short: which stretches of the source play, in what order, with the dead air removed.

Input: the plan (pieces of words, app/core/clipper/editorial.py:plan_from_edl), every word with its timing, and the
measured silence map. Output: ``segments`` ([[start, end], ...] on the source, in playing order) and the words on
the short's own timeline (for captions). Pure functions; tested in tests/test_clipper.py.

Rules, the way an editor cuts:
* Cuts land on word boundaries with a breath of padding, never inside a word.
* A pause of ``speech.PAUSE`` or more inside a piece is removed (word gaps and measured silences both count).
* Hesitations ("um", "uh") are dropped everywhere; a connective ("so", "and", "but") only where the whole short
  opens, since inside the edit it carries the join between two pieces.
* A cold open plays first and again in its place.
* If the measured cut still lands under the length floor, spare sentences are played straight after the piece
  they continue (never after the ending).
"""
from __future__ import annotations

import bisect

from app.core.clipper.speech import EDGE_CONNECTIVES, FILLERS, PAD_IN, PAD_OUT, PAUSE, norm

MERGE_GAP = 0.06


def _kept(words: list[list], w0: int, w1: int, *, opening: bool) -> list[int]:
    idx = [k for k in range(max(0, w0), min(len(words), w1)) if norm(words[k][2]) not in FILLERS]
    if opening:
        while len(idx) > 3 and norm(words[idx[0]][2]) in EDGE_CONNECTIVES:
            idx.pop(0)
    return idx


def _gap_between(gaps: list[list[float]], starts: list[float], a_end: float, b_start: float) -> list[float] | None:
    """A measured silence of at least PAUSE seconds lying between two consecutive words."""
    k = bisect.bisect_left(starts, a_end - 0.6)
    while k < len(gaps) and gaps[k][0] < b_start:
        g = gaps[k]
        if g[1] - g[0] >= PAUSE and g[1] > a_end - 0.6 and g[0] < b_start + 0.25:
            return g
        k += 1
    return None


def piece_spans(words: list[list], idx: list[int], gaps: list[list[float]], starts: list[float]) -> list[list[float]]:
    """Continuous-speech spans for the kept words, padded, cut at pauses."""
    if not idx:
        return []
    spans: list[list[float]] = []
    cur_s, cur_e = words[idx[0]][0], words[idx[0]][1]
    for p, k in zip(idx, idx[1:]):
        s, e = words[k][0], words[k][1]
        split_at = None
        if k != p + 1:
            split_at = (cur_e, s)                       # a dropped filler between them: always cut it out
        elif s - cur_e >= PAUSE:
            split_at = (cur_e, s)
        else:
            g = _gap_between(gaps, starts, words[p][1], s) if gaps else None
            if g and g[0] > words[p][0] + 0.1:
                split_at = (min(cur_e, g[0]), max(s, g[1] - 0.04))
        if split_at:
            spans.append([cur_s, split_at[0]])
            cur_s, cur_e = split_at[1], max(e, split_at[1] + 0.05)
        else:
            cur_e = max(cur_e, e)
    spans.append([cur_s, cur_e])
    # Breath padding, without running into the neighbouring word.
    out = []
    for n, (s, e) in enumerate(spans):
        lo = s - PAD_IN
        hi = e + PAD_OUT
        if out:
            lo = max(lo, out[-1][1])
        if n + 1 < len(spans):
            hi = min(hi, spans[n + 1][0] - 0.02)
        out.append([max(0.0, lo), max(lo + 0.05, hi)])
    return out


def nudged(plan: list[dict], words: list[list], nudge_start: float, nudge_end: float) -> list[dict]:
    """Move the short's first spoken word earlier/later by ``nudge_start`` seconds, its end by ``nudge_end``
    (whole words only). Positive start = start later; positive end = end later."""
    plan = [dict(p) for p in plan]
    body = [p for p in plan if not p.get("spare") and not p.get("cold_open")]
    if not body or not words:
        return plan
    starts = [w[0] for w in words]
    if nudge_start:
        first = body[0]
        t = words[first["w0"]][0] + nudge_start
        k = bisect.bisect_left(starts, t - 0.01)
        first["w0"] = max(0, min(k, first["w1"] - 1))
    if nudge_end:
        last = body[-1]
        t = words[last["w1"] - 1][1] + nudge_end
        k = bisect.bisect_right([w[1] for w in words], t + 0.01)
        last["w1"] = max(last["w0"] + 1, min(len(words), k))
    return plan


def build_cut(plan: list[dict], words: list[list], gaps: list[list[float]], *, floor: float = 0.0,
              nudge_start: float = 0.0, nudge_end: float = 0.0) -> dict:
    plan = nudged(plan, words, nudge_start, nudge_end)
    gaps = sorted(gaps or [])
    starts = [g[0] for g in gaps]
    played = [p for p in plan if not p.get("spare")]
    spares = [p for p in plan if p.get("spare")]

    def spans_for(p, opening):
        return piece_spans(words, _kept(words, p["w0"], p["w1"], opening=opening), gaps, starts)

    pieces = [spans_for(p, n == 0) for n, p in enumerate(played)]
    origin = list(range(len(pieces)))      # which planned piece each entry is (spares: the piece they follow)
    last = len(pieces) - 1
    total = sum(e - s for sp in pieces for s, e in sp)
    if total < floor and spares:
        for sp in spares:
            if total >= floor:
                break
            after = sp.get("after", -1)
            if not 0 <= after < last:                    # never after the ending
                continue
            at = max(k for k, o in enumerate(origin) if o == after) + 1
            extra = spans_for(sp, False)
            pieces.insert(at, extra)
            origin.insert(at, after)
            total += sum(e - s for s, e in extra)

    segments: list[list[float]] = []
    for sp in pieces:
        for s, e in sp:
            if segments and 0 <= s - segments[-1][1] < MERGE_GAP:
                segments[-1][1] = e
            else:
                segments.append([round(s, 3), round(e, 3)])
    segments = [s for s in segments if s[1] - s[0] >= 0.08]

    # Words on the short's own timeline.
    out_words: list[list] = []
    offset = 0.0
    word_starts = [w[0] for w in words]
    for s, e in segments:
        k = bisect.bisect_left(word_starts, s - 0.3)
        while k < len(words) and words[k][0] < e:
            w = words[k]
            mid = (w[0] + w[1]) / 2
            if s - 0.02 <= mid <= e + 0.02 and norm(w[2]) not in FILLERS:
                out_words.append([round(offset + max(0.0, w[0] - s), 3), round(offset + min(e, w[1]) - s, 3), w[2]])
            k += 1
        offset += e - s
    return {"segments": segments, "words": out_words, "length": round(offset, 2)}
