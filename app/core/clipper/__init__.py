"""Video Clipper: turn a long video (a sermon, a teaching, a podcast) into ready-to-post shorts.

Pipeline (each step is a Celery task in app/tasks.py; whisper runs on the "transcription" queue so it shares
the loaded model with sermon transcription, everything else on the single-slot "clipper" queue):

1. fetch      media.py      download the link (yt-dlp) or take the upload; probe it; extract 16 kHz audio
2. transcribe speech.py     every word with its timing (faster-whisper, word timestamps); silence map
3. compose    editorial.py  three AI calls: where the real content is, build the shorts, review them
4. render     cut.py        exact cut on word boundaries with dead air removed
              reframe.py    a virtual camera that follows the speaker (YuNet face detection)
              captions.py   word-by-word captions (ASS, burned in) + .srt
              render.py     ffmpeg

Access: app/core/clipper/options.py:allowed. Files: settings.clipper_dir (private, never under MEDIA_ROOT).
"""
