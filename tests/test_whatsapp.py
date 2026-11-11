"""WhatsApp feature: who may use it, who may grant it, and delivery of scheduled posts (gateway faked)."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app.core import access
from app.core import whatsapp as wa


@pytest.mark.parametrize("role,override,dept,ok", [
    ("super_admin", None, None, True),
    ("super_admin", "deny", None, True),            # the HQ Administrator always has it
    ("hod", None, "Media/ICT", True),               # Media HOD by default
    ("hod", None, "Media and ICT", True),
    ("hod", "deny", "Media/ICT", False),            # ...unless switched off
    ("hod", None, "Choir", False),
    ("hod", "grant", "Choir", True),                # any HOD when granted
    ("resident_pastor", None, None, False),
    ("resident_pastor", "grant", None, True),
    ("branch_admin", "grant", None, False),         # only HODs and Resident Pastors can be granted
    ("hq_media", "grant", None, False),
    ("hod", None, "Multimedia", False),             # "media" must be a word, not part of one
])
def test_who_may_use_whatsapp(role, override, dept, ok):
    assert wa.allowed(role, override, dept) is ok


def test_who_may_grant():
    sup = NS(role="super_admin", branch_id=1)
    badmin = NS(role="branch_admin", branch_id=2)
    hod_same, hod_other = NS(role="hod", branch_id=2), NS(role="hod", branch_id=3)
    rp_same, other_admin = NS(role="resident_pastor", branch_id=2), NS(role="branch_admin", branch_id=2)
    assert wa.can_grant(sup, hod_other) and wa.can_grant(sup, rp_same)
    assert wa.can_grant(badmin, hod_same) and wa.can_grant(badmin, rp_same)
    assert not wa.can_grant(badmin, hod_other)       # never another branch
    assert not wa.can_grant(badmin, other_admin)     # only HODs and Resident Pastors
    assert not wa.can_grant(NS(role="hod", branch_id=2), hod_same)


def test_paths_are_covered_by_the_policy():
    assert access.required_permission("GET", "/admin/whatsapp") == "whatsapp"
    assert access.required_permission("POST", "/admin/whatsapp/posts") == "whatsapp"
    assert access.required_permission("GET", "/admin/whatsapp/chats.json") == "whatsapp"
    assert access.required_permission("POST", "/admin/access/whatsapp/7") == "hods"


def test_tenant_ids_are_per_church():
    assert wa.tenant(1) == "b1" and wa.tenant("12") == "b12"


def _post(**kw):
    base = dict(branch_id=4, kind="message", text="Hello", media_path=None, media_kind=None, media_name=None,
                targets=[], result=None, status="sending", next_at=None, error=None, send_at=datetime.utcnow())
    base.update(kw)
    return NS(**base)


@pytest.fixture
def instant(monkeypatch):
    """Pauses between recipients are instant in tests."""
    async def no_sleep(_s):
        return None

    monkeypatch.setattr(wa.asyncio, "sleep", no_sleep)


def test_post_to_many_drips_two_recipients_per_round_and_never_resends(monkeypatch, instant):
    calls = []

    async def fake_call(method, path, **kw):
        calls.append(kw.get("json"))
        if kw["json"]["to"] == "bad@g.us":
            raise wa.GatewayError("not a member", 400)
        return {"ok": True}

    monkeypatch.setattr(wa, "call", fake_call)
    post = _post(targets=[{"jid": "a@g.us"}, {"jid": "bad@g.us"}, {"jid": "c@g.us"}, {"jid": "d@newsletter"}])
    now = datetime.utcnow()
    assert asyncio.run(wa._advance(post, now)) is False             # round 1: a ok, bad refused (noted), stop
    assert post.result == {"a@g.us": "ok", "bad@g.us": "not a member"} and post.status == "sending"
    assert post.next_at > now and all(c["bulk"] is True for c in calls)
    assert asyncio.run(wa._advance(post, now)) is True              # round 2: the last two, then finished
    assert [c["to"] for c in calls] == ["a@g.us", "bad@g.us", "c@g.us", "d@newsletter"]   # nobody twice
    assert post.status == "partial" and "1 of 4" in post.error


def test_safety_limit_pauses_the_post_until_it_frees_up(monkeypatch, instant):
    async def limited(method, path, **kw):
        raise wa.GatewayError("Safety limit reached", 429, "2030-01-01T10:00:00.000Z")

    monkeypatch.setattr(wa, "call", limited)
    post = _post(targets=[{"jid": "a@g.us"}])
    assert asyncio.run(wa._advance(post, datetime.utcnow())) is False
    assert post.status == "sending" and post.next_at >= datetime(2030, 1, 1, 10, 0) and "safety" in post.error.lower()
    assert not post.result                                          # nothing was marked as sent


def test_disconnected_account_waits(monkeypatch, instant):
    async def offline(method, path, **kw):
        raise wa.GatewayError("WhatsApp is not connected", 409)

    monkeypatch.setattr(wa, "call", offline)
    post = _post(targets=[{"jid": "a@g.us"}])
    assert asyncio.run(wa._advance(post, datetime.utcnow())) is False and post.status == "sending"
    post.send_at = datetime.utcnow() - timedelta(hours=7)           # waited too long: give up
    assert asyncio.run(wa._advance(post, datetime.utcnow())) is True and post.status == "failed"


def test_sends_at_any_hour_the_sender_chooses(monkeypatch, instant):
    sent = []

    async def fake_call(method, path, **kw):
        sent.append(kw["json"]["to"])
        return {"ok": True}

    monkeypatch.setattr(wa, "call", fake_call)
    post = _post(targets=[{"jid": "a@g.us"}])
    assert asyncio.run(wa._advance(post, datetime(2026, 11, 11, 23, 30))) is True   # 23:30: goes out, no night hold
    assert sent == ["a@g.us"] and post.status == "sent"
    assert not hasattr(wa, "QUIET_HOURS")


def test_status_post_goes_out_once(monkeypatch, instant):
    calls = []

    async def fake_call(method, path, **kw):
        calls.append((path, kw.get("json")))
        return {"ok": True}

    monkeypatch.setattr(wa, "call", fake_call)
    post = _post(kind="status", text="Sunday!")
    assert asyncio.run(wa._advance(post, datetime.utcnow())) is True and post.status == "sent"
    assert calls == [("/sessions/b4/status", {"kind": "text", "text": "Sunday!", "file": None, "caption": "Sunday!"})]


def test_name_personalisation():
    assert wa.personalise("Hi {name}, see you Sunday!", "Grace Okafor") == "Hi Grace, see you Sunday!"
    assert wa.personalise("Hi {name}, see you Sunday!", "+234 803 000 0000") == "Hi, see you Sunday!"
    assert wa.personalise("Hi {name}!", "~Ben") == "Hi Ben!"
    assert wa.personalise("No token here", "Ann") == "No token here"


def test_gateway_off_means_a_clear_message(monkeypatch):
    monkeypatch.delenv("WA_GATEWAY_TOKEN", raising=False)
    with pytest.raises(wa.GatewayError) as e:
        asyncio.run(wa.call("GET", "/sessions/b1"))
    assert e.value.status == 503 and "not set up" in str(e.value)
