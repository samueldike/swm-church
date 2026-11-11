from datetime import date, datetime

from sqlalchemy import Column, Date, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# A member can serve in several departments (choir, ushering, media...). Departments are shared by HQ and every
# branch; which church a member belongs to stays Member.branch_id. Rows go with the member or the department.
member_departments = Table(
    "member_departments",
    Base.metadata,
    Column("member_id", Integer, ForeignKey("members.id", ondelete="CASCADE"), primary_key=True),
    Column("department_id", Integer, ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"))
    first_name: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    # Optional place of residence (ISO codes, app/core/geo.py) — e.g. diaspora members.
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    region_code: Mapped[str | None] = mapped_column(String(10))
    city: Mapped[str | None] = mapped_column(String(120))
    gender: Mapped[str | None] = mapped_column(String(10))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    joined_at: Mapped[date | None] = mapped_column(Date)
    membership_status: Mapped[str] = mapped_column(String(20), default="active")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    branch = relationship("Branch", back_populates="members", lazy="selectin")
    departments = relationship("Department", secondary=member_departments, lazy="selectin",
                               order_by="Department.name", passive_deletes=True)

    @property
    def full_name(self) -> str:
        return f"{self.first_name or ''} {self.last_name or ''}".strip()
