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

    def first(self):
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


# ─── SEC-03: the visitor's IP for rate limits ────────────────────────

def test_spoofed_forwarded_for_does_not_change_the_rate_limit_key():
    from app.core.client_ip import resolve

    nginx = "172.18.0.5"                                              # proxy on the Docker network
    # What nginx sends for a visitor at 102.89.1.1 who typed their own X-Forwarded-For:
    spoofed = {"x-real-ip": "102.89.1.1", "x-forwarded-for": "6.6.6.6, 102.89.1.1"}
    assert resolve(spoofed, nginx) == "102.89.1.1"
    assert resolve({"x-forwarded-for": "6.6.6.6, 102.89.1.1"}, nginx) == "102.89.1.1"   # no X-Real-IP: last hop
    assert resolve({"x-real-ip": "6.6.6.6", "x-forwarded-for": "6.6.6.6"}, "102.89.1.1") == "102.89.1.1"  # no proxy
    assert resolve({}, nginx) == nginx and resolve({}, None) == "unknown"


def test_every_ip_lookup_uses_the_shared_helper():
    for path in ("app/routes/public.py", "app/routes/api.py", "app/routes/admin.py", "app/core/analytics.py"):
        src = pathlib.Path(path).read_text(encoding="utf-8")
        assert 'get("x-forwarded-for", "").split(",")[0]' not in src, path


# ─── SEC-04: accepting an HOD invitation ─────────────────────────────

def _accept_hod(monkeypatch, password, email_taken=None):
    invitation = SimpleNamespace(name="Ada Choir", email="ada@gmail.com", branch_id=2, department_id=9, accepted_at=None)
    db = FakeDB(invitation, email_taken)
    shown = {}

    async def render(_request, _template, ctx):
        shown.update(ctx)
        return "form"

    monkeypatch.setattr(admin_routes, "render_template", render)
    request = SimpleNamespace(session={})
    resp = asyncio.run(admin_routes.hod_invitation_accept("tok", request, password, password, db))
    return resp, db, shown, invitation


def test_hod_invitation_gives_the_account_its_department(monkeypatch):
    resp, db, _shown, invitation = _accept_hod(monkeypatch, "Choir-2026-ok")
    assert resp.status_code == 303 and resp.headers["location"] == "/admin/hod/reports"
    hod = db.added[0]
    assert (hod.role, hod.branch_id, hod.department_id) == ("hod", 2, 9)
    assert invitation.accepted_at is not None


@pytest.mark.parametrize("password,taken,message", [("a", None, "at least 8"), ("Choir-2026-ok", (1,), "already exists")])
def test_hod_invitation_refuses_short_password_and_existing_email(monkeypatch, password, taken, message):
    resp, db, shown, invitation = _accept_hod(monkeypatch, password, taken)
    assert resp == "form" and db.added == [] and invitation.accepted_at is None
    assert message in shown["error_messages"]["password"][0]


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


# ─── DATA-01: online gifts are reconciled with the gateway ───────────

@pytest.fixture
def gifts(monkeypatch):
    """In-memory database with gifts, a scripted gateway and a receipt counter."""
    from datetime import datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import giving_reconcile as gr
    from app.core import mailer
    from app.database import Base
    from app.models.giving import Giving

    state = SimpleNamespace(answer={"state": "error"}, receipts=0, calls=0)

    async def verify(_giving, _secret, _tx):
        state.calls += 1
        return dict(state.answer)

    async def secret(_db, _giving):
        return "sk_test_x"

    async def send_notice(*_a, **_k):
        return True, ""

    def fire(coro):
        state.receipts += 1
        coro.close()

    monkeypatch.setattr(gr, "_verify", verify)
    monkeypatch.setattr(gr, "gateway_secret", secret)
    async def mail_config():
        return SimpleNamespace(api_key="re_x", sender="office@gmail.com", name="SWM")

    monkeypatch.setattr(mailer, "send_notice", send_notice)
    monkeypatch.setattr(mailer, "fire", fire)
    monkeypatch.setattr(mailer, "get_config", mail_config)

    eng = create_async_engine("sqlite+aiosqlite://")
    maker = async_sessionmaker(eng, expire_on_commit=False)

    async def setup():
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)

    asyncio.run(setup())

    async def add(ref="swm_a", amount=5000, age_hours=1.0, status="pending", **kw):
        from datetime import timedelta

        async with maker() as db:
            fields = dict(reference=ref, email="giver@gmail.com", amount=amount, currency="NGN", giving_type="Tithe",
                          payment_gateway="paystack", method="online", status=status, gateway_owner="hq",
                          created_at=datetime.utcnow() - timedelta(hours=age_hours))
            g = Giving(**{**fields, **kw})
            db.add(g)
            await db.commit()
            return g.id

    async def load(db, gid):
        from sqlalchemy import select
        return (await db.execute(select(Giving).where(Giving.id == gid))).scalar_one()

    state.maker, state.add, state.load, state.gr = maker, add, load, gr
    yield state
    asyncio.run(eng.dispose())


