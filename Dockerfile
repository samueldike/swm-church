# Deno: the JavaScript runtime yt-dlp needs to solve YouTube's player challenges (without it YouTube
# offers "only images", whatever cookies are given). Taken from the official image, pinned.
FROM denoland/deno:bin-2.9.8 AS deno

FROM python:3.12-slim

# MEDIA_ROOT is baked in so the app and worker always share the mounted media volume,
# even when a server's compose file or .env omits it (they once wrote to different dirs).
# TELEGRAM_BOT_API_CONF_DIR is where the app hands the telegram-bot-api container its
# api_id/api_hash (saved in Settings); nothing is written unless that volume is mounted.
# APP_ENV=production: secure session cookie, no /docs or /openapi.json, and the app refuses to
# start without a real SECRET_KEY. Local development runs uvicorn directly and stays "development".
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    MEDIA_ROOT=/app/media \
    TELEGRAM_BOT_API_CONF_DIR=/run/swm-telegram \
    TELEGRAM_ARCHIVE_DIR=/app/telegram-archive

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
        ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# constraints.txt pins every package (not just the direct ones) so a rebuild cannot pull a new major version.
COPY --from=deno /deno /usr/local/bin/deno

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# Video Clipper "only the senior pastor": the WeSpeaker ResNet34 voiceprint model (VoxCeleb2, CC-BY-4.0,
# github.com/wenet-e2e/wespeaker), fetched once at build and checked against its known checksum.
RUN python -c "import hashlib, urllib.request, os; os.makedirs('/opt/models', exist_ok=True); u='https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx'; p='/opt/models/wespeaker-resnet34-lm.onnx'; urllib.request.urlretrieve(u, p); h=hashlib.sha256(open(p,'rb').read()).hexdigest(); assert h=='7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068', h"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
