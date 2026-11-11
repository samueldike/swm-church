"""Document library: who sees which file, offices, and what may be uploaded (app/core/documents.py)."""
import asyncio
import io
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import documents as docs

HQ, ABUJA = 1, 2


def _run(fn):
    async def go():
        import app.models  # noqa: F401
        from app.database import Base

        eng = create_async_engine("sqlite+aiosqlite://")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with Session() as db:
                return await fn(db)
        finally:
            await eng.dispose()
    return asyncio.run(go())


PEOPLE = {
    "owner": NS(id=10, role="branch_admin", branch_id=ABUJA),
    "abuja_hod": NS(id=11, role="hod", branch_id=ABUJA),
    "hq_finance": NS(id=12, role="hq_finance", branch_id=None),
    "super": NS(id=13, role="super_admin", branch_id=HQ),
    "abuja_rp": NS(id=14, role="resident_pastor", branch_id=ABUJA),
    "other_branch": NS(id=15, role="branch_admin", branch_id=3),
}


def test_offices_a_person_belongs_to():
    assert docs.targets_of(PEOPLE["owner"], HQ) == {"user:10", f"office:{ABUJA}"}
    assert docs.targets_of(PEOPLE["abuja_hod"], HQ) == {"user:11", f"hods:{ABUJA}"}
    assert docs.targets_of(PEOPLE["hq_finance"], HQ) == {"user:12", "role:hq_finance"}
    assert docs.targets_of(PEOPLE["super"], HQ) == {"user:13", "role:super_admin", f"office:{HQ}"}
    assert docs.church_of(PEOPLE["hq_finance"], HQ) == HQ and docs.church_of(PEOPLE["owner"], HQ) == ABUJA
    assert docs.target_ok("office:2") and docs.target_ok("role:hq_media") and not docs.target_ok("role:hod") and not docs.target_ok("user:x")


def test_who_sees_which_document():
    async def body(db):
        from app.models.document import Document, DocumentShare

        def mk(title, vis, branch=ABUJA, owner=10):
            return Document(title=title, visibility=vis, branch_id=branch, owner_id=owner, filename=title, ext="pdf", stored_path="x", size=1)
        rows = {
            "private": mk("private", "private"),
            "church": mk("church", "church"),
            "everyone": mk("everyone", "everyone"),
            "private_to_finance": mk("private_to_finance", "private"),
            "private_to_hods": mk("private_to_hods", "private"),
        }
        db.add_all(rows.values())
        await db.flush()
        db.add_all([DocumentShare(document_id=rows["private_to_finance"].id, target="role:hq_finance", access="edit"),
                    DocumentShare(document_id=rows["private_to_hods"].id, target=f"hods:{ABUJA}", access="view")])
        await db.commit()
        seen = {}
        for who, person in PEOPLE.items():
            q = select(Document.title).where(docs.visible_condition(person, HQ))
            seen[who] = set((await db.execute(q)).scalars().all())
        access = {who: await docs.access_of(db, PEOPLE[who], HQ, rows["private_to_finance"]) for who in ("owner", "hq_finance", "super", "abuja_hod")}
        return seen, access

    seen, access = _run(body)
    assert seen["owner"] == {"private", "church", "everyone", "private_to_finance", "private_to_hods"}
    assert seen["abuja_hod"] == {"church", "everyone", "private_to_hods"}           # same church + the HOD office share
    assert seen["abuja_rp"] == {"church", "everyone"}
    assert seen["hq_finance"] == {"everyone", "private_to_finance"}                  # another church: only what is shared
    assert seen["other_branch"] == {"everyone"}
    assert seen["super"] == {"church", "everyone"}                                   # HQ Administrator: never "only me" files
    assert access == {"owner": "manage", "hq_finance": "edit", "super": None, "abuja_hod": None}


class _Upload:
    def __init__(self, name, data):
        self.filename, self._buf = name, io.BytesIO(data)

    async def read(self, n=-1):
        return self._buf.read(n)


def test_uploads_are_checked(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(type(settings), "documents_dir", property(lambda self: str(tmp_path)))
    rel, name, ext, mime, size = asyncio.run(docs.save_upload(_Upload("Minutes March.PDF", b"%PDF-1.4 hello")))
    assert ext == "pdf" and mime == "application/pdf" and size == 14 and (tmp_path / rel).read_bytes().startswith(b"%PDF")
    for bad in ("virus.exe", "page.html", "image.svg", "script.js", "noext"):
        with pytest.raises(ValueError):
            asyncio.run(docs.save_upload(_Upload(bad, b"x")))
    with pytest.raises(ValueError):
        asyncio.run(docs.save_upload(_Upload("empty.pdf", b"")))
    monkeypatch.setattr(settings, "DOCUMENTS_MAX_UPLOAD_MB", 0)
    with pytest.raises(ValueError):
        asyncio.run(docs.save_upload(_Upload("big.pdf", b"x" * 10)))
    assert list(tmp_path.rglob("*.pdf")) == [tmp_path / rel]                         # refused files leave nothing behind
    with pytest.raises(ValueError):
        docs.abspath("../../etc/passwd")
    assert docs.clean_name('a<b>:"c".pdf') == "abc.pdf" and "html" not in docs.INLINE and "svg" not in docs.TYPES


def test_every_documents_url_needs_the_documents_permission():
    from app.core import access

    for role in ("super_admin", "hq_media", "hq_finance", "hq_pastoral", "branch_admin", "resident_pastor", "hod"):
        assert access.has_perm(role, "documents"), role
    assert access.required_permission("GET", "/admin/documents") == "documents"
    assert access.required_permission("POST", "/admin/documents/5/send") == "documents"
