from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Settings keys whose values are secrets (API keys/tokens) and are encrypted at rest.
ENCRYPTED_SETTING_KEYS = {
    "telegram_bot_token",
    "resend_api_key",
    "paystack_secret_key",
    "flutterwave_secret_key",
    "flutterwave_encryption_key",
    "anthropic_api_key",
    "openrouter_api_key",
    "groq_api_key",
    "gemini_api_key",
}
_ENC_PREFIX = "enc:v1:"

# Legacy snake_case keys (from the pre-migration MySQL/PHP dataset and older admin
# form saves) mapped to the canonical camelCase keys the app reads everywhere else.
# Rows saved before the admin Settings form's key mapping existed are still stored
# under the legacy key, so reads fall back to it when the canonical key is empty.
LEGACY_SETTING_KEY_ALIASES = {
    "site_name": "siteName",
    "site_tagline": "siteTagline",
    "public_contact_email": "siteContactEmail",
    "support_phone": "siteSupportPhone",
    "headquarters_address": "siteHeadquartersAddress",
    "privacy_policy_content": "privacy_policy",
    "terms_of_service_content": "terms_of_service",
}


def _fernet():
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    from app.config import settings as app_settings

    key_bytes = hashlib.sha256(app_settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def _encrypt(value: str) -> str:
    if not value:
        return value
    return _ENC_PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_ENC_PREFIX):].encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def _decrypt_strict(value: str) -> str | None:
    """Plaintext of a stored secret, or ``None`` when it is encrypted but unreadable
    (e.g. SECRET_KEY was rotated). Non-encrypted legacy values pass through."""
    if not value:
        return ""
    if not value.startswith(_ENC_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_ENC_PREFIX):].encode("utf-8")).decode("utf-8")
    except Exception:
        return None


async def get_secret_status(db: AsyncSession) -> dict[str, dict]:
    """Per secret key: ``{"state": "unset"|"set"|"unreadable", "hint": "abcd"}``.

    Only the last four characters are ever exposed, so the settings page can show
    that a key is saved without putting it (even encrypted) into the HTML.
    """
    from app.models.site_settings import SiteSetting

    rows = {r.key: r.value or "" for r in (await db.execute(select(SiteSetting))).scalars().all()}
    out: dict[str, dict] = {}
    for key in ENCRYPTED_SETTING_KEYS:
        raw = rows.get(key, "")
        plain = _decrypt_strict(raw)
        if not raw:
            out[key] = {"state": "unset", "hint": ""}
        elif plain is None:
            out[key] = {"state": "unreadable", "hint": ""}
        else:
            out[key] = {"state": "set" if plain else "unset", "hint": plain[-4:] if len(plain) >= 8 else ""}
    return out


async def encrypt_plaintext_secrets(db: AsyncSession) -> int:
    """Encrypt any secret still stored in plaintext (saved before encryption existed)."""
    from app.models.site_settings import SiteSetting

    rows = (await db.execute(select(SiteSetting).where(SiteSetting.key.in_(ENCRYPTED_SETTING_KEYS)))).scalars().all()
    n = 0
    for row in rows:
        if row.value and not row.value.startswith(_ENC_PREFIX):
            row.value = _encrypt(row.value)
            n += 1
    if n:
        await db.flush()
    return n


async def get_settings(db: AsyncSession) -> dict[str, str]:
    from app.models.site_settings import SiteSetting

    result = await db.execute(select(SiteSetting))
    rows = result.scalars().all()
    settings = {
        row.key: (_decrypt(row.value) if row.key in ENCRYPTED_SETTING_KEYS else row.value)
        for row in rows
    }
    for legacy_key, canonical_key in LEGACY_SETTING_KEY_ALIASES.items():
        if not settings.get(canonical_key) and settings.get(legacy_key):
            settings[canonical_key] = settings[legacy_key]
    return settings


async def get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    settings = await get_settings(db)
    return settings.get(key, default)


async def update_settings(db: AsyncSession, settings_dict: dict[str, str]) -> None:
    from app.models.site_settings import SiteSetting

    for key, value in settings_dict.items():
        stored_value = _encrypt(value) if key in ENCRYPTED_SETTING_KEYS else value
        result = await db.execute(
            select(SiteSetting).where(SiteSetting.key == key)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = stored_value
        else:
            db.add(SiteSetting(key=key, value=stored_value))

    await db.flush()
