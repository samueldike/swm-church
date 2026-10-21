from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    # Exactly one branch is HQ (the General Overseer's church). Only HQ-owned content reaches the public website.
    is_hq: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    events = relationship("Event", back_populates="branch")
    sermons = relationship("Sermon", back_populates="branch")
    members = relationship("Member", back_populates="branch")
    contact_inquiries = relationship("ContactInquiry", back_populates="branch")
    unit_reports = relationship("UnitReport", back_populates="branch")
    admin_invitations = relationship("BranchAdminInvitation", back_populates="branch")
    hod_invitations = relationship("HodInvitation", back_populates="branch")
    admins = relationship("Admin", back_populates="branch", foreign_keys="Admin.branch_id")
    harvest_workers = relationship("HarvestWorker", back_populates="branch")
    harvest_teams = relationship("HarvestTeam", back_populates="branch")
