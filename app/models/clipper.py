"""Video Clipper (Admin -> Video Clipper): long videos turned into ready-to-post shorts.

* A ``ClipProject`` is one source video (a link or an upload) owned by a church (``branch_id``). Its files live in
  ``settings.clipper_dir/<project id>/`` (outside the public media root): ``source.mp4``, ``audio.wav``,
  ``words.json`` (every spoken word with its timing), ``shorts/<short id>.mp4|.jpg|.srt``.
* ``status`` walks ``queued -> fetching -> transcribing -> composing -> rendering -> ready`` (or ``failed``);
  ``stage``/``progress`` describe the current step for the progress bar.
* A ``ClipShort`` is one finished short: ``plan`` is the edit (source pieces in playing order, see
  app/core/clipper/editorial.py), ``segments`` the exact cut after dead air was removed.
* The source video is deleted ``CLIPPER_KEEP_SOURCE_DAYS`` after the project finished (``source_deleted_at``);
  shorts stay, but can no longer be re-cut.
"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.utcnow()


class ClipProject(Base):
    __tablename__ = "clip_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    source_kind: Mapped[str] = mapped_column(String(20), default="url")      # url | upload
    source_url: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(String(255))
    settings: Mapped[dict | None] = mapped_column(JSON)                        # see app/core/clipper/options.py
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(200))
    progress: Mapped[float] = mapped_column(Float, default=0.0)               # 0..1 within the current status
    error: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    brief: Mapped[dict | None] = mapped_column(JSON)                           # niche, audience, central point, body ranges
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    shorts: Mapped[list["ClipShort"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ClipShort.position", lazy="selectin"
    )
    branch = relationship("Branch", lazy="selectin")

    @property
    def busy(self) -> bool:
        return self.status in ("queued", "fetching", "transcribing", "composing", "rendering")


class ClipShort(Base):
    __tablename__ = "clip_shorts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("clip_projects.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(String(20), default="ai")              # ai | manual
    title: Mapped[str] = mapped_column(String(255), default="")
    caption: Mapped[str | None] = mapped_column(Text)                          # post caption with hashtags
    value: Mapped[str | None] = mapped_column(Text)                            # what the viewer takes away
    score: Mapped[int | None] = mapped_column(Integer)
    plan: Mapped[list | None] = mapped_column(JSON)                            # [{start,end,text,role,cold_open?,spare?}]
    segments: Mapped[list | None] = mapped_column(JSON)                        # exact cut [[start,end],...] after trimming
    nudge_start: Mapped[float] = mapped_column(Float, default=0.0)             # seconds added (-) / removed (+) at the start
    nudge_end: Mapped[float] = mapped_column(Float, default=0.0)               # seconds added (+) / removed (-) at the end
    length: Mapped[float | None] = mapped_column(Float)
    format: Mapped[str] = mapped_column(String(20), default="vertical")        # vertical | square | landscape
    quality: Mapped[int] = mapped_column(Integer, default=1080)
    framing: Mapped[str] = mapped_column(String(20), default="speaker")        # speaker | centre | fit
    caption_style: Mapped[str] = mapped_column(String(20), default="bold")     # bold | clean | none
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued | rendering | ready | failed
    error: Mapped[str | None] = mapped_column(Text)
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    project: Mapped[ClipProject] = relationship(back_populates="shorts")
