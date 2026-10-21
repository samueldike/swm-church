from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Announcement(Base):
    """A notice from HQ to its branches (directives, programme dates, resources).

    Read-only for branches: only the HQ Administrator writes them. ``audience`` is ``all`` (every branch)
    or ``selected`` (only the branches in ``branch_ids``). Nothing is emailed; branches see it in the console.
    """

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(10), default="all")          # all | selected
    branch_ids: Mapped[list | None] = mapped_column(JSON)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    author = relationship("Admin", foreign_keys=[created_by], lazy="selectin")
