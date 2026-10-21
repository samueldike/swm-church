"""Public pages added for visitors: Plan a visit, branch directory, event details / calendar files / reminders,
unsubscribe, giving-by-transfer notices, prayer confirmation, sitemap.xml and robots.txt."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import mailer
from app.core import site_content as sc
from app.core.branch_settings import get_branch_settings
from app.core.settings_cache import get_settings
from app.core.template import render_template
from app.database import get_db
from app.routes import public as pub

logger = logging.getLogger(__name__)
router = APIRouter(tags=["public-extra"])
open_router = APIRouter(tags=["public-open"])       # token-authorised POSTs: mail clients cannot send a CSRF token

_ANY_TRUE = ("1", "true", "on", "yes")


def _local_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=1)          # church time (Nigeria, UTC+1 all year)


def _flag(v: str) -> bool:
    return (v or "").strip().lower() in _ANY_TRUE


def _maps_link(address: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote(address) if address else ""


def _lines(raw: str | None) -> list[str]:
    return [ln.strip() for ln in (raw or "").splitlines() if ln.strip()][:12]


async def branch_public(db: AsyncSession, b, site: dict) -> dict:
    """Everything the public may see about one branch (or HQ, which comes from the site-wide settings)."""
    if b.is_hq:
        address = (site.get("siteHeadquartersAddress") or "").strip()
        return {"id": b.id, "is_hq": True, "name": site.get("siteName") or b.name, "slug": b.slug, "address": address, "place": "Headquarters",
                "phone": sc.clean_phone(site.get("siteSupportPhone")), "email": (site.get("siteContactEmail") or "").strip(),
                "whatsapp": sc.whatsapp_chat_url(site.get("whatsappNumber")), "pastor": (site.get("pastorName") or "").strip(),
                "about": "", "services": [f"{s['day']} {s['time']} — {s['name']}".replace("  ", " ") for s in sc.get_services(site)],
                "directions": _maps_link(address), "listed": True, "state": ""}
    bs = await get_branch_settings(db, b.id)
    parts = [p for p in (b.address, b.city, b.state) if p and p.strip()]
    address = ", ".join(dict.fromkeys(p.strip() for p in parts))
    return {"id": b.id, "is_hq": False, "name": b.name, "slug": b.slug, "address": address,
            "place": ", ".join(p.strip() for p in (b.city, b.state) if p and p.strip()), "state": (b.state or "").strip(),
            "phone": sc.clean_phone(b.phone), "email": (b.email or "").strip(), "whatsapp": sc.whatsapp_chat_url(bs.get("public_whatsapp")),
            "pastor": (bs.get("public_pastor") or "").strip(), "about": (bs.get("public_about") or "").strip(),
            "services": _lines(bs.get("service_times")), "directions": sc.safe_https(bs.get("directions_url")) or _maps_link(address),
            "listed": bs.get("listed_publicly") == "1"}


async def listed_branches(db: AsyncSession, site: dict) -> list[dict]:
    """HQ first, then every active branch HQ has approved for the public directory."""
    from app.models.branch import Branch

    rows = (await db.execute(select(Branch).where(Branch.is_active == True).order_by(Branch.is_hq.desc(), Branch.name))).scalars().all()  # noqa: E712
    out = []
    for b in rows:
        d = await branch_public(db, b, site)
        if d["listed"]:
            out.append(d)
    return out


def _church_jsonld(request: Request, site: dict, name: str, address: str, phone: str, email: str, services: list[dict], url: str) -> str:
    base = pub._public_base_url(request)
    same_as = [u for u in (site.get("socialFacebook") or "https://www.facebook.com/swmnigeria", site.get("socialInstagram") or "https://www.instagram.com/swm_nigeria/",
                           site.get("socialX") or "", site.get("socialYouTube") or "") if sc.safe_https(u)]
    data = sc.church_jsonld(name=name, url=url, description=site.get("siteTagline") or "", address=address, phone=phone, email=email,
                            services=services, same_as=same_as, image=base + "/images/swm-logo.png")
    return sc.jsonld_script(data)


# ══════════════════════════════════════════════════════════════════════════
#  Plan your visit
# ══════════════════════════════════════════════════════════════════════════
@router.get("/visit", name="visit")
async def visit(request: Request, db: AsyncSession = Depends(get_db), branch: str = Query("")):
    site = await get_settings(db)
    services = sc.get_services(site)
    address = (site.get("siteHeadquartersAddress") or "").strip()
    base = pub._public_base_url(request)
    info = {k: (site.get(k) or "").strip() for k in sc.VISIT_KEYS}
    jsonld = _church_jsonld(request, site, site.get("siteName") or "Scent of Water Ministry", address, sc.clean_phone(site.get("siteSupportPhone")),
                            (site.get("siteContactEmail") or "").strip(), services, base + "/")
    return await render_template(request, "pages/visit.html", {
        "_db": db, "expect": sc.get_expect(site), "info": info, "directionsUrl": _maps_link(address), "listed": [b for b in await listed_branches(db, site) if not b["is_hq"]],
        "branches": await pub._public_branches(db), "selected_branch": branch, "today": _local_now().date().isoformat(), "jsonld": jsonld,
        "whatsappGreeting": sc.whatsapp_chat_url(site.get("whatsappNumber"), "Hello, I would like to visit on Sunday. Can you help me?"),
    })


@router.post("/visit", name="visit_store")
async def visit_store(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), email: str = Form(""), phone: str = Form(""), branch_id: str = Form(""),
    visit_date: str = Form(""), party: str = Form("1"), note: str = Form(""), subscribe: str = Form(""),
    website: str = Form(""),                                   # honeypot: real visitors never see or fill it
):
    from app.models.contact_inquiry import ContactInquiry

    if website.strip():
        return pub._redirect(request, "/visit/thanks")
    if pub._rate_limited(request, "visit"):
        return pub._redirect(request, "/visit", "You have sent several plans in a short time. Please wait a few minutes and try again.", "error")
    name, email = name.strip()[:255], email.strip()[:255]
    if not name or not mailer.valid_email(email):
        return pub._redirect(request, "/visit#plan", "Please give your name and a valid email address so we can welcome you.", "error")
    when = None
    if visit_date.strip():
        try:
            when = date.fromisoformat(visit_date.strip())
        except ValueError:
            when = None
        if when and (when < _local_now().date() or when > _local_now().date() + timedelta(days=366)):
            return pub._redirect(request, "/visit#plan", "Please choose a date from today onwards.", "error")
    try:
        people = min(max(int(party or 1), 1), 50)
    except ValueError:
        people = 1
    church_id = await pub._pick_branch_id(db, branch_id)
    from app.models.branch import Branch

    church = (await db.execute(select(Branch).where(Branch.id == church_id))).scalar_one()
    site = await get_settings(db)
    text = f"Planning to visit{' on ' + when.strftime('%A %d %B %Y') if when else ''}. Coming with {people} {'person' if people == 1 else 'people'} in total."
    if note.strip():
        text += "\n\n" + note.strip()[:2000]
    inquiry = ContactInquiry(name=name, email=email, phone=phone.strip()[:50] or None, subject="Plan a visit", message=text, branch_id=church_id, kind="visit")
    db.add(inquiry)
    await db.flush()
    subscribed = False
    if _flag(subscribe):
        subscribed = await subscribe_email(db, email, "visit")
    d = await branch_public(db, church, site)
    base = pub._public_base_url(request)
    when_txt = when.strftime("%A %d %B %Y") if when else "any service"
    details = [("Where", d["name"] + (f" — {d['address']}" if d["address"] else "")), ("Planned", when_txt + f" · {people} {'person' if people == 1 else 'people'}")]
    if d["services"]:
        details.append(("Services", " · ".join(d["services"][:4])))
    mailer.fire(mailer.send_notice(
        email, f"We look forward to seeing you — {d['name']}", base_url=base, kind="visit", heading=f"We look forward to seeing you, {name.split()[0]}",
        paragraphs=["Thank you for planning a visit. Someone from our welcome team will be glad to greet you when you arrive.",
                    "If your plans change, or you would like us to answer a question first, just reply to this email."],
        details=details, button=("Get directions", d["directions"]) if d["directions"] else None, reply_to=d["email"] or (site.get("siteContactEmail") or None)))
    mailer.fire(mailer.notify_staff(church_id, "Someone is planning to visit", base_url=base, kind="staff", heading="A visitor is planning to visit",
                                    paragraphs=[f"A new visit plan for {d['name']} is waiting in Inquiries. Please welcome them."], link_path="/admin/inquiries"))
    request.session["visit_thanks"] = {"name": name.split()[0], "branch": d["name"], "when": when_txt, "subscribed": subscribed, "emailed": True}
    return pub._redirect(request, "/visit/thanks")


@router.get("/visit/thanks", name="visit_thanks")
async def visit_thanks(request: Request, db: AsyncSession = Depends(get_db)):
    data = request.session.pop("visit_thanks", None)
    if not data:
        return pub._redirect(request, "/visit")
    site = await get_settings(db)
    return await render_template(request, "pages/visit-thanks.html", {"_db": db, "done": data, "robots": "noindex, follow"})


async def subscribe_email(db: AsyncSession, email: str, source: str) -> bool:
    """Add (or re-activate) a newsletter subscriber. Returns True when a *new* subscription was made."""
    from app.models.newsletter_subscriber import NewsletterSubscriber

    email = email.strip().lower()
    row = (await db.execute(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email))).scalar_one_or_none()
    if row is None:
        db.add(NewsletterSubscriber(email=email, is_active=True, token=secrets.token_urlsafe(24), source=source))
        await db.flush()
        return True
    if not row.is_active:
        row.is_active, row.unsubscribed_at = True, None
        if not row.token:
            row.token = secrets.token_urlsafe(24)
        return True
    if not row.token:
        row.token = secrets.token_urlsafe(24)
    return False


# ══════════════════════════════════════════════════════════════════════════
#  Find a branch
# ══════════════════════════════════════════════════════════════════════════
@router.get("/branches", name="branches")
async def branches(request: Request, db: AsyncSession = Depends(get_db), q: str = Query(""), state: str = Query("")):
    site = await get_settings(db)
    allb = await listed_branches(db, site)
    hq = next((b for b in allb if b["is_hq"]), None)
    others = [b for b in allb if not b["is_hq"]]
    states = sorted({b["state"] for b in others if b["state"]})
    needle = q.strip().lower()
    shown = [b for b in others if (not state or b["state"] == state) and (not needle or needle in " ".join((b["name"], b["place"], b["address"], b["pastor"])).lower())]
    base = pub._public_base_url(request)
    ld = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "url": f"{base}/branches/{b['slug']}", "name": b["name"]} for i, b in enumerate(others)]}
    return await render_template(request, "pages/branches.html", {
        "_db": db, "hq": hq, "shown": shown, "states": states, "q": q, "state": state, "total": len(others), "jsonld": sc.jsonld_script(ld) if others else ""})


@router.get("/branches/{slug}", name="branch_detail")
async def branch_detail(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.branch import Branch

    site = await get_settings(db)
    b = (await db.execute(select(Branch).where(Branch.slug == slug, Branch.is_active == True))).scalar_one_or_none()  # noqa: E712
    d = await branch_public(db, b, site) if b else None
    if not d or not d["listed"]:
        return HTMLResponse("Branch not found", status_code=404)
    if d["is_hq"]:
        return pub._redirect(request, "/visit")
    base = pub._public_base_url(request)
    services_dicts = []
    for ln in d["services"]:                                    # "Sunday 8:00 am — Main service": read the day and time if it is that simple
        m = re.match(r"^\s*(\w+day)\s+(.+?)\s*[—–-]\s*(.+)$", ln)
        if m:
            services_dicts.append({"day": m.group(1), "time": m.group(2), "name": m.group(3)})
    return await render_template(request, "pages/branch.html", {
        "_db": db, "b": d, "jsonld": _church_jsonld(request, site, d["name"], d["address"], d["phone"], d["email"], services_dicts, f"{base}/branches/{d['slug']}"),
        "pageMetaDescription": f"{d['name']} — {d['place']}"})


# ══════════════════════════════════════════════════════════════════════════
#  Events: detail page, calendar files, reminders
# ══════════════════════════════════════════════════════════════════════════
async def _published_event(db: AsyncSession, event_id: int):
    from app.models.event import Event

    return (await db.execute(select(Event).where(Event.id == event_id, Event.is_published == True))).scalar_one_or_none()  # noqa: E712


@router.get("/events/{event_id}", name="event_detail")
async def event_detail(event_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    from app.core import ics

    ev = await _published_event(db, event_id)
    if ev is None:
        return HTMLResponse("Event not found", status_code=404)
    base = pub._public_base_url(request)
    ld = {"@context": "https://schema.org", "@type": "Event", "name": ev.title, "startDate": ev.starts_at.strftime("%Y-%m-%dT%H:%M:00+01:00"),
          "endDate": ics.event_ends(ev).strftime("%Y-%m-%dT%H:%M:00+01:00"), "eventStatus": "https://schema.org/EventScheduled",
          "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode", "url": f"{base}/events/{ev.id}",
          "description": (ev.description or ev.title)[:500], "organizer": {"@type": "Organization", "name": "Scent of Water Ministry", "url": base}}
    if ev.location:
        ld["location"] = {"@type": "Place", "name": ev.location, "address": ev.location}
    if ev.cover_image:
        ld["image"] = base + (ev.cover_image if ev.cover_image.startswith("/") else "/" + ev.cover_image)
    started = ev.starts_at < _local_now()
    return await render_template(request, "pages/event.html", {
        "_db": db, "ev": ev, "googleUrl": ics.google_calendar_url(ev), "started": started, "jsonld": sc.jsonld_script(ld),
        "canRemind": ev.starts_at > _local_now() + timedelta(hours=3), "metaType": "article",
        "metaDescription": (ev.description or f"{ev.title} — {ev.starts_at.strftime('%A %d %B %Y')}")[:200], "metaTitle": ev.title,
        "metaImage": ev.cover_image or None})


def _ics_response(body: str, filename: str) -> Response:
    return Response(content=body, media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "public, max-age=900"})


@router.get("/events/{event_id}/calendar.ics", name="event_ics")
async def event_ics(event_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    from app.core import ics

    ev = await _published_event(db, event_id)
    if ev is None:
        return HTMLResponse("Event not found", status_code=404)
    site = await get_settings(db)
    host = request.url.hostname or "localhost"
    slug = re.sub(r"[^a-z0-9]+", "-", ev.title.lower()).strip("-")[:50] or "event"
    return _ics_response(ics.calendar(site.get("siteName") or "Scent of Water Ministry", ics.vevent(ev, host, f"{pub._public_base_url(request)}/events/{ev.id}")), f"{slug}.ics")


@router.get("/calendar.ics", name="calendar_ics")
async def calendar_ics(request: Request, db: AsyncSession = Depends(get_db)):
    """Subscribe-able feed of every published event from a month ago onwards."""
    from app.core import ics
    from app.models.event import Event

    site = await get_settings(db)
    events = list((await db.execute(select(Event).where(Event.is_published == True, Event.starts_at >= _local_now() - timedelta(days=31)).order_by(Event.starts_at.asc()).limit(500))).scalars().all())  # noqa: E712
    return _ics_response(ics.events_ics(events, site.get("siteName") or "Scent of Water Ministry", request.url.hostname or "localhost", pub._public_base_url(request)), "events.ics")


@router.get("/services.ics", name="services_ics")
async def services_ics(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core import ics

    site = await get_settings(db)
    return _ics_response(ics.services_ics(sc.get_services(site), site.get("siteName") or "Scent of Water Ministry", request.url.hostname or "localhost", _local_now()), "weekly-services.ics")


@router.post("/events/{event_id}/remind", name="event_remind")
async def event_remind(event_id: int, request: Request, db: AsyncSession = Depends(get_db),
                       email: str = Form(""), name: str = Form(""), website: str = Form("")):
    from app.models.engagement import EventReminder

    back = f"/events/{event_id}"
    ev = await _published_event(db, event_id)
    if ev is None:
        return pub._redirect(request, "/events", "That event is no longer listed.", "error")
    if website.strip():
        return pub._redirect(request, back, "Reminder set.")
    if pub._rate_limited(request, "remind", limit=10):
        return pub._redirect(request, back, "Too many attempts. Please try again in a few minutes.", "error")
    email = email.strip().lower()
    if not mailer.valid_email(email):
        return pub._redirect(request, back + "#remind", "Please enter a valid email address.", "error")
    now_local = _local_now()
    if ev.starts_at <= now_local + timedelta(hours=3):
        return pub._redirect(request, back, "This event starts too soon to set a reminder.", "error")
    remind_local = max(ev.starts_at - timedelta(hours=24), now_local + timedelta(minutes=2))
    remind_utc = remind_local - timedelta(hours=1)
    existing = (await db.execute(select(EventReminder).where(EventReminder.event_id == ev.id, EventReminder.email == email))).scalar_one_or_none()
    if existing and existing.status == "pending":
        return pub._redirect(request, back, "You already have a reminder set for this event.", "info")
    if existing:
        await db.delete(existing)
        await db.flush()
    db.add(EventReminder(event_id=ev.id, email=email, name=name.strip()[:255] or None, token=secrets.token_urlsafe(24), remind_at=remind_utc))
    await db.flush()
    when = remind_local.strftime("%A %d %B, %I:%M %p").replace(" 0", " ")
    base = pub._public_base_url(request)
    mailer.fire(mailer.send_notice(email, f"Reminder set: {ev.title}", base_url=base, kind="reminder_confirm", heading="Your reminder is set",
                                   paragraphs=[f"We will email you once about “{ev.title}”, on {when}.", "That is the only message you will get — you are not added to any list."],
                                   details=[("Event", ev.title), ("When", ev.starts_at.strftime("%A %d %B %Y, %I:%M %p").replace(" 0", " "))] + ([("Where", ev.location)] if ev.location else []),
                                   button=("View the event", f"{base}/events/{ev.id}")))
    return pub._redirect(request, back, f"Done — we will email you once, on {when}.")


# ══════════════════════════════════════════════════════════════════════════
#  Unsubscribe (token is the credential; POST is exempt from CSRF so mail clients' one-click works)
# ══════════════════════════════════════════════════════════════════════════
async def _subscriber_by_token(db: AsyncSession, token: str):
    from app.models.newsletter_subscriber import NewsletterSubscriber

    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        return None
    return (await db.execute(select(NewsletterSubscriber).where(NewsletterSubscriber.token == token))).scalar_one_or_none()


@router.get("/unsubscribe/{token}", name="unsubscribe")
async def unsubscribe_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    sub = await _subscriber_by_token(db, token)
    return await render_template(request, "pages/unsubscribe.html", {"_db": db, "sub": sub, "token": token, "done": False, "robots": "noindex, nofollow"})


@open_router.post("/unsubscribe/{token}", name="unsubscribe_confirm")
async def unsubscribe_confirm(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    sub = await _subscriber_by_token(db, token)
    if sub is not None and sub.is_active:
        sub.is_active, sub.unsubscribed_at = False, datetime.utcnow()
        await db.flush()
    return await render_template(request, "pages/unsubscribe.html", {"_db": db, "sub": sub, "token": token, "done": True, "robots": "noindex, nofollow"})


@router.post("/resubscribe/{token}", name="resubscribe")
async def resubscribe(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    sub = await _subscriber_by_token(db, token)
    if sub is not None and not sub.is_active:
        sub.is_active, sub.unsubscribed_at = True, None
        await db.flush()
    return pub._redirect(request, "/", "Welcome back — you are subscribed again." if sub else None)


# ══════════════════════════════════════════════════════════════════════════
#  Giving by bank transfer: "I have sent a transfer"
# ══════════════════════════════════════════════════════════════════════════
@router.post("/give/transfer-notice", name="give_transfer_notice")
async def give_transfer_notice(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), email: str = Form(""), amount: str = Form(""), purpose: str = Form(""), branch_id: str = Form(""),
    bank_ref: str = Form(""), sent_on: str = Form(""), website: str = Form(""),
):
    from decimal import Decimal, InvalidOperation

    from app.models.giving import Giving

    if website.strip():
        return pub._redirect(request, "/give", "Thank you — we have noted your transfer.")
    if pub._rate_limited(request, "transfer", limit=6):
        return pub._redirect(request, "/give#transfer", "Too many attempts. Please try again in a few minutes.", "error")
    name, email = name.strip()[:255], email.strip()[:255]
    try:
        value = Decimal(re.sub(r"[,\s₦]", "", amount))
        if value <= 0 or value > Decimal("1000000000"):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return pub._redirect(request, "/give#transfer", "Please enter the amount you sent.", "error")
    if not name or not mailer.valid_email(email):
        return pub._redirect(request, "/give#transfer", "Please give your name and a valid email address so we can send your receipt.", "error")
    site = await get_settings(db)
    church_id = await pub._pick_branch_id(db, branch_id)
    from app.core.hq_branch import get_hq_branch_id

    hq_id = await get_hq_branch_id(db)
    model = (site.get("giving_model") or "central").strip()
    owner = "branch" if (model == "branch" and church_id != hq_id) else "hq"
    when = None
    try:
        when = date.fromisoformat(sent_on.strip()) if sent_on.strip() else None
    except ValueError:
        when = None
    note = "Reported by the giver after a bank transfer; awaiting confirmation against the statement."
    if when:
        note += f" Sent on {when.isoformat()}."
    if bank_ref.strip():
        note += f" Bank reference: {bank_ref.strip()[:120]}."
    g = Giving(reference="TRF-" + secrets.token_hex(6).upper(), email=email, full_name=name, amount=value, giving_type=(purpose.strip() or "Offering")[:50],
               payment_gateway="transfer", method="transfer", status="pending", branch_id=church_id if church_id != hq_id else None, gateway_owner=owner, note=note)
    db.add(g)
    await db.flush()
    base = pub._public_base_url(request)
    mailer.fire(mailer.send_notice(email, "We have noted your transfer", base_url=base, kind="giving", heading="Thank you for giving",
                                   paragraphs=["We have noted the transfer you told us about. Our finance team will confirm it against the bank statement, and you will not need to do anything more.",
                                               "Your gift is a seed sown in God’s house — thank you for your generosity."],
                                   details=[("Amount", f"₦{value:,.2f}"), ("For", g.giving_type), ("Reference", g.reference)] + ([("Bank reference", bank_ref.strip()[:120])] if bank_ref.strip() else []),
                                   footnote="If any of this is wrong, reply to this email and tell us."))
    return pub._redirect(request, "/give", "Thank you — we have noted your transfer and emailed you a confirmation. Finance will confirm it against our statement.")


# ══════════════════════════════════════════════════════════════════════════
#  Prayer confirmation
# ══════════════════════════════════════════════════════════════════════════
@router.get("/prayer/thanks", name="prayer_thanks")
async def prayer_thanks(request: Request, db: AsyncSession = Depends(get_db)):
    data = request.session.pop("prayer_thanks", None)
    if not data:
        return pub._redirect(request, "/prayer")
    return await render_template(request, "pages/prayer-thanks.html", {"_db": db, "done": data, "robots": "noindex, follow"})


# ══════════════════════════════════════════════════════════════════════════
#  robots.txt and sitemap.xml
# ══════════════════════════════════════════════════════════════════════════
@router.get("/robots.txt", name="robots")
async def robots(request: Request):
    base = pub._public_base_url(request)
    body = "\n".join(["User-agent: *", "Disallow: /admin", "Disallow: /api", "Disallow: /give/", "Disallow: /unsubscribe/", "Disallow: /visit/thanks", "Disallow: /prayer/thanks",
                      "Allow: /", "", f"Sitemap: {base}/sitemap.xml", ""])
    return PlainTextResponse(body)


@router.get("/sitemap.xml", name="sitemap_xml")
async def sitemap_xml(request: Request, db: AsyncSession = Depends(get_db)):
    from html import escape

    from app.models.event import Event
    from app.models.sermon import Sermon
    from app.models.story import Story

    base = pub._public_base_url(request)
    static = ["/", "/about", "/our-pastor", "/departments", "/messages", "/events", "/calendar", "/branches", "/visit", "/contact", "/prayer", "/give", "/get-involved", "/stories", "/casom", "/watch",
              "/privacy-policy", "/terms-of-service"]
    urls: list[tuple[str, str | None]] = [(p, None) for p in static]
    site = await get_settings(db)
    urls += [(f"/branches/{b['slug']}", None) for b in await listed_branches(db, site) if not b["is_hq"]]
    for s in (await db.execute(select(Sermon.slug, Sermon.updated_at).where(Sermon.status == "published", await pub._hq_only(db)).order_by(Sermon.published_at.desc()).limit(2000))).all():
        urls.append((f"/messages/{s[0]}", s[1].strftime("%Y-%m-%d") if s[1] else None))
    for s in (await db.execute(select(Story.slug, Story.updated_at).where(Story.status == "published").limit(1000))).all():
        urls.append((f"/stories/{s[0]}", s[1].strftime("%Y-%m-%d") if s[1] else None))
    for e in (await db.execute(select(Event.id, Event.updated_at).where(Event.is_published == True, Event.starts_at >= _local_now() - timedelta(days=1)).limit(500))).all():  # noqa: E712
        urls.append((f"/events/{e[0]}", e[1].strftime("%Y-%m-%d") if e[1] else None))
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, last in urls:
        xml.append(f"<url><loc>{escape(base + path)}</loc>" + (f"<lastmod>{last}</lastmod>" if last else "") + "</url>")
    xml.append("</urlset>")
    return Response(content="\n".join(xml), media_type="application/xml")
