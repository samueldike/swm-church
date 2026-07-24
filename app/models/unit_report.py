from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UnitReport(Base):
    __tablename__ = "unit_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"))
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE")
    )
    hod_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admins.id", ondelete="CASCADE")
    )
    report_month: Mapped[str | None] = mapped_column(String(20))
    introduction: Mapped[str] = mapped_column(Text)
    attendance: Mapped[dict | None] = mapped_column(JSON)
    attendance_note: Mapped[str | None] = mapped_column(Text)
    collections: Mapped[dict | None] = mapped_column(JSON)
    observations: Mapped[str | None] = mapped_column(Text)
    challenges: Mapped[str | None] = mapped_column(Text)
    recommendations: Mapped[str | None] = mapped_column(Text)
    projections: Mapped[str | None] = mapped_column(Text)
    resident_pastor_comment: Mapped[str | None] = mapped_column(Text)
    senior_pastor_comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    branch = relationship("Branch", back_populates="unit_reports")
    department = relationship("Department", back_populates="unit_reports")
    hod_admin = relationship("Admin", back_populates="unit_reports")
