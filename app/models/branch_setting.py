from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BranchSetting(Base):
    """Per-branch configuration (Telegram channel, bank accounts, gateway keys, HQ share).

    Values for keys in ``app.core.branch_settings.SECRET_KEYS`` are stored encrypted, exactly
    like the site-wide secrets in ``site_settings``.
    """

    __tablename__ = "branch_settings"
    __table_args__ = (UniqueConstraint("branch_id", "key", name="uq_branch_settings_branch_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())


class Remittance(Base):
    """Money moved between a branch and HQ (recorded by finance)."""

    __tablename__ = "remittances"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(20))  # "to_hq" (branch paid HQ) | "to_branch" (HQ paid branch)
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="NGN", server_default="NGN")   # the platform (HQ) currency
    paid_on: Mapped[date] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.utcnow())

    branch = relationship("Branch", lazy="selectin")
