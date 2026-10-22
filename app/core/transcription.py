from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        # large-v3-turbo + greedy decoding: measured on the production VPS (6 shared cores) at
        # 1.2-1.3x faster than real time; large-v3 with beam 5 took 1.5x-10x LONGER than the audio.
        model_size = os.getenv("WHISPER_MODEL", "large-v3-turbo")
        compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        cpu_threads = int(os.getenv("WHISPER_CPU_THREADS", "4"))

        logger.info("Loading Whisper model %s (compute=%s, threads=%d)", model_size, compute_type, cpu_threads)
        _model = WhisperModel(
            model_size,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=cpu_threads,
        )
        logger.info("Whisper model loaded successfully")
    return _model


def transcribe_audio(file_path: str | Path, language: str | None = None, hotwords: str | None = None) -> str:
    """Transcribe one file. ``hotwords``: names to spell right (ministry, programmes, speaker)."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    model = _get_model()

    kwargs = {
        # Greedy decoding, and no conditioning on the previous window: with beam 5 + conditioning,
        # unclear passages (music, singing) were re-decoded at up to six temperatures, which is how
        # a 5-minute message once took 52 minutes.
        "beam_size": int(os.getenv("WHISPER_BEAM_SIZE", "1")),
        "condition_on_previous_text": False,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
    }
    language = language or os.getenv("WHISPER_LANGUAGE", "en") or None   # "" = auto-detect
    if language:
        kwargs["language"] = language
    if hotwords:
        kwargs["hotwords"] = hotwords

    segments, info = model.transcribe(str(file_path), **kwargs)

    logger.info("Transcribing %.0fs of audio (language %s)", info.duration, info.language)

    paragraphs: list[str] = []
    current_paragraph: list[str] = []
    word_count = 0

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue

        current_paragraph.append(text)
        word_count += len(text.split())

        if word_count >= 80 or text.endswith((".", "!", "?")):
            paragraphs.append(" ".join(current_paragraph))
            current_paragraph = []
            word_count = 0

    if current_paragraph:
        paragraphs.append(" ".join(current_paragraph))

    return "\n\n".join(paragraphs)
