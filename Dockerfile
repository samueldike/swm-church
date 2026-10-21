FROM python:3.12-slim

# MEDIA_ROOT is baked in so the app and worker always share the mounted media volume,
# even when a server's compose file or .env omits it (they once wrote to different dirs).
# TELEGRAM_BOT_API_CONF_DIR is where the app hands the telegram-bot-api container its
# api_id/api_hash (saved in Settings); nothing is written unless that volume is mounted.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ROOT=/app/media \
    TELEGRAM_BOT_API_CONF_DIR=/run/swm-telegram \
    TELEGRAM_ARCHIVE_DIR=/app/telegram-archive

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
