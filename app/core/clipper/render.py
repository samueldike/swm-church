"""Rendering one short with ffmpeg.

1. Each segment of the cut is encoded on its own (accurate seek, the camera's crop, a 12 ms audio fade at each edge
   so joins never click) at a high-quality intermediate setting.
2. The segments are joined (stream copy), then one final pass burns the captions (if any), normalises loudness to
   the -14 LUFS that social platforms play at, and writes a web-ready MP4 (+faststart).
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from app.core.clipper import captions, reframe
from app.core.clipper.media import ClipperError, run

logger = logging.getLogger(__name__)


def _fps(src_fps: float) -> int:
    return 25 if src_fps and src_fps < 26.5 else 30


def render(*, src: Path, info: dict, segments: list[list[float]], words: list[list], out: Path, fmt: str,
           quality: int, framing: str, caption_style: str, progress=None) -> dict:
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
    track = reframe.needs_tracking(fmt, framing, src_w, src_h)
    crop_w = (int(src_h * ow / oh) // 2 * 2) / src_w if track else 1.0

    work = Path(tempfile.mkdtemp(prefix="clip_", dir=str(out.parent)))
    try:
        parts = []
        centre = None
        total = len(segments)
        for n, (s, e) in enumerate(segments):
            d = max(0.05, e - s)
            keys = None
            if track:
                samples = reframe.detect(str(src), s, e, src_w, src_h)
                subject = reframe.choose_subject(samples, centre)
                keys = reframe.camera_path(subject, crop_w, start_x=centre, duration=d)
                centre = keys[-1][1]
            vf = reframe.video_filter(fmt=fmt, quality=quality, framing=framing, src_w=src_w, src_h=src_h, keys=keys)
            fade = min(0.012, d / 4)
            af = f"aresample=48000,afade=t=in:st=0:d={fade:.3f},afade=t=out:st={max(0.0, d - fade):.3f}:d={fade:.3f}"
            part = work / f"p{n:03d}.mp4"
            cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.3f}", "-i", str(src), "-t", f"{d:.3f}",
                   "-vf", f"{vf},fps={fps}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                   "-pix_fmt", "yuv420p"]
            # Sources without sound are refused when they are fetched (the clipper works from speech).
            cmd += ["-af", af, "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000"]
            cmd += ["-video_track_timescale", "90000", "-avoid_negative_ts", "make_zero", str(part)]
            run(cmd, timeout=1800)
            parts.append(part)
            if progress:
                progress((n + 1) / (total + 1))

        listing = work / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        joined = work / "joined.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)],
            timeout=900)

        tmp_out = work / "final.mp4"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(joined)]
        if caption_style in ("bold", "clean") and words:
            ass_file = work / "captions.ass"
            ass_file.write_text(captions.ass(words, style=caption_style, width=ow, height=oh), encoding="utf-8")
            esc = str(ass_file).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            fonts = captions.fonts_dir().replace("\\", "/").replace(":", "\\:")
            cmd += ["-vf", f"subtitles='{esc}':fontsdir='{fonts}'", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "20", "-pix_fmt", "yuv420p"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-movflags", "+faststart", str(tmp_out)]
        run(cmd, timeout=1800)
        tmp_out.replace(out)

        length = sum(e - s for s, e in segments)
        out.with_suffix(".srt").write_text(captions.srt(words), encoding="utf-8")
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.5, length / 3):.2f}", "-i", str(out), "-frames:v", "1",
             "-vf", "scale=360:-2", "-q:v", "4", str(out.with_suffix(".jpg"))], timeout=120)
        if progress:
            progress(1.0)
        return {"length": round(length, 2), "width": ow, "height": oh}
    finally:
        shutil.rmtree(work, ignore_errors=True)
