"""A plain-language checklist of what the church still has to fill in before the public website is complete and honest."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import site_content as sc


async def site_readiness(db: AsyncSession, site: dict[str, str], app_url: str = "") -> list[dict[str, Any]]:
    """Each item: ``{"key", "label", "ok", "detail"}``. ``ok`` False means the church should do something."""
    from app.models.engagement import EmailLog

    items: list[dict[str, Any]] = []

    def add(key: str, label: str, ok: bool, detail: str) -> None:
        items.append({"key": key, "label": label, "ok": bool(ok), "detail": detail})

    raw_phone = (site.get("siteSupportPhone") or site.get("support_phone") or "").strip()
    add("phone", "A real contact phone number", bool(sc.clean_phone(raw_phone)),
        "Visitors see it on the contact, visit and prayer pages." if sc.clean_phone(raw_phone) else
        ("The saved number looks like a placeholder, so it is hidden from the site. Enter the church's real number." if raw_phone else "No phone number is saved. Add the church's number under General site."))
    add("email", "A public contact email", bool((site.get("siteContactEmail") or "").strip()), "Shown in the footer, on receipts and in the privacy policy.")
    add("address", "Headquarters address", bool((site.get("siteHeadquartersAddress") or "").strip()), "Used for directions, the map and search-engine listings.")
    add("services", "Service times confirmed", bool((site.get("serviceTimes") or "").strip()),
        "The site is showing the times it was launched with. Open Website content and confirm or correct them." if not (site.get("serviceTimes") or "").strip() else "Saved.")
    chan = sc.channels(site)
    add("whatsapp", "WhatsApp or Telegram link", bool(chan["whatsappChat"] or chan["whatsappChannel"] or chan["telegramJoin"]),
        "Add a WhatsApp number, WhatsApp channel or Telegram link so people can reach you where they already are.")
    accounts = (site.get("church_accounts") or "").strip()
    add("bank", "Bank account for transfers", accounts not in ("", "[]", "null"), "Add at least one account under Church accounts so donors can give by transfer.")
    key, frm = (site.get("resend_api_key") or "").strip(), (site.get("resend_from_email") or "").strip()
    add("email_delivery", "Email delivery connected", bool(key and frm), "Add the Resend API key and a verified sender address so receipts, confirmations and reminders are actually sent.")
    if key and frm:
        failed = (await db.execute(select(func.count()).select_from(EmailLog).where(EmailLog.status == "failed"))).scalar() or 0
        sent = (await db.execute(select(func.count()).select_from(EmailLog).where(EmailLog.status == "sent"))).scalar() or 0
        add("email_health", "Emails are being accepted by the provider", not (failed and not sent),
            "Every recent attempt failed. Send a test email from Email delivery to see the provider's reason (usually the sender domain is not verified)." if failed and not sent else "No problems seen.")
    url = (site.get("siteUrl") or app_url or "").strip()
    add("site_url", "Public web address set", bool(url) and "localhost" not in url and "127.0.0.1" not in url,
        "Links in emails (reminders, campaigns) use this address. Set it under Website content.")
    add("privacy", "Privacy policy and terms reviewed", bool((site.get("privacy_policy") or "").strip() and (site.get("terms_of_service") or "").strip()),
        "The standard texts are published. Have your adviser review them, then save your own version under Legal pages.")
    add("prayer", "Prayer response time stated", bool((site.get("prayerResponseTime") or "").strip()), "Visitors are told to expect a reply within this time. The default is three working days — set what your team can really keep.")
    return items
