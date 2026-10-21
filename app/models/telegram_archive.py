from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TelegramArchiveItem(Base):
    """One audio post found in the HQ Telegram channel by the archive (Admin → Telegram archive).

    Found by reading the channel as a signed-in Telegram user, which reaches posts from before the
    bot was added. ``status``: found → saved (original file in TELEGRAM_ARCHIVE_DIR, never public)
    | failed; ``on_website`` when the site already has it (the bot posted it). ``sermon_id`` is the
    draft message created from it.
    """

    __tablename__ = "telegram_archive_items"
    __table_args__ = (UniqueConstraint("channel_id", "message_id", name="uq_telegram_archive_post"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    channel_title: Mapped[str | None] = mapped_column(String(255))
    message_id: Mapped[int] = mapped_column(Integer)
    posted_at: Mapped[datetime] = mapped_column(DateTime)                      # naive UTC
    caption: Mapped[str | None] = mapped_column(Text)
    audio_title: Mapped[str | None] = mapped_column(String(255))
    performer: Mapped[str | None] = mapped_column(String(255))
    file_name: Mapped[str | None] = mapped_column(String(500))
    mime: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    duration: Mapped[int | None] = mapped_column(Integer)                       # seconds
    status: Mapped[str] = mapped_column(String(20), default="found", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    stored_path: Mapped[str | None] = mapped_column(String(500))               # relative to the archive dir
    sha256: Mapped[str | None] = mapped_column(String(64))
    sermon_id: Mapped[int | None] = mapped_column(ForeignKey("sermons.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())