def _reconcile(state, gid):
    async def run():
        async with state.maker() as db:
            g = await state.load(db, gid)
            return await state.gr.reconcile(db, g, base_url="https://x"), g.status
    return asyncio.run(run())


def test_unreachable_gateway_never_marks_a_gift_failed(gifts):
    gid = asyncio.run(gifts.add())
    gifts.answer = {"state": "error"}
    assert _reconcile(gifts, gid) == ("error", "pending")


def test_paid_gift_is_confirmed_once_with_one_receipt(gifts):
    gid = asyncio.run(gifts.add(amount=5000))
    gifts.answer = {"state": "paid", "success": True, "amount_minor": 500000, "currency": "NGN"}
    assert _reconcile(gifts, gid) == ("completed", "completed")
    assert _reconcile(gifts, gid) == ("completed", "completed")
    assert gifts.receipts == 1 and gifts.calls == 1               # the second call did not even ask the gateway


def test_two_simultaneous_confirmations_send_one_receipt(gifts):
    """DATA-15: callback and webhook arrive together; both loaded the gift while it was still pending."""
    gid = asyncio.run(gifts.add(amount=5000))
    gifts.answer = {"state": "paid", "success": True, "amount_minor": 500000, "currency": "NGN"}

    async def run():
        async with gifts.maker() as a, gifts.maker() as b:
            ga, gb = await gifts.load(a, gid), await gifts.load(b, gid)
            return await gifts.gr.reconcile(a, ga), await gifts.gr.reconcile(b, gb)

    assert asyncio.run(run()) == ("completed", "completed")
    assert gifts.receipts == 1


def test_short_payment_is_a_mismatch_not_a_gift(gifts):
    gid = asyncio.run(gifts.add(amount=5000))
    gifts.answer = {"state": "paid", "success": True, "amount_minor": 100, "currency": "NGN"}
    assert _reconcile(gifts, gid) == ("mismatch", "mismatch")
    gifts.answer = {"state": "paid", "success": True, "amount_minor": 500000, "currency": "USD"}
    gid2 = asyncio.run(gifts.add(ref="swm_b", amount=5000))
    assert _reconcile(gifts, gid2) == ("mismatch", "mismatch") and gifts.receipts == 0


def test_unpaid_gift_is_abandoned_only_after_72_hours(gifts):
    gifts.answer = {"state": "pending"}
    fresh = asyncio.run(gifts.add(ref="swm_new", age_hours=2))
    old = asyncio.run(gifts.add(ref="swm_old", age_hours=80))
    assert _reconcile(gifts, fresh) == ("pending", "pending")
    assert _reconcile(gifts, old) == ("abandoned", "abandoned")


def test_scheduler_rechecks_only_due_gifts(gifts):
    from datetime import datetime, timedelta

    ids = {
        "too_new": asyncio.run(gifts.add(ref="r1", age_hours=0.05)),
        "due": asyncio.run(gifts.add(ref="r2", age_hours=1)),
        "checked_recently": asyncio.run(gifts.add(ref="r3", age_hours=1, checked_at=datetime.utcnow() - timedelta(minutes=5))),
        "checked_long_ago": asyncio.run(gifts.add(ref="r4", age_hours=5, checked_at=datetime.utcnow() - timedelta(hours=1))),
        "too_old": asyncio.run(gifts.add(ref="r5", age_hours=24 * 10)),
        "done": asyncio.run(gifts.add(ref="r6", age_hours=1, status="completed")),
    }

    async def run():
        async with gifts.maker() as db:
            return set(await gifts.gr.due_for_recheck(db))

    assert asyncio.run(run()) == {ids["due"], ids["checked_long_ago"]}


