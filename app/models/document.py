"""Document library (Admin -> Documents): the church offices' shared, permissioned file archive.

* A ``Document`` belongs to a church library (``branch_id``) and optionally a ``DocumentFolder`` of that library.
  Its file lives in the private documents directory (``settings.documents_dir``), never under the public media
  root, and is only served after ``app.core.documents.can_view``.
* ``visibility``: ``private`` (owner + explicit shares), ``church`` (every console user of the owner's church),
  ``everyone`` (every console user of every church). The HQ Administrator sees everything except ``private``.
* ``DocumentShare``: explicit access for one person (``user:<admin id>``) or an office (``role:hq_media``,
  ``office:<branch id>``, ``hods:<branch id>``) with ``view`` or ``edit``.
* ``DocumentDelivery`` is a "Send": the file arrives in the recipient's Received box with a note; per-person read
  state is ``DocumentRead`` (an office delivery is read separately by each member of that office).
* Every upload of new content is a ``DocumentVersion``; the document points at the newest one.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DocumentFolder(Base):
    __tablename__ = "document_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("document_folders.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    color: Mapped[str | None] = mapped_column(String(20))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("document_folders.id", ondelete="SET NULL"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(String(500))            # comma-separated
    visibility: Mapped[str] = mapped_column(String(10), default="private", index=True)   # private | church | everyone
    # newest version, copied here for fast listing
    filename: Mapped[str] = mapped_column(String(255))
    ext: Mapped[str] = mapped_column(String(12), default="")
    mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    stored_path: Mapped[str] = mapped_column(String(500))           # relative to settings.documents_dir
    version: Mapped[int] = mapped_column(Integer, default=1)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    owner = relationship("Admin", lazy="selectin", foreign_keys=[owner_id])
    branch = relationship("Branch", lazy="selectin")
    folder = relationship("DocumentFolder", lazy="selectin")


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    stored_path: Mapped[str] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(String(300))
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())

    uploader = relationship("Admin", lazy="selectin", foreign_keys=[uploaded_by])


class DocumentShare(Base):
    __tablename__ = "document_shares"
    __table_args__ = (UniqueConstraint("document_id", "target", name="uq_document_share_target"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    target: Mapped[str] = mapped_column(String(40), index=True)     # user:<id> | role:<role> | office:<branch id> | hods:<branch id>
    access: Mapped[str] = mapped_column(String(6), default="view")  # view | edit
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())


class DocumentDelivery(Base):
    __tablename__ = "document_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"), index=True)
    target: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow(), index=True)

    sender = relationship("Admin", lazy="selectin", foreign_keys=[sender_id])
    document = relationship("Document", lazy="selectin")


class DocumentRead(Base):
    __tablename__ = "document_reads"

    delivery_id: Mapped[int] = mapped_column(ForeignKey("document_deliveries.id", ondelete="CASCADE"), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())


class DocumentStar(Base):
    __tablename__ = "document_stars"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), primary_key=True)
