from datetime import date, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Sermon(Base):
    __tablename__ = "sermons"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    caption: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    preacher: Mapped[str | None] = mapped_column(String(255))
    audio_file_path: Mapped[str | None] = mapped_column(String(500))
    media_url: Mapped[str | None] = mapped_column(String(500))
    type: Mapped[str] = mapped_column(String(20), default="audio")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    original_file_name: Mapped[str | None] = mapped_column(String(500))
    original_file_size: Mapped[int | None] = mapped_column(Integer)
    telegram_file_id: Mapped[str | None] = mapped_column(String(512))
    telegram_message_id: Mapped[str | None] = mapped_column(String(255))
    platform_targets: Mapped[dict | None] = mapped_column(JSON)
    platform_links: Mapped[dict | None] = mapped_column(JSON)
    distribution_status: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    transcript: Mapped[str | None] = mapped_column(Text)
    transcription_status: Mapped[str] = mapped_column(String(20), default="none")
    transcription_started_at: Mapped[datetime | None] = mapped_column()
    transcription_completed_at: Mapped[datetime | None] = mapped_column()

    branch = relationship("Branch", back_populates="sermons")
    creator = relationship("Admin", back_populates="sermons")
