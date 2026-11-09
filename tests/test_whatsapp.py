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


def test_deliver_reports_partial_and_waits_when_disconnected(monkeypatch):
    calls = []

    async def fake_call(method, path, **kw):
        calls.append((path, kw.get("json")))
        to = (kw.get("json") or {}).get("to")
        if to == "bad@g.us":
            raise wa.GatewayError("not a member", 400)
        if to == "offline@c.us":
            raise wa.GatewayError("WhatsApp is not connected", 409)
        return {"ok": True}

    monkeypatch.setattr(wa, "call", fake_call)
    post = NS(branch_id=4, kind="message", text="Hello", media_path=None, media_kind=None, media_name=None,
              targets=[{"jid": "a@g.us"}, {"jid": "bad@g.us"}])
    status, result, error = asyncio.run(wa.deliver(post))
    assert status == "partial" and result["a@g.us"] == "ok" and "1 of 2" in error
    assert calls[0][0] == "/sessions/b4/send"

    post.targets = [{"jid": "offline@c.us"}]
    with pytest.raises(wa.GatewayError):            # 409 propagates so the post waits and is retried
        asyncio.run(wa.deliver(post))

    status_post = NS(branch_id=4, kind="status", text="Sunday!", media_path=None, media_kind=None, media_name=None, targets=[])
    assert asyncio.run(wa.deliver(status_post))[0] == "sent"
    assert calls[-1][0] == "/sessions/b4/status" and calls[-1][1]["kind"] == "text"


def test_gateway_off_means_a_clear_message(monkeypatch):
    monkeypatch.delenv("WA_GATEWAY_TOKEN", raising=False)
    with pytest.raises(wa.GatewayError) as e:
        asyncio.run(wa.call("GET", "/sessions/b1"))
    assert e.value.status == 503 and "not set up" in str(e.value)
