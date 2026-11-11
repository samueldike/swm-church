"""Document library rules: allowed files, private storage, offices, and who may see / edit / manage a document.

Kept in one place so the list, the preview, the download and every action ask the same questions.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime

from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.access import BRANCH_ADMIN, HOD, HQ_FINANCE, HQ_MEDIA, HQ_PASTORAL, RESIDENT_PASTOR, SUPER, normalize_role

VISIBILITY = {
    "private": "Only me and people I share with",
    "church": "Everyone at my church",
    "everyone": "Everyone in every church",
}
ACCESS = {"view": "Can view", "edit": "Can edit"}

# extension -> (kind, mime). Anything not here is refused (no programs, scripts or web pages).
TYPES: dict[str, tuple[str, str]] = {
    "pdf": ("pdf", "application/pdf"),
    "doc": ("word", "application/msword"),
    "docx": ("word", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "odt": ("word", "application/vnd.oasis.opendocument.text"),
    "rtf": ("word", "application/rtf"),
    "txt": ("text", "text/plain"),
    "md": ("text", "text/plain"),
    "csv": ("sheet", "text/csv"),
    "xls": ("sheet", "application/vnd.ms-excel"),
    "xlsx": ("sheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "ods": ("sheet", "application/vnd.oasis.opendocument.spreadsheet"),
    "ppt": ("slides", "application/vnd.ms-powerpoint"),
    "pptx": ("slides", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "odp": ("slides", "application/vnd.oasis.opendocument.presentation"),
    "jpg": ("image", "image/jpeg"), "jpeg": ("image", "image/jpeg"), "png": ("image", "image/png"),
    "gif": ("image", "image/gif"), "webp": ("image", "image/webp"), "heic": ("image", "image/heic"),
    "mp3": ("audio", "audio/mpeg"), "m4a": ("audio", "audio/mp4"), "wav": ("audio", "audio/wav"), "ogg": ("audio", "audio/ogg"),
    "mp4": ("video", "video/mp4"), "mov": ("video", "video/quicktime"),
    "zip": ("archive", "application/zip"),
}
KINDS = {"pdf": "PDF", "word": "Documents", "sheet": "Spreadsheets", "slides": "Presentations", "image": "Images",
         "audio": "Audio", "video": "Video", "text": "Text", "archive": "Archives"}
# Safe to open in the browser (never HTML/SVG): everything else is downloaded.
INLINE = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "txt", "md", "csv", "mp3", "m4a", "wav", "ogg", "mp4"}

HQ_ROLE_OFFICES = {SUPER: "HQ Administrator", HQ_MEDIA: "HQ Media", HQ_FINANCE: "HQ Finance", HQ_PASTORAL: "HQ Pastoral"}


def max_bytes() -> int:
    return settings.DOCUMENTS_MAX_UPLOAD_MB * 1024 * 1024


def ext_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower().lstrip(".")[:12]


def kind_of(ext: str) -> str:
    return TYPES.get(ext, ("other", ""))[0]


def clean_name(filename: str) -> str:
    base = os.path.basename((filename or "file").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\"<>|*?:]", "", base).strip(" .") or "file"
    return base[:255]


def abspath(stored: str) -> str:
    """The file on disk; refuses anything that would leave the documents directory."""
    root = os.path.realpath(settings.documents_dir)
    full = os.path.realpath(os.path.join(root, stored))
    if not full.startswith(root + os.sep):
        raise ValueError("bad document path")
    return full


async def save_upload(upload) -> tuple[str, str, str, str, int]:
    """Store an uploaded file privately. Returns (stored_path, clean filename, ext, mime, size). Raises ValueError."""
    name = clean_name(upload.filename)
    ext = ext_of(name)
    if ext not in TYPES:
        raise ValueError(f"“{name}” is not an allowed type. Allowed: PDF, Word, Excel, PowerPoint, text, CSV, images, audio, video and ZIP.")
    now = datetime.utcnow()
    rel = f"{now:%Y}/{now:%m}/{uuid.uuid4().hex}.{ext}"
    full = abspath(rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    size, limit = 0, max_bytes()
    try:
        with open(full, "wb") as fh:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError(f"“{name}” is larger than {settings.DOCUMENTS_MAX_UPLOAD_MB} MB.")
                fh.write(chunk)
    except ValueError:
        os.remove(full)
        raise
    if size == 0:
        os.remove(full)
        raise ValueError(f"“{name}” is empty.")
    return rel, name, ext, TYPES[ext][1], size


def remove_file(stored: str) -> None:
    try:
        os.remove(abspath(stored))
    except (OSError, ValueError):
        pass


# ─── offices and churches ───
def church_of(admin, hq_id: int) -> int:
    """The church whose library a console user works in (HQ staff and accounts without a branch: HQ)."""
    role = normalize_role(admin.role)
    if role in HQ_ROLE_OFFICES or not admin.branch_id:
        return hq_id
    return admin.branch_id


def targets_of(admin, hq_id: int) -> set[str]:
    """Everything a share or a "Send" can be addressed to that reaches this person."""
    role = normalize_role(admin.role)
    out = {f"user:{admin.id}"}
    if role in HQ_ROLE_OFFICES:
        out.add(f"role:{role}")
    church = church_of(admin, hq_id)
    if role in (BRANCH_ADMIN, RESIDENT_PASTOR) or role == SUPER:
        out.add(f"office:{church}")                   # a church office: its admin and Resident Pastor (HQ: the HQ Administrator)
    if role == HOD:
        out.add(f"hods:{admin.branch_id or hq_id}")
    return out


def target_ok(target: str) -> bool:
    return bool(re.fullmatch(r"(user|office|hods):\d{1,9}|role:(super_admin|hq_media|hq_finance|hq_pastoral)", target or ""))


async def office_options(db: AsyncSession) -> list[dict]:
    """Every office a file can be shared with or sent to."""
    from app.models.branch import Branch

    out = [{"target": f"role:{r}", "label": label, "kind": "office"} for r, label in HQ_ROLE_OFFICES.items()]
    for b in (await db.execute(select(Branch).where(Branch.is_active == True).order_by(Branch.is_hq.desc(), Branch.name))).scalars():  # noqa: E712
        church = "HQ" if b.is_hq else b.name
        out.append({"target": f"office:{b.id}", "label": f"{church} office", "kind": "office"})
        out.append({"target": f"hods:{b.id}", "label": f"{church} heads of department", "kind": "office"})
    return out


async def people_options(db: AsyncSession) -> list[dict]:
    from app.models.admin import Admin

    rows = (await db.execute(select(Admin).where(Admin.is_active == True).order_by(Admin.full_name))).scalars().all()  # noqa: E712
    return [{"target": f"user:{a.id}", "label": a.full_name, "sub": a.email, "kind": "person"} for a in rows]


async def labels_for(db: AsyncSession, targets: set[str]) -> dict[str, str]:
    from app.models.admin import Admin
    from app.models.branch import Branch

    out: dict[str, str] = {}
    users = [int(t[5:]) for t in targets if t.startswith("user:")]
    if users:
        for a in (await db.execute(select(Admin).where(Admin.id.in_(users)))).scalars():
            out[f"user:{a.id}"] = a.full_name
    churches = {int(t.split(":")[1]) for t in targets if t.startswith(("office:", "hods:"))}
    names = {}
    if churches:
        for b in (await db.execute(select(Branch).where(Branch.id.in_(churches)))).scalars():
            names[b.id] = "HQ" if b.is_hq else b.name
    for t in targets:
        if t.startswith("role:"):
            out[t] = HQ_ROLE_OFFICES.get(t[5:], t)
        elif t.startswith("office:"):
            out[t] = f"{names.get(int(t[7:]), 'Church')} office"
        elif t.startswith("hods:"):
            out[t] = f"{names.get(int(t[5:]), 'Church')} heads of department"
        out.setdefault(t, "Someone")
    return out


async def members_of(db: AsyncSession, target: str, hq_id: int) -> list:
    """Active console users a target reaches (for email notices)."""
    from app.models.admin import Admin

    kind, _, key = target.partition(":")
    q = select(Admin).where(Admin.is_active == True)  # noqa: E712
    if kind == "user":
        q = q.where(Admin.id == int(key))
    elif kind == "role":
        q = q.where(Admin.role == key)
    elif kind == "office":
        church = int(key)
        if church == hq_id:
            q = q.where(or_(Admin.role == SUPER, and_(Admin.role == RESIDENT_PASTOR, Admin.branch_id == church)))
        else:
            q = q.where(Admin.role.in_((BRANCH_ADMIN, RESIDENT_PASTOR, "admin", "branch_super_admin")), Admin.branch_id == church)
    elif kind == "hods":
        q = q.where(Admin.role == HOD, Admin.branch_id == int(key))
    else:
        return []
    return list((await db.execute(q)).scalars().all())


# ─── permissions ───
def visible_condition(admin, hq_id: int):
    """SQL condition: documents this person may see (not counting the Trash)."""
    from app.models.document import Document, DocumentShare

    shared = select(DocumentShare.document_id).where(DocumentShare.target.in_(targets_of(admin, hq_id)))
    cond = or_(
        Document.owner_id == admin.id,
        Document.visibility == "everyone",
        and_(Document.visibility == "church", Document.branch_id == church_of(admin, hq_id)),
        Document.id.in_(shared),
        (Document.visibility != "private") if normalize_role(admin.role) == SUPER else false(),
    )
    return cond


async def access_of(db: AsyncSession, admin, hq_id: int, doc) -> str | None:
    """None (no access), "view", "edit" or "manage" (owner / HQ Administrator: sharing, visibility, delete)."""
    from app.models.document import DocumentShare

    if doc.owner_id == admin.id:
        return "manage"
    is_super = normalize_role(admin.role) == SUPER
    if is_super and doc.visibility != "private":
        return "manage"
    shares = (await db.execute(select(DocumentShare.access).where(DocumentShare.document_id == doc.id,
                                                                  DocumentShare.target.in_(targets_of(admin, hq_id))))).scalars().all()
    if "edit" in shares:
        return "edit"
    if shares or doc.visibility == "everyone" or (doc.visibility == "church" and doc.branch_id == church_of(admin, hq_id)):
        return "view"
    return None


def human_size(n: int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
