from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BranchReport(Base):
    """The branch admin's own monthly report to HQ.

    HQ does **not** read a branch's department (HOD) reports; those go to the branch's own admin.
    The branch admin compiles this report and it is the only branch report HQ sees.
    """

    __tablename__ = "branch_reports"
    __table_args__ = (UniqueConstraint("branch_id", "report_month", name="uq_branch_reports_branch_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    author_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    report_month: Mapped[str] = mapped_column(String(7))          # "YYYY-MM"
    summary: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict | None] = mapped_column(JSON)           # avg_attendance, first_timers, new_members, converts, baptised
    finance_note: Mapped[str | None] = mapped_column(Text)
    highlights: Mapped[str | None] = mapped_column(Text)
    challenges: Mapped[str | None] = mapped_column(Text)
    requests_to_hq: Mapped[str | None] = mapped_column(Text)
    projections: Mapped[str | None] = mapped_column(Text)
    rollup: Mapped[dict | None] = mapped_column(JSON)            # snapshot at submit: department reports submitted / expected
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | submitted | acknowledged
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    hq_comment: Mapped[str | None] = mapped_column(Text)
    hq_commented_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    hq_commented_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    branch = relationship("Branch", lazy="selectin")
    author = relationship("Admin", foreign_keys=[author_admin_id], lazy="selectin")
