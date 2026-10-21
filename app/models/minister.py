from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Minister(Base):
    """Bio-data record of a minister serving at HQ or in a branch.

    A minister is a *person on record*; they only get a console login when appointed a department
    head (HOD) or the branch's Resident Pastor, at which point ``admin_id`` links the account.
    """

    __tablename__ = "ministers"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"), unique=True)
    title: Mapped[str | None] = mapped_column(String(60))            # Pastor, Rev., Evangelist, Deacon, ...
    full_name: Mapped[str] = mapped_column(String(255))
    gender: Mapped[str | None] = mapped_column(String(20))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    marital_status: Mapped[str | None] = mapped_column(String(30))
    spouse_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(500))
    state_of_origin: Mapped[str | None] = mapped_column(String(100))
    education: Mapped[str | None] = mapped_column(String(500))
    ministry_rank: Mapped[str | None] = mapped_column(String(120))   # e.g. Associate Pastor, Minister-in-training
    date_joined: Mapped[date | None] = mapped_column(Date)
    ordination_date: Mapped[date | None] = mapped_column(Date)
    photo: Mapped[str | None] = mapped_column(String(500))
    bio: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | retired | on_leave | left
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    branch = relationship("Branch", lazy="selectin")
    account = relationship("Admin", foreign_keys=[admin_id], lazy="selectin")
