from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

admin_branches = Table(
    "admin_branches",
    Base.metadata,
    Column("admin_id", Integer, ForeignKey("admins.id", ondelete="CASCADE"), primary_key=True),
    Column("branch_id", Integer, ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True),
)


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="admin")
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    branch = relationship("Branch", back_populates="admins", foreign_keys=[branch_id])
    department = relationship("Department", foreign_keys=[department_id])
    departments = relationship("Department", back_populates="creator", foreign_keys="Department.created_by_admin_id")
    sermons = relationship("Sermon", back_populates="creator")
    unit_reports = relationship("UnitReport", back_populates="hod_admin")
    branch_invitations_sent = relationship("BranchAdminInvitation", back_populates="invited_by")
    hod_invitations_sent = relationship("HodInvitation", back_populates="invited_by")

    @property
    def name(self):
        return self.full_name


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(255))
    target_type: Mapped[str | None] = mapped_column(String(100))
    target_id: Mapped[int | None] = mapped_column(Integer)
    extra_data: Mapped[dict | None] = mapped_column("metadata", JSON)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )

    assigned_to = relationship("Admin", foreign_keys=[assigned_to_id])
