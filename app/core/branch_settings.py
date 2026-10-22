"""Per-branch configuration: each branch brings its own Telegram bot, bank accounts and (optionally)
payment-gateway keys. Secrets are encrypted at rest with the same scheme as site-wide secrets."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_cache import _ENC_PREFIX, _decrypt_strict, _encrypt
from app.models.branch_setting import BranchSetting

SECRET_KEYS = {"telegram_bot_token", "paystack_secret_key", "flutterwave_secret_key", "flutterwave_encryption_key",
               "bachs_secret_key", "bachs_webhook_secret"}
PLAIN_KEYS = {
    "telegram_audio_chat_id", "paystack_public_key", "flutterwave_public_key", "active_giving_gateway",
    "church_accounts", "hq_share_percent", "collects_own_giving", "transcription_enabled", "service_times", "resident_pastor_since",
    "public_about", "public_pastor", "public_whatsapp", "directions_url", "listed_publicly",
}
ALL_KEYS = SECRET_KEYS | PLAIN_KEYS


async def get_branch_settings(db: AsyncSession, branch_id: int) -> dict[str, str]:
    rows = (await db.execute(select(BranchSetting).where(BranchSetting.branch_id == branch_id))).scalars().all()
    out: dict[str, str] = {}
    for r in rows:
        if r.key in SECRET_KEYS:
            plain = _decrypt_strict(r.value or "")
            out[r.key] = plain if plain is not None else ""
        else:
            out[r.key] = r.value or ""
    return out


async def get_secret_status(db: AsyncSession, branch_id: int) -> dict[str, dict]:
    rows = {r.key: r.value or "" for r in (await db.execute(select(BranchSetting).where(BranchSetting.branch_id == branch_id))).scalars().all()}
    out = {}
    for key in SECRET_KEYS:
        raw = rows.get(key, "")
        plain = _decrypt_strict(raw)
        if not raw:
            out[key] = {"state": "unset", "hint": ""}
        elif plain is None:
            out[key] = {"state": "unreadable", "hint": ""}
        else:
            out[key] = {"state": "set" if plain else "unset", "hint": plain[-4:] if len(plain) >= 8 else ""}
    return out


async def update_branch_settings(db: AsyncSession, branch_id: int, values: dict[str, str]) -> None:
    """Upsert keys. Unknown keys are ignored; secret values are encrypted; an empty secret clears it."""
    existing = {r.key: r for r in (await db.execute(select(BranchSetting).where(BranchSetting.branch_id == branch_id))).scalars().all()}
    for key, value in values.items():
        if key not in ALL_KEYS:
            continue
        stored = _encrypt(value) if key in SECRET_KEYS and value else value
        if key in existing:
            existing[key].value = stored
        else:
            db.add(BranchSetting(branch_id=branch_id, key=key, value=stored))
    await db.flush()


def parse_accounts(raw: str | None) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []


def share_percent(raw: str | None) -> float:
    try:
        return min(100.0, max(0.0, float(raw))) if raw not in (None, "") else 0.0
    except ValueError:
        return 0.0


__all__ = [
    "SECRET_KEYS", "PLAIN_KEYS", "ALL_KEYS", "get_branch_settings", "get_secret_status", "update_branch_settings",
    "parse_accounts", "share_percent", "_ENC_PREFIX",
]
