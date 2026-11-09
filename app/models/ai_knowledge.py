from datetime import datetime
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AIKnowledge(Base):
    __tablename__ = "ai_knowledge"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(2000))
    is_active: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class ScentAIFeedback(Base):
    """A visitor's thumbs up / down on one ScentAI answer (no name, email or IP is kept)."""
    __tablename__ = "scentai_feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    rating: Mapped[str] = mapped_column(String(4))            # up | down
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
