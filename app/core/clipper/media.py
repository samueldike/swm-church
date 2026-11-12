"""Files and tools for the Video Clipper: where things live, ffmpeg/ffprobe, yt-dlp, the silence map."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "assets"
MIN_FREE_BYTES = 6 * 1024 ** 3          # refuse new work below this much free disk


class ClipperError(Exception):
    """A failure with a message fit to show the person who started the job."""


def root() -> Path:
    p = Path(settings.clipper_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_dir(project_id: int) -> Path:
    p = root() / str(int(project_id))
    (p / "shorts").mkdir(parents=True, exist_ok=True)
    return p


def source_path(project_id: int) -> Path:
    return project_dir(project_id) / "source.mp4"


def audio_path(project_id: int) -> Path:
    return project_dir(project_id) / "audio.wav"


def words_path(project_id: int) -> Path:
    return project_dir(project_id) / "words.json"


def short_file(project_id: int, short_id: int, ext: str) -> Path:
    return project_dir(project_id) / "shorts" / f"{int(short_id)}.{ext}"


def free_bytes() -> int:
    try:
        return shutil.disk_usage(root()).free
    except OSError:
        return 0


def enough_disk() -> bool:
    return free_bytes() >= MIN_FREE_BYTES


def remove_project_files(project_id: int) -> None:
    shutil.rmtree(root() / str(int(project_id)), ignore_errors=True)


def remove_source(project_id: int) -> None:
    for p in (source_path(project_id), audio_path(project_id)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# ─── running tools ────────────────────────────────────────────────────

def run(cmd: list[str], *, timeout: float | None = None, what: str = "ffmpeg") -> subprocess.CompletedProcess:
    logger.debug("clipper run: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ClipperError(f"{what} is not installed on the server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClipperError(f"{what} took too long and was stopped.") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        logger.warning("%s failed (%s): %s", what, proc.returncode, " | ".join(tail))
        if any("No space left" in line for line in tail):
            raise ClipperError("The server's disk is full. Delete old projects, then try again.")
        raise ClipperError(f"{what} failed: {(tail[-1] if tail else 'unknown error')[:300]}")
    return proc


def probe(path: Path) -> dict:
    """Duration, size, frame rate and whether there is a video and an audio stream."""
    proc = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
               timeout=120, what="ffprobe")
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 30.0
    if video:
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
        try:
            num, den = (float(x) for x in rate.split("/"))
            fps = num / den if den else 30.0
        except ValueError:
            fps = 30.0
        if not 5 <= fps <= 120:
            fps = 30.0
    duration = float((data.get("format") or {}).get("duration") or (video or {}).get("duration") or 0)
    rotation = 0
    for sd in (video or {}).get("side_data_list") or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"]) % 360
    w, h = int((video or {}).get("width") or 0), int((video or {}).get("height") or 0)
    if rotation in (90, 270):
        w, h = h, w
    return {"duration": duration, "width": w, "height": h, "fps": fps, "has_video": bool(video),
            "has_audio": bool(audio), "size": int((data.get("format") or {}).get("size") or 0)}


# ─── getting the source ──────────────────────────────────────────────

_URL_OK = re.compile(r"^https?://[^\s]+$", re.I)


def valid_url(url: str) -> bool:
    return bool(url) and len(url) <= 2000 and _URL_OK.match(url.strip()) is not None


def download(url: str, dest: Path, *, start: float | None = None, end: float | None = None,
             max_height: int = 1080, progress=None) -> str:
    """Fetch ``url`` (YouTube, Facebook, Vimeo, a direct .mp4 link...) into ``dest`` as mp4. Returns its title.

    With ``start``/``end`` only that part is fetched, which is much quicker for a long service.
    """
    work = dest.parent / "incoming"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    fmt = (f"bv*[height<={max_height}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={max_height}]+ba/"
           f"b[height<={max_height}]/bv*+ba/b")
    cmd = ["yt-dlp", "--no-playlist", "--no-progress", "--newline", "--no-warnings", "-f", fmt,
           "--merge-output-format", "mp4", "--restrict-filenames", "--print", "before_dl:TITLE %(title)s",
           "--progress-template", "download:PROGRESS %(progress._percent_str)s",
           "-o", str(work / "source.%(ext)s"), "--retries", "5", "--fragment-retries", "10"]
    if start is not None or end is not None:
        a = f"{start or 0:.0f}"
        b = f"{end:.0f}" if end is not None else "inf"
        cmd += ["--download-sections", f"*{a}-{b}", "--force-keyframes-at-cuts"]
    cmd.append(url.strip())

    title = ""
    tail: list[str] = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError as exc:
        raise ClipperError("yt-dlp is not installed on the server.") from exc
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("TITLE "):
            title = line[6:].strip()
        elif line.startswith("PROGRESS "):
            m = re.search(r"([\d.]+)%", line)
            if m and progress:
                progress(min(1.0, float(m.group(1)) / 100))
        elif line:
            tail = (tail + [line])[-8:]
    code = proc.wait()
    if code != 0:
        text = " ".join(tail)
        logger.warning("yt-dlp failed for %s: %s", url, text[-600:])
        if "Sign in to confirm" in text or "not a bot" in text:
            raise ClipperError("YouTube refused this server's request (it asks the server to sign in). "
                               "Download the video and upload the file instead.")
        if "Private video" in text or "members-only" in text or "This video is private" in text:
            raise ClipperError("That video is private. Upload the file instead.")
        if "is not a valid URL" in text or "Unsupported URL" in text:
            raise ClipperError("That link is not a video page the clipper can read. Upload the file instead.")
        if "No space left" in text:
            raise ClipperError("The server's disk is full. Delete old projects, then try again.")
        raise ClipperError("The video could not be downloaded. Check the link, or upload the file instead.")
    files = sorted(work.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise ClipperError("The download finished but no video file arrived.")
    got = files[0]
    if got.suffix.lower() != ".mp4":
        remux(got, dest)
    else:
        got.replace(dest)
    shutil.rmtree(work, ignore_errors=True)
    return title


def remux(src: Path, dest: Path) -> None:
    """Into an .mp4 container without re-encoding when possible (re-encode only if the codecs refuse)."""
    tmp = dest.with_suffix(".tmp.mp4")
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
             "-movflags", "+faststart", str(tmp)], timeout=3600)
    except ClipperError:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-map", "0:v:0?", "-map", "0:a:0?",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
             "-movflags", "+faststart", str(tmp)], timeout=6 * 3600)
    tmp.replace(dest)
    if src != dest:
        try:
            src.unlink()
        except FileNotFoundError:
            pass


def trim_upload(src: Path, dest: Path, start: float | None, end: float | None) -> None:
    """Keep only the chosen part of an uploaded file (stream copy: instant, cut on the nearest keyframe)."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if end is not None:
        cmd += ["-t", f"{max(1.0, end - (start or 0)):.3f}"]
    tmp = dest.with_suffix(".tmp.mp4")
    cmd += ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart", str(tmp)]
    run(cmd, timeout=3600)
    tmp.replace(dest)


def extract_audio(src: Path, dest: Path) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
         str(dest)], timeout=3 * 3600)


def silence_map(audio: Path, *, noise_db: int = -32, min_len: float = 0.3) -> list[list[float]]:
    """Every pause of at least ``min_len`` seconds as [start, end], from the audio itself."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-nostats", "-i", str(audio), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=3 * 3600)
    gaps: list[list[float]] = []
    start = None
    for line in (proc.stderr or "").splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            gaps.append([round(start, 3), round(float(m.group(1)), 3)])
            start = None
    return gaps
