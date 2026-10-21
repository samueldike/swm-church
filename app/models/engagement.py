"""Outreach plumbing: what we emailed, event reminders, subscriber campaigns and privacy-friendly traffic counts."""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.utcnow()


class EmailLog(Base):
    """One row per email we tried to send: who, what kind, and whether the provider accepted it.
    The body is not stored (prayer follow-ups and receipts are personal)."""

    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    to_email: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(30), index=True, default="general")
    status: Mapped[str] = mapped_column(String(10), default="sent")          # sent | failed | skipped
    error: Mapped[str | None] = mapped_column(String(500))
    provider_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class EventReminder(Base):
    """A visitor asked to be reminded about an event by email (sent once, then removed after the event)."""

    __tablename__ = "event_reminders"
    __table_args__ = (UniqueConstraint("event_id", "email", name="uq_event_reminders_event_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime, index=True)            # naive UTC
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)   # pending | sending | sent | failed
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    event = relationship("Event", lazy="selectin")


class EmailCampaign(Base):
    """A message an administrator sends to the newsletter subscribers (a new message, a devotional day, or free text)."""

    __tablename__ = "email_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(255))
    preheader: Mapped[str | None] = mapped_column(String(255))
    body_text: Mapped[str] = mapped_column(Text, default="")                      # what the administrator wrote (plain text)
    body_html: Mapped[str] = mapped_column(Text)                                  # derived from body_text, sanitised
    button_label: Mapped[str | None] = mapped_column(String(60))
    button_url: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20), default="custom")             # custom | message | devotional
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)     # draft | sending | sent | cancelled
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[int] = mapped_column(Integer, default=0)                       # last subscriber id handled
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class AnalyticsCounter(Base):
    """Daily counters for the built-in analytics. No IP address, cookie or user agent is stored:
    ``kind`` is ``page`` (path), ``ref`` (referring host), ``device`` or ``visitor`` (an unrecoverable
    per-day hash used only to count distinct visitors)."""

    __tablename__ = "analytics_counters"
    __table_args__ = (UniqueConstraint("day", "kind", "key", name="uq_analytics_day_kind_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(10), index=True)
    key: Mapped[str] = mapped_column(String(300))
    count: Mapped[int] = mapped_column(Integer, default=0)
