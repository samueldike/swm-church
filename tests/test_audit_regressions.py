"""Regression tests for the promise audit (pure logic; no database or network)."""
import asyncio
import pathlib
from types import SimpleNamespace

import httpx
import pytest

from app.core import payment, throttle
from app.routes import admin as admin_routes
from app.routes import public as public_routes


# ─── login / form throttle ───────────────────────────────────────────

def test_throttle_blocks_after_limit_and_clears():
    throttle.reset_all()
    for _ in range(4):
        throttle.record_failure("k", window=60)
    assert throttle.is_blocked("k", limit=5, window=60) == 0
    throttle.record_failure("k", window=60)
    assert 0 < throttle.is_blocked("k", limit=5, window=60) <= 60
    assert throttle.is_blocked("other", limit=5, window=60) == 0
    throttle.clear("k")
    assert throttle.is_blocked("k", limit=5, window=60) == 0


def test_throttle_window_expires(monkeypatch):
    throttle.reset_all()
    now = [1000.0]
    monkeypatch.setattr(throttle.time, "monotonic", lambda: now[0])
    for _ in range(5):
        throttle.record_failure("k", window=10)
    assert throttle.is_blocked("k", limit=5, window=10)
    now[0] += 11
    assert throttle.is_blocked("k", limit=5, window=10) == 0


# ─── audio headers + Range ───────────────────────────────────────────

def test_content_disposition_is_header_safe():
    for title in ("Faith “Speaks” — Ọmọ", 'He said "hi"', "a/b\c", "", "日本語"):
        value = public_routes._content_disposition("inline", title, ".mp3")
        value.encode("latin-1")            # would raise UnicodeEncodeError for an unsafe header
        assert value.startswith("inline; filename=\"") and "filename*=UTF-8''" in value
    assert 'filename="Simple Title.mp3"' in public_routes._content_disposition("attachment", "Simple Title", ".mp3")


def _req(range_header=None, method="GET"):
    headers = {"range": range_header} if range_header else {}
    return SimpleNamespace(headers=headers, method=method)


def _body(resp) -> bytes:
    async def collect():
        return b"".join([chunk async for chunk in resp.body_iterator])

    return asyncio.run(collect())


@pytest.fixture()
def audio(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(bytes(range(200)))
    return str(p)


def test_local_audio_full_and_ranges(audio):
    data = bytes(range(200))
    full = public_routes._serve_local_audio(_req(), audio, "inline")
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes" and _body(full) == data
    part = public_routes._serve_local_audio(_req("bytes=10-19"), audio, "inline")
    assert part.status_code == 206 and part.headers["content-range"] == "bytes 10-19/200" and _body(part) == data[10:20]
    tail = public_routes._serve_local_audio(_req("bytes=-5"), audio, "inline")
    assert _body(tail) == data[-5:]
    open_end = public_routes._serve_local_audio(_req("bytes=190-"), audio, "inline")
    assert _body(open_end) == data[190:]
    assert public_routes._serve_local_audio(_req("bytes=500-"), audio, "inline").status_code == 416
    junk = public_routes._serve_local_audio(_req("bytes=abc-def"), audio, "inline")
    assert junk.status_code == 200                                  # unparsable -> ignore the header
    assert public_routes._serve_local_audio(_req(method="HEAD"), audio, "inline").status_code == 200


# ─── CSV export ──────────────────────────────────────────────────────

def test_csv_cells_are_neutralised_against_formula_injection():
    assert admin_routes._csv_cell("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")"
    for lead in ("+1", "-1", "@a", "\tx"):
        assert admin_routes._csv_cell(lead).startswith("'")
    assert admin_routes._csv_cell("plain") == "plain" and admin_routes._csv_cell(None) == "" and admin_routes._csv_cell(5) == "5"
    resp = admin_routes._csv_response("x.csv", ["a"], [["=1+1"]])
    assert "attachment" in resp.headers["content-disposition"]


# ─── payment verification states ─────────────────────────────────────

def _patch_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("raw,state", [("success", "paid"), ("abandoned", "pending"), ("ongoing", "pending"), ("failed", "failed")])
def test_paystack_verify_state(monkeypatch, raw, state):
    _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"status": True, "data": {"status": raw, "amount": 500000}}))
    out = asyncio.run(payment.verify_paystack("ref", "sk"))
    assert out["state"] == state and out["success"] == (state == "paid")


def test_paystack_verify_provider_error(monkeypatch):
    _patch_http(monkeypatch, lambda req: httpx.Response(500))
    assert asyncio.run(payment.verify_paystack("ref", "sk"))["state"] == "error"


@pytest.mark.parametrize("raw,state", [("successful", "paid"), ("pending", "pending"), ("failed", "failed")])
def test_flutterwave_verify_state(monkeypatch, raw, state):
    _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"status": "success", "data": {"status": raw, "tx_ref": "R", "amount": 100}}))
    assert asyncio.run(payment.verify_flutterwave("1", "R", "sk"))["state"] == state
    assert asyncio.run(payment.verify_flutterwave("", "R", "sk"))["state"] == "pending"


# ─── promise wiring (static checks on the templates/routes that broke before) ──

def _read(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


def test_giving_status_names_agree_between_callback_and_dashboard():
    pub, adm = _read("app/routes/public.py"), _read("app/routes/admin.py")
    assert 'giving.status = "completed"' in pub
    # The dashboard trend counts the shared PAID_STATUSES, which must include the callback's "completed".
    assert 'PAID_STATUSES = ("completed", "success")' in adm
    assert "paid = Giving.status.in_(PAID_STATUSES)" in adm


def test_prayer_newsletter_and_donations_are_reachable_from_the_admin_and_public_site():
    side = _read("app/views/partials/admin-sidebar.html")
    for name in ("admin_prayer_requests", "admin_subscribers", "admin_donations"):
        assert f"url_for('{name}')" in side
    assert "newsletter_subscribe" in _read("app/views/partials/theme-page-close.html")
    assert "newsletter_subscribe" in _read("app/views/pages/home.html")
    for page in ("theme-page-close", "home"):
        src = _read("app/views/partials/theme-page-close.html" if page == "theme-page-close" else "app/views/pages/home.html")
        assert "public-flash.html" in src


def test_public_pages_no_longer_hide_error_notices():
    for page in ("contact", "prayer", "give"):
        assert "session.get('prayer_success')" not in _read(f"app/views/pages/{page}.html")
