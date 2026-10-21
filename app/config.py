from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://swm_user:swm_password@postgres:5432/swm_db"
    REDIS_URL: str = "redis://redis:6379/0"
    SECRET_KEY: str = "change-me"
    APP_ENV: str = "development"
    APP_NAME: str = "Scent of Water Ministry"
    APP_URL: str = "http://localhost"

    # Telegram keys (bot token, channel, api_id/api_hash) and the large-file switch are
    # managed in Admin → Settings → Audio distribution, never here.
    # Public cloud Bot API: uploads capped at 50MB, getFile downloads at 20MB.
    TELEGRAM_API_BASE: str = "https://api.telegram.org"
    # The self-hosted telegram-bot-api (--local, 2000MB) used once "large files" is on.
    TELEGRAM_LOCAL_API_BASE: str = "http://telegram-bot-api:8081"
    # Shared with the telegram-bot-api container, which reads the api_id/api_hash
    # saved in Settings from here. Empty outside Docker: nothing is written.
    TELEGRAM_BOT_API_CONF_DIR: str = ""
    # Website upload ceiling, and the hard limit of a --local Bot API server.
    TELEGRAM_MAX_UPLOAD_MB: int = 2000
    # Telegram archive (Admin → Telegram archive): original channel audio is kept here,
    # deliberately OUTSIDE the media root, which nginx and the app serve publicly.
    # Empty = a "telegram-archive" folder next to the media root.
    TELEGRAM_ARCHIVE_DIR: str = ""

    # Absolute path the app writes uploads to. Must match the media volume
    # mount in every container that needs to read them (worker, nginx,
    # telegram-bot-api), since large audio is handed over by path, not bytes.
    MEDIA_ROOT: str = ""

    # Books Pub AI drafting. The key and model are managed in Admin → Settings → AI
    # drafting (encrypted in the database); these are only a fallback when nothing is saved there.
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_API_BASE: str = "https://api.anthropic.com"
    ANTHROPIC_MODEL: str = "claude-sonnet-5"
    # How many subtopics are written in parallel by one job.
    PUBLICATION_AI_CONCURRENCY: int = 3

    PAYSTACK_PUBLIC_KEY: str = ""
    PAYSTACK_SECRET_KEY: str = ""

    FLUTTERWAVE_PUBLIC_KEY: str = ""
    FLUTTERWAVE_SECRET_KEY: str = ""
    FLUTTERWAVE_ENCRYPTION_KEY: str = ""


    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def media_root(self) -> str:
        """Absolute media directory. Defaults to ``app/media`` next to this file."""
        import os

        if self.MEDIA_ROOT:
            return self.MEDIA_ROOT
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")

    @property
    def telegram_archive_dir(self) -> str:
        """Private archive directory: never under the public media root."""
        import os

        if self.TELEGRAM_ARCHIVE_DIR:
            return self.TELEGRAM_ARCHIVE_DIR
        return os.path.join(os.path.dirname(os.path.normpath(self.media_root)), "telegram-archive")

    @property
    def telegram_api_base(self) -> str:
        return self.TELEGRAM_API_BASE.rstrip("/")

    @property
    def telegram_max_upload_bytes(self) -> int:
        return self.TELEGRAM_MAX_UPLOAD_MB * 1024 * 1024

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # retired keys left in an old .env must not stop the app starting


settings = Settings()
