"""Confirming online gifts with the payment gateway (DATA-01).

Every trigger uses the same path: the donor's return to /give/callback, the gateway's webhook, the scheduled
re-check of pending gifts and the "Re-check with gateway" button on Donations. The gateway's verify API is the
only source of truth: a webhook body or callback query string only says *which* gift to check.

- A gateway that cannot be reached ("error") leaves the gift as it was, never "failed": the money may have been taken.
- "completed" is claimed with a conditional UPDATE, so two simultaneous confirmations send one receipt (DATA-15).
- The session is committed before calling the gateway, so no transaction is held open during the HTTP call (DATA-17).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

RECHECK_AFTER = timedelta(minutes=10)     # give the donor time to finish paying before the first re-check
RECHECK_EVERY = timedelta(minutes=30)
ABANDON_AFTER = timedelta(hours=72)       # still unpaid by then: the donor left the checkout
RECHECK_WINDOW = timedelta(days=7)        # the scheduler leaves older gifts alone; the admin button still works


async def gateway_secret(db: AsyncSession, giving) -> str:
    """The secret key of the account that took this gift (a branch's own, or HQ's)."""
    from app.config import settings as app_settings
    from app.core.settings_cache import get_settings

    if giving.gateway_owner == "branch" and giving.branch_id:
        from app.core.branch_settings import get_branch_settings

        own = (await get_branch_settings(db, giving.branch_id)).get(f"{giving.payment_gateway}_secret_key", "")
        if own:
            return own
    site = await get_settings(db)
    if giving.payment_gateway == "flutterwave":
        return site.get("flutterwave_secret_key") or app_settings.FLUTTERWAVE_SECRET_KEY
    return site.get("paystack_secret_key") or app_settings.PAYSTACK_SECRET_KEY


async def _verify(giving, secret: str, transaction_id: str) -> dict:
    from app.core import payment

    if giving.payment_gateway == "flutterwave":
        return await payment.verify_flutterwave(transaction_id=transaction_id, tx_ref=giving.reference, secret_key=secret)
    return await payment.verify_paystack(reference=giving.reference, secret_key=secret)


def amount_matches(giving, verification: dict) -> bool:
    """The provider must have taken the gift's amount IN THE GIFT'S CURRENCY ("paid" alone is not enough)."""
    from app.core import geo

    units = 10 ** geo.minor_units(giving.currency)
    expected = round(float(giving.amount) * units)
    if "amount_minor" in verification:
        taken = int(verification["amount_minor"])
    else:
        taken = round(float(verification.get("amount", 0)) * units)
    same_currency = (verification.get("currency") or giving.currency or "").upper() == (giving.currency or "NGN").upper()
    return same_currency and taken >= expected


async def reconcile(db: AsyncSession, giving, *, transaction_id: str = "", base_url: str = "") -> str:
    """Ask the gateway about one online gift and record the answer.

    Returns "completed", "mismatch", "pending", "abandoned", "failed" or "error" (gateway unreachable; nothing changed).
    """
    from app.models.giving import Giving

    if giving.status == "completed":
        return "completed"
    if giving.method != "online" or giving.payment_gateway not in ("paystack", "flutterwave"):
        return giving.status

    secret = await gateway_secret(db, giving)
    gift_id = giving.id
    await db.commit()
    try:
        verification = await _verify(giving, secret, transaction_id)
    except Exception:
        logger.exception("Payment verification failed for %s", giving.reference)
        verification = {"state": "error"}
    state = verification.get("state") or ("paid" if verification.get("success") else "failed")
    now = datetime.utcnow()

    if state == "error":
        return "error"
    if state == "paid" and not amount_matches(giving, verification):
        logger.warning("Giving %s amount mismatch: expected %s %s", giving.reference, giving.amount, giving.currency)
        outcome = "mismatch"
    elif state == "paid":
        claimed = await db.execute(
            update(Giving).where(Giving.id == gift_id, Giving.status != "completed")
            .values(status="completed", paid_at=now).execution_options(synchronize_session=False))
        await db.commit()
        await db.refresh(giving)
        if claimed.rowcount == 1:
            await send_receipt(db, giving, base_url)
        return "completed"
    elif state == "pending":
        outcome = "abandoned" if giving.created_at and now - giving.created_at > ABANDON_AFTER else "pending"
    else:
        outcome = "failed"
    await db.execute(update(Giving).where(Giving.id == gift_id, Giving.status != "completed")
                     .values(status=outcome).execution_options(synchronize_session=False))
    await db.commit()
    await db.refresh(giving)
    return giving.status


async def send_receipt(db: AsyncSession, giving, base_url: str) -> bool:
    """Email the giver a receipt in the background. False when nothing will be sent (no address or email not set up)."""
    from app.core import geo, mailer
    from app.core.settings_cache import get_settings

    if not mailer.valid_email(giving.email) or await mailer.get_config() is None:
        return False
    church = (await get_settings(db)).get("siteName") or "Scent of Water Ministry"
    if giving.branch_id:
        from app.models.branch import Branch

        b = (await db.execute(select(Branch).where(Branch.id == giving.branch_id))).scalar_one_or_none()
        church = b.name if b else church
    first = (giving.full_name or "friend").split()[0] if not giving.is_anonymous or giving.full_name else "friend"
    mailer.fire(mailer.send_notice(
        giving.email, f"Your gift receipt — {giving.reference}", base_url=base_url or "https://", kind="receipt",
        heading="Thank you for your gift",
        paragraphs=[f"Dear {first}, your gift has been received. God bless you for your generosity.",
                    "Please keep this email as your record of the gift."],
        details=[("Amount", geo.format_money(giving.amount, giving.currency)), ("For", giving.giving_type.replace("_", " ").title()),
                 ("Given to", church), ("Reference", giving.reference), ("Date", (giving.paid_at or datetime.utcnow()).strftime("%d %B %Y"))],
        footnote="If anything on this receipt looks wrong, reply to this email with your reference and we will look into it."))
    return True


async def due_for_recheck(db: AsyncSession, now: datetime | None = None, limit: int = 20) -> list[int]:
    """Pending online gifts the scheduler should ask about now."""
    from app.models.giving import Giving

    now = now or datetime.utcnow()
    q = (select(Giving.id).where(
        Giving.method == "online", Giving.status == "pending", Giving.payment_gateway.in_(("paystack", "flutterwave")),
        Giving.created_at <= now - RECHECK_AFTER, Giving.created_at >= now - RECHECK_WINDOW,
        (Giving.checked_at.is_(None)) | (Giving.checked_at <= now - RECHECK_EVERY),
    ).order_by(Giving.created_at).limit(limit))
    return [i for (i,) in (await db.execute(q)).all()]


async def recheck_pending(base_url: str = "") -> int:
    """Scheduler job: re-verify pending gifts whose donor never came back. Returns how many were confirmed."""
    from app.database import async_session
    from app.models.giving import Giving

    async with async_session() as db:
        ids = await due_for_recheck(db)
    if ids and not base_url:
        from app.core.scheduler import _base_url

        base_url = await _base_url()
    confirmed = 0
    for gid in ids:
        async with async_session() as db:
            now = datetime.utcnow()
            stamp = await db.execute(update(Giving).where(        # claim: other workers skip it for RECHECK_EVERY
                Giving.id == gid, Giving.status == "pending",
                (Giving.checked_at.is_(None)) | (Giving.checked_at <= now - RECHECK_EVERY)).values(checked_at=now))
            await db.commit()
            if stamp.rowcount != 1:
                continue
            giving = (await db.execute(select(Giving).where(Giving.id == gid))).scalar_one()
            confirmed += await reconcile(db, giving, base_url=base_url) == "completed"
    return confirmed
