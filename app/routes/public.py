from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
    JSONResponse,
)
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.pagination import paginate
from app.core.template import render_template

router = APIRouter(tags=["public"])


async def _hq_only(db):
    """Only HQ-owned messages are ever public. A branch's messages belong to that branch's own
    channels (its Telegram bot) and must never appear on the HQ website, feed or stream URLs.
    Legacy rows without a branch are treated as HQ's."""
    from sqlalchemy import or_

    from app.core.hq_branch import get_hq_branch_id
    from app.models.sermon import Sermon

    return or_(Sermon.branch_id == await get_hq_branch_id(db), Sermon.branch_id.is_(None))


async def _public_branches(db):
    """Active branches for the public forms, HQ first."""
    from app.models.branch import Branch

    rows = (await db.execute(select(Branch).where(Branch.is_active == True).order_by(Branch.is_hq.desc(), Branch.name))).scalars().all()  # noqa: E712
    return list(rows)


async def _pick_branch_id(db, posted) -> int:
    """Branch chosen on a public form, falling back to HQ when missing or invalid."""
    from app.core.hq_branch import get_hq_branch_id

    branches = await _public_branches(db)
    try:
        wanted = int(posted) if posted not in (None, "") else None
    except (TypeError, ValueError):
        wanted = None
    if wanted and any(b.id == wanted for b in branches):
        return wanted
    return await get_hq_branch_id(db)


def _content_disposition(kind: str, title: str, ext: str) -> str:
    """Header-safe Content-Disposition. Titles with curly quotes, accents or non-Latin letters used
    to raise UnicodeEncodeError (a 500) because HTTP headers are latin-1."""
    from urllib.parse import quote
    import re as _re

    full = f"{(title or 'sermon').strip() or 'sermon'}{ext}"
    ascii_name = _re.sub(r"[^A-Za-z0-9._ -]+", "_", full).strip(" ._") or f"sermon{ext}"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(full, safe='')}"


def _serve_local_audio(request: Request, path: str, disposition: str):
    """Serve a local audio file with HTTP Range support so players can seek and resume."""
    import mimetypes
    import os

    from fastapi.responses import Response

    size = os.path.getsize(path)
    media_type = mimetypes.guess_type(path)[0] or "audio/mpeg"
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": disposition, "Cache-Control": "public, max-age=3600"}
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range", "")
    if rng.startswith("bytes=") and "," not in rng:
        first, _, last = rng[6:].partition("-")
        try:
            if first == "":                      # suffix range: the last N bytes
                start = max(0, size - int(last))
            else:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
        except ValueError:
            start, end = 0, size - 1
        else:
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1
    headers["Content-Length"] = str(length)

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=media_type)
    return StreamingResponse(body(), status_code=status, media_type=media_type, headers=headers)


def _rate_limited(request: Request, bucket: str, limit: int = 8, window: float = 600.0) -> bool:
    """Per-IP cap on public form submissions (spam / abuse guard). Counts every attempt."""
    from app.core import throttle

    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    key = f"public:{bucket}:{ip}"
    if throttle.is_blocked(key, limit=limit, window=window):
        return True
    throttle.record_failure(key, window=window)
    return False


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
    """Page link that keeps the filters (search, series, ...) already on the URL."""
    from urllib.parse import urlencode

    params = [(k, v) for k, v in request.query_params.multi_items() if k != "page" and v != ""]
    params.append(("page", str(page)))
    return f"{base_path}?{urlencode(params)}"


def _public_base_url(request: Request) -> str:
    """request.base_url as seen by the app is http:// behind an SSL-terminating
    proxy; honor X-Forwarded-Proto so generated absolute URLs (feed enclosures,
    links) reflect the scheme visitors actually use."""
    base = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto")
    if proto == "https" and base.startswith("http://"):
        base = "https://" + base[len("http://") :]
    return base


@router.get("/", name="home")
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    """Homepage — dark, frame-by-frame scroll design."""
    from app.models.site_settings import SiteSetting
    from app.models.slider import Slider
    from app.models.sermon import Sermon
    from app.models.program_flyer import ProgramFlyer

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
            .where(Sermon.status == "published", await _hq_only(db))
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

    # "Visit us": headquarters (from the site-wide settings) plus only the branches HQ has approved for the public directory.
    branches = []
    try:
        from app.routes.public_extra import listed_branches

        branches = await listed_branches(db, settings)
    except Exception:
        logging.exception("home branches")

    gallery = await _photos(db, "gallery") or _build_gallery(sliders, featuredStories)
    outreach = await _photos(db, "outreach")

    program_flyers = []
    try:
        result = await db.execute(
            select(ProgramFlyer)
            .where(ProgramFlyer.is_active == True)
            .order_by(ProgramFlyer.created_at.desc(), ProgramFlyer.id.desc())
        )
        program_flyers = list(result.scalars().all())
    except Exception:
        pass

    from app.core import site_content as _sc
    from app.routes.public_extra import _church_jsonld

    _base = _public_base_url(request)
    _addr = (settings.get("siteHeadquartersAddress") or "").strip()
    _home_ld = _church_jsonld(request, settings, settings.get("siteName") or "Scent of Water Ministry", _addr, _sc.clean_phone(settings.get("siteSupportPhone")),
                              (settings.get("siteContactEmail") or "").strip(), _sc.get_services(settings), _base + "/")
    return await render_template(
        request,
        "pages/home.html",
        {
            "_db": db,
            "jsonld": _home_ld,
            "sliders": sliders,
            "settings": settings,
            "latest_sermons": latest_sermons,
            "featuredStories": featuredStories,
            "branches": branches,
            "gallery": gallery,
            "outreach": outreach,
            "program_flyers": program_flyers,
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
            {
                "src": p.src,
                "alt": p.caption or "Scent of Water Ministry",
                "caption": p.caption,
            }
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
            (
                "Who it reaches",
                "Inmates and awaiting-trial detainees at the remand home beside our Mile 50 headquarters.",
            ),
            (
                "What we bring",
                "The preached word, provisions, and follow-up for those approaching release.",
            ),
            (
                "When it runs",
                "Annually, coordinated by The Joseph Ministry with volunteers from across our units.",
            ),
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
            (
                "Who it reaches",
                "Families and children in Abakaliki and surrounding rural communities in Ebonyi State.",
            ),
            (
                "What we bring",
                "Free consultations, medication, and relief materials, staffed by volunteer medical personnel.",
            ),
            ("When it runs", "Annually, as the Annual Community Medical Outreach."),
        ],
    },
}


