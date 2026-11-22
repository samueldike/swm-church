"""Clip only the senior pastor: his voice, told apart from everyone else, including pastors who sound like him.

Per church (branch setting ``clipper_voices``, JSON)::

    {"mode": "all" | "senior",
     "people": [{"id", "name", "role": "senior" | "other",
                 "samples": [{"id", "label", "embs": [[256 floats], ...]}]}]}

Each sample (a stretch of a past video, or an uploaded recording) becomes several 8-second voiceprints
(app/core/clipper/voice.py). A stretch of a sermon is credited to the senior pastor only when

* it is clearly like him: cosine to his voiceprints >= ``accept`` (calibrated from how alike his own samples are,
  never below ``MIN_ACCEPT``), and
* it is clearly more like him than like any other registered pastor (``MARGIN``): a pastor who has learned his
  phrasing still has his own vocal tract, and registering him is what makes the difference measurable.

Windows of ``STEP`` seconds are judged across each sermon, quick flips are smoothed (``SMOOTH``), and only his runs of
at least ``MIN_RUN`` seconds are clipped. Pure rules are tested in tests/test_clipper.py.
"""
from __future__ import annotations

import json
import uuid

import numpy as np

STEP = 15.0
MIN_ACCEPT = 0.55
MARGIN = 0.10
SMOOTH = 3              # windows each side in the majority vote
MIN_RUN = 60.0
QUIET_DB = -45.0        # windows quieter than this are not judged (silence, a pause)
MAX_SAMPLES = 8


def empty() -> dict:
    return {"mode": "all", "people": []}


