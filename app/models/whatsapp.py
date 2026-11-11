"""WhatsApp posts that wait for their time: a message to chats/groups/channels, or a Status.

Live chatting is not stored here: chats and messages are read straight from the connected WhatsApp account
through the gateway (whatsapp-gateway/), exactly as WhatsApp itself holds them.
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WhatsAppPost(Base):
    __tablename__ = "whatsapp_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(10), default="message")      # message | status
    # message: [{"jid": "...@g.us", "name": "Choir"}, ...]; status: []
    targets: Mapped[list | None] = mapped_column(JSON)
    text: Mapped[str | None] = mapped_column(Text)
    media_path: Mapped[str | None] = mapped_column(String(500))          # relative to MEDIA_ROOT
    media_kind: Mapped[str | None] = mapped_column(String(12))           # image | video | audio | document
    media_name: Mapped[str | None] = mapped_column(String(255))
    send_at: Mapped[datetime] = mapped_column(DateTime, index=True)      # UTC
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending|sending|sent|partial|failed|cancelled
    result: Mapped[dict | None] = mapped_column(JSON)                   # per-target outcome
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_at: Mapped[datetime | None] = mapped_column(DateTime)           # drip-sending: next recipients not before (UTC)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)      # a worker is sending this post
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())

    branch = relationship("Branch", lazy="selectin")
    author = relationship("Admin", lazy="selectin", foreign_keys=[created_by])