@router.get("/outreach/{kind}", name="outreach_detail")
async def outreach_detail(
    kind: str, request: Request, db: AsyncSession = Depends(get_db)
):
    from fastapi import HTTPException

    page = OUTREACH_PAGES.get(kind)
    if not page:
        raise HTTPException(status_code=404, detail="Outreach not found")

    return await render_template(
        request,
        "pages/outreach.html",
        {
            "_db": db,
            "page": page,
            "kind": kind,
            "photos": await _photos(db, "outreach"),
        },
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
            .where(Sermon.status == "published", await _hq_only(db))
            .order_by(Sermon.published_at.desc())
            .limit(4)
        )
        sermons = list(result.scalars().all())
    except Exception:
        pass

    from app.core.settings_cache import get_settings

    site = await get_settings(db)
    return await render_template(
        request, "pages/our-pastor.html", {"_db": db, "sermons": sermons, "pastorStory": (site.get("pastorStory") or "").strip()}
    )


@router.get("/about", name="about")
async def about(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core import site_content as _sc
    from app.core.settings_cache import get_settings

    _site = await get_settings(db)
    _extra = {"teaching": _sc.get_teaching(_site), "ourStory": (_site.get("ourStory") or "").strip(), "faithUrl": _sc.safe_https(_site.get("faithStatementUrl"))}
    return await render_template(request, "pages/about.html", {"_db": db, **_extra})


@router.get("/departments", name="departments")
async def departments(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.department import Department

    departments_list = []
    try:
        result = await db.execute(
            select(Department)
            .where(Department.is_active == True)
            .order_by(Department.name)
        )
        departments_list = list(result.scalars().all())
    except Exception:
        pass
    return await render_template(
        request,
        "pages/departments.html",
        {
            "_db": db,
            "departments_list": departments_list,
        },
    )


@router.get("/messages", name="messages")
async def messages_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    search: str = Query("", alias="search"),
    series: str = Query(""),
    preacher: str = Query(""),
    year: str = Query(""),
    page: int = Query(1, ge=1),
):
    from sqlalchemy import and_, extract

    from app.models.sermon import Sermon

    base_where = (Sermon.status == "published", await _hq_only(db))
    query = (
        select(Sermon)
        .where(*base_where)
        .options(selectinload(Sermon.branch))
    )

    search = search.strip()[:100]
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Sermon.title.ilike(search_pattern),
                Sermon.preacher.ilike(search_pattern),
                Sermon.caption.ilike(search_pattern),
                Sermon.description.ilike(search_pattern),
                Sermon.series.ilike(search_pattern),
                Sermon.scripture.ilike(search_pattern),
                # a transcript is searched only once an administrator has reviewed and published it
                and_(Sermon.transcript_public == True, Sermon.transcript.ilike(search_pattern)),  # noqa: E712
            )
        )
    if series.strip():
        query = query.where(Sermon.series == series.strip())
    if preacher.strip():
        query = query.where(Sermon.preacher == preacher.strip())
    if year.strip().isdigit():
        query = query.where(extract("year", Sermon.published_at) == int(year))

    # Filter choices come from what is actually published.
    series_opts = [r[0] for r in (await db.execute(select(Sermon.series).where(*base_where, Sermon.series.isnot(None), Sermon.series != "").distinct().order_by(Sermon.series))).all()]
    preacher_opts = [r[0] for r in (await db.execute(select(Sermon.preacher).where(*base_where, Sermon.preacher.isnot(None), Sermon.preacher != "").distinct().order_by(Sermon.preacher))).all()]
    year_opts = sorted({int(r[0]) for r in (await db.execute(select(extract("year", Sermon.published_at)).where(*base_where, Sermon.published_at.isnot(None)).distinct())).all() if r[0]}, reverse=True)
    filtered = bool(search or series.strip() or preacher.strip() or year.strip())

    query = query.order_by(Sermon.published_at.desc())

    pagination = await paginate(db, query, page=page, per_page=12)
    sermons = pagination["items"]

    featured_sermon = None
    try:
        feat_result = await db.execute(
            select(Sermon)
            .where(Sermon.status == "published", await _hq_only(db), Sermon.is_featured == True)
            .order_by(Sermon.published_at.desc())
            .limit(1)
        )
        featured_sermon = feat_result.scalar_one_or_none()
    except Exception:
        pass

    if filtered:
        featured_sermon = None          # a filtered list is a plain result list; no "featured" hero
    elif featured_sermon is None and sermons:
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
            "series_opts": series_opts, "preacher_opts": preacher_opts, "year_opts": year_opts,
            "f_series": series.strip(), "f_preacher": preacher.strip(), "f_year": year.strip(), "filtered": filtered,
            "metaDescription": "Audio messages from Scent of Water Ministry — search by title, preacher, series or scripture.",
        },
    )


