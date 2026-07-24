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

        model_size = os.getenv("WHISPER_MODEL", "large-v3")
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


def transcribe_audio(file_path: str | Path, language: str | None = None) -> str:
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    model = _get_model()

    kwargs = {
        "beam_size": 5,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
    }
    if language:
        kwargs["language"] = language

    segments, info = model.transcribe(str(file_path), **kwargs)

    logger.info("Detected language: %s (prob=%.2f)", info.language, info.language_probability)

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
