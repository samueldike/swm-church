from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.utcnow()


class Publication(Base):
    """A Books Pub project: a daily devotional or a book built from sermon transcripts."""

    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # "devotionals" | "books"
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    author_name: Mapped[str | None] = mapped_column(String(255))

    # Devotional duration. `duration_type`: year | half | quarter | month | custom.
    duration_type: Mapped[str | None] = mapped_column(String(20))
    year: Mapped[int | None] = mapped_column(Integer)
    period_index: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    # The Word-like editor's document (sanitised HTML). Null until first built.
    document_html: Mapped[str | None] = mapped_column(Text)
    document_built_at: Mapped[datetime | None] = mapped_column(DateTime)
    document_saved_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    sources = relationship(
        "PublicationSource",
        back_populates="publication",
        cascade="all, delete-orphan",
        order_by="PublicationSource.position",
    )

    @property
    def is_devotional(self) -> bool:
        return self.kind == "devotionals"

    @property
    def total_days(self) -> int | None:
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return None


class PublicationSource(Base):
    """One message included in a project. For a devotional it is a "main topic"; for a book, a chapter."""

    __tablename__ = "publication_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"), index=True)
    sermon_id: Mapped[int] = mapped_column(ForeignKey("sermons.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    main_title: Mapped[str | None] = mapped_column(String(255))
    # none | queued | processing | done | failed
    topics_status: Mapped[str] = mapped_column(String(20), default="none")
    topics_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    publication = relationship("Publication", back_populates="sources")
    sermon = relationship("Sermon")
    subtopics = relationship(
        "PublicationSubtopic",
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="PublicationSubtopic.position",
    )


class PublicationSubtopic(Base):
    """One day of a devotional, or one section of a book chapter."""

    __tablename__ = "publication_subtopics"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("publication_sources.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(255))
    points: Mapped[list | None] = mapped_column(JSON)

    scripture: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)  # paragraphs separated by a blank line
    action_plan: Mapped[str | None] = mapped_column(Text)
    prayer_point: Mapped[str | None] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)

    # none | queued | processing | done | failed
    content_status: Mapped[str] = mapped_column(String(20), default="none")
    content_error: Mapped[str | None] = mapped_column(Text)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    source = relationship("PublicationSource", back_populates="subtopics")

    @property
    def has_content(self) -> bool:
        return bool((self.body or "").strip())