@router.get("/messages/{slug}", name="messages_show")
async def sermon_detail(
    slug: str, request: Request, db: AsyncSession = Depends(get_db)
):
    from sqlalchemy.orm import selectinload

    from app.models.sermon import Sermon

    result = await db.execute(
        select(Sermon)
        .options(selectinload(Sermon.branch))
        .where(Sermon.slug == slug, Sermon.status == "published", await _hq_only(db))
    )
    sermon = result.scalar_one_or_none()
    if not sermon:
        return HTMLResponse("Sermon not found", status_code=404)

    related = []
    try:
        if sermon.series:
            result = await db.execute(
                select(Sermon).options(selectinload(Sermon.branch))
                .where(Sermon.status == "published", await _hq_only(db), Sermon.id != sermon.id, Sermon.series == sermon.series)
                .order_by(Sermon.published_at.asc()).limit(3)
            )
            related = list(result.scalars().all())
        if len(related) < 3:
            have = {r.id for r in related} | {sermon.id}
            result = await db.execute(
                select(Sermon).options(selectinload(Sermon.branch))
                .where(Sermon.status == "published", await _hq_only(db), Sermon.id.notin_(have))
                .order_by(Sermon.published_at.desc()).limit(3 - len(related))
            )
            related += list(result.scalars().all())
    except Exception:
        pass

    import json as _json

    base = _public_base_url(request)
    ld = {"@context": "https://schema.org", "@type": "AudioObject", "name": sermon.title, "description": (sermon.caption or sermon.description or sermon.title)[:500],
          "url": f"{base}/messages/{sermon.slug}", "encodingFormat": "audio/mpeg",
          "publisher": {"@type": "Organization", "name": "Scent of Water Ministry", "url": base}}
    if sermon.published_at:
        ld["uploadDate"] = sermon.published_at.strftime("%Y-%m-%d")
    if sermon.preacher:
        ld["author"] = {"@type": "Person", "name": sermon.preacher}
    if sermon.audio_file_path or sermon.media_url:
        ld["contentUrl"] = f"{base}/messages/{sermon.id}/stream"
    from app.core.site_content import jsonld_script

    show_transcript = bool(sermon.transcript_public and (sermon.transcript or "").strip())

    return await render_template(
        request,
        "pages/messages_show.html",
        {
            "_db": db,
            "sermon": sermon,
            "related": related,
            "show_transcript": show_transcript,
            "jsonld": jsonld_script(ld),
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
        .where(Sermon.status == "published", await _hq_only(db))
        .where(or_(Sermon.media_url.isnot(None), Sermon.audio_file_path.isnot(None), Sermon.telegram_file_id.isnot(None)))
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
    ET.SubElement(
        channel, "description"
    ).text = f"Sermon audio messages from {site_name}."
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(channel, f"{{{ITUNES_NS}}}author").text = site_name

    for sermon in sermons:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = sermon.title
        ET.SubElement(item, "link").text = f"{base_url}/messages/{sermon.slug}"
        ET.SubElement(
            item, "guid", {"isPermaLink": "false"}
        ).text = f"sermon-{sermon.id}"
        if sermon.caption or sermon.description:
            ET.SubElement(item, "description").text = (
                sermon.caption or sermon.description
            )
        if sermon.preacher:
            ET.SubElement(item, f"{{{ITUNES_NS}}}author").text = sermon.preacher
        if sermon.published_at:
            ET.SubElement(item, "pubDate").text = format_datetime(sermon.published_at)
        if sermon.media_url:
            enclosure_url = (
                sermon.media_url
                if sermon.media_url.startswith("http")
                else f"{base_url}{sermon.media_url}"
            )
        else:
            enclosure_url = f"{base_url}/messages/{sermon.id}/stream"
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": enclosure_url,
                "type": "audio/mpeg",
                "length": str(sermon.original_file_size or 0),
            },
        )

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    return HTMLResponse(content=xml_bytes, media_type="application/rss+xml")


