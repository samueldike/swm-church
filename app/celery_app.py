from celery import Celery

from app.config import settings

celery_app = Celery(
    "swm",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Africa/Lagos",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # With acks_late, Redis hands a task out AGAIN if it is not done within the visibility
    # timeout (default 1h): long transcriptions and archive downloads would then run twice.
    broker_transport_options={"visibility_timeout": 12 * 3600},
    # Whisper is heavy (one ~3GB model per process): transcriptions go to their own queue,
    # served one at a time by the "transcriber" worker, so they never starve Telegram sends.
    task_routes={"app.tasks.transcribe_audio_task": {"queue": "transcription"}},
    beat_schedule={},
)
