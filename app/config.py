from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://swm_user:swm_password@postgres:5432/swm_db"
    REDIS_URL: str = "redis://redis:6379/0"
    SECRET_KEY: str = "change-me"
    APP_ENV: str = "development"
    APP_NAME: str = "Scent of Water Ministry"
    APP_URL: str = "http://localhost"

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_AUDIO_CHAT_ID: str = ""
    # Base URL of the Bot API. The public cloud API caps uploads at 50MB and
    # getFile downloads at 20MB. Point this at a self-hosted telegram-bot-api
    # server running in --local mode to lift both limits (see docker-compose).
    TELEGRAM_API_BASE: str = "https://api.telegram.org"
    # True when TELEGRAM_API_BASE is a --local server: uploads may be handed
    # over as filesystem paths, and getFile returns a path instead of a URL.
    TELEGRAM_LOCAL_API: bool = False
    # Upload ceiling enforced before we touch the network. 2000MB is the hard
    # limit of a --local Bot API server; the cloud API only allows 50.
    TELEGRAM_MAX_UPLOAD_MB: int = 2000

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

    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "Scent of Water Ministry"

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
    def telegram_api_base(self) -> str:
        return self.TELEGRAM_API_BASE.rstrip("/")

    @property
    def telegram_max_upload_bytes(self) -> int:
        return self.TELEGRAM_MAX_UPLOAD_MB * 1024 * 1024

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
