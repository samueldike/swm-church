"""Rendering one short with ffmpeg, in ONE encode.

The cut's segments are read straight from the source (segments close together share one accurately seeked
input), each gets the camera's crop and a 12 ms audio fade at each edge so joins never click, they are joined
inside the filter graph, the captions (if any) are burned in and the loudness is normalised to the -14 LUFS that
social platforms play at, then the MP4 is written once (+faststart). Encoding once instead of per segment and
again for the final pass roughly halves the time a short takes.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.clipper import captions, reframe
from app.core.clipper.media import ClipperError, run

logger = logging.getLogger(__name__)

CLUSTER_GAP = 40.0    # segments closer than this read from one input (decoding the gap is cheaper than a seek)
LEAD = 1.0            # seconds decoded before a cluster's first segment
PRESET = "veryfast"    # x264 speed/size trade-off for the one encode
CRF = "20"


def _fps(src_fps: float) -> int:
    return 25 if src_fps and src_fps < 26.5 else 30


def clusters(segments: list[list[float]], gap: float = CLUSTER_GAP) -> list[list[int]]:
    """Indexes of segments grouped into runs that one input can serve, in source order (pure; tested)."""
    order = sorted(range(len(segments)), key=lambda i: segments[i][0])
    groups: list[list[int]] = []
    for i in order:
        if groups and segments[i][0] - max(segments[j][1] for j in groups[-1]) <= gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def _scoped(vf: str, n: int) -> str:
    """Give a segment's filter-chain labels (the blurred-background split) names of their own."""
    for label in ("bg", "fg", "a", "b"):
        vf = vf.replace(f"[{label}]", f"[x{label}{n}]")
    return vf


def graph(segments: list[list[float]], vfs: list[str], fps: int, *, subtitles: str | None) -> tuple[list[tuple[float, float]], str]:
    """(inputs as (seek, duration), filter graph) for the whole short (pure; tested)."""
    inputs: list[tuple[float, float]] = []
    where: dict[int, tuple[int, float]] = {}
    for group in clusters(segments):
        start = max(0.0, min(segments[i][0] for i in group) - LEAD)
        end = max(segments[i][1] for i in group) + 0.5
        for i in group:
            where[i] = (len(inputs), start)
        inputs.append((start, end - start))

    uses: dict[int, list[int]] = {}
    for i in range(len(segments)):
        uses.setdefault(where[i][0], []).append(i)
    lines, vlab, alab = [], {}, {}
    for k, idx in uses.items():
        if len(idx) == 1:
            vlab[idx[0]], alab[idx[0]] = f"{k}:v", f"{k}:a"
        else:
            lines.append(f"[{k}:v]split={len(idx)}" + "".join(f"[sv{i}]" for i in idx))
            lines.append(f"[{k}:a]asplit={len(idx)}" + "".join(f"[sa{i}]" for i in idx))
            for i in idx:
                vlab[i], alab[i] = f"sv{i}", f"sa{i}"

    for n, (s, e) in enumerate(segments):
        base = where[n][1]
        a, b = s - base, e - base
        d = max(0.05, e - s)
        fade = min(0.012, d / 4)
        lines.append(f"[{vlab[n]}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,{_scoped(vfs[n], n)},"
                     f"fps={fps},format=yuv420p[vseg{n}]")
        lines.append(f"[{alab[n]}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=channel_layouts=stereo,afade=t=in:st=0:d={fade:.3f},"
                     f"afade=t=out:st={max(0.0, d - fade):.3f}:d={fade:.3f}[aseg{n}]")
    joined = "".join(f"[vseg{n}][aseg{n}]" for n in range(len(segments)))
    lines.append(f"{joined}concat=n={len(segments)}:v=1:a=1[vc][ac]")
    lines.append(f"[vc]{subtitles}[vo]" if subtitles else "[vc]null[vo]")
    lines.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]")
    return inputs, ";\n".join(lines)