@router.api_route("/messages/{sermon_id}/stream", methods=["GET", "HEAD"], name="messages_stream")
async def stream_sermon(
    sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    from app.models.sermon import Sermon
    from app.core.telegram import TelegramDistributor

    result = await db.execute(
        select(Sermon).where(Sermon.id == sermon_id, Sermon.status == "published", await _hq_only(db))
    )
    sermon = result.scalar_one_or_none()

    if not sermon:
        return HTMLResponse("Audio not found", status_code=404)

    if sermon.audio_file_path:
        from app.core.media import to_abspath

        own_file = to_abspath(sermon.audio_file_path)
        if own_file:
            import os

            return _serve_local_audio(
                request, own_file,
                _content_disposition("inline", sermon.title, os.path.splitext(own_file)[1] or ".mp3"),
            )

    if sermon.media_url:
        return RedirectResponse(url=sermon.media_url, status_code=302)

    if not sermon.telegram_file_id:
        return HTMLResponse("Audio not found", status_code=404)

    tg = TelegramDistributor()
    local_path, file_url = await tg.resolve_file(sermon.telegram_file_id)

    # A --local Bot API server hands back a filesystem path, not a URL.
    if local_path:
        return _serve_local_audio(request, local_path, _content_disposition("inline", sermon.title, ".mp3"))

    if not file_url:
        return HTMLResponse("Unable to retrieve audio", status_code=502)

    import httpx

    async def audio_stream():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("GET", file_url) as upstream:
                async for chunk in upstream.aiter_bytes(chunk_size=65536):
                    yield chunk

    return StreamingResponse(
        audio_stream(),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": _content_disposition("inline", sermon.title, ".mp3"),
            "Accept-Ranges": "none",   # this branch proxies Telegram without byte-range support
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.api_route("/messages/{sermon_id}/download", methods=["GET", "HEAD"], name="messages_download")
async def download_sermon(
    sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    from app.models.sermon import Sermon
    from app.core.telegram import TelegramDistributor

    result = await db.execute(
        select(Sermon).where(Sermon.id == sermon_id, Sermon.status == "published", await _hq_only(db))
    )
    sermon = result.scalar_one_or_none()

    if not sermon:
        return HTMLResponse("Audio not found", status_code=404)

    safe_title = (sermon.title or "sermon").replace('"', "_").replace("/", "_")

    if sermon.audio_file_path:
        import os
        from fastapi.responses import FileResponse
        from app.core.media import to_abspath

        # Resolve through MEDIA_ROOT so this matches where the app actually writes.
        file_path = to_abspath(sermon.audio_file_path)
        if file_path:
            ext = os.path.splitext(file_path)[1] or ".mp3"
            return _serve_local_audio(request, file_path, _content_disposition("attachment", sermon.title, ext))

    if not sermon.telegram_file_id:
        return HTMLResponse("Audio not found", status_code=404)

    tg = TelegramDistributor()
    local_path, file_url = await tg.resolve_file(sermon.telegram_file_id)

    if local_path:
        import os
        from fastapi.responses import FileResponse

        ext = os.path.splitext(local_path)[1] or ".mp3"
        return _serve_local_audio(request, local_path, _content_disposition("attachment", sermon.title, ext))

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
            "Content-Disposition": _content_disposition("attachment", sermon.title, ".mp3"),
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


async def _calendar_month_context(db: AsyncSession, month: str | None) -> dict:
    """Everything the calendar page (or its JSON API) needs to render one
    month. Shared so the server-rendered page and the no-reload fetch path
    can never drift apart."""
    from datetime import date
    from app.models.event import Event
    from app.core.calendar_utils import (
        month_bounds,
        month_grid_days,
        events_by_date,
        year_months,
    )

    now = datetime.utcnow()
    if month:
        try:
            y, m = month.split("-")
            year, mo = int(y), int(m)
        except (ValueError, IndexError):
            year, mo = now.year, now.month
    else:
        year, mo = now.year, now.month

    currentMonth, prevMonth, nextMonth = month_bounds(year, mo)
    calendarDays = month_grid_days(year, mo)

    range_start = calendarDays[0]
    range_end = calendarDays[-1]
    result = await db.execute(
        select(Event)
        .where(
            Event.is_published == True,
            # the grid's last day is a date (midnight): include the whole of it
            Event.starts_at < datetime.combine(range_end, datetime.min.time()) + timedelta(days=1),
            or_(Event.ends_at == None, Event.ends_at >= range_start),
        )
        .order_by(Event.starts_at.asc())
    )
    grid_events = list(result.scalars().all())
    eventsByDate = events_by_date(grid_events)
    monthOnlyEvents = [
        ev
        for ev in grid_events
        if ev.starts_at.month == currentMonth.month
        and ev.starts_at.year == currentMonth.year
    ]

    return {
        "calendarDays": calendarDays,
        "eventsByDate": eventsByDate,
        "monthOnlyEvents": monthOnlyEvents,
        "currentMonth": currentMonth,
        "prevMonth": prevMonth,
        "nextMonth": nextMonth,
        "prevYear": currentMonth.replace(year=currentMonth.year - 1),
        "nextYear": currentMonth.replace(year=currentMonth.year + 1),
        "yearMonths": year_months(currentMonth.year, currentMonth.month),
        "today": date.today().isoformat(),
        "isCurrentMonth": currentMonth.year == now.year
        and currentMonth.month == now.month,
    }


@router.get("/calendar", name="calendar")
async def calendar(
    request: Request, db: AsyncSession = Depends(get_db), month: str = Query(None)
):
    """The public yearly calendar — a real month grid over whatever events an
    admin has published, not a hand-typed schedule. Server-rendered here for
    the first paint / no-JS fallback; /calendar/data serves the same shape as
    JSON so month/year navigation can happen without a page reload."""
    ctx = await _calendar_month_context(db, month)
    ctx["_db"] = db
    return await render_template(request, "pages/calendar.html", ctx)


@router.get("/calendar/data", name="calendar_data")
async def calendar_data(
    request: Request, db: AsyncSession = Depends(get_db), month: str = Query(None)
):
    """JSON twin of /calendar for the client-side nav layer — same month math,
    serialized instead of rendered, so prev/next/year/chip clicks never
    reload the page."""
    from app.core.calendar_utils import serialize_event

    ctx = await _calendar_month_context(db, month)
    return JSONResponse(
        {
            "currentMonth": {
                "y": ctx["currentMonth"].year,
                "m": ctx["currentMonth"].month,
                "label": ctx["currentMonth"].strftime("%B"),
                "year": ctx["currentMonth"].strftime("%Y"),
            },
            "prevMonth": ctx["prevMonth"].strftime("%Y-%m"),
            "nextMonth": ctx["nextMonth"].strftime("%Y-%m"),
            "prevYear": ctx["prevYear"].strftime("%Y-%m"),
            "nextYear": ctx["nextYear"].strftime("%Y-%m"),
            "yearMonths": ctx["yearMonths"],
            "today": ctx["today"],
            "isCurrentMonth": ctx["isCurrentMonth"],
            "calendarDays": [
                {
                    "date": d.isoformat(),
                    "day": d.day,
                    "isOut": d.month != ctx["currentMonth"].month,
                    "isToday": d.isoformat() == ctx["today"],
                    "events": [
                        serialize_event(ev)
                        for ev in ctx["eventsByDate"].get(d.isoformat(), [])
                    ],
                }
                for d in ctx["calendarDays"]
            ],
            "monthOnlyEvents": [
                {
                    **serialize_event(ev),
                    "date_label": ev.starts_at.strftime("%d %b").upper(),
                }
                for ev in ctx["monthOnlyEvents"]
            ],
        }
    )


@router.get("/casom", name="casom")
async def casom(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/casom.html", {"_db": db})


@router.get("/contact", name="contact")
async def contact(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core import lists
    from app.core.settings_cache import get_settings as _gs

    return await render_template(request, "pages/contact.html", {"_db": db, "branches": await _public_branches(db),
                                                                 "inquiry_topics": lists.items(await _gs(db), "inquiry_topics")})


@router.post("/contact", name="contact_store")
async def contact_store(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    subject: str = Form(""),
    topic: str = Form(""),
    message: str = Form(...),
    branch_id: str = Form(""),
    website: str = Form(""),                     # honeypot: real visitors never see or fill it
    db: AsyncSession = Depends(get_db),
):
    from app.models.contact_inquiry import ContactInquiry

    if website.strip():
        return _redirect(request, "/contact", "Thank you for reaching out! We will get back to you shortly.")
    if _rate_limited(request, "contact"):
        return _redirect(request, "/contact", "You have sent several messages in a short time. Please wait a few minutes and try again.", "error")
    if not name.strip() or not email.strip() or not message.strip():
        return _redirect(
            request, "/contact", "Please fill in all required fields.", "error"
        )
    if len(message) > 5000:
        return _redirect(request, "/contact", "Please keep your message under 5,000 characters.", "error")

    from app.core import geo, lists
    from app.core.platform import church_country
    from app.core.settings_cache import get_settings as _gs

    church_id = await _pick_branch_id(db, branch_id)
    topics = lists.items(await _gs(db), "inquiry_topics")
    topic_value = lists.canonical(topic, topics) or (topics[0] if topics else None)
    inquiry = ContactInquiry(
        name=name.strip()[:255],
        email=email.strip()[:255],
        phone=geo.normalize_phone(phone, await church_country(db, church_id)) or None,
        topic=topic_value,
        subject=(subject.strip()[:255] if subject and subject.strip() else (topic_value or "General question")),
        message=message.strip(),
        branch_id=church_id,
    )
    db.add(inquiry)
    await db.flush()

    from app.core import mailer

    base = _public_base_url(request)
    if mailer.valid_email(inquiry.email):
        mailer.fire(mailer.send_notice(
            inquiry.email, "We have received your message", base_url=base, kind="contact", heading="Thank you for writing to us",
            paragraphs=[f"Hello {inquiry.name.split()[0]}, your message has reached us and a real person will read it.",
                        "We usually reply within a few working days. If it is urgent, please call us or send a WhatsApp message."],
            details=[("Subject", inquiry.subject)], footnote="You can reply to this email if you want to add something."))
    mailer.fire(mailer.notify_staff(inquiry.branch_id, "A new message is waiting", base_url=base, kind="staff", heading="A new message is waiting",
                                    paragraphs=["Someone has written through the website contact form. Please open it and reply."], link_path="/admin/inquiries"))

    return _redirect(
        request,
        "/contact",
        "Thank you for reaching out! We will get back to you shortly."
        + (" A confirmation has been emailed to you." if mailer.valid_email(inquiry.email) else ""),
    )


@router.get("/get-involved", name="get_involved")
async def get_involved(request: Request, db: AsyncSession = Depends(get_db)):
    return await render_template(request, "pages/get-involved.html", {"_db": db})


@router.get("/give", name="give")
async def give(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.settings_cache import get_settings
    import json

    settings = await get_settings(db)
    from app.core import geo, lists

    giving_purposes = lists.items(settings, "giving_purposes") + [lists.OTHERS]
    church_accounts = []
    if settings.get("church_accounts"):
        try:
            church_accounts = json.loads(settings["church_accounts"])
        except Exception:
            church_accounts = []
    from app.core.branch_settings import get_branch_settings, parse_accounts

    giving_model = (settings.get("giving_model") or "central").strip()
    branches = await _public_branches(db)
    hq = next((b for b in branches if b.is_hq), None)
    from app.core.platform import platform_currency

    hq_currency = await platform_currency(db)          # one currency for all giving: HQ's

    def money_info(cur: str) -> dict:
        return {"currency": cur, "symbol": geo.currency_symbol(cur), "quick": geo.quick_amounts(cur), "min": geo.minimum_gift(cur)}

    give_branches = []
    if giving_model == "branch":
        for b in branches:
            if b.is_hq:
                give_branches.append({"id": b.id, "name": b.name, "is_hq": True, "accounts": church_accounts, **money_info(hq_currency)})
            else:
                bs = await get_branch_settings(db, b.id)
                give_branches.append({"id": b.id, "name": b.name, "is_hq": False, "accounts": parse_accounts(bs.get("church_accounts")),
                                      "country": geo.country_name(b.country_code), **money_info(hq_currency)})
    return await render_template(
        request,
        "pages/give.html",
        {
            "_db": db,
            "giving_purposes": giving_purposes,
            "church_accounts": church_accounts,
            "giving_model": giving_model,
            "give_branches": give_branches,
            "default_money": money_info(hq_currency),
        },
    )


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
    branch_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    from app.config import settings as app_settings
    from app.core.payment import initialize_paystack, initialize_flutterwave
    from app.core.settings_cache import get_settings
    from app.models.giving import Giving

    import math
    import re as _re

    if _rate_limited(request, "give", limit=15):
        return _redirect(request, "/give", "Too many attempts. Please wait a few minutes and try again.", "error")

    if not math.isfinite(amount) or amount <= 0 or amount > 100_000_000:
        return _redirect(request, "/give", "Please enter a valid amount.", "error")
    amount = round(amount, 2)
    if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()) or len(email) > 255:
        return _redirect(request, "/give", "Please enter a valid email address.", "error")

    site_settings = await get_settings(db)
    payment_gateway = (
        site_settings.get("active_giving_gateway") or payment_gateway or "paystack"
    ).strip()

    # Who is this gift for, and whose payment account takes it? (Admin switch: giving_model.)
    from app.core.branch_settings import get_branch_settings
    from app.core.hq_branch import get_hq_branch_id

    hq_id = await get_hq_branch_id(db)
    target_branch, gateway_owner, secret_override = hq_id, "hq", ""
    if (site_settings.get("giving_model") or "central") == "branch":
        target_branch = await _pick_branch_id(db, branch_id)
        if target_branch != hq_id:
            bs = await get_branch_settings(db, target_branch)
            own_gw = bs.get("active_giving_gateway") or "paystack"
            if bs.get("collects_own_giving") == "1" and bs.get(f"{own_gw}_secret_key"):
                gateway_owner, payment_gateway, secret_override = "branch", own_gw, bs[f"{own_gw}_secret_key"]
    # The purpose is always one of the list (so totals group); "Others" keeps the giver's words as a note.
    from app.core import geo, lists
    from app.models.branch import Branch

    giving_type = lists.canonical(purpose, lists.items(site_settings, "giving_purposes") + [lists.OTHERS])
    if not giving_type:
        return _redirect(request, "/give", "Please choose what your gift is for.", "error")
    other_note = purpose_text.strip()[:500] if giving_type == lists.OTHERS else ""

    # One platform currency (HQ's) for all giving, whoever's payment account takes it.
    from app.core.platform import platform_currency

    currency = await platform_currency(db)
    owner = (await db.execute(select(Branch).where(Branch.id == target_branch))).scalar_one_or_none()
    amount_minor = int(round(amount * 10 ** geo.minor_units(currency)))
    if amount_minor <= 0:
        return _redirect(request, "/give", "Please enter a valid amount.", "error")

    reference = f"swm_{secrets.token_urlsafe(16)}"
    callback_url = f"{app_settings.APP_URL}/give/callback?reference={reference}"

    giving = Giving(
        reference=reference,
        email=email.strip(),
        full_name=full_name.strip()[:255] if full_name else None,
        phone=geo.normalize_phone(phone, owner.country_code if owner else None) or None,
        amount=amount,
        currency=currency,
        giving_type=giving_type,
        note=(f"Purpose: {other_note}" if other_note else None),
        is_anonymous=anonymous,
        dedication=dedication.strip() if dedication else None,
        payment_gateway=payment_gateway,
        branch_id=target_branch,
        gateway_owner=gateway_owner,
        method="online",
        status="pending",
    )
    db.add(giving)
    await db.flush()

    authorization_url = None
    try:
        authorization_url = await _init_gateway(
            payment_gateway, secret_override or _hq_secret(site_settings, app_settings, payment_gateway), email, full_name, phone, amount,
            amount_minor, reference, callback_url, (other_note or giving_type), anonymous, dedication,
            initialize_flutterwave, initialize_paystack, currency,
        )
    except Exception:
        logging.exception("Payment gateway initialisation failed")
        giving.status = "failed"
        await db.flush()

    if not authorization_url:
        giving.status = "failed"
        await db.flush()
        return _redirect(
            request, "/give", "Unable to initialize payment. Please try again.", "error"
        )

    return RedirectResponse(url=authorization_url, status_code=303)


def _hq_secret(site_settings, app_settings, gateway: str) -> str:
    if gateway == "flutterwave":
        return site_settings.get("flutterwave_secret_key") or app_settings.FLUTTERWAVE_SECRET_KEY
    return site_settings.get("paystack_secret_key") or app_settings.PAYSTACK_SECRET_KEY


async def _init_gateway(payment_gateway, secret_key, email, full_name, phone, amount,
                        amount_minor, reference, callback_url, giving_type, anonymous, dedication,
                        initialize_flutterwave, initialize_paystack, currency="NGN"):
    """Call the selected gateway; returns the hosted-checkout URL or ``None``."""
    if payment_gateway == "flutterwave":
        authorization_url = await initialize_flutterwave(
            email=email.strip(),
            amount=amount,
            currency=currency,
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
            secret_key=secret_key,
        )
    else:
        authorization_url = await initialize_paystack(
            email=email.strip(),
            amount_minor=amount_minor,
            currency=currency,
            reference=reference,
            callback_url=callback_url,
            metadata={
                "giving_type": giving_type,
                "full_name": full_name,
                "phone": phone,
                "anonymous": anonymous,
                "dedication": dedication,
            },
            secret_key=secret_key,
        )
    return authorization_url


@router.get("/give/callback", name="give_callback")
async def give_callback(
    request: Request,
    reference: str = Query(""),
    trxref: str = Query(""),
    status: str = Query(""),
    transaction_id: str = Query(""),
    tx_ref: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    from app.core import mailer
    from app.core.giving_reconcile import reconcile
    from app.models.giving import Giving

    # Paystack uses trxref; Flutterwave uses tx_ref + transaction_id
    actual_ref = trxref or reference or tx_ref

    result = await db.execute(select(Giving).where(Giving.reference == actual_ref))
    giving = result.scalar_one_or_none()

    if not giving:
        return _redirect(request, "/give", "Payment reference not found.", "error")

    already = giving.status == "completed"
    outcome = await reconcile(db, giving, transaction_id=transaction_id, base_url=_public_base_url(request))
    if outcome == "completed":
        return _redirect(
            request, "/give",
            "Thank you for your generous giving! God bless you."
            + (" A receipt has been emailed to you." if mailer.valid_email(giving.email) and not already else ""),
            "success",
        )
    if outcome == "mismatch":
        return _redirect(
            request, "/give",
            "Your payment was received but the amount does not match what was entered. "
            "Please contact us with reference " + giving.reference + ".", "error",
        )
    if outcome == "error":
        return _redirect(
            request, "/give",
            "We could not reach the payment provider to confirm your gift yet. If you were charged, it will be recorded "
            "automatically once they confirm it; your reference is " + giving.reference + ".", "info",
        )
    if outcome == "pending":
        return _redirect(
            request, "/give",
            "Your payment is still being processed. If you completed it, we will record it shortly; "
            "otherwise you can try again.", "info",
        )
    return _redirect(
        request, "/give",
        "Payment could not be confirmed. Please contact us if you were charged.",
        "error",
    )


@router.get("/privacy-policy", name="privacy_policy")
async def privacy_policy(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.settings_cache import get_setting

    from app.core.legal_defaults import LEGAL_UPDATED, privacy_html
    from app.core.settings_cache import get_settings
    from app.core.site_content import clean_phone

    s = await get_settings(db)
    content = (s.get("privacy_policy") or "").strip() or privacy_html(
        s.get("siteName") or "Scent of Water Ministry", s.get("siteHeadquartersAddress") or "", s.get("siteContactEmail") or "", clean_phone(s.get("siteSupportPhone")))
    return await render_template(
        request,
        "pages/privacy-policy.html",
        {
            "_db": db,
            "content": content,
            "legalUpdated": LEGAL_UPDATED,
        },
    )


@router.get("/terms-of-service", name="terms_of_service")
async def terms_of_service(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.settings_cache import get_setting

    from app.core.legal_defaults import LEGAL_UPDATED, terms_html
    from app.core.settings_cache import get_settings
    from app.core.site_content import clean_phone

    s = await get_settings(db)
    content = (s.get("terms_of_service") or "").strip() or terms_html(
        s.get("siteName") or "Scent of Water Ministry", s.get("siteHeadquartersAddress") or "", s.get("siteContactEmail") or "", clean_phone(s.get("siteSupportPhone")))
    return await render_template(
        request,
        "pages/terms-of-service.html",
        {
            "_db": db,
            "content": content,
            "legalUpdated": LEGAL_UPDATED,
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
    return await render_template(request, "pages/prayer.html", {"_db": db, "branches": await _public_branches(db)})


@router.post("/prayer", name="prayer_store")
async def prayer_store(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    request_text: str = Form("", alias="request"),
    anonymous: str = Form(""),
    notify_me: str = Form(""),
    branch_id: str = Form(""),
    # Older clients/tests used these names; the public form sends name / request / anonymous.
    full_name: str = Form(""),
    prayer_request: str = Form(""),
    is_public: bool = Form(False),
    website: str = Form(""),                     # honeypot
    db: AsyncSession = Depends(get_db),
):
    from app.models.prayer_request import PrayerRequest

    if website.strip():
        return _redirect(request, "/prayer/thanks")
    if _rate_limited(request, "prayer"):
        return _redirect(request, "/prayer", "You have sent several requests in a short time. Please wait a few minutes and try again.", "error")
    text = (request_text or prayer_request or "").strip()
    who = (name or full_name or "").strip()
    hide_name = anonymous.lower() in ("1", "true", "on", "yes")
    if not text or (not who and not hide_name):
        return _redirect(
            request, "/prayer", "Please write your request and your name (or tick anonymous).", "error"
        )
    if len(text) > 5000:
        return _redirect(request, "/prayer", "Please keep your request under 5,000 characters.", "error")

    from app.core import geo
    from app.core.platform import church_country

    req = PrayerRequest(
        full_name=("Anonymous" if hide_name or not who else who)[:255],
        email=email.strip()[:255] if email else None,
        phone=geo.normalize_phone(phone, await church_country(db, await _pick_branch_id(db, branch_id))) or None,
        prayer_request=text,
        is_public=is_public,
        notify_me=notify_me.lower() in ("1", "true", "on", "yes") and bool(email.strip() or phone.strip()),
        status="new",
        branch_id=await _pick_branch_id(db, branch_id),
    )
    db.add(req)
    await db.flush()

    from app.core import mailer
    from app.core.settings_cache import get_settings
    from app.core.site_content import DEFAULT_PRAYER_RESPONSE

    site = await get_settings(db)
    response = (site.get("prayerResponseTime") or "").strip() or DEFAULT_PRAYER_RESPONSE
    base = _public_base_url(request)
    emailed = bool(req.email and mailer.valid_email(req.email))
    if emailed:
        who = req.full_name.split()[0] if req.full_name and req.full_name != "Anonymous" else "friend"
        mailer.fire(mailer.send_notice(
            req.email, "We have received your prayer request", base_url=base, kind="prayer", heading="We are standing with you",
            paragraphs=[f"Dear {who}, your prayer request has reached our prayer team. Thank you for trusting us with it.",
                        f"We pray over every request within {response}." + (" You asked us to let you know, and we will write again when it has been lifted up in prayer." if req.notify_me else ""),
                        "Your request is never published, and only the prayer team of the branch you chose can see it."],
            footnote="If this is urgent or you need to speak to someone, please reply or call us. In an emergency, contact the emergency services first."))
    mailer.fire(mailer.notify_staff(req.branch_id, "A prayer request is waiting", base_url=base, kind="staff", heading="A prayer request is waiting",
                                    paragraphs=["Someone has sent a prayer request through the website. For their privacy the request is not included in this email — please sign in to read it."],
                                    link_path="/admin/prayer-requests"))
    request.session["prayer_thanks"] = {"notify": req.notify_me, "email": req.email if emailed else "", "emailed": emailed,
                                        "branch": None}
    try:
        from app.models.branch import Branch

        b = (await db.execute(select(Branch).where(Branch.id == req.branch_id))).scalar_one_or_none()
        request.session["prayer_thanks"]["branch"] = b.name if b and not b.is_hq else None
    except Exception:
        pass
    return _redirect(request, "/prayer/thanks")


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
    if _rate_limited(request, "newsletter", limit=10):
        return _redirect(request, ref, "Too many attempts. Please try again in a few minutes.", "error")

    import re as _re

    if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()) or len(email.strip()) > 255:
        return _redirect(request, ref, "Please enter a valid email address.", "error")

    email_lower = email.strip().lower()

    from app.core import mailer
    from app.routes.public_extra import subscribe_email

    existing = (await db.execute(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email_lower))).scalar_one_or_none()
    if existing and existing.is_active:
        return _redirect(request, ref, "You are already subscribed!", "info")
    was_back = existing is not None
    await subscribe_email(db, email_lower, "footer")
    row = (await db.execute(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email_lower))).scalar_one()
    base = _public_base_url(request)
    mailer.fire(mailer.send_notice(
        email_lower, "You are subscribed", base_url=base, kind="welcome", heading="Welcome — you are on the list",
        paragraphs=["Thank you for subscribing. We will send you new messages, event notices and the occasional devotional — never spam, and never more than we should.",
                    "Every email has an unsubscribe link, and you can use the one below at any time."],
        button=("Listen to the latest message", f"{base}/messages"), unsubscribe_url=f"{base}/unsubscribe/{row.token}"))
    return _redirect(
        request, ref, "Welcome back! Your subscription has been reactivated." if was_back else "Thank you for subscribing! A welcome email is on its way.",
    )
