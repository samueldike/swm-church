from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Giving(Base):
    __tablename__ = "givings"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    giving_type: Mapped[str] = mapped_column(String(50), default="tithe")
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    dedication: Mapped[str | None] = mapped_column(Text)
    payment_gateway: Mapped[str] = mapped_column(String(50), default="paystack")
    # Which branch the gift is for (NULL = HQ) and whose gateway/account took the money ("hq" | "branch").
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="SET NULL"), index=True)
    gateway_owner: Mapped[str] = mapped_column(String(10), default="hq")
    # "online" (gateway) | "cash" | "transfer" | "cheque" | "other" (recorded by hand)
    method: Mapped[str] = mapped_column(String(20), default="online")
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )
