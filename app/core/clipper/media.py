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


def is_youtube(url: str) -> bool:
    return re.search(r"(^|\.|//)(youtube\.com|youtu\.be)(/|$)", (url or "").lower()) is not None


def valid_cookies(text: str) -> bool:
    """A Netscape cookies.txt that holds YouTube/Google cookies (what browser exporters write)."""
    lines = [l.removeprefix("#HttpOnly_") for l in (text or "").splitlines()]
    lines = [l for l in lines if l.strip() and not l.startswith("#")]
    good = [l for l in lines if l.count("\t") >= 6 and (".youtube.com" in l or ".google.com" in l)]
    return len(good) >= 3


def download(url: str, dest: Path, *, start: float | None = None, end: float | None = None,
             max_height: int = 1080, cookies: str | None = None, progress=None) -> str:
    """Fetch ``url`` (YouTube, Facebook, Vimeo, a direct .mp4 link...) into ``dest`` as mp4. Returns its title.

    With ``start``/``end`` only that part is kept. The whole file is fetched at full speed and cut here with a
    stream copy: yt-dlp's ranged download pipes through ffmpeg at about real time, so 15 minutes took 15 minutes.
    """
    work = dest.parent / "incoming"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    fmt = (f"bv*[height<={max_height}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={max_height}]+ba/"
           f"b[height<={max_height}]/bv*+ba/b")
    cmd = ["yt-dlp", "--no-playlist", "--newline", "--no-warnings", "-f", fmt,
           "--merge-output-format", "mp4", "--restrict-filenames", "--print", "before_dl:TITLE %(title)s",
           "--progress-template", "download:PROGRESS %(progress._percent_str)s",
           "-o", str(work / "source.%(ext)s"), "--retries", "5", "--fragment-retries", "10",
           "--concurrent-fragments", "4"]
    cookie_file = None
    if cookies:
        # yt-dlp may write refreshed cookies back; the file lives only for this download.
        cookie_file = work / "cookies.txt"
        cookie_file.write_text(cookies, encoding="utf-8")
        os.chmod(cookie_file, 0o600)
        cmd += ["--cookies", str(cookie_file)]
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
    if cookie_file is not None:
        cookie_file.unlink(missing_ok=True)
    if code != 0:
        text = " ".join(tail)
        logger.warning("yt-dlp failed for %s: %s", url, text[-600:])
        if "Sign in to confirm" in text or "not a bot" in text or "cookies" in text.lower() and is_youtube(url):
            shutil.rmtree(work, ignore_errors=True)
            if cookies:
                raise ClipperError("YouTube refused the saved sign-in (it has probably expired). The HQ Administrator "
                                   "can save fresh YouTube cookies under Video Clipper → YouTube access, or upload the "
                                   "video file instead.")
            raise ClipperError("YouTube refused this server (it asks for a signed-in viewer). The HQ Administrator can "
                               "connect YouTube under Video Clipper → YouTube access, or upload the video file instead.")
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
    if start is not None or end is not None:
        trim_upload(got, dest, start, end)
    elif got.suffix.lower() != ".mp4":
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


FRAME_SEC = 0.02


def frame_levels(audio: Path) -> "list[float]":
    """Loudness (dB RMS) of every 20 ms frame of a 16 kHz mono 16-bit WAV, read in chunks."""
    import wave

    import numpy as np

    out = []
    with wave.open(str(audio), "rb") as wf:
        rate = wf.getframerate()
        hop = max(1, int(rate * FRAME_SEC))
        chunk = hop * 3000                      # one minute at a time
        rest = np.zeros(0, dtype=np.float32)
        while True:
            raw = wf.readframes(chunk)
            if not raw:
                break
            data = np.concatenate([rest, np.frombuffer(raw, dtype="<i2").astype(np.float32)])
            n = len(data) // hop
            if n:
                frames = data[: n * hop].reshape(n, hop)
                rms = np.sqrt(np.mean(frames * frames, axis=1)) + 1e-3
                out.append(20 * np.log10(rms / 32768.0))
            rest = data[n * hop:]
    return np.concatenate(out).tolist() if out else []


def silences_from_levels(levels: list[float], *, min_len: float = 0.3) -> list[list[float]]:
    """Pauses relative to THIS recording: below a threshold set between its noise floor and its speech level.

    A fixed threshold fails on real church recordings: room noise, a hum or a music bed sits above -32 dB, so
    ffmpeg's silencedetect found no pause at all in a typical service.
    """
    import numpy as np

    if not levels:
        return []
    db = np.asarray(levels, dtype=np.float32)
    floor = float(np.percentile(db, 6))
    speech = float(np.percentile(db, 80))
    if speech - floor < 8:                      # no usable contrast (music throughout, or near silence)
        return []
    threshold = floor + max(5.0, (speech - floor) * 0.32)
    quiet = db < threshold
    gaps: list[list[float]] = []
    need = int(round(min_len / FRAME_SEC))
    k, n = 0, len(quiet)
    while k < n:
        if quiet[k]:
            j = k
            while j < n and quiet[j]:
                j += 1
            if j - k >= need:
                gaps.append([round(k * FRAME_SEC, 3), round(j * FRAME_SEC, 3)])
            k = j
        else:
            k += 1
    return gaps


def silence_map(audio: Path, *, min_len: float = 0.3) -> list[list[float]]:
    """Every pause of at least ``min_len`` seconds as [start, end], measured from the audio itself."""
    return silences_from_levels(frame_levels(audio), min_len=min_len)
