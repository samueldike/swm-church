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


def _redirect(
    request: Request,
    url: str,
    flash_msg: str | None = None,
    flash_type: str = "success",
) -> RedirectResponse:
    if flash_msg:
        request.session["flash_messages"] = [{"type": flash_type, "message": flash_msg}]
        request.session[flash_type] = flash_msg
    resp = RedirectResponse(url=url, status_code=303)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


def _paginate_url(request: Request, page: int, base_path: str) -> str:
    return f"{base_path}?page={page}"


def _public_base_url(request: Request) -> str:
    """request.base_url as seen by the app is http:// behind an SSL-terminating
    proxy; honor X-Forwarded-Proto so generated absolute URLs (feed enclosures,
    links) reflect the scheme visitors actually use."""
    base = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto")
    if proto == "https" and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


@router.get("/", name="home")
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    """Homepage — dark, frame-by-frame scroll design."""
    from app.models.site_settings import SiteSetting
    from app.models.slider import Slider
    from app.models.sermon import Sermon

    sliders = []
    try:
        result = await db.execute(
            select(Slider)
            .where(Slider.is_active == True)
            .order_by(Slider.sort_order.asc())
        )
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
            .limit(HOME2_MESSAGE_LIMIT)
        )
        latest_sermons = list(result.scalars().all())
    except Exception:
        pass

    featuredStories = []
    try:
        from app.models.story import Story

        result = await db.execute(
            select(Story)
            .where(Story.status == "published")
            .order_by(Story.published_at.desc())
            .limit(3)
        )
        featuredStories = list(result.scalars().all())
    except Exception:
        pass

    branches = []
    try:
        from app.models.branch import Branch

        result = await db.execute(
            select(Branch).where(Branch.is_active == True).order_by(Branch.name)
        )
        branches = list(result.scalars().all())
    except Exception:
        pass

    gallery = await _photos(db, "gallery") or _build_gallery(sliders, featuredStories)
    outreach = await _photos(db, "outreach")

    return await render_template(
        request,
        "pages/home.html",
        {
            "_db": db,
            "sliders": sliders,
            "settings": settings,
            "latest_sermons": latest_sermons,
            "featuredStories": featuredStories,
            "branches": branches,
            "gallery": gallery,
            "outreach": outreach,
        },
    )


HOME2_MESSAGE_LIMIT = 5


async def _photos(db: AsyncSession, collection: str) -> list[dict]:
    """Active photos for one admin-managed collection, in display order."""
    from app.models.photo import Photo

    try:
        result = await db.execute(
            select(Photo)
            .where(Photo.collection == collection, Photo.is_active == True)
            .order_by(Photo.sort_order.asc(), Photo.id.asc())
        )
        return [
            {"src": p.src, "alt": p.caption or "Scent of Water Ministry", "caption": p.caption}
            for p in result.scalars().all()
        ]
    except Exception:
        return []


GALLERY_DIRS = ("images/sliders", "images/stories", "images/gallery")
GALLERY_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}

# Shipped by seed_content.py as demo artwork — generic stock crowds that are
# not this congregation. Never surface them as the ministry's own photography.
GALLERY_PLACEHOLDERS = {"slide-1.jpg", "slide-2.jpg", "slide-3.jpg"}


def _build_gallery(sliders, stories) -> list[dict]:
    """Collect real, already-uploaded imagery for the v2 gallery.

    `images/gallery` is the curated folder and wins outright when populated.
    Only if it is empty do we fall back to slider/story artwork, so the
    gallery never mixes curated photography with hero crops or seed art.

    A dedicated Gallery model with admin CRUD would supersede this.
    """
    from pathlib import Path

    shots: list[dict] = []
    seen: set[str] = set()

    def add(path: str | None, alt: str) -> None:
        if not path:
            return
        src = path if path.startswith("/") else f"/{path}"
        if src in seen:
            return
        seen.add(src)
        shots.append({"src": src, "alt": alt})

    static_root = Path(__file__).resolve().parents[1] / "static"

    curated = static_root / "images/gallery"
    if curated.is_dir():
        for f in sorted(curated.iterdir()):
            if f.is_file() and f.suffix.lower() in GALLERY_EXTS:
                add(f"images/gallery/{f.name}", "Scent of Water Ministry")
    if shots:
        return shots

    for s in sliders:
        add(s.image_path, (s.title or "Scent of Water Ministry").replace("\n", " "))
    for s in stories:
        add(s.featured_image, s.title)
    for rel_dir in GALLERY_DIRS:
        folder = static_root / rel_dir
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.suffix.lower() not in GALLERY_EXTS:
                continue
            if f.name in GALLERY_PLACEHOLDERS:
                continue
            add(f"{rel_dir}/{f.name}", "Scent of Water Ministry")

    return shots


OUTREACH_PAGES = {
    "prison": {
        "eyebrow": "Beyond the walls",
        "title": "Annual Prison Outreach",
        "lede": (
            "Our headquarters sits beside the remand home at Mile 50. Every year we "
            "walk the short distance from our altar to those the world has written "
            "off, carrying the same message of restoration we preach on Sunday."
        ),
        "points": [
            ("Who it reaches", "Inmates and awaiting-trial detainees at the remand home beside our Mile 50 headquarters."),
            ("What we bring", "The preached word, provisions, and follow-up for those approaching release."),
            ("When it runs", "Annually, coordinated by The Joseph Ministry with volunteers from across our units."),
        ],
    },
    "medical": {
        "eyebrow": "Beyond the walls",
        "title": "Free Medical Outreach",
        "lede": (
            "A no-cost medical and community outreach for Abakaliki and surrounding "
            "communities — consultations, treatment and care for families who would "
            "otherwise go without."
        ),
        "points": [
            ("Who it reaches", "Families and children in Abakaliki and surrounding rural communities in Ebonyi State."),
            ("What we bring", "Free consultations, medication, and relief materials, staffed by volunteer medical personnel."),
            ("When it runs", "Annually, as the Annual Community Medical Outreach."),
        ],
    },
}


@router.get("/outreach/{kind}", name="outreach_detail")
async def outreach_detail(kind: str, request: Request, db: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException

    page = OUTREACH_PAGES.get(kind)
    if not page:
        raise HTTPException(status_code=404, detail="Outreach not found")

    return await render_template(
        request,
        "pages/outreach.html",
        {"_db": db, "page": page, "kind": kind, "photos": await _photos(db, "outreach")},
    )


@router.get("/our-pastor", name="our_pastor")
async def our_pastor(request: Request, db: AsyncSession = Depends(get_db)):
    """Senior pastor profile. Name, title, photo and bio all come from
    Admin → Settings, so this page never needs a code change to update."""
    from app.models.sermon import Sermon

    sermons = []
    try:
        result = await db.execute(
            select(Sermon)
            .where(Sermon.status == "published")
            .order_by(Sermon.published_at.desc())
            .limit(4)
        )
        sermons = list(result.scalars().all())
    except Exception:
        pass

    return await render_template(
        request, "pages/our-pastor.html", {"_db": db, "sermons": sermons}
    )


@router.get("/about", name="about")
async def about(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/about.html", {"_db": db})


@router.get("/departments", name="departments")
async def departments(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.department import Department
    departments_list = []
    try:
        result = await db.execute(
            select(Department).where(Department.is_active == True).order_by(Department.name)
        )
        departments_list = list(result.scalars().all())
    except Exception:
        pass
    return await render_template(request, "pages/departments.html", {
        "_db": db,
        "departments_list": departments_list,
    })


@router.get("/messages", name="messages")
async def messages_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    search: str = Query("", alias="search"),
    page: int = Query(1, ge=1),
):
    from app.models.sermon import Sermon

    query = (
        select(Sermon)
        .where(Sermon.status == "published")
        .options(selectinload(Sermon.branch))
    )

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
    pagination["prev_url"] = (
        _paginate_url(request, pagination["page"] - 1, base_path)
        if pagination["has_prev"]
        else None
    )
    pagination["next_url"] = (
        _paginate_url(request, pagination["page"] + 1, base_path)
        if pagination["has_next"]
        else None
    )

    return await render_template(
        request,
        "pages/messages.html",
        {
            "_db": db,
            "sermons": sermons,
            "featured_sermon": featured_sermon,
            "search": search,
            "pagination": pagination,
        },
    )


@router.get("/messages/{slug}", name="messages_show")
async def sermon_detail(
    slug: str, request: Request, db: AsyncSession = Depends(get_db)
):
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

    return await render_template(
        request,
        "pages/messages_show.html",
        {
            "_db": db,
            "sermon": sermon,
            "related": related,
        },
    )


@router.get("/feed.xml", name="podcast_feed")
async def podcast_feed(request: Request, db: AsyncSession = Depends(get_db)):
    """Podcast-standard RSS 2.0 + iTunes feed of published audio messages.

    This is the single-point-of-distribution artifact: submit this one URL to
    Spotify for Podcasters / Apple Podcasts Connect once, and every future
    published sermon propagates automatically — no per-platform integration.
    """
    import xml.etree.ElementTree as ET
    from email.utils import format_datetime

    from app.models.sermon import Sermon
    from app.core.settings_cache import get_settings

    site = await get_settings(db)
    site_name = site.get("siteName", "Scent of Water Ministry")
    base_url = _public_base_url(request)

    result = await db.execute(
        select(Sermon)
        .where(Sermon.status == "published")
        .where(or_(Sermon.media_url.isnot(None), Sermon.telegram_file_id.isnot(None)))
        .order_by(Sermon.published_at.desc())
        .limit(200)
    )
    sermons = list(result.scalars().all())

    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    ET.register_namespace("itunes", ITUNES_NS)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{site_name} — Audio Messages"
    ET.SubElement(channel, "link").text = f"{base_url}/messages"
    ET.SubElement(channel, "description").text = f"Sermon audio messages from {site_name}."
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(channel, f"{{{ITUNES_NS}}}author").text = site_name

    for sermon in sermons:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = sermon.title
        ET.SubElement(item, "link").text = f"{base_url}/messages/{sermon.slug}"
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"sermon-{sermon.id}"
        if sermon.caption or sermon.description:
            ET.SubElement(item, "description").text = sermon.caption or sermon.description
        if sermon.preacher:
            ET.SubElement(item, f"{{{ITUNES_NS}}}author").text = sermon.preacher
        if sermon.published_at:
            ET.SubElement(item, "pubDate").text = format_datetime(sermon.published_at)
        if sermon.media_url:
            enclosure_url = sermon.media_url if sermon.media_url.startswith("http") else f"{base_url}{sermon.media_url}"
        else:
            enclosure_url = f"{base_url}/messages/{sermon.id}/stream"
        ET.SubElement(item, "enclosure", {
            "url": enclosure_url,
            "type": "audio/mpeg",
            "length": str(sermon.original_file_size or 0),
        })

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    return HTMLResponse(content=xml_bytes, media_type="application/rss+xml")


@router.get("/messages/{sermon_id}/stream", name="messages_stream")
async def stream_sermon(
    sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    from app.models.sermon import Sermon
    from app.core.telegram import TelegramDistributor

    result = await db.execute(
        select(Sermon).where(Sermon.id == sermon_id, Sermon.status == "published")
    )
    sermon = result.scalar_one_or_none()

    if not sermon:
        return HTMLResponse("Audio not found", status_code=404)

    if sermon.media_url:
        return RedirectResponse(url=sermon.media_url, status_code=302)

    if not sermon.telegram_file_id:
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
async def download_sermon(
    sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    from app.models.sermon import Sermon
    from app.core.telegram import TelegramDistributor

    result = await db.execute(
        select(Sermon).where(Sermon.id == sermon_id, Sermon.status == "published")
    )
    sermon = result.scalar_one_or_none()

    if not sermon:
        return HTMLResponse("Audio not found", status_code=404)

    safe_title = (sermon.title or "sermon").replace('"', "_").replace("/", "_")

    if sermon.audio_file_path:
        import os
        from fastapi.responses import FileResponse

        file_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), sermon.audio_file_path
        )
        if os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1] or ".mp3"
            return FileResponse(file_path, media_type="audio/mpeg", filename=f"{safe_title}{ext}")

    if not sermon.telegram_file_id:
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
            .where(
                Event.is_published == True,
                Event.starts_at >= datetime.utcnow(),
            )
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
            .where(Event.starts_at >= datetime.utcnow())
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

    return await render_template(
        request,
        "pages/events.html",
        {
            "_db": db,
            "upcoming_events": upcoming_events,
            "featured_event": featured_event,
            "recent_events": recent_events,
        },
    )


@router.get("/calendar", name="calendar")
async def calendar(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/calendar.html", {"_db": db})


@router.get("/casom", name="casom")
async def casom(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/casom.html", {"_db": db})


@router.get("/contact", name="contact")
async def contact(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/contact.html", {"_db": db})


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
        return _redirect(
            request, "/contact", "Please fill in all required fields.", "error"
        )

    inquiry = ContactInquiry(
        name=name.strip(),
        email=email.strip(),
        phone=phone.strip() if phone else None,
        subject=subject.strip() if subject else "General Inquiry",
        message=message.strip(),
    )
    db.add(inquiry)
    await db.flush()

    return _redirect(
        request,
        "/contact",
        "Thank you for reaching out! We will get back to you shortly.",
    )


@router.get("/get-involved", name="get_involved")
async def get_involved(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/get-involved.html", {"_db": db})


@router.get("/give", name="give")
async def give(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/give.html", {"_db": db})


@router.post("/give/checkout", name="give_checkout")
async def give_checkout(
    request: Request,
    amount: float = Form(...),
    email: str = Form(...),
    full_name: str = Form(""),
    phone: str = Form(""),
    purpose: str = Form("Tithe"),
    purpose_text: str = Form(""),
    anonymous: bool = Form(False),
    dedication: str = Form(""),
    payment_gateway: str = Form("paystack"),
    db: AsyncSession = Depends(get_db),
):
    from app.config import settings as app_settings
    from app.core.payment import initialize_paystack, initialize_flutterwave
    from app.core.settings_cache import get_settings
    from app.models.giving import Giving

    if amount <= 0:
        return _redirect(request, "/give", "Please enter a valid amount.", "error")

    site_settings = await get_settings(db)
    payment_gateway = (site_settings.get("active_giving_gateway") or payment_gateway or "paystack").strip()
    giving_type = purpose.strip() or "Tithe"
    if giving_type == "Others" and purpose_text.strip():
        giving_type = purpose_text.strip()

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
            secret_key=site_settings.get("flutterwave_secret_key") or app_settings.FLUTTERWAVE_SECRET_KEY,
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
            secret_key=site_settings.get("paystack_secret_key") or app_settings.PAYSTACK_SECRET_KEY,
        )

    if not authorization_url:
        return _redirect(
            request, "/give", "Unable to initialize payment. Please try again.", "error"
        )

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

    result = await db.execute(select(Giving).where(Giving.reference == actual_ref))
    giving = result.scalar_one_or_none()

    if giving:
        normalized = status.lower() if status else "abandoned"
        if normalized == "successful" or normalized == "success":
            giving.status = "completed"
            giving.paid_at = datetime.utcnow()
        elif normalized in ("abandoned", "cancelled", "failed"):
            giving.status = "failed"
        else:
            giving.status = normalized

        await db.flush()

    return _redirect(
        request,
        "/give",
        "Thank you for your generous giving! God bless you."
        if giving and giving.status == "completed"
        else "Payment could not be confirmed. Please try again.",
        "success" if giving and giving.status == "completed" else "error",
    )


@router.get("/privacy-policy", name="privacy_policy")
async def privacy_policy(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.settings_cache import get_setting

    content = await get_setting(db, "privacy_policy", "")
    return await render_template(
        request,
        "pages/privacy-policy.html",
        {
            "_db": db,
            "content": content,
        },
    )


@router.get("/terms-of-service", name="terms_of_service")
async def terms_of_service(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.settings_cache import get_setting

    content = await get_setting(db, "terms_of_service", "")
    return await render_template(
        request,
        "pages/terms-of-service.html",
        {
            "_db": db,
            "content": content,
        },
    )


@router.get("/sitemap", name="sitemap")
async def sitemap(request: Request):
    return await render_template(request, "pages/sitemap.html")


@router.get("/coming-soon", name="coming_soon")
async def coming_soon(request: Request):
    return await render_template(request, "pages/coming-soon.html")


@router.get("/stories", name="stories")
async def stories(
    request: Request,
    db: AsyncSession = Depends(get_db),
    search: str = Query("", alias="search"),
    page: int = Query(1, ge=1),
):
    from app.models.story import Story

    query = select(Story).where(Story.status == "published")
    if search:
        pat = f"%{search}%"
        query = query.where(
            or_(
                Story.title.ilike(pat),
                Story.excerpt.ilike(pat),
                Story.content.ilike(pat),
            )
        )
    query = query.order_by(Story.published_at.desc())
    pagination = await paginate(db, query, page=page, per_page=9)
    pagination["prev_url"] = (
        f"/stories?search={search}&page={page - 1}" if pagination["has_prev"] else None
    )
    pagination["next_url"] = (
        f"/stories?search={search}&page={page + 1}" if pagination["has_next"] else None
    )
    stories_list = pagination["items"]
    return await render_template(
        request,
        "pages/stories.html",
        {
            "_db": db,
            "stories": stories_list,
            "search": search,
            "pagination": pagination,
        },
    )


@router.get("/stories/{slug}", name="story_detail")
async def story_detail(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.story import Story

    result = await db.execute(
        select(Story).where(Story.slug == slug, Story.status == "published")
    )
    story = result.scalar_one_or_none()
    if not story:
        return HTMLResponse("Story not found", status_code=404)
    related = []
    try:
        result = await db.execute(
            select(Story)
            .where(Story.status == "published", Story.id != story.id)
            .order_by(Story.published_at.desc())
            .limit(3)
        )
        related = list(result.scalars().all())
    except Exception:
        pass
    return await render_template(
        request,
        "pages/stories_show.html",
        {
            "_db": db,
            "story": story,
            "related": related,
        },
    )


@router.get("/prayer", name="prayer")
async def prayer(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/prayer.html", {"_db": db})


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
        return _redirect(
            request, "/prayer", "Please fill in all required fields.", "error"
        )

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

    return _redirect(
        request,
        "/prayer",
        "Your prayer request has been submitted. We are standing with you in prayer!",
    )


@router.get("/watch", name="watch")
async def watch(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/watch.html", {"_db": db})


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
        return _redirect(
            request, ref, "Welcome back! Your subscription has been reactivated."
        )

    subscriber = NewsletterSubscriber(
        email=email_lower,
        is_active=True,
    )
    db.add(subscriber)
    await db.flush()

    return _redirect(
        request, ref, "Thank you for subscribing! You will receive updates from us."
    )
