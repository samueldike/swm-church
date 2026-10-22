"""Regression tests for the lead-engineer audit (audit/AUDIT_REPORT.md). No database or network:
routes run with a fake signed-in admin and a fake session that records what they did."""
import asyncio
import pathlib
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import access
from app.database import get_db
from app.main import app
from app.routes import admin as admin_routes
from app.routes import api as api_routes


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class FakeDB:
    """Answers each execute() with the next queued value; records deletes."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.deleted = []
        self.added = []

    async def execute(self, *_a, **_k):
        return FakeResult(self.answers.pop(0) if self.answers else None)

    async def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for i, obj in enumerate(self.added, start=100):
            if getattr(obj, "id", None) is None:
                obj.id = i


def _admin(role, branch_id=2):
    return SimpleNamespace(id=7, role=role, branch_id=branch_id)


@pytest.fixture
def api(monkeypatch):
    """TestClient whose caller is `state.admin` and whose database is `state.db`."""
    state = SimpleNamespace(admin=None, db=FakeDB(), csrf_ok=False)

    async def current(_request):
        return state.admin

    async def fake_db():
        yield state.db

    async def csrf(_request):
        if not state.csrf_ok:
            from app.core.csrf import CSRFError
            raise CSRFError()

    async def noop_log(*_a, **_k):
        return None

    async def hq_id(_db):
        return 1

    monkeypatch.setattr(api_routes, "get_current_admin", current)
    monkeypatch.setattr(api_routes, "log_admin_action", noop_log)
    monkeypatch.setattr("app.core.csrf.csrf_protect", csrf)
    monkeypatch.setattr("app.core.hq_branch.get_hq_branch_id", hq_id)
    app.dependency_overrides[get_db] = fake_db
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─── SEC-01: /api/events writes ──────────────────────────────────────

@pytest.mark.parametrize("role", ["hod", "hq_finance", "resident_pastor"])
def test_api_event_writes_refuse_roles_without_events_permission(api, role):
    client, state = api
    state.admin, state.csrf_ok = _admin(role), True
    assert not access.has_perm(role, "events")
    assert client.delete("/api/events/5").status_code == 403
    assert client.put("/api/events/5", json={"title": "x"}).status_code == 403
    assert client.post("/api/events", json={"title": "x", "starts_at": "2026-11-01T10:00"}).status_code == 403
    assert state.db.deleted == [] and state.db.added == []


def test_api_event_writes_refuse_anonymous_and_missing_csrf(api):
    client, state = api
    assert client.delete("/api/events/5").status_code == 403
    state.admin = _admin("branch_admin")                       # may manage events, but no CSRF header
    assert client.delete("/api/events/5").status_code == 403
    assert state.db.deleted == []


def test_branch_admin_cannot_touch_another_churchs_event(api):
    client, state = api
    state.admin, state.csrf_ok = _admin("branch_admin", branch_id=2), True
    hq_event = SimpleNamespace(id=5, title="Convention", branch_id=1)
    state.db.answers = [hq_event]
    assert client.delete("/api/events/5").status_code == 403
    state.db.answers = [hq_event]
    assert client.put("/api/events/5", json={"title": "Hijacked"}).status_code == 403
    assert state.db.deleted == [] and hq_event.title == "Convention"


def test_branch_admin_events_are_pinned_to_their_branch(api):
    client, state = api
    state.admin, state.csrf_ok = _admin("branch_admin", branch_id=2), True
    r = client.post("/api/events", json={"title": "Vigil", "starts_at": "2026-11-01T22:00", "branch_id": 1})
    assert r.status_code == 201
    assert state.db.added[0].branch_id == 2


def test_hq_administrator_may_choose_the_branch(api):
    client, state = api
    state.admin, state.csrf_ok = _admin("super_admin", branch_id=1), True
    r = client.post("/api/events", json={"title": "Crusade", "starts_at": "2026-11-01T10:00", "branch_id": 3})
    assert r.status_code == 201 and state.db.added[0].branch_id == 3


def test_unsafe_api_routes_are_reviewed():
    """Every state-changing /api route must be listed here after checking how it authorises the caller:
    /api sits outside the admin access policy and CSRF dependency."""
    reviewed = {
        ("POST", "/api/events"), ("PUT", "/api/events/{event_id}"), ("DELETE", "/api/events/{event_id}"),  # _event_editor
    }
    unsafe = set()
    for r in api_routes.router.routes:
        for m in getattr(r, "methods", None) or []:
            if m not in ("GET", "HEAD", "OPTIONS") and not r.path.startswith("/api/harvesttrack/"):
                unsafe.add((m, r.path))                       # HarvestTrack uses its own bearer tokens
    assert unsafe == reviewed, unsafe ^ reviewed


# ─── SEC-02: uploads served from our own domain ──────────────────────

def test_audio_upload_is_judged_by_extension_not_content_type():
    src = re.sub(r"\s+", " ", pathlib.Path("app/routes/admin.py").read_text(encoding="utf-8"))
    assert 'audio_file.content_type.startswith("audio/")' not in src
    assert {"html", "htm", "svg", "js", ""}.isdisjoint(admin_routes.AUDIO_UPLOAD_EXTENSIONS)
    assert "mp3" in admin_routes.AUDIO_UPLOAD_EXTENSIONS


def test_uploaded_files_cannot_run_script(tmp_path, monkeypatch):
    from starlette.applications import Starlette
    from starlette.staticfiles import StaticFiles

    from app.core.upload_headers import UploadHeadersMiddleware

    (tmp_path / "probe.html").write_text("<script>alert(1)</script>")
    inner = Starlette()
    inner.mount("/media", StaticFiles(directory=str(tmp_path)))
    r = TestClient(UploadHeadersMiddleware(inner)).get("/media/probe.html")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"] and "script-src" not in r.headers["content-security-policy"]
    assert any(m.cls is UploadHeadersMiddleware for m in app.user_middleware)


# ─── DATA-03: deleting a department ──────────────────────────────────

def _call_destroy(monkeypatch, db):
    async def current(_request):
        return _admin("super_admin", branch_id=1)

    monkeypatch.setattr(admin_routes, "get_current_admin", current)
    request = SimpleNamespace(session={})
    resp = asyncio.run(admin_routes.admin_departments_destroy(4, request, db))
    return resp, request.session


def test_department_with_reports_is_not_deleted(monkeypatch):
    dept = SimpleNamespace(id=4, name="Choir")
    db = FakeDB(dept, 12, 0)                                  # department, report count, HOD count
    resp, session = _call_destroy(monkeypatch, db)
    assert resp.status_code == 303 and db.deleted == []
    assert session["flash_messages"][0]["type"] == "error" and "12 reports" in session["error"]


def test_department_with_an_hod_is_not_deleted(monkeypatch):
    db = FakeDB(SimpleNamespace(id=4, name="Choir"), 0, 1)
    _resp, session = _call_destroy(monkeypatch, db)
    assert db.deleted == [] and "1 HOD account" in session["error"]


def test_unused_department_is_deleted(monkeypatch):
    dept = SimpleNamespace(id=4, name="Choir")
    db = FakeDB(dept, 0, 0)
    _resp, session = _call_destroy(monkeypatch, db)
    assert db.deleted == [dept] and session["flash_messages"][0]["type"] == "success"


def test_unit_reports_never_cascade_from_departments():
    from app.models.department import Department
    from app.models.unit_report import UnitReport

    fk = next(iter(UnitReport.__table__.c.department_id.foreign_keys))
    assert fk.ondelete == "RESTRICT"
    assert "delete" not in Department.unit_reports.property.cascade
