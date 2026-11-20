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


# Noise reduction like CapCut's: RNNoise (ffmpeg arnndn), a neural network that keeps speech and removes hum, hiss,
# fans and room noise. Measured on a real sermon: the noise between words fell 17 dB (natural) / 22 dB (strong) while
# speech moved under 2 dB; ffmpeg's plain FFT denoiser managed 1 dB. Models: github.com/GregorR/rnnoise-models
# (beguiling-drafter = voice in a recording, somnolent-hogwash = speech in a recording; not subject to copyright).
DENOISE_MODELS = {"natural": "rnnoise-voice.rnnn", "strong": "rnnoise-speech.rnnn"}


def denoise_chain(level: str | None) -> str:
    """The audio filters for a noise-reduction level ("" when off) (pure; tested)."""
    from app.core.clipper.media import ASSETS

    model = DENOISE_MODELS.get(level or "")
    if not model:
        return ""
    return f"highpass=f=70,arnndn=m='{(ASSETS / model).as_posix()}',"


def graph(segments: list[list[float]], vfs: list[str], fps: int, *, subtitles: str | None,
          denoise: str | None = None) -> tuple[list[tuple[float, float]], str]:
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
    lines.append(f"[ac]{denoise_chain(denoise)}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]")
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
           quality: int, framing: str, caption_style: str, progress=None, frame_x: float | None = None,
           denoise: str | None = None) -> dict:
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
        inputs, filters = graph(segments, vfs, fps, subtitles=None, denoise=denoise)
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
        for pattern in (f"{out.stem}.cap-*.mp4", f"{out.stem}.fin-*.mp4"):   # finished copies of the previous cut
            for old in out.parent.glob(pattern):
                old.unlink(missing_ok=True)
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.5, length / 3):.2f}", "-i", str(out), "-frames:v", "1",
             "-vf", "scale=360:-2", "-q:v", "4", str(out.with_suffix(".jpg"))], timeout=120)
        report(1.0)
        return {"length": round(length, 2), "width": ow, "height": oh}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def captioned_path(master: Path, style: str) -> Path:
    return master.with_name(f"{master.stem}.cap-{style}.mp4")


def finished_path(master: Path, style: str | None, frame_id: str | None, outro_id: str | None = None) -> Path:
    if not frame_id and not outro_id:
        return captioned_path(master, style) if style else master
    return master.with_name(f"{master.stem}.fin-{style or 'none'}-{frame_id or 'none'}-{outro_id or 'none'}.mp4")


def finish(master: Path, style: str | None, frame: Path | None = None, frame_id: str | None = None,
           outro: Path | None = None, outro_item: dict | None = None) -> Path:
    """The master with its frame and then its captions on top, followed by its outro, in ONE encode, cached beside
    the master. Without an outro the sound is copied. With nothing to add, the master itself."""
    from app.core.clipper import outros

    sid = captions.style_id(style) if style else None
    if frame is not None and not frame.exists():
        frame, frame_id = None, None
    if outro is not None and (not outro.exists() or not outro_item):
        outro, outro_item = None, None
    if sid is None and frame is None and outro is None:
        return master
    out = finished_path(master, sid, frame_id if frame is not None else None, outro_item["id"] if outro is not None else None)
    if out.exists() and out.stat().st_mtime >= master.stat().st_mtime:
        return out
    meta_file = master.with_suffix(".words.json")
    if sid and not meta_file.exists():
        raise ClipperError("Re-cut this short once to add captions to it.")
    from app.core.clipper.media import probe

    info = probe(master)
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {"words": []}
    width, height = int(meta.get("width") or info["width"]), int(meta.get("height") or info["height"])
    fps = _fps(float(info.get("fps") or 30))
    work = Path(tempfile.mkdtemp(prefix="fin_", dir=str(master.parent)))
    try:
        chain, last = [], "0:v"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(master)]
        n = 1
        if frame is not None:
            cmd += ["-i", str(frame)]
            chain.append(f"[{n}:v]scale={width}:{height},format=rgba[fr]")
            chain.append(f"[{last}][fr]overlay=0:0:format=auto[vf]")
            last, n = "vf", n + 1
        if sid:
            ass_file = work / "captions.ass"
            ass_file.write_text(captions.ass(meta["words"], style=sid, width=width, height=height), encoding="utf-8")
            esc = str(ass_file).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            fonts = captions.fonts_dir().replace("\\", "/").replace(":", "\\:")
            chain.append(f"[{last}]subtitles='{esc}':fontsdir='{fonts}'[vc]")
            last = "vc"
        if outro is None:
            chain.append(f"[{last}]format=yuv420p[vo]")
            maps, audio = ["-map", "[vo]", "-map", "0:a?"], ["-c:a", "copy"]
        else:
            secs = outros.seconds_of(outro_item) or 3.0
            if outro_item["kind"] == "image":
                cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{secs:.2f}", "-i", str(outro)]
            else:
                cmd += ["-i", str(outro)]
            oi = n
            n += 1
            if outro_item["kind"] == "video" and outro_item.get("audio"):
                oa = f"[{oi}:a]aresample=48000,aformat=channel_layouts=stereo[oa]"
            else:
                cmd += ["-f", "lavfi", "-t", f"{secs:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
                oa = f"[{n}:a]aformat=channel_layouts=stereo[oa]"
                n += 1
            chain.append(f"[{last}]fps={fps},format=yuv420p,setsar=1[vb]")
            chain.append(outros.fit_filter(width, height, f"{oi}:v", "vx"))
            chain.append(f"[vx]fps={fps},trim=duration={secs:.2f},setpts=PTS-STARTPTS[vy]")
            chain.append("[0:a]aresample=48000,aformat=channel_layouts=stereo[ba]")
            chain.append(oa)
            chain.append("[vb][ba][vy][oa]concat=n=2:v=1:a=1[vo][ao]")
            maps, audio = ["-map", "[vo]", "-map", "[ao]"], ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
        tmp = work / "out.mp4"
        script = work / "graph.txt"
        script.write_text(";\n".join(chain), encoding="utf-8")
        cmd += ["-/filter_complex", str(script), *maps, "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
                *audio, "-movflags", "+faststart", str(tmp)]
        run(cmd, timeout=900)
        tmp.replace(out)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def burn(master: Path, style: str) -> Path:
    """The master with the ``style`` captions burned in (no frame)."""
    return finish(master, style)