def _encode(cmd: list[str], total: float, progress=None, timeout: float = 1800) -> None:
    """Run ffmpeg with -progress on stdout, reporting the share of the short written so far."""
    import time

    proc = subprocess.Popen(cmd + ["-progress", "pipe:1", "-nostats"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    t0 = time.time()
    try:
        for line in proc.stdout:
            if line.startswith("out_time_us=") and progress and total > 0:
                try:
                    progress(min(0.99, int(line.split("=", 1)[1]) / 1e6 / total))
                except ValueError:
                    pass
            if time.time() - t0 > timeout:
                proc.kill()
                raise ClipperError("ffmpeg took too long and was stopped.")
        err = proc.stderr.read()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        tail = (err or "").strip().splitlines()[-6:]
        logger.warning("ffmpeg failed (%s): %s", proc.returncode, " | ".join(tail))
        if any("No space left" in line for line in tail):
            raise ClipperError("The server's disk is full. Delete old projects, then try again.")
        raise ClipperError(f"ffmpeg failed: {(tail[-1] if tail else 'unknown error')[:300]}")


def render(*, src: Path, info: dict, segments: list[list[float]], words: list[list], out: Path, fmt: str,
           quality: int, framing: str, caption_style: str, progress=None, frame_x: float | None = None) -> dict:
    """Write the short to ``out`` (+ .srt and .jpg beside it). Returns {length, width, height}."""
    if not segments:
        raise ClipperError("Nothing is left to play in this short.")
    if not src.exists():
        raise ClipperError("The source video has been removed, so this short can no longer be re-cut.")
    src_w, src_h = int(info.get("width") or 0), int(info.get("height") or 0)
    if not info.get("has_video", True) or not src_w:
        raise ClipperError("The source has no picture.")
    ow, oh = reframe.output_size(fmt, quality)
    fps = _fps(float(info.get("fps") or 30))
    manual = frame_x is not None and framing != "fit"
    track = reframe.needs_tracking(fmt, framing, src_w, src_h) and not manual
    crop_w = (int(src_h * ow / oh) // 2 * 2) / src_w if track else 1.0
    report = progress or (lambda f: None)
    length = sum(e - s for s, e in segments)
    # A YouTube source is kept whole when only part of it was chosen; the words are timed from that part's start.
    offset = float(info.get("offset") or 0.0)

    work = Path(tempfile.mkdtemp(prefix="clip_", dir=str(out.parent)))
    try:
        # 1. The camera: where the speaker is in each segment (a light 6 fps look at a small picture).
        vfs, centre = [], None
        looked: dict[int, tuple[float, list[dict]]] = {}
        if track:  # one look per group of nearby segments, then each segment takes its own samples
            for group in clusters(segments):
                a, b = min(segments[i][0] for i in group), max(segments[i][1] for i in group)
                samples = reframe.detect(str(src), a + offset, b + offset, src_w, src_h)
                for i in group:
                    looked[i] = (a, samples)
        for n, (s, e) in enumerate(segments):
            keys = None
            if track:
                a, every = looked[n]
                samples = [{**x, "t": x["t"] - (s - a)} for x in every if s - a - 1e-6 <= x["t"] <= e - a + 1e-6]
                if samples:
                    samples[0] = {**samples[0], "cut": False}
                subject = reframe.choose_subject(samples, centre)
                keys = reframe.camera_path(subject, crop_w, start_x=centre, duration=max(0.05, e - s))
                centre = keys[-1][1]
            if manual:                     # the editor's fixed crop position
                # frame_x 0..1 = the crop from the left edge to the right edge; keys hold the crop's centre.
                frac = min(1.0, (int(src_h * ow / oh) // 2 * 2) / src_w) if src_w else 1.0
                fx = frac / 2 + min(1.0, max(0.0, float(frame_x))) * (1 - frac)
                keys = [(0.0, fx), (max(0.05, e - s), fx)]
            vfs.append(reframe.video_filter(fmt=fmt, quality=quality, framing="speaker" if manual else framing,
                                            src_w=src_w, src_h=src_h, keys=keys))
            report(0.25 * (n + 1) / len(segments) if track else 0.02)

        # 2. One encode: segments, joins, loudness. The master never has captions burned in: they are drawn live
        # in the browser and burned into a copy (``burn``) for the download and the post.
        inputs, filters = graph(segments, vfs, fps, subtitles=None)
        script = work / "graph.txt"
        script.write_text(filters, encoding="utf-8")
        tmp_out = work / "final.mp4"
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for seek, dur in inputs:
            cmd += ["-ss", f"{seek + offset:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
        cmd += ["-/filter_complex", str(script), "-map", "[vo]", "-map", "[ao]",
                "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(tmp_out)]
        start_share = 0.25 if track else 0.02
        _encode(cmd, length, lambda f: report(start_share + (0.97 - start_share) * f))
        tmp_out.replace(out)

        out.with_suffix(".srt").write_text(captions.srt(words), encoding="utf-8")
        out.with_suffix(".words.json").write_text(json.dumps({"words": words, "width": ow, "height": oh}),
                                                  encoding="utf-8")
        for old in out.parent.glob(f"{out.stem}.cap-*.mp4"):      # captioned copies of the previous cut
            old.unlink(missing_ok=True)
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.5, length / 3):.2f}", "-i", str(out), "-frames:v", "1",
             "-vf", "scale=360:-2", "-q:v", "4", str(out.with_suffix(".jpg"))], timeout=120)
        report(1.0)
        return {"length": round(length, 2), "width": ow, "height": oh}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def captioned_path(master: Path, style: str) -> Path:
    return master.with_name(f"{master.stem}.cap-{style}.mp4")


def burn(master: Path, style: str) -> Path:
    """The master with the ``style`` captions burned in (cached beside it). Video re-encoded once, sound copied."""
    sid = captions.style_id(style)
    if sid is None:
        return master
    out = captioned_path(master, sid)
    if out.exists() and out.stat().st_mtime >= master.stat().st_mtime:
        return out
    meta_file = master.with_suffix(".words.json")
    if not meta_file.exists():
        raise ClipperError("Re-cut this short once to add captions to it.")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="cap_", dir=str(master.parent)))
    try:
        ass_file = work / "captions.ass"
        ass_file.write_text(captions.ass(meta["words"], style=sid, width=int(meta["width"]), height=int(meta["height"])),
                            encoding="utf-8")
        esc = str(ass_file).replace("\\", "/").replace(":", "\:").replace("'", "\'")
        fonts = captions.fonts_dir().replace("\\", "/").replace(":", "\:")
        tmp = work / "out.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(master), "-vf", f"subtitles='{esc}':fontsdir='{fonts}'",
             "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p", "-c:a", "copy",
             "-movflags", "+faststart", str(tmp)], timeout=900)
        tmp.replace(out)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)
