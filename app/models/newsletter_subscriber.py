from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscribers"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)   # one-click unsubscribe
    source: Mapped[str | None] = mapped_column(String(30))                          # footer | visit | prayer | admin ...
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )
