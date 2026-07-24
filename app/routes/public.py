from __future__ import annotations

import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.pagination import paginate
from app.core.template import render_template

router = APIRouter(tags=["public"])


def _redirect(request: Request, url: str, flash_msg: str | None = None, flash_type: str = "success") -> RedirectResponse:
    if flash_msg:
        request.session["flash_messages"] = [{"type": flash_type, "message": flash_msg}]
        request.session[flash_type] = flash_msg
    return RedirectResponse(url=url, status_code=303)


def _paginate_url(request: Request, page: int, base_path: str) -> str:
    return f"{base_path}?page={page}"


@router.get("/", name="home")
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.site_settings import SiteSetting
    from app.models.slider import Slider
    from app.models.sermon import Sermon

    sliders = []
    try:
        result = await db.execute(select(Slider).where(Slider.is_active == True).order_by(Slider.sort_order.asc()))
        sliders = list(result.scalars().all())
    except Exception:
        pass

    settings = {}
    try:
        result = await db.execute(select(SiteSetting))
        settings = {s.key: s.value for s in result.scalars().all()}
    except Exception:
        pass

    latest_sermons = []
    try:
        result = await db.execute(
            select(Sermon)
            .where(Sermon.status == "published")
            .order_by(Sermon.published_at.desc())
            .limit(3)
        )
        latest_sermons = list(result.scalars().all())
    except Exception:
        pass

    return await render_template(request, "pages/home.html", {
        "_db": db,
        "sliders": sliders,
        "settings": settings,
        "latest_sermons": latest_sermons,
    })


@router.get("/about", name="about")
async def about(request: Request):
    return await render_template(request, "pages/about.html")


@router.get("/departments", name="departments")
async def departments(request: Request):
    return await render_template(request, "pages/departments.html")


@router.get("/messages", name="messages")
async def messages_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    search: str = Query("", alias="search"),
    page: int = Query(1, ge=1),
):
    from app.models.sermon import Sermon

    query = select(Sermon).where(Sermon.status == "published").options(selectinload(Sermon.branch))

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Sermon.title.ilike(search_pattern),
                Sermon.preacher.ilike(search_pattern),
                Sermon.description.ilike(search_pattern),
            )
        )

    query = query.order_by(Sermon.published_at.desc())

    pagination = await paginate(db, query, page=page, per_page=12)
    sermons = pagination["items"]

    featured_sermon = None
    try:
        feat_result = await db.execute(
            select(Sermon)
            .where(Sermon.status == "published", Sermon.is_featured == True)
            .order_by(Sermon.published_at.desc())
            .limit(1)
        )
        featured_sermon = feat_result.scalar_one_or_none()
    except Exception:
        pass

    if featured_sermon is None and sermons:
        featured_sermon = sermons[0]

    for i in range(len(sermons)):
        if featured_sermon and sermons[i].id == featured_sermon.id:
            sermons.pop(i)
            break

    base_path = "/messages"
    pagination["prev_url"] = _paginate_url(request, pagination["page"] - 1, base_path) if pagination["has_prev"] else None
    pagination["next_url"] = _paginate_url(request, pagination["page"] + 1, base_path) if pagination["has_next"] else None

    return await render_template(request, "pages/messages.html", {
        "_db": db,
        "sermons": sermons,
        "featured_sermon": featured_sermon,
        "search": search,
        "pagination": pagination,
    })


@router.get("/messages/{slug}", name="messages_show")
async def sermon_detail(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.sermon import Sermon

    result = await db.execute(
        select(Sermon).where(Sermon.slug == slug, Sermon.status == "published")
    )
    sermon = result.scalar_one_or_none()
    if not sermon:
        return HTMLResponse("Sermon not found", status_code=404)

    related = []
    try:
        result = await db.execute(
            select(Sermon)
            .where(Sermon.status == "published", Sermon.id != sermon.id)
            .order_by(Sermon.published_at.desc())
            .limit(3)
        )
        related = list(result.scalars().all())
    except Exception:
        pass

    return await render_template(request, "pages/messages_show.html", {
        "_db": db,
        "sermon": sermon,
        "related": related,
    })


@router.get("/messages/{sermon_id}/stream", name="messages_stream")
async def stream_sermon(sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.sermon import Sermon
    from app.core.telegram import TelegramDistributor

    result = await db.execute(
        select(Sermon).where(Sermon.id == sermon_id, Sermon.status == "published")
    )
    sermon = result.scalar_one_or_none()

    if not sermon or not sermon.telegram_file_id:
        return HTMLResponse("Audio not found", status_code=404)

    tg = TelegramDistributor()
    file_url = await tg.file_download_url(sermon.telegram_file_id)
    if not file_url:
        return HTMLResponse("Unable to retrieve audio", status_code=502)

    import httpx

    async def audio_stream():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("GET", file_url) as upstream:
                async for chunk in upstream.aiter_bytes(chunk_size=65536):
                    yield chunk

    headers = {}
    range_header = request.headers.get("range")
    if range_header:
        headers["Accept-Ranges"] = "bytes"
        headers["Content-Range"] = f"bytes */-1"

    return StreamingResponse(
        audio_stream(),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'inline; filename="{sermon.title or "sermon"}.mp3"',
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/messages/{sermon_id}/download", name="messages_download")
async def download_sermon(sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.sermon import Sermon
    from app.core.telegram import TelegramDistributor

    result = await db.execute(
        select(Sermon).where(Sermon.id == sermon_id, Sermon.status == "published")
    )
    sermon = result.scalar_one_or_none()

    if not sermon or not sermon.telegram_file_id:
        return HTMLResponse("Audio not found", status_code=404)

    tg = TelegramDistributor()
    file_url = await tg.file_download_url(sermon.telegram_file_id)
    if not file_url:
        return HTMLResponse("Unable to retrieve audio", status_code=502)

    import httpx

    async def audio_download():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("GET", file_url) as upstream:
                async for chunk in upstream.aiter_bytes(chunk_size=65536):
                    yield chunk

    safe_title = (sermon.title or "sermon").replace('"', "_").replace("/", "_")
    return StreamingResponse(
        audio_download(),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_title}.mp3"',
            "Content-Type": "audio/mpeg",
        },
    )


@router.get("/events", name="events")
async def events_page(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.event import Event

    upcoming_events = []
    featured_event = None
    recent_events = []

    try:
        result = await db.execute(
            select(Event)
            .where(Event.is_published == True, Event.starts_at >= datetime.now(timezone.utc))
            .order_by(Event.starts_at.asc())
            .limit(6)
        )
        upcoming_events = list(result.scalars().all())
    except Exception:
        pass

    try:
        result = await db.execute(
            select(Event)
            .where(Event.is_published == True, Event.is_major == True)
            .where(Event.starts_at >= datetime.now(timezone.utc))
            .order_by(Event.starts_at.asc())
            .limit(1)
        )
        featured_event = result.scalar_one_or_none()
    except Exception:
        pass

    try:
        result = await db.execute(
            select(Event)
            .where(Event.is_published == True)
            .order_by(Event.starts_at.desc())
            .limit(3)
        )
        recent_events = list(result.scalars().all())
    except Exception:
        pass

    return await render_template(request, "pages/events.html", {
        "_db": db,
        "upcoming_events": upcoming_events,
        "featured_event": featured_event,
        "recent_events": recent_events,
    })


@router.get("/calendar", name="calendar")
async def calendar(request: Request):
    return await render_template(request, "pages/calendar.html")


@router.get("/casom", name="casom")
async def casom(request: Request):
    return await render_template(request, "pages/casom.html")


@router.get("/contact", name="contact")
async def contact(request: Request):
    return await render_template(request, "pages/contact.html")


@router.post("/contact", name="contact_store")
async def contact_store(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    subject: str = Form(""),
    message: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    from app.models.contact_inquiry import ContactInquiry

    if not name.strip() or not email.strip() or not message.strip():
        return _redirect(request, "/contact", "Please fill in all required fields.", "error")

    inquiry = ContactInquiry(
        name=name.strip(),
        email=email.strip(),
        phone=phone.strip() if phone else None,
        subject=subject.strip() if subject else "General Inquiry",
        message=message.strip(),
    )
    db.add(inquiry)
    await db.flush()

    return _redirect(request, "/contact", "Thank you for reaching out! We will get back to you shortly.")


@router.get("/get-involved", name="get_involved")
async def get_involved(request: Request):
    return await render_template(request, "pages/get-involved.html")


@router.get("/give", name="give")
async def give(request: Request):
    return await render_template(request, "pages/give.html")


@router.post("/give/checkout", name="give_checkout")
async def give_checkout(
    request: Request,
    amount: float = Form(...),
    email: str = Form(...),
    full_name: str = Form(""),
    phone: str = Form(""),
    giving_type: str = Form("tithe"),
    anonymous: bool = Form(False),
    dedication: str = Form(""),
    payment_gateway: str = Form("paystack"),
    db: AsyncSession = Depends(get_db),
):
    from app.config import settings as app_settings
    from app.core.payment import initialize_paystack, initialize_flutterwave
    from app.models.giving import Giving

    if amount <= 0:
        return _redirect(request, "/give", "Please enter a valid amount.", "error")

    amount_kobo = int(amount * 100)

    reference = f"swm_{secrets.token_urlsafe(16)}"
    callback_url = f"{app_settings.APP_URL}/give/callback?reference={reference}"

    giving = Giving(
        reference=reference,
        email=email.strip(),
        full_name=full_name.strip() if full_name else None,
        phone=phone.strip() if phone else None,
        amount=amount,
        giving_type=giving_type,
        is_anonymous=anonymous,
        dedication=dedication.strip() if dedication else None,
        payment_gateway=payment_gateway,
        status="pending",
    )
    db.add(giving)
    await db.flush()

    authorization_url = None

    if payment_gateway == "flutterwave":
        authorization_url = await initialize_flutterwave(
            email=email.strip(),
            amount_naira=amount,
            reference=reference,
            redirect_url=callback_url,
            customer={
                "email": email.strip(),
                "name": full_name.strip() if full_name else "",
                "phone_number": phone.strip() if phone else "",
            },
            customizations={
                "title": "Scent of Water Ministry - Giving",
                "description": f"Donation: {giving_type}",
            },
            secret_key=app_settings.FLUTTERWAVE_SECRET_KEY,
        )
    else:
        authorization_url = await initialize_paystack(
            email=email.strip(),
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=callback_url,
            metadata={
                "giving_type": giving_type,
                "full_name": full_name,
                "phone": phone,
                "anonymous": anonymous,
                "dedication": dedication,
            },
            secret_key=app_settings.PAYSTACK_SECRET_KEY,
        )

    if not authorization_url:
        return _redirect(request, "/give", "Unable to initialize payment. Please try again.", "error")

    return RedirectResponse(url=authorization_url, status_code=303)


@router.get("/give/callback", name="give_callback")
async def give_callback(
    request: Request,
    reference: str = Query(""),
    trxref: str = Query(""),
    status: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    from app.models.giving import Giving

    actual_ref = trxref or reference

    result = await db.execute(
        select(Giving).where(Giving.reference == actual_ref)
    )
    giving = result.scalar_one_or_none()

    if giving:
        normalized = status.lower() if status else "abandoned"
        if normalized == "successful" or normalized == "success":
            giving.status = "completed"
            giving.paid_at = datetime.now(timezone.utc)
        elif normalized in ("abandoned", "cancelled", "failed"):
            giving.status = "failed"
        else:
            giving.status = normalized

        await db.flush()

    return _redirect(
        request, "/give",
        "Thank you for your generous giving! God bless you." if giving and giving.status == "completed" else "Payment could not be confirmed. Please try again.",
        "success" if giving and giving.status == "completed" else "error",
    )


@router.get("/privacy-policy", name="privacy_policy")
async def privacy_policy(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.settings_cache import get_setting

    content = await get_setting(db, "privacy_policy", "")
    return await render_template(request, "pages/privacy-policy.html", {
        "_db": db,
        "content": content,
    })


@router.get("/terms-of-service", name="terms_of_service")
async def terms_of_service(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.settings_cache import get_setting

    content = await get_setting(db, "terms_of_service", "")
    return await render_template(request, "pages/terms-of-service.html", {
        "_db": db,
        "content": content,
    })


@router.get("/sitemap", name="sitemap")
async def sitemap(request: Request):
    return await render_template(request, "pages/sitemap.html")


@router.get("/coming-soon", name="coming_soon")
async def coming_soon(request: Request):
    return await render_template(request, "pages/coming-soon.html")


@router.get("/stories", name="stories")
async def stories(request: Request):
    return await render_template(request, "pages/stories.html")


@router.get("/prayer", name="prayer")
async def prayer(request: Request):
    return await render_template(request, "pages/prayer.html")


@router.post("/prayer", name="prayer_store")
async def prayer_store(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    prayer_request: str = Form(...),
    is_public: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    from app.models.prayer_request import PrayerRequest

    if not full_name.strip() or not prayer_request.strip():
        return _redirect(request, "/prayer", "Please fill in all required fields.", "error")

    req = PrayerRequest(
        full_name=full_name.strip(),
        email=email.strip() if email else None,
        phone=phone.strip() if phone else None,
        prayer_request=prayer_request.strip(),
        is_public=is_public,
        status="pending",
    )
    db.add(req)
    await db.flush()

    return _redirect(request, "/prayer", "Your prayer request has been submitted. We are standing with you in prayer!")


@router.get("/watch", name="watch")
async def watch(request: Request):
    return await render_template(request, "pages/watch.html")


@router.post("/newsletter", name="newsletter_subscribe")
async def newsletter_subscribe(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    from app.models.newsletter_subscriber import NewsletterSubscriber

    ref = request.headers.get("referer", "/")
    ref_parsed = urlparse(ref)
    if ref_parsed.netloc and ref_parsed.netloc != request.url.netloc:
        ref = "/"

    if not email.strip():
        return _redirect(request, ref, "Please enter a valid email address.", "error")

    email_lower = email.strip().lower()

    result = await db.execute(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == email_lower)
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.is_active:
            return _redirect(request, ref, "You are already subscribed!", "info")
        existing.is_active = True
        existing.unsubscribed_at = None
        await db.flush()
        return _redirect(request, ref, "Welcome back! Your subscription has been reactivated.")

    subscriber = NewsletterSubscriber(
        email=email_lower,
        is_active=True,
    )
    db.add(subscriber)
    await db.flush()

    return _redirect(request, ref, "Thank you for subscribing! You will receive updates from us.")
