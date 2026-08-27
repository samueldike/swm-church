from datetime import datetime

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Photo(Base):
    """Admin-managed imagery for the public site.

    One table serves every photo strip rather than a table per section;
    `collection` selects which strip a row belongs to (see COLLECTIONS).
    """

    __tablename__ = "photos"

    GALLERY = "gallery"
    OUTREACH = "outreach"
    COLLECTIONS = (GALLERY, OUTREACH)

    id: Mapped[int] = mapped_column(primary_key=True)
    collection: Mapped[str] = mapped_column(String(32), index=True, default=GALLERY)
    image_path: Mapped[str] = mapped_column(String(500))
    caption: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    @property
    def src(self) -> str:
        """Web path, tolerant of rows stored with or without a leading slash."""
        return self.image_path if self.image_path.startswith("/") else f"/{self.image_path}"