def test_paystack_webhook_requires_a_valid_signature(gifts, monkeypatch):
    import hashlib
    import hmac
    import json

    from app.core import throttle

    throttle.reset_all()
    gid = asyncio.run(gifts.add(ref="swm_hook", amount=5000))
    gifts.answer = {"state": "paid", "success": True, "amount_minor": 500000, "currency": "NGN"}

    async def session():
        async with gifts.maker() as db:
            yield db

    app.dependency_overrides[get_db] = session
    try:
        client = TestClient(app)
        body = json.dumps({"event": "charge.success", "data": {"reference": "swm_hook", "amount": 500000}}).encode()
        bad = client.post("/give/webhook/paystack", content=body, headers={"x-paystack-signature": "0" * 128})
        assert bad.status_code == 401 and gifts.calls == 0
        sig = hmac.new(b"sk_test_x", body, hashlib.sha512).hexdigest()
        ok = client.post("/give/webhook/paystack", content=body, headers={"x-paystack-signature": sig})
        assert ok.status_code == 200 and gifts.calls == 1
        unknown = client.post("/give/webhook/paystack", content=b'{"data":{"reference":"nope"}}')
        assert unknown.status_code == 200                        # not ours: acknowledged so Paystack stops retrying
    finally:
        app.dependency_overrides.pop(get_db, None)

    async def status():
        async with gifts.maker() as db:
            return (await gifts.load(db, gid)).status

    assert asyncio.run(status()) == "completed"


# ─── DATA-02 / DATA-16: bank-transfer notices ────────────────────────

def _transfer(gifts, monkeypatch, gid, action, role="hq_finance", branch_id=1):
    async def current(_request):
        return _admin(role, branch_id=branch_id)

    async def noop_log(*_a, **_k):
        return None

    monkeypatch.setattr(admin_routes, "get_current_admin", current)
    monkeypatch.setattr(admin_routes, "log_admin_action", noop_log)

    async def run():
        async with gifts.maker() as db:
            request = SimpleNamespace(session={}, headers={}, base_url="https://x/")
            await admin_routes.admin_donations_transfer(gid, request, db, action, "2026-10-20")
            await db.commit()
            g = await gifts.load(db, gid)
            return request.session.get("flash_messages", [{}])[0], g.status, g.paid_at
    return asyncio.run(run())


def test_transfer_notice_is_confirmed_and_receipted_once(gifts, monkeypatch):
    gid = asyncio.run(gifts.add(ref="TRF-1", payment_gateway="transfer", method="transfer", branch_id=2))
    flash, status, paid_at = _transfer(gifts, monkeypatch, gid, "confirm")
    assert flash["type"] == "success" and status == "completed" and paid_at.date().isoformat() == "2026-10-20"
    flash, status, _ = _transfer(gifts, monkeypatch, gid, "confirm")          # second tab / double click
    assert "already" in flash["message"] and status == "completed" and gifts.receipts == 1


def test_transfer_notice_not_received_is_not_counted(gifts, monkeypatch):
    gid = asyncio.run(gifts.add(ref="TRF-2", payment_gateway="transfer", method="transfer", branch_id=2))
    _flash, status, _ = _transfer(gifts, monkeypatch, gid, "reject")
    assert status == "rejected" and status not in admin_routes.PAID_STATUSES and gifts.receipts == 0


def test_branch_admin_cannot_confirm_another_churchs_transfer(gifts, monkeypatch):
    gid = asyncio.run(gifts.add(ref="TRF-3", payment_gateway="transfer", method="transfer", branch_id=1))
    flash, status, _ = _transfer(gifts, monkeypatch, gid, "confirm", role="branch_admin", branch_id=2)
    assert flash["type"] == "error" and status == "pending"


def test_online_gifts_cannot_be_confirmed_as_transfers(gifts, monkeypatch):
    gid = asyncio.run(gifts.add(ref="swm_online"))
    flash, status, _ = _transfer(gifts, monkeypatch, gid, "confirm")
    assert flash["type"] == "error" and status == "pending"


def test_hq_transfer_notices_belong_to_hq():
    src = pathlib.Path("app/routes/public_extra.py").read_text(encoding="utf-8")
    assert "branch_id=church_id if church_id != hq_id else None" not in src


def test_flutterwave_transaction_id_never_reaches_the_url_unchecked():
    from app.core import payment

    assert asyncio.run(payment.verify_flutterwave("../../v3/transfers", "swm_x", "k"))["state"] == "error"


def test_unit_reports_never_cascade_from_departments():
    from app.models.department import Department
    from app.models.unit_report import UnitReport

    fk = next(iter(UnitReport.__table__.c.department_id.foreign_keys))
    assert fk.ondelete == "RESTRICT"
    assert "delete" not in Department.unit_reports.property.cascade
