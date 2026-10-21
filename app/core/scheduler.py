"""Small in-process job loop for the web app: event reminders, subscriber campaigns, analytics flush and housekeeping.

It runs inside the web process (no worker required) and is safe with several web workers: every job row is claimed with an
atomic ``UPDATE ... WHERE status = 'pending'`` before it is sent, so two processes never send the same email.
Disable with ``SWM_DISABLE_SCHEDULER=1`` (tests do).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update

from app.database import async_session

logger = logging.getLogger(__name__)

TICK_SECONDS = 20
CAMPAIGN_BATCH = 40
CAMPAIGN_PAUSE = 0.6          # seconds between emails: stays inside the provider's rate limit


async def _base_url() -> str:
    from app.core.settings_cache import get_settings

    async with async_session() as db:
        s = await get_settings(db)
    from app.config import settings as _cfg

    return (s.get("siteUrl") or _cfg.APP_URL or "").strip().rstrip("/")


async def process_reminders(now: datetime | None = None) -> int:
    """Send every reminder whose time has come. Returns how many were sent."""
    from app.core import mailer
    from app.core.site_content import clean_phone  # noqa: F401
    from app.models.engagement import EventReminder

    now = now or datetime.utcnow()
    sent = 0
    async with async_session() as db:
        ids = [r for (r,) in (await db.execute(select(EventReminder.id).where(EventReminder.status == "pending", EventReminder.remind_at <= now).limit(100))).all()]
    for rid in ids:
        async with async_session() as db:
            claimed = await db.execute(update(EventReminder).where(EventReminder.id == rid, EventReminder.status == "pending").values(status="sending"))
            await db.commit()
            if claimed.rowcount != 1:
                continue
            r = (await db.execute(select(EventReminder).where(EventReminder.id == rid))).scalar_one()
            ev = r.event
            email, name, token = r.email, r.name, r.token
        if ev is None or not ev.starts_at or ev.starts_at < _local_now() - timedelta(hours=1):
            outcome = "failed"          # the event moved into the past or was removed: nothing useful to say
        else:
            base = await _base_url()
            when = ev.starts_at.strftime("%A %d %B %Y, %I:%M %p").replace(" 0", " ")
            ok, _ = await mailer.send_notice(
                email, f"Reminder: {ev.title}", base_url=base or "https://", kind="reminder",
                heading=f"{ev.title} is coming up",
                paragraphs=[f"Hello{' ' + name if name else ''}, this is the reminder you asked for."],
                details=[("When", when)] + ([("Where", ev.location)] if ev.location else []),
                button=("See the details", f"{base}/events/{ev.id}") if base else None,
                footnote="We only send this one reminder. You will not be added to any list.",
            )
            outcome = "sent" if ok else "failed"
            sent += 1 if ok else 0
        async with async_session() as db:
            await db.execute(update(EventReminder).where(EventReminder.id == rid).values(status=outcome, sent_at=datetime.utcnow()))
            await db.commit()
    return sent


def _local_now() -> datetime:
    """Naive 'church time' (event times are stored as local wall-clock time; Nigeria is UTC+1 all year)."""
    return datetime.utcnow() + timedelta(hours=1)


async def process_campaigns() -> int:
    """Advance every campaign that is 'sending' by one batch."""
    from app.core import mailer
    from app.core.campaigns import render_campaign
    from app.models.engagement import EmailCampaign
    from app.models.newsletter_subscriber import NewsletterSubscriber

    total = 0
    async with async_session() as db:
        cids = [c for (c,) in (await db.execute(select(EmailCampaign.id).where(EmailCampaign.status == "sending"))).all()]
    if not cids:
        return 0
    cfg = await mailer.get_config()
    base = await _base_url()
    for cid in cids:
        async with async_session() as db:
            camp = (await db.execute(select(EmailCampaign).where(EmailCampaign.id == cid))).scalar_one_or_none()
            if camp is None or camp.status != "sending":
                continue
            subs = (await db.execute(select(NewsletterSubscriber).where(NewsletterSubscriber.is_active == True, NewsletterSubscriber.id > camp.cursor)  # noqa: E712
                                     .order_by(NewsletterSubscriber.id).limit(CAMPAIGN_BATCH))).scalars().all()
            snap = {"subject": camp.subject, "preheader": camp.preheader or "", "body_html": camp.body_html,
                    "button": (camp.button_label, camp.button_url) if camp.button_label and camp.button_url else None}
        if not subs:
            async with async_session() as db:
                await db.execute(update(EmailCampaign).where(EmailCampaign.id == cid).values(status="sent", finished_at=datetime.utcnow()))
                await db.commit()
            continue
        if cfg is None:
            async with async_session() as db:               # nothing can be sent: park it instead of failing every row
                await db.execute(update(EmailCampaign).where(EmailCampaign.id == cid).values(status="cancelled", finished_at=datetime.utcnow()))
                await db.commit()
            continue
        ok_n = bad_n = 0
        last = 0
        for sub in subs:
            html, text, headers = await render_campaign(snap, sub.token or "", base)
            ok, _ = await mailer.send_email(sub.email, snap["subject"], html, text, kind="campaign", headers=headers, cfg=cfg)
            ok_n += 1 if ok else 0
            bad_n += 0 if ok else 1
            last = sub.id
            await asyncio.sleep(CAMPAIGN_PAUSE)
        async with async_session() as db:
            c = (await db.execute(select(EmailCampaign).where(EmailCampaign.id == cid))).scalar_one()
            c.sent_count += ok_n
            c.failed_count += bad_n
            c.cursor = last
            await db.commit()
        total += ok_n
    return total


async def housekeeping() -> None:
    """Daily tidy-up: reminders after their event, old email log rows, old analytics counters."""
    from app.models.engagement import AnalyticsCounter, EmailLog, EventReminder
    from app.models.event import Event

    async with async_session() as db:
        cutoff = _local_now() - timedelta(days=3)
        old_events = select(Event.id).where(Event.starts_at < cutoff)
        await db.execute(delete(EventReminder).where(EventReminder.event_id.in_(old_events)))
        await db.execute(delete(EventReminder).where(EventReminder.status.in_(("sent", "failed")), EventReminder.sent_at < datetime.utcnow() - timedelta(days=3)))
        await db.execute(delete(EmailLog).where(EmailLog.created_at < datetime.utcnow() - timedelta(days=180)))
        await db.execute(delete(AnalyticsCounter).where(AnalyticsCounter.day < (datetime.utcnow() - timedelta(days=800)).date()))
        await db.commit()


async def _loop() -> None:
    from app.core import analytics

    last_housekeeping = datetime.min
    while True:
        try:
            await analytics.flush()
            await process_reminders()
            await process_campaigns()
            if datetime.utcnow() - last_housekeeping > timedelta(hours=24):
                await housekeeping()
                last_housekeeping = datetime.utcnow()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler tick failed")
        await asyncio.sleep(TICK_SECONDS)


def start() -> asyncio.Task | None:
    if os.environ.get("SWM_DISABLE_SCHEDULER") == "1":
        return None
    return asyncio.get_running_loop().create_task(_loop())


async def stop(task: asyncio.Task | None) -> None:
    from app.core import analytics

    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    try:
        await analytics.flush()
    except Exception:
        pass
