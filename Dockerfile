FROM python:3.12-slim

# MEDIA_ROOT is baked in so the app and worker always share the mounted media volume,
# even when a server's compose file or .env omits it (they once wrote to different dirs).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ROOT=/app/media

WORKDIR /app

# ffmpeg: compresses audio that is over Telegram's upload cap (see telegram.shrink_audio).
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
