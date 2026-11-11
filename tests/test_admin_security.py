"""Console sign-in security (app/core/admin_security.py): password policy, two-step codes, recovery codes,
reset links, and the gaps closed in the same change (HarvestTrack self-registration, honest "emailed" notes)."""
import asyncio
import time

import pytest

from app.core import admin_security as sec


def _policy(monkeypatch, strong: bool):
    async def setting_on(_db, key):
        return strong and key == "enforce_strong_passwords"

    monkeypatch.setattr(sec, "setting_on", setting_on)


def test_password_policy_off_needs_8_characters(monkeypatch):
    _policy(monkeypatch, False)
    assert asyncio.run(sec.password_problem(None, "short")) == "Password must be at least 8 characters."
    assert asyncio.run(sec.password_problem(None, "longenough")) is None


@pytest.mark.parametrize("pw,ok", [
    ("Sunday2026Service", True),
    ("short1A", False),                 # too short
    ("onlylettersheretoo", False),      # no digits
    ("123456789012345", False),         # no letters
    ("password123", False),             # too short and common
    ("aaaaaaaaaaaa1", False),           # too simple
    ("grace.okafor2026x", False),       # contains the email name
])
def test_strong_password_policy(monkeypatch, pw, ok):
    _policy(monkeypatch, True)
    problem = asyncio.run(sec.password_problem(None, pw, "grace.okafor@example.org"))
    assert (problem is None) is ok, problem


def test_totp_codes_and_secret_encryption():
    import pyotp

    secret = sec.new_secret()
    now_code = pyotp.TOTP(secret).now()
    assert sec.verify_code(secret, now_code)
    assert sec.verify_code(secret, now_code[:3] + " " + now_code[3:])      # spaces are fine
    assert not sec.verify_code(secret, "000000" if now_code != "000000" else "111111")
    assert not sec.verify_code(secret, "abc")
    old = pyotp.TOTP(secret).at(time.time() - 600)                          # 10 minutes old: refused
    assert not sec.verify_code(secret, old) or old == now_code
    stored = sec.store_secret(secret)
    assert stored != secret and sec.read_secret(stored) == secret
    assert "otpauth://totp/" in sec.otpauth_uri(secret, "a@b.org", "SWM") and "<svg" in sec.qr_svg(sec.otpauth_uri(secret, "a@b.org", "SWM"))


def test_recovery_codes_work_once():
    plain, stored = sec.new_recovery_codes()
    assert len(plain) == 10 and sec.recovery_left(stored) == 10
    left = sec.use_recovery_code(stored, plain[3].upper())                  # any case, with its dash
    assert left is not None and sec.recovery_left(left) == 9
    assert sec.use_recovery_code(left, plain[3]) is None                    # spent
    assert sec.use_recovery_code(stored, "nope-nope") is None


def test_reset_links_are_signed_and_expire(monkeypatch):
    tok = sec.reset_token(7, 3)
    assert sec.read_reset_token(tok) == (7, 3)
    assert sec.read_reset_token(tok + "x") is None                         # tampered
    monkeypatch.setattr(sec, "RESET_MAX_AGE", -1)
    assert sec.read_reset_token(tok) is None                                # expired


def test_retention_is_clamped(monkeypatch):
    async def fake_get(_db, key, default=""):
        return values[key]

    monkeypatch.setattr(sec, "get_setting", fake_get)
    for raw, want in (("5", 30), ("400", 400), ("99999", 3650), ("abc", 365)):
        values = {"audit_log_retention_days": raw}
        assert asyncio.run(sec.retention_days(None)) == want


def test_harvesttrack_self_registration_is_closed():
    from app.routes import api

    r = asyncio.run(api.ht_register(None))
    assert r.status_code == 403 and b"church office" in r.body


def test_can_send_is_false_without_email_setup(monkeypatch):
    from app.core import mailer

    async def no_config():
        return None

    monkeypatch.setattr(mailer, "get_config", no_config)
    monkeypatch.delenv("SWM_EMAIL_DRY_RUN", raising=False)
    assert asyncio.run(mailer.can_send("someone@gmail.com")) is False
    assert asyncio.run(mailer.can_send("not-an-email")) is False
    assert asyncio.run(mailer.can_send("x@example.org")) is False           # reserved test domain
