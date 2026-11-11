"""Console sign-in security: password policy, two-step sign-in (authenticator app), "remember me",
password reset links and audit-log retention.

Settings (Admin -> Settings -> Admin security, HQ Administrator):
* ``enforce_strong_passwords`` = "1": new console passwords need 12+ characters with letters and numbers and
  may not be a common password or contain the person's email name. Off: at least 8 characters.
* ``enable_admin_2fa`` = "1": every console account must turn on two-step sign-in (they are sent to My Profile
  until they do). Off: anyone may still turn it on for their own account.
* ``allow_admin_remember_me`` = "1": the sign-in page offers "Keep me signed in for 30 days" on that device.
* ``audit_log_retention_days`` (30-3650, default 365): older audit-log rows are deleted by the daily tidy-up.

Two-step codes are standard TOTP (RFC 6238; Google Authenticator, Microsoft Authenticator, Authy...). The secret
is stored Fernet-encrypted on the account (``Admin.totp_secret``); ten one-time recovery codes are stored hashed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_cache import _decrypt_strict, _encrypt, get_setting

REMEMBER_DAYS = 30
RESET_MAX_AGE = 3600                    # a password reset link works for one hour
RETENTION_DEFAULT, RETENTION_MIN, RETENTION_MAX = 365, 30, 3650

_COMMON = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890", "qwerty123", "qwertyuiop",
    "iloveyou", "admin123", "administrator", "welcome1", "welcome123", "letmein1", "abc12345", "11111111", "00000000",
    "church123", "jesus123", "jesuschrist", "godisgood", "blessed1", "scentofwater", "changeme", "football1",
}


async def setting_on(db: AsyncSession, key: str) -> bool:
    """A security switch from Settings. If settings cannot be read, the switch counts as off (the standard rules,
    e.g. 8+ character passwords, still apply)."""
    try:
        return (await get_setting(db, key, "0")) == "1"
    except Exception:
        return False


async def retention_days(db: AsyncSession) -> int:
    raw = (await get_setting(db, "audit_log_retention_days", str(RETENTION_DEFAULT))).strip()
    try:
        days = int(raw)
    except ValueError:
        days = RETENTION_DEFAULT
    return max(RETENTION_MIN, min(RETENTION_MAX, days))


def password_rules(strong: bool) -> str:
    return ("At least 12 characters, with letters and numbers; not a common password."
            if strong else "At least 8 characters.")


async def password_problem(db: AsyncSession, password: str, email: str | None = None) -> str | None:
    """Why this new console password is not acceptable, or None."""
    pw = password or ""
    if not await setting_on(db, "enforce_strong_passwords"):
        return None if len(pw) >= 8 else "Password must be at least 8 characters."
    if len(pw) < 12:
        return "Password must be at least 12 characters (strong passwords are required)."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Password must contain both letters and numbers."
    low = pw.lower()
    if low in _COMMON or len(set(low)) <= 3:
        return "That password is too common or too simple. Choose another."
    local = (email or "").split("@")[0].lower()
    if len(local) >= 4 and local in low:
        return "Password must not contain your email name."
    return None


# ─── two-step sign-in (TOTP) ───
def new_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def otpauth_uri(secret: str, email: str, issuer: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=(issuer or "SWM Console")[:60])


def qr_svg(uri: str) -> str:
    """The setup QR code as inline SVG (no external service sees the secret)."""
    import io

    import segno

    out = io.BytesIO()
    segno.make(uri, error="m").save(out, kind="svg", scale=5, border=2, xmldecl=False, svgns=True, dark="#111111", light="#ffffff")
    return out.getvalue().decode("utf-8")


def verify_code(secret: str, code: str) -> bool:
    import pyotp

    code = re.sub(r"\s|-", "", code or "")
    if not re.fullmatch(r"\d{6}", code) or not secret:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def store_secret(secret: str) -> str:
    return _encrypt(secret)


def read_secret(stored: str | None) -> str | None:
    return _decrypt_strict(stored) if stored else None


def _hash_code(code: str) -> str:
    return hashlib.sha256(re.sub(r"[\s-]", "", code or "").lower().encode()).hexdigest()


def new_recovery_codes(n: int = 10) -> tuple[list[str], str]:
    """Plain codes to show once, and the JSON of their hashes to store."""
    plain = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return plain, json.dumps([_hash_code(c) for c in plain])


def use_recovery_code(stored_json: str | None, code: str) -> str | None:
    """If ``code`` is an unused recovery code, return the remaining codes' JSON (the code is spent); else None."""
    try:
        hashes = json.loads(stored_json or "[]")
    except ValueError:
        return None
    h = _hash_code(code)
    for i, x in enumerate(hashes):
        if hmac.compare_digest(x, h):
            return json.dumps(hashes[:i] + hashes[i + 1:])
    return None


def recovery_left(stored_json: str | None) -> int:
    try:
        return len(json.loads(stored_json or "[]"))
    except ValueError:
        return 0


# ─── password reset links (signed; die as soon as the password changes) ───
def _serializer():
    from itsdangerous import URLSafeTimedSerializer

    from app.config import settings

    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="admin-password-reset")


def reset_token(admin_id: int, session_version: int) -> str:
    return _serializer().dumps({"a": admin_id, "v": session_version or 0})


def read_reset_token(token: str) -> tuple[int, int] | None:
    from itsdangerous import BadSignature, SignatureExpired

    try:
        data = _serializer().loads(token, max_age=RESET_MAX_AGE)
        return int(data["a"]), int(data["v"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None