def parse(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    out = empty()
    out["mode"] = "senior" if data.get("mode") == "senior" else "all"
    for p in data.get("people") or []:
        if isinstance(p, dict) and p.get("id") and p.get("role") in ("senior", "other"):
            out["people"].append({"id": p["id"], "name": str(p.get("name") or "")[:80], "role": p["role"],
                                  "samples": [s for s in p.get("samples") or [] if isinstance(s, dict) and s.get("embs")]})
    return out


def senior(cfg: dict) -> dict | None:
    return next((p for p in cfg.get("people") or [] if p["role"] == "senior"), None)


def prints(person: dict | None) -> np.ndarray:
    rows = [e for s in (person or {}).get("samples") or [] for e in s["embs"]]
    return np.asarray(rows, dtype=np.float32) if rows else np.zeros((0, 256), dtype=np.float32)


def _score(x: np.ndarray, bank: np.ndarray) -> float:
    """How alike a voiceprint is to a person: the mean of its 3 best matches among their voiceprints."""
    if not len(bank):
        return -1.0
    sims = bank @ x / ((np.linalg.norm(bank, axis=1) * np.linalg.norm(x)) + 1e-9)
    top = np.sort(sims)[-3:]
    return float(top.mean())


def calibrate(cfg: dict) -> dict:
    """{accept, consistency, closest_other, strength, note} from the registered samples (pure; tested)."""
    s = senior(cfg)
    own = prints(s)
    if len(own) < 4 or len({x["id"] for x in (s or {}).get("samples") or []}) < 2:
        return {"accept": None, "consistency": None, "closest_other": None, "strength": "missing",
                "note": "Add at least two parts of the chosen preacher's preaching, from different services."}
    # Leave-one-sample-out: how alike his samples are to each other (different days, rooms, moods).
    by_sample = {}
    for smp in s["samples"]:
        by_sample[smp["id"]] = np.asarray(smp["embs"], dtype=np.float32)
    held = []
    for sid, embs in by_sample.items():
        rest = np.concatenate([e for k, e in by_sample.items() if k != sid]) if len(by_sample) > 1 else embs
        held += [_score(x, rest) for x in embs]
    consistency = float(np.percentile(held, 20))
    accept = max(MIN_ACCEPT, consistency - 0.08)
    others = [p for p in cfg["people"] if p["role"] == "other" and len(prints(p))]
    closest = max((_score(x, own) for p in others for x in prints(p)), default=None)
    if closest is not None and closest > accept - MARGIN:
        strength, note = "fair", ("Another voice in the library sounds very close; add more parts of both from "
                                  "different services so the difference is clearer.")
    elif consistency >= 0.7:
        strength, note = "strong", "The voice is recognised reliably."
    else:
        strength, note = "fair", "Add another sample from a different service to make recognition steadier."
    return {"accept": round(accept, 3), "consistency": round(consistency, 3),
            "closest_other": None if closest is None else round(closest, 3), "strength": strength, "note": note}


def judge(windows: list[tuple[float, np.ndarray | None]], cfg: dict, accept: float) -> list[tuple[float, bool | None]]:
    """[(time, is_him or None for a quiet window)] after smoothing (pure; tested)."""
    own = prints(senior(cfg))
    others = [prints(p) for p in cfg["people"] if p["role"] == "other" and len(prints(p))]
    raw: list[bool | None] = []
    for _t, x in windows:
        if x is None:
            raw.append(None)
            continue
        me = _score(x, own)
        rival = max((_score(x, o) for o in others), default=-1.0)
        raw.append(bool(me >= accept and me - rival >= MARGIN))
    out = []
    for k, (t, _x) in enumerate(windows):
        if raw[k] is None:
            out.append((t, None))
            continue
        votes = [v for v in raw[max(0, k - SMOOTH):k + SMOOTH + 1] if v is not None]
        out.append((t, sum(votes) * 2 > len(votes)))
    return out


def his_ranges(verdicts: list[tuple[float, bool | None]], body: list[list[float]]) -> list[list[float]]:
    """The parts of the sermon(s) he preaches: runs of his windows (quiet ones carry the run) >= MIN_RUN (pure)."""
    out: list[list[float]] = []
    for a, b in body:
        run = None
        for t, him in [v for v in verdicts if a <= v[0] < b] + [(b, False)]:
            if him or (him is None and run is not None):
                run = run if run is not None else t
            elif run is not None:
                if min(t, b) - run >= MIN_RUN:
                    out.append([run, min(t, b)])
                run = None
    return out


def share(verdicts: list[tuple[float, bool | None]]) -> float:
    judged = [v for _t, v in verdicts if v is not None]
    return sum(judged) / len(judged) if judged else 0.0


def new_id() -> str:
    return uuid.uuid4().hex[:10]


async def load(db, branch_id: int) -> dict:
    from app.core.branch_settings import get_branch_settings

    return parse((await get_branch_settings(db, branch_id)).get("clipper_voices"))


async def save(db, branch_id: int, cfg: dict) -> None:
    from app.core.branch_settings import update_branch_settings

    await update_branch_settings(db, branch_id, {"clipper_voices": json.dumps(cfg)})


def sample_prints(audio_path, start: float, end: float) -> list[list[float]]:
    """Voiceprints for a stretch of 16 kHz audio: one per 8 s of real speech (quiet stretches skipped)."""
    from app.core.clipper import voice

    out = []
    t = start
    while t + voice.WINDOW <= end and len(out) < 24:
        x = voice.read(audio_path, t, t + voice.WINDOW)
        if len(x) and 20 * np.log10(np.sqrt(float((x * x).mean())) + 1e-9) > QUIET_DB:
            out.append([round(float(v), 4) for v in voice.embed(x)])
        t += voice.WINDOW
    return out


def windows_for(audio_path, body: list[list[float]]) -> list[tuple[float, np.ndarray | None]]:
    """A voiceprint every STEP seconds across the sermon(s); None for quiet windows."""
    from app.core.clipper import voice

    out = []
    for a, b in body:
        t = a
        while t + voice.WINDOW <= b:
            x = voice.read(audio_path, t, t + voice.WINDOW)
            loud = len(x) and 20 * np.log10(np.sqrt(float((x * x).mean())) + 1e-9) > QUIET_DB
            out.append((t, voice.embed(x) if loud else None))
            t += STEP
    return out
