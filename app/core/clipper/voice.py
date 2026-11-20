"""Who is speaking: voiceprints for "only clip the senior pastor".

A voiceprint (speaker embedding) is a 256-number summary of a voice made by WeSpeaker's ResNet34 model
(github.com/wenet-e2e/wespeaker, trained on VoxCeleb2, model licence CC-BY-4.0; ~0.9 % equal error rate on
VoxCeleb1-O). It captures the physical sound of a voice (vocal tract, timbre), not the preaching style, so a pastor
who has learned the senior pastor's phrasing and rhythm still has his own voiceprint.

The model runs on onnxruntime (already in the image for Whisper's VAD) from Kaldi-style log-mel filterbanks computed
here in numpy (no torch). Pure functions are tested in tests/test_clipper.py; ``embed`` needs the model file
(``MODEL``, fetched by the Dockerfile with a checksum).
"""
from __future__ import annotations

import logging
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MODEL = Path("/opt/models/wespeaker-resnet34-lm.onnx")
RATE = 16000
WINDOW = 8.0            # seconds of speech per voiceprint sample


def fbank(x: np.ndarray, rate: int = RATE, bins: int = 80) -> np.ndarray:
    """Kaldi-compatible log mel filterbank (25 ms Povey windows every 10 ms, pre-emphasis 0.97, DC removed,
    20 Hz - Nyquist), as WeSpeaker was trained on; then the mean over time is subtracted (pure)."""
    x = np.asarray(x, dtype=np.float32) * 32768.0          # Kaldi works on 16-bit sample values
    flen, shift, nfft = int(0.025 * rate), int(0.010 * rate), 512
    if len(x) < flen:
        return np.zeros((0, bins), dtype=np.float32)
    n = 1 + (len(x) - flen) // shift
    idx = np.arange(flen)[None, :] + shift * np.arange(n)[:, None]
    frames = x[idx].astype(np.float64)
    frames -= frames.mean(axis=1, keepdims=True)
    frames[:, 1:] -= 0.97 * frames[:, :-1]
    frames[:, 0] -= 0.97 * frames[:, 0]
    frames *= (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(flen) / (flen - 1))) ** 0.85
    power = np.abs(np.fft.rfft(frames, nfft)) ** 2

    def mel(f):
        return 1127.0 * np.log(1 + f / 700.0)

    lo, hi = mel(20.0), mel(rate / 2)
    edges = np.linspace(lo, hi, bins + 2)
    freqs = mel(np.arange(nfft // 2 + 1) * rate / nfft)
    fb = np.zeros((bins, nfft // 2 + 1))
    for m in range(bins):
        left, centre, right = edges[m], edges[m + 1], edges[m + 2]
        up = (freqs - left) / (centre - left)
        down = (right - freqs) / (right - centre)
        fb[m] = np.maximum(0, np.minimum(up, down))
    feats = np.log(np.maximum(power @ fb.T, np.finfo(np.float32).eps))
    feats -= feats.mean(axis=0, keepdims=True)
    return feats.astype(np.float32)


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort

    if not MODEL.exists():
        raise FileNotFoundError(f"The voice model is missing ({MODEL}). Rebuild the image.")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    return ort.InferenceSession(str(MODEL), opts, providers=["CPUExecutionProvider"])


def embed(samples: np.ndarray) -> np.ndarray:
    """A unit-length voiceprint of a stretch of 16 kHz mono speech."""
    sess = _session()
    feats = fbank(samples)[None, :, :]
    out = sess.run(None, {sess.get_inputs()[0].name: feats})[0][0]
    return (out / (np.linalg.norm(out) + 1e-9)).astype(np.float32)


def read(audio: Path, start: float, end: float) -> np.ndarray:
    """Samples of a 16 kHz mono 16-bit WAV between two times, as floats in -1..1."""
    with wave.open(str(audio), "rb") as w:
        rate = w.getframerate()
        w.setpos(max(0, min(w.getnframes(), int(start * rate))))
        raw = w.readframes(max(0, int((end - start) * rate)))
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))
