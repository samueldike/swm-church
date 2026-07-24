from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class HarvestWorker(Base):
    __tablename__ = "harvest_workers"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    password_hash: Mapped[str] = mapped_column(String(255))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    tokens = relationship("HarvestWorkerToken", back_populates="worker", cascade="all, delete-orphan")
    contacts = relationship("HarvestContact", back_populates="worker", cascade="all, delete-orphan")
    reminders = relationship("HarvestReminder", back_populates="worker", cascade="all, delete-orphan")


class HarvestWorkerToken(Base):
    __tablename__ = "harvest_worker_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("harvest_workers.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )

    worker = relationship("HarvestWorker", back_populates="tokens")


class HarvestContact(Base):
    __tablename__ = "harvest_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("harvest_workers.id", ondelete="CASCADE"))
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    worker = relationship("HarvestWorker", back_populates="contacts")
    follow_ups = relationship("HarvestFollowUp", back_populates="contact", cascade="all, delete-orphan")
    reminders = relationship("HarvestReminder", back_populates="contact")


class HarvestFollowUp(Base):
    __tablename__ = "harvest_follow_ups"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("harvest_contacts.id", ondelete="CASCADE"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("harvest_workers.id", ondelete="CASCADE"))
    follow_type: Mapped[str] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )

    contact = relationship("HarvestContact", back_populates="follow_ups")
    worker = relationship("HarvestWorker")


class HarvestReminder(Base):
    __tablename__ = "harvest_reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("harvest_workers.id", ondelete="CASCADE"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("harvest_contacts.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_completed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    worker = relationship("HarvestWorker", back_populates="reminders")
    contact = relationship("HarvestContact", back_populates="reminders")
