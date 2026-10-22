from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import and_, func, not_, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.core.auth import get_current_admin, login_admin, logout_admin
from app.core.pagination import paginate
from app.core.security import hash_password, verify_password
from app.core.audit import log_admin_action
from app.core.template import render_template
from app.core.settings_cache import get_settings, update_settings
from app.core.hq_branch import get_hq_branch_id
from app.core.access import has_perm, is_super as is_super_role

router = APIRouter(tags=["admin"])


def _redirect(request: Request, url: str, flash_msg: str | None = None, flash_type: str = "success") -> RedirectResponse:
    if flash_msg:
        request.session["flash_messages"] = [{"type": flash_type, "message": flash_msg}]
        request.session[flash_type] = flash_msg
    resp = RedirectResponse(url=url, status_code=303)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


def _admin_role_ctx(target) -> dict:
    from app.core.access import FORM_ROLES, ROLE_DESCRIPTIONS, ROLE_LABELS, normalize_role

    return {
        "role_options": [(r, ROLE_LABELS[r]) for r in FORM_ROLES],
        "role_descriptions": ROLE_DESCRIPTIONS,
        "role_now": normalize_role(target.role) if target is not None else "branch_admin",
    }


async def _known_spelling(db: AsyncSession, column, value: str | None, limit: int = 255) -> str | None:
    """Reuse the spelling already recorded for this value (case and spaces ignored), so one speaker or
    series never appears as two in filters and reports."""
    v = " ".join((value or "").split())[:limit]
    if not v:
        return None
    known = (await db.execute(select(column).where(func.lower(func.trim(column)) == v.lower()).limit(1))).scalar_one_or_none()
    return " ".join(known.split()) if known else v


async def _message_form_ctx(db, admin) -> dict:
    """Distribution facts the audio form must state truthfully for this admin."""
    from app.core.access import BRANCH_ADMIN, normalize_role
    from app.core.telegram import TelegramDistributor
    from app.models.branch import Branch

    branch = (await db.execute(select(Branch).where(Branch.id == admin.branch_id))).scalar_one_or_none() if admin.branch_id else None
    owner_is_branch = normalize_role(admin.role) == BRANCH_ADMIN and not (branch and branch.is_hq)
    from app.models.sermon import Sermon as _S

    series_list = [r[0] for r in (await db.execute(select(_S.series).where(_S.series.isnot(None), _S.series != "").distinct().order_by(_S.series))).all()]

    # Speaker suggestions: names already used (most used first), then the church's active ministers,
    # so the same preacher is always spelled the same way.
    from app.models.minister import Minister

    used = select(_S.preacher, func.count()).where(_S.preacher.isnot(None), _S.preacher != "")
    ministers_q = select(Minister.title, Minister.full_name).where(Minister.status == "active")
    if owner_is_branch:
        used, ministers_q = used.where(_S.branch_id == admin.branch_id), ministers_q.where(Minister.branch_id == admin.branch_id)
    speakers, seen = [], set()
    names = [n for n, _ in (await db.execute(used.group_by(_S.preacher).order_by(func.count().desc(), _S.preacher))).all()]
    names += [f"{t} {n}".strip() if t else n for t, n in (await db.execute(ministers_q.order_by(Minister.full_name))).all()]
    for name in names:
        name = " ".join(name.split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            speakers.append(name)

    tg = TelegramDistributor(admin.branch_id if owner_is_branch else None)
    return {
        "series_list": series_list,
        "speaker_list": speakers[:300],
        "is_branch_owner": owner_is_branch,
        "own_branch_name": branch.name if branch else "",
        "telegram_ready": await tg.configured(),
        "telegram_limit_bytes": await tg.upload_limit(),
        "telegram_large_files": tg.is_local,
    }


def _email_invitation(request: Request, *, email: str, name: str | None, role_line: str, path: str, inviter: str) -> None:
    """Email an invitation link (best effort: the "Copy invite link" button keeps working when email is not set up)."""
    from app.core import mailer

    base = _public_base_url(request)
    mailer.fire(mailer.send_notice(
        email, "You have been invited to serve", base_url=base, kind="invitation", heading="You have been invited",
        paragraphs=[f"Hello{' ' + name.split()[0] if name else ''}, {inviter} has invited you to {role_line}.",
                    "Use the button below to create your password and activate your account. The link works for 7 days."],
        button=("Activate my account", base + path), footnote="If you did not expect this invitation, you can ignore this email."))


def _foreign(admin, branch_id) -> bool:
    """True when a record belongs to a branch this admin may not act on (only HQ administrators may cross branches)."""
    from app.core.access import is_super

    if is_super(admin.role):
        return False
    return admin.branch_id is None or branch_id is None or branch_id != admin.branch_id


async def _value_taken(db: AsyncSession, column, value: str, exclude_id: int | None = None) -> bool:
    """True when another row already has ``value`` in a unique column (case-insensitive).

    Unique indexes are the last line of defence, but hitting one is a 500 for the user;
    checking first lets the form say "already exists" instead.
    """
    q = select(column.class_.id).where(func.lower(column) == value.strip().lower())
    if exclude_id is not None:
        q = q.where(column.class_.id != exclude_id)
    return (await db.execute(q.limit(1))).first() is not None


async def _get_admin(request: Request):
    admin = await get_current_admin(request)
    if not admin:
        return None
    return admin


def _public_base_url(request: Request) -> str:
    """request.base_url is http:// behind an SSL-terminating proxy; honor
    X-Forwarded-Proto so links shown to admins match what visitors use."""
    base = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto")
    if proto == "https" and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


ADMIN_THEME_PRESETS = {
    "default": {"name": "Ministry Gold", "description": "Black, grey and gold — the default.",
                "primary": "#b8891f", "primary_dark": "#8a6414", "primary_light": "#e8c870", "sidebar": "#121110"},
    "blue": {"name": "Royal Blue", "description": "Cool royal blue accent.",
             "primary": "#2563eb", "primary_dark": "#1d4ed8", "primary_light": "#93c5fd", "sidebar": "#0f172a"},
    "green": {"name": "Nature Green", "description": "Fresh green accent.",
              "primary": "#16a34a", "primary_dark": "#15803d", "primary_light": "#86efac", "sidebar": "#0f2417"},
}
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _admin_theme_css(settings: dict) -> str:
    """CSS custom-property overrides for the admin console, from the saved appearance settings.

    Only strictly validated #rrggbb values are emitted (the result is rendered unescaped
    inside a <style> block). The default preset needs no overrides.
    """
    preset = (settings.get("admin_theme_preset") or "default").strip()
    if preset == "custom":
        colors = {
            "primary": settings.get("admin_theme_custom_primary", ""),
            "primary_dark": settings.get("admin_theme_custom_primary_dark", ""),
            "primary_light": settings.get("admin_theme_custom_primary_light", ""),
            "sidebar": settings.get("admin_theme_custom_sidebar", ""),
        }
    elif preset in ADMIN_THEME_PRESETS and preset != "default":
        colors = ADMIN_THEME_PRESETS[preset]
    else:
        return ""

    colors = {k: (v or "").strip() for k, v in colors.items()}
    colors = {k: v for k, v in colors.items() if _HEX_COLOR.match(v)}
    if not colors:
        return ""

    decls = []
    if "primary" in colors:
        decls += [f"--color-gold: {colors['primary']};", f"--color-primary: {colors['primary']};",
                  f"--color-gold-tint: color-mix(in srgb, {colors['primary']} 12%, white);"]
    if "primary_dark" in colors:
        decls += [f"--color-gold-text: {colors['primary_dark']};", f"--color-primary-dark: {colors['primary_dark']};"]
    if "primary_light" in colors:
        decls += [f"--color-gold-hi: {colors['primary_light']};", f"--color-primary-light: {colors['primary_light']};"]
    if "sidebar" in colors:
        decls += [f"--color-chrome: {colors['sidebar']};", f"--color-sidebar-bg: {colors['sidebar']};"]
    return " ".join(decls)


async def _admin_context(request: Request, db: AsyncSession, active_page: str = "") -> dict:
    from app.models.member import Member
    from app.models.event import Event
    from app.models.contact_inquiry import ContactInquiry
    from app.models.sermon import Sermon
    from app.models.unit_report import UnitReport
    from app.models.admin import Admin

    admin = await get_current_admin(request)
    if not admin:
        return {}

    is_super = admin.role == "super_admin"
    is_hod = admin.role == "hod"
    branch_id = admin.branch_id if hasattr(admin, "branch_id") else None

    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    badges = {"members": 0, "events": 0, "inquiries": 0, "messages": 0, "unit_reports": 0, "prayers": 0, "branch_reports": 0, "data_check": 0}

    try:
        q = select(func.count()).select_from(Member)
        if not is_super and branch_id:
            q = q.where(Member.branch_id == branch_id)
        q = q.where(Member.created_at >= seven_days_ago)
        badges["members"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        # Calendar: published events happening in the next 7 days (event times are naive church time,
        # UTC+1). Rises when one is added for this week, falls as each one ends.
        from app.core import geo as _geo
        from app.core.platform import platform_timezone

        church_now = _geo.local_now(await platform_timezone(db))
        q = select(func.count()).select_from(Event).where(
            Event.is_published == True,  # noqa: E712
            Event.starts_at <= church_now + timedelta(days=7),
            func.coalesce(Event.ends_at, Event.starts_at) >= church_now)
        if not is_super and branch_id:
            q = q.where(_visible_event_scope(admin, await get_hq_branch_id(db)))   # own + HQ's, as on their calendar
        badges["events"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        q = select(func.count()).select_from(ContactInquiry).where(ContactInquiry.status == "new")
        if not is_super and branch_id:
            q = q.where(ContactInquiry.branch_id == branch_id)
        badges["inquiries"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        from app.models.branch_report import BranchReport

        badges["branch_reports"] = 0
        if admin.role in ("super_admin", "hq_pastoral"):
            badges["branch_reports"] = (await db.execute(select(func.count()).select_from(BranchReport).where(BranchReport.status == "submitted"))).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        from app.models.prayer_request import PrayerRequest

        badge_q = await _prayer_scope(db, admin, select(func.count()).select_from(PrayerRequest).where(PrayerRequest.status.in_(("new", "pending"))))
        badges["prayers"] = (await db.execute(badge_q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        q = select(func.count()).select_from(Sermon).where(Sermon.status == "draft")
        if not is_super and branch_id:
            q = q.where(Sermon.branch_id == branch_id)
        badges["messages"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        from app.core.access import RESIDENT_PASTOR, normalize_role

        scope = await _unit_report_scope_filter(admin, db)
        q = select(func.count()).select_from(UnitReport).where(UnitReport.status.in_(("submitted", "reviewed")))
        if scope is not None:
            q = q.where(scope)
        if normalize_role(admin.role) == RESIDENT_PASTOR:      # waiting for MY comment
            q = q.where(or_(UnitReport.resident_pastor_comment.is_(None), UnitReport.resident_pastor_comment == ""))
        else:                                                   # waiting for the office's comment
            q = q.where(or_(UnitReport.senior_pastor_comment.is_(None), UnitReport.senior_pastor_comment == ""))
        badges["unit_reports"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    if is_super:
        try:
            from app.core import geo_migration

            badges["data_check"] = len(await geo_migration.review_items(db))
        except Exception:
            logging.exception("Failed to count data-check items")

    from app.core.access import ROLE_LABELS, has_perm, normalize_role

    role_label = ROLE_LABELS.get(normalize_role(admin.role), "Administrator")
    name = admin.full_name if hasattr(admin, "full_name") else (admin.name if hasattr(admin, "name") else "Admin")
    parts = name.split() if name else ["A"]
    initials = "".join(p[0].upper() for p in parts[:2])

    branch_name = site_name = "Scent of Water Ministry"
    settings: dict = {}
    try:
        settings = await get_settings(db)
        branch_name = settings.get("siteName", site_name)
    except Exception:
        logging.exception("Unhandled error")

    try:
        from app.models.branch import Branch
        if branch_id:
            br = (await db.execute(select(Branch).where(Branch.id == branch_id))).scalar_one_or_none()
            if br:
                branch_name = br.name
    except Exception:
        logging.exception("Unhandled error")

    critical_notifications = []
    if is_super:
        try:
            from app.models.admin import AdminAuditLog
            q = (
                select(AdminAuditLog)
                .where(AdminAuditLog.reviewed_at == None)
                .where(AdminAuditLog.created_at >= datetime.utcnow() - timedelta(days=1))
                .order_by(AdminAuditLog.created_at.desc())
                .limit(6)
            )
            critical_notifications = list((await db.execute(q)).scalars().all())
        except Exception:
            logging.exception("Unhandled error")

    return {
        "can": (lambda perm, _r=admin.role: has_perm(_r, perm)),
        "role_name": (lambda r: ROLE_LABELS.get(normalize_role(r), r or "")),
        "admin_branch_id": branch_id,
        "admin": admin,
        "is_super": is_super,
        "is_hod": is_hod,
        "badges": badges,
        "role_label": role_label,
        "active_page": active_page,
        "admin_initials": initials,
        "admin_name": name,
        "admin_branch_name": branch_name,
        "critical_notifications": critical_notifications,
        "critical_alerts": [n for n in critical_notifications if _audit_severity(n.action) == "critical"],
        "adminThemeCss": _admin_theme_css(settings),
    }


async def _get_site_name(db: AsyncSession) -> str:
    try:
        settings = await get_settings(db)
        return settings.get("siteName", "Scent of Water Ministry")
    except Exception:
        return "Scent of Water Ministry"


# ─── Auth Routes ───────────────────────────────────────────────────

@router.get("/", name="admin_login")
async def admin_login_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await get_current_admin(request)
    if admin:
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    flash_error = request.session.pop("flash_error", None)
    flash_messages = request.session.pop("flash_messages", [])
    site_name = await _get_site_name(db)

    return await render_template(request, "admin/login.html", {
        "_db": db,
        "siteName": site_name,
        "flash_error": flash_error,
        "flash_messages": flash_messages,
        "adminThemeCss": "",
        "error_messages": {},
    })


@router.post("/login", name="admin_login_submit")
async def admin_login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    from app.models.admin import Admin

    if not email or not password:
        request.session["flash_error"] = "Please enter your email and password."
        return RedirectResponse(url="/admin", status_code=303)

    from app.core import throttle

    from app.core.client_ip import client_ip as _ip

    client_ip = _ip(request)
    keys = (f"login:ip:{client_ip}", f"login:email:{email.lower().strip()}")
    wait = max(throttle.is_blocked(k, limit=10 if k.startswith("login:ip") else 5) for k in keys)
    if wait:
        request.session["flash_error"] = f"Too many failed sign-in attempts. Try again in {max(1, wait // 60)} minute(s)."
        return RedirectResponse(url="/admin", status_code=303)

    result = await db.execute(select(Admin).where(Admin.email == email.lower().strip()))
    admin = result.scalar_one_or_none()

    if not admin or not verify_password(password, admin.password_hash):
        for k in keys:
            throttle.record_failure(k)
        await log_admin_action(
            db, admin_id=admin.id if admin else 0,
            action="auth.login.failed",
            request=request,
        )
        request.session["flash_error"] = "Invalid email or password."
        return RedirectResponse(url="/admin", status_code=303)
    for k in keys:
        throttle.clear(k)

    if not admin.is_active:
        request.session["flash_error"] = "Your account has been deactivated."
        return RedirectResponse(url="/admin", status_code=303)

    login_admin(request, admin.id, admin.session_version)

    await log_admin_action(
        db, admin_id=admin.id, action="auth.login.success",
        request=request,
    )

    if admin.role == "hod":
        return RedirectResponse(url="/admin/hod/reports", status_code=303)
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.post("/logout", name="admin_logout")
async def admin_logout_view(request: Request):
    logout_admin(request)
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/invitation/{token}", name="admin_invitation_accept_page")
async def admin_invitation_accept_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    from app.models.branch_admin_invitation import BranchAdminInvitation

    result = await db.execute(
        select(BranchAdminInvitation)
        .options(selectinload(BranchAdminInvitation.branch))
        .where(BranchAdminInvitation.token == token)
        .where(BranchAdminInvitation.accepted_at == None)
        .where(BranchAdminInvitation.expires_at > datetime.utcnow())
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        return RedirectResponse(url="/admin", status_code=303)

    site_name = await _get_site_name(db)
    return await render_template(request, "admin/invitation-accept.html", {
        "_db": db, "invitation": invitation, "token": token,
        "siteName": site_name, "adminThemeCss": "", "error_messages": {},
    })


@router.post("/invitation/{token}", name="admin_invitation_accept")
async def admin_invitation_accept(
    token: str, request: Request,
    password: str = Form(...), password_confirmation: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload
    from app.models.branch_admin_invitation import BranchAdminInvitation
    from app.models.admin import Admin

    result = await db.execute(
        select(BranchAdminInvitation)
        .options(selectinload(BranchAdminInvitation.branch))
        .where(BranchAdminInvitation.token == token)
        .where(BranchAdminInvitation.accepted_at == None)
        .where(BranchAdminInvitation.expires_at > datetime.utcnow())
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        return RedirectResponse(url="/admin", status_code=303)

    if password != password_confirmation:
        return await render_template(request, "admin/invitation-accept.html", {
            "_db": db, "invitation": invitation, "token": token,
            "error_messages": {"password": ["Passwords do not match."]},
        })

    if len(password) < 8:
        return await render_template(request, "admin/invitation-accept.html", {
            "_db": db, "invitation": invitation, "token": token,
            "error_messages": {"password": ["Password must be at least 8 characters."]},
        })

    admin_user = Admin(
        full_name=invitation.name or "Branch Admin",
        email=invitation.email,
        password_hash=hash_password(password),
        role="branch_super_admin",
        branch_id=invitation.branch_id,
        is_active=True,
    )
    db.add(admin_user)
    await db.flush()

    invitation.accepted_at = datetime.utcnow()
    await db.flush()

    login_admin(request, admin_user.id, admin_user.session_version)
    return RedirectResponse(url="/admin/dashboard", status_code=303)


async def _giving_trend_data(db: AsyncSession, admin=None, currency: str | None = None):
    """Six-period giving trend (tithe/offering/special) IN ONE CURRENCY, plus this period's total per currency.

    Amounts in different currencies are never added together: the chart shows ``currency`` (default: the
    viewer's own church's currency), and ``money`` lists every currency's total for the latest period.
    Returns (trend, max, money) where money = {"currency", "currencies", "this_month": [(cur, total)]}.
    """
    from app.core import geo, lists
    from app.models.branch import Branch
    from app.models.giving import Giving

    now_naive = datetime.utcnow()
    givingTrend: list[dict] = []
    maxGiving = 0.0
    money = {"currency": currency or geo.DEFAULT_CURRENCY, "currencies": [], "this_month": []}
    empty = [{"label": (now_naive - timedelta(days=30 * (5 - i))).strftime("%b"), "tithe": 0.0, "offering": 0.0, "special": 0.0} for i in range(6)]
    # Money is need-to-know: only roles with the donations permission see giving, and a branch sees only its own.
    scope_branch = None
    if admin is not None:
        if not has_perm(admin.role, "donations"):
            return empty, 0.0, money
        if not _sees_all_gifts(admin):
            scope_branch = admin.branch_id
    paid = Giving.status.in_(PAID_STATUSES)
    scoped = (lambda q: q.where(Giving.branch_id == scope_branch)) if scope_branch is not None else (lambda q: q)
    try:
        money["currencies"] = sorted(c for (c,) in (await db.execute(scoped(select(Giving.currency).where(paid, Giving.currency.isnot(None))).distinct())).all())
        if not currency:
            from app.core.platform import platform_currency

            currency = await platform_currency(db)          # the platform currency is HQ's
        money["currency"] = currency.upper()
        site = await get_settings(db)
        tithe_key = (lists.canonical("Tithe", lists.items(site, "giving_purposes")) or "Tithe").lower()
        offering_key = (lists.canonical("Offering", lists.items(site, "giving_purposes")) or "Offering").lower()
        for i in range(6):
            window_end = now_naive - timedelta(days=30 * (5 - i))
            window_start = window_end - timedelta(days=30)
            base = scoped(select(Giving.currency, Giving.giving_type, func.sum(Giving.amount)).where(
                paid, Giving.paid_at >= window_start, Giving.paid_at < window_end).group_by(Giving.currency, Giving.giving_type))
            rows = (await db.execute(base)).all()
            tithe = offering = special = 0.0
            per_currency: dict[str, float] = {}
            for cur, giving_type, total in rows:
                total = float(total or 0)
                per_currency[cur or geo.DEFAULT_CURRENCY] = per_currency.get(cur or geo.DEFAULT_CURRENCY, 0.0) + total
                if (cur or geo.DEFAULT_CURRENCY) != money["currency"]:
                    continue
                gt = (giving_type or "").strip().lower()
                if gt == tithe_key:
                    tithe += total
                elif gt == offering_key:
                    offering += total
                else:
                    special += total
            givingTrend.append({"label": window_end.strftime("%b"), "tithe": tithe, "offering": offering, "special": special})
            maxGiving = max(maxGiving, tithe, offering, special)
            if i == 5:
                money["this_month"] = sorted(per_currency.items(), key=lambda kv: (kv[0] != money["currency"], -kv[1]))
    except Exception:
        logging.exception("Failed to compute giving trend")
        return empty, 0.0, money
    return givingTrend, maxGiving, money


# ─── Dashboard ─────────────────────────────────────────────────────

@router.get("/dashboard", name="admin_dashboard")
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    ctx = await _admin_context(request, db, "admin.dashboard")

    from app.models.member import Member
    from app.models.event import Event
    from app.models.contact_inquiry import ContactInquiry
    from app.models.sermon import Sermon

    total_members = 0
    upcoming_events = []
    recent_inquiries = []

    try:
        q = select(func.count()).select_from(Member)
        if not ctx["is_super"] and admin.branch_id:
            q = q.where(Member.branch_id == admin.branch_id)
        total_members = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        q = (
            select(Event)
            .where(Event.is_published == True, Event.starts_at >= datetime.utcnow())
            .order_by(Event.starts_at.asc())
            .limit(5)
        )
        if not ctx["is_super"] and admin.branch_id:
            q = q.where(_visible_event_scope(admin, await get_hq_branch_id(db)))
        upcoming_events = list((await db.execute(q)).scalars().all())
    except Exception:
        logging.exception("Unhandled error")

    try:
        q = (
            select(ContactInquiry)
            .where(ContactInquiry.status == "new")
            .order_by(ContactInquiry.created_at.desc())
            .limit(5)
        )
        if not ctx["is_super"] and admin.branch_id:
            q = q.where(ContactInquiry.branch_id == admin.branch_id)
        recent_inquiries = list((await db.execute(q)).scalars().all())
    except Exception:
        logging.exception("Unhandled error")

    # Load real data for dashboard sections
    from app.models.department import Department
    from app.models.branch import Branch
    from app.models.admin import AdminAuditLog

    departmentsCount = 0
    branchesCount = 0
    sermonsCount = 0
    recentMembers = []
    departmentDistribution = {}
    branchPerformance = []
    activityFeed = []
    upcomingEvents = []

    try:
        departmentsCount = (await db.execute(select(func.count()).select_from(Department))).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        if ctx["is_super"]:
            branchesCount = (await db.execute(select(func.count()).select_from(Branch))).scalar_one() or 0
        else:
            branchesCount = 1 if admin.branch_id else 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        q = select(func.count()).select_from(Sermon)
        if not ctx["is_super"] and admin.branch_id:
            q = q.where(Sermon.branch_id == admin.branch_id)
        sermonsCount = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    # Recent members
    try:
        from sqlalchemy.orm import selectinload
        q = select(Member).options(selectinload(Member.branch)).order_by(Member.created_at.desc()).limit(10)
        if not ctx["is_super"] and admin.branch_id:
            q = q.where(Member.branch_id == admin.branch_id)
        recentMembers = list((await db.execute(q)).scalars().all())
    except Exception:
        logging.exception("Failed to load recent members for dashboard")

    # Member distribution. (It used to be "members by department", but members are not assigned to
    # departments, and the old query matched branch ids against department ids: invented numbers.)
    distributionTitle, distributionSubtitle = "Members by gender", "Across your church"
    countrySummary: list[dict] = []
    try:
        from app.core import geo
        from app.models.giving import Giving

        if ctx["is_super"]:
            per_branch = dict((await db.execute(select(Member.branch_id, func.count(Member.id)).group_by(Member.branch_id))).all())
            all_branches = list((await db.execute(select(Branch))).scalars().all())
            countries = {b.country_code for b in all_branches if b.country_code}
            by_country = len(countries) > 1
            groups: dict[str, int] = {}
            for b in all_branches:
                key = geo.country_name(b.country_code) if by_country else (geo.region_name(b.region_code) or b.state or "Region not set")
                groups[key or "Country not set"] = groups.get(key or "Country not set", 0) + per_branch.get(b.id, 0)
            rows = sorted(groups.items(), key=lambda kv: -kv[1])
            distributionTitle = "Members by country" if by_country else "Members by state / region"
            distributionSubtitle = "Where each member's church is" if by_country else f"All churches are in {geo.country_name(next(iter(countries), ''))}"
            # Churches by country: branches, members and this month's confirmed giving (platform currency).
            month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            gifts = dict((await db.execute(select(Giving.branch_id, func.coalesce(func.sum(Giving.amount), 0)).where(
                Giving.status.in_(PAID_STATUSES), Giving.paid_at >= month_start).group_by(Giving.branch_id))).all())
            hq_id = next((b.id for b in all_branches if b.is_hq), None)
            summary: dict[str, dict] = {}
            for b in all_branches:
                row = summary.setdefault(b.country_code or "", {"code": b.country_code, "name": geo.country_name(b.country_code) or "Country not set",
                                                                  "branches": 0, "members": 0, "giving": 0.0})
                row["branches"] += 1
                row["members"] += per_branch.get(b.id, 0)
                row["giving"] += float(gifts.get(b.id, 0) or 0) + (float(gifts.get(None, 0) or 0) if b.id == hq_id else 0.0)
            countrySummary = sorted(summary.values(), key=lambda r: (-r["members"], r["name"]))
        else:
            q = select(Member.gender, func.count(Member.id)).group_by(Member.gender)
            if admin.branch_id:
                q = q.where(Member.branch_id == admin.branch_id)
            labels = {"male": "Male", "female": "Female"}
            rows = sorted(((labels.get(g or "", "Not recorded"), n) for g, n in (await db.execute(q)).all()), key=lambda kv: -kv[1])
        total = sum(n for _, n in rows) or 1
        for i, (name, n) in enumerate(rows):
            departmentDistribution[i] = {"name": name, "members_count": n, "pct": round(n / total * 100)}
    except Exception:
        logging.exception("Failed to compute the member distribution")

    # Branch performance (grouped by branch id: two branches with the same name are two branches)
    try:
        q = (select(Branch.id, Branch.name, Branch.country_code, func.count(Member.id).label("cnt"))
             .join(Member, Member.branch_id == Branch.id, isouter=True).group_by(Branch.id, Branch.name, Branch.country_code)
             .order_by(func.count(Member.id).desc()))
        rows = (await db.execute(q)).all()
        max_branch = max((r.cnt for r in rows), default=1) if rows else 1
        for row in rows:
            bar_pct = round(row.cnt / max_branch * 100) if max_branch else 0
            branchPerformance.append({"name": row.name, "country": row.country_code, "members_count": row.cnt, "growth": 0, "bar_pct": bar_pct})
    except Exception:
        logging.exception("Unhandled error")

    # Activity feed
    try:
        q = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(10)
        activityFeed = list((await db.execute(q)).scalars().all())
    except Exception:
        logging.exception("Unhandled error")

    if ctx.get("is_super"):
        dashboardScopeLabel = "across all branches"
    elif admin.branch_id and getattr(admin, "branch", None):
        dashboardScopeLabel = f"at {admin.branch.name}"
    else:
        dashboardScopeLabel = f"at {(await get_settings(db)).get('siteName', 'SWM')} HQ"

    givingTrend, maxGiving, givingMoney = await _giving_trend_data(db, admin, (request.query_params.get("currency") or "").upper() or None)

    try:
        dash_announcements = (await _announcements_for(db, admin, 3)) if has_perm(admin.role, "announcements.view") else []
    except Exception:
        logging.exception("announcements")
        dash_announcements = []
    todo_site: list = []
    if admin.role == "super_admin":
        try:
            from app.config import settings as _cfg
            from app.core.readiness import site_readiness

            todo_site = [i for i in await site_readiness(db, await get_settings(db), _cfg.APP_URL) if not i["ok"]]
        except Exception:
            logging.exception("readiness")
    return await render_template(request, "admin/dashboard.html", {
        "_db": db,
        "site_todo": todo_site,
        "announcements": dash_announcements,
        "title": "Dashboard",
        "headerIcon": "ri-dashboard-3-line",
        "dashboardScopeLabel": dashboardScopeLabel,
        "total_members": total_members,
        "upcoming_events": upcoming_events,
        "recent_inquiries": recent_inquiries,
        "current_date": datetime.utcnow().strftime("%A, %b %d, %Y"),
        "membersCount": total_members,
        "eventsCount": len(upcoming_events),
        "newInquiries": len(recent_inquiries),
        "departmentsCount": departmentsCount,
        "branchesCount": branchesCount,
        "sermonsCount": sermonsCount,
        "membersDelta": 0,
        "eventsDelta": 0,
        "inquiriesDelta": 0,
        "messagesDelta": 0,
        "attendanceTrend": [],
        "branchesForChart": {},
        "maxAttendance": 0,
        "weeklyAttendance": [],
        "givingTrend": givingTrend,
        "maxGiving": maxGiving,
        "givingMoney": givingMoney,
        "recentMembers": recentMembers,
        "departmentDistribution": departmentDistribution,
        "distributionTitle": distributionTitle, "distributionSubtitle": distributionSubtitle, "countrySummary": countrySummary,
        "branchPerformance": branchPerformance,
        "activityFeed": activityFeed,
        "upcomingEvents": upcoming_events,
        "recentMessages": [],
        "recentInquiries": recent_inquiries,
        "isSuper": ctx.get("is_super", False),
        "securityAlerts": [],
        **ctx,
    })


@router.get("/metrics", name="admin.dashboard.metrics")
async def admin_dashboard_metrics(request: Request, db: AsyncSession = Depends(get_db), mode: str = Query("7d"), start_date: str = Query(None), end_date: str = Query(None)):
    mode = (mode or "7d").lower()
    admin = await _get_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from app.models.member import Member
    from app.models.event import Event
    from app.models.branch import Branch
    from app.models.department import Department
    from app.models.sermon import Sermon
    from app.models.contact_inquiry import ContactInquiry
    from app.models.unit_report import UnitReport
    now = datetime.utcnow()
    now_naive = datetime.utcnow()
    def _parse(q):
        try:
            dt = datetime.fromisoformat(q)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except:
            return None
    sd = _parse(start_date); ed = _parse(end_date)
    if mode == "7d": sd = now_naive - timedelta(days=7)
    elif mode == "30d": sd = now_naive - timedelta(days=30)
    elif mode == "90d": sd = now_naive - timedelta(days=90)
    elif mode == "1y": sd = now_naive - timedelta(days=365)
    elif mode == "all": sd = None
    elif mode == "range":
        if not sd: sd = now_naive - timedelta(days=7)
        if not ed: ed = now_naive
    is_super = admin.role == "super_admin"; branch_id = admin.branch_id
    def _scope(q, model):
        if not is_super and branch_id and hasattr(model, "branch_id"):
            return q.where(model.branch_id == branch_id)
        return q
    async def _count(model, extra=None):
        q = select(func.count()).select_from(model)
        q = _scope(q, model)
        if extra is not None:
            for c in extra: q = q.where(c)
        if sd and hasattr(model, "created_at"): q = q.where(model.created_at >= sd)
        if ed and hasattr(model, "created_at"): q = q.where(model.created_at <= ed)
        try: return (await db.execute(q)).scalar_one() or 0
        except: return 0
    membersCount = await _count(Member)
    eventsCount = await _count(Event, [Event.is_published == True])
    try:
        branchesCount = (await db.execute(select(func.count()).select_from(Branch))).scalar_one() or 0 if is_super else 1
    except: branchesCount = 1 if not is_super else 0
    try:
        departmentsCount = (await db.execute(select(func.count()).select_from(Department))).scalar_one() or 0
    except: departmentsCount = 0
    sermonsCount = await _count(Sermon)
    inquiriesCount = await _count(ContactInquiry, [ContactInquiry.status == "new"])
    try:
        q = select(func.count()).select_from(Member).where(Member.created_at >= now_naive - timedelta(days=7))
        q = _scope(q, Member); recent_members = (await db.execute(q)).scalar_one() or 0
        q2 = select(func.count()).select_from(Member).where(Member.created_at >= now_naive - timedelta(days=14), Member.created_at < now_naive - timedelta(days=7))
        q2 = _scope(q2, Member); prev_members = (await db.execute(q2)).scalar_one() or 0
        membersDelta = recent_members - prev_members
    except: membersDelta = 0
    try:
        q = select(func.count()).select_from(Event).where(Event.is_published == True, Event.starts_at >= now_naive)
        q = _scope(q, Event); eventsDelta = (await db.execute(q)).scalar_one() or 0
    except: eventsDelta = 0
    try:
        q = select(func.count()).select_from(ContactInquiry).where(ContactInquiry.status == "new")
        q = _scope(q, ContactInquiry); inquiriesDelta = (await db.execute(q)).scalar_one() or 0
    except: inquiriesDelta = 0
    try:
        q = select(func.count()).select_from(Sermon).where(Sermon.status == "draft")
        q = _scope(q, Sermon); messagesDelta = (await db.execute(q)).scalar_one() or 0
    except: messagesDelta = 0
    try:
        bq = select(Branch).where(Branch.is_active == True).order_by(Branch.is_hq.desc(), Branch.name)  # noqa: E712
        if not is_super:
            bq = bq.where(Branch.id == branch_id)              # a branch sees only itself
        branches_list = list((await db.execute(bq)).scalars().all())
    except: branches_list = []
    palette = ["#c9a227","#34d399","#38bdf8","#a78bfa","#fb923c"]
    # Average Sunday attendance as each branch reported it to HQ (Branch Reports) - real figures only.
    attendanceTrend = []; maxAttendance = 0
    try:
        from app.models.branch_report import BranchReport

        months = []
        y, m = now_naive.year, now_naive.month
        for _ in range(6):
            months.append(f"{y:04d}-{m:02d}"); m -= 1
            if m == 0: y, m = y - 1, 12
        months.reverse()
        rows = (await db.execute(select(BranchReport).where(BranchReport.report_month.in_(months), BranchReport.status.in_(("submitted", "acknowledged")),
                                                            BranchReport.branch_id.in_([b.id for b in branches_list] or [-1])))).scalars().all()
        got = {(r.branch_id, r.report_month): (r.metrics or {}).get("avg_attendance") for r in rows}
        for mk in months:
            br_vals = {}
            for b in branches_list:
                v = got.get((b.id, mk)) or 0
                br_vals[b.name] = v; maxAttendance = max(maxAttendance, v)
            attendanceTrend.append({"label": datetime.strptime(mk, "%Y-%m").strftime("%b"), "branches": br_vals})
    except: pass
    weeklyAttendance = []      # no per-service attendance is recorded yet, so nothing is invented
    givingTrend, maxGiving, givingMoney = await _giving_trend_data(db, admin, (request.query_params.get("currency") or "").upper() or None)
    return JSONResponse({"stats": {"members": {"value": membersCount, "delta": membersDelta, "suffix": ""}, "events": {"value": eventsCount, "delta": eventsDelta, "suffix": ""}, "inquiries": {"value": inquiriesCount, "delta": inquiriesDelta, "suffix": ""}, "departments": {"value": departmentsCount, "delta": 0, "suffix": ""}, "branches": {"value": branchesCount, "delta": 0, "suffix": ""}, "messages": {"value": sermonsCount, "delta": messagesDelta, "suffix": ""}}, "attendanceTrend": attendanceTrend, "branches": [b.name for b in branches_list], "palette": palette, "maxAttendance": maxAttendance, "weeklyAttendance": weeklyAttendance, "givingTrend": givingTrend, "maxGiving": maxGiving, "givingMoney": givingMoney, "range": {"mode": mode}})


# ─── Members ───────────────────────────────────────────────────────

@router.get("/members", name="admin_members")
async def admin_members(request: Request, db: AsyncSession = Depends(get_db), q: str = Query(""), page: int = Query(1, ge=1),
                        branch_id: str = Query(""), country: str = Query("")):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.branch import Branch
    from app.models.member import Member
    from sqlalchemy.orm import selectinload
    query = select(Member).options(selectinload(Member.branch))

    sees_all = admin.role == "super_admin" or not admin.branch_id
    if not sees_all:
        query = query.where(Member.branch_id == admin.branch_id)
    else:
        if _to_int(branch_id):
            query = query.where(Member.branch_id == _to_int(branch_id))
        if country:
            query = query.where(Member.branch_id.in_(select(Branch.id).where(Branch.country_code == country.upper())))

    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Member.first_name.ilike(pattern), Member.last_name.ilike(pattern), Member.email.ilike(pattern),
                                Member.phone.ilike(pattern), Member.city.ilike(pattern)))

    query = query.order_by(Member.created_at.desc())
    pagination = await paginate(db, query, page=page, per_page=20)
    kw = dict(q=q, branch_id=branch_id, country=country)
    pagination["prev_url"] = _qs("/admin/members", page=page - 1, **kw) if pagination["has_prev"] else None
    pagination["next_url"] = _qs("/admin/members", page=page + 1, **kw) if pagination["has_next"] else None
    branches, country_options = [], []
    if sees_all:
        from app.core import geo

        branches = list((await db.execute(select(Branch).order_by(Branch.is_hq.desc(), Branch.name))).scalars().all())
        country_options = sorted({(b.country_code, geo.country_name(b.country_code)) for b in branches if b.country_code}, key=lambda x: x[1])

    ctx = await _admin_context(request, db, "admin.members")
    return await render_template(request, "admin/members.html", {
        "_db": db, "members": pagination["items"], "pagination": pagination, "search": q,
        "filter_branch_id": branch_id, "filter_country": country.upper(), "branches": branches, "country_options": country_options,
        "title": "Members", "headerIcon": "ri-team-line", **ctx,
    })


@router.get("/members/create", name="admin_members_create_page")
async def admin_members_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    ctx = await _admin_context(request, db, "admin.members")
    return await render_template(request, "admin/member-form.html", {
        "_db": db, "member": None,
        "title": "New Member", "headerIcon": "ri-team-line", **ctx,
    })


@router.get("/members/{member_id}/edit", name="admin_members_edit_page")
async def admin_members_edit_page(member_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    from sqlalchemy.orm import selectinload

    query = select(Member).options(selectinload(Member.branch)).where(Member.id == member_id)
    if admin.role != "super_admin" and admin.branch_id:
        query = query.where(Member.branch_id == admin.branch_id)
    member = (await db.execute(query)).scalar_one_or_none()
    if not member:
        return _redirect(request, "/admin/members", "Member not found.", "error")

    branches = []
    if admin.role == "super_admin":
        from app.models.branch import Branch
        branches = list((await db.execute(select(Branch).order_by(Branch.name))).scalars().all())

    ctx = await _admin_context(request, db, "admin.members")
    return await render_template(request, "admin/member-form.html", {
        "_db": db, "member": member, "branches": branches,
        "title": "Edit Member", "headerIcon": "ri-team-line", **ctx,
    })


@router.get("/members/import", name="admin_members_import_page")
async def admin_members_import_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    ctx = await _admin_context(request, db, "admin.members")
    return await render_template(request, "admin/members-import.html", {
        "_db": db,
        "title": "Bulk Import Members", "headerIcon": "ri-team-line", **ctx,
    })


async def _branch_currency(db: AsyncSession, branch_id: int | None) -> str:
    """Currency for amounts recorded by any church: always the platform currency (HQ's)."""
    from app.core.platform import platform_currency

    return await platform_currency(db)


async def _branch_country(db: AsyncSession, branch_id: int | None) -> str:
    """The church's country: the default for local phone numbers and residence."""
    from app.core import geo
    from app.models.branch import Branch

    if branch_id:
        cc = (await db.execute(select(Branch.country_code).where(Branch.id == branch_id))).scalar_one_or_none()
        if cc:
            return cc
    hq = (await db.execute(select(Branch.country_code).where(Branch.is_hq == True))).scalar_one_or_none()  # noqa: E712
    return hq or geo.DEFAULT_COUNTRY


async def _apply_member_location(db: AsyncSession, member, country: str, region: str, city: str) -> str | None:
    """Optional residence. A country given must be real; a region must belong to it. Error or None."""
    from app.core import geo

    cc, rc = (country or "").strip().upper(), (region or "").strip().upper()
    if not cc:
        member.country_code = member.region_code = None
        member.city = _tidy_city(city)
        return None
    if not geo.is_country(cc):
        return "Choose the member's country from the list."
    if rc and not geo.is_region(cc, rc):
        return f"Choose a state / region from {geo.country_name(cc)}'s list."
    member.country_code, member.region_code = cc, rc or None
    member.city = await _canonical_city(db, rc, city)
    return None


@router.post("/members", name="admin_members_store")
async def admin_members_store(
    request: Request, db: AsyncSession = Depends(get_db),
    first_name: str = Form(...), last_name: str = Form(...),
    email: str = Form(""), phone: str = Form(""), gender: str = Form(""),
    country_code: str = Form(""), region_code: str = Form(""), city: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.core import geo, lists
    from app.models.member import Member

    if gender.strip() and not lists.gender(gender):
        return _redirect(request, "/admin/members/create", "Choose the member's gender from the list.", "error")
    member = Member(
        first_name=first_name.strip(), last_name=last_name.strip(),
        email=email.strip() if email else None,
        phone=geo.normalize_phone(phone, await _branch_country(db, admin.branch_id)) or None,
        gender=lists.gender(gender),
        branch_id=admin.branch_id,
    )
    if err := await _apply_member_location(db, member, country_code, region_code, city):
        return _redirect(request, "/admin/members/create", err, "error")
    db.add(member)
    await db.flush()
    return _redirect(request, "/admin/members", "Member added successfully.")


@router.get("/members/import-template", name="admin_members_import_template")
async def admin_members_import_template(request: Request):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["first_name", "last_name", "email", "phone", "gender", "date_of_birth", "country", "state_region", "city"])
    writer.writerow(["John", "Doe", "john@example.com", "08012345678", "male", "1990-01-15", "Nigeria", "Ebonyi", "Abakaliki"])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=member_import_template.csv"},
    )


@router.get("/members/{member_id}", name="admin_members_show")
async def admin_members_show(member_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    from sqlalchemy.orm import selectinload

    query = select(Member).options(selectinload(Member.branch)).where(Member.id == member_id)
    if admin.role != "super_admin" and admin.branch_id:
        query = query.where(Member.branch_id == admin.branch_id)
    member = (await db.execute(query)).scalar_one_or_none()
    if not member:
        return _redirect(request, "/admin/members", "Member not found.", "error")

    ctx = await _admin_context(request, db, "admin.members")
    from app.models.branch import Branch as _Branch
    transfer_branches = list((await db.execute(select(_Branch).where(_Branch.is_active == True).order_by(_Branch.is_hq.desc(), _Branch.name))).scalars().all()) if admin.role == "super_admin" else []  # noqa: E712
    return await render_template(request, "admin/member-show.html", {"transfer_branches": transfer_branches, 
        "_db": db, "member": member,
        "title": member.first_name + " " + member.last_name, "headerIcon": "ri-team-line", **ctx,
    })


@router.post("/members/import", name="admin_members_import")
async def admin_members_import(
    request: Request, db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.core import geo, lists
    from app.models.member import Member

    content = (await file.read()).decode("utf-8-sig", errors="replace")    # -sig: Excel's byte-order mark
    reader = csv.DictReader(io.StringIO(content))
    home = await _branch_country(db, admin.branch_id)
    count, skipped, unknown = 0, 0, {}

    def note(field: str, value: str) -> None:
        unknown.setdefault(field, set()).add(value[:30])

    for raw in reader:
        row = {str(k or "").strip().lower(): str(v or "").strip() for k, v in raw.items()}
        if not row.get("first_name") or not row.get("last_name"):
            skipped += 1
            continue
        gnd = lists.gender(row.get("gender"))
        if row.get("gender") and not gnd:
            note("gender", row["gender"])
        cc = geo.match_country(row.get("country")) if row.get("country") else None
        if row.get("country") and not cc:
            note("country", row["country"])
        region_text = row.get("state_region") or row.get("state") or ""
        rc = geo.match_region(cc, region_text) if cc and region_text else None
        if cc and region_text and not rc:
            note("state/region", region_text)
        dob = None
        if row.get("date_of_birth"):
            try:
                dob = datetime.fromisoformat(row["date_of_birth"]).date()
            except ValueError:
                note("date_of_birth (use YYYY-MM-DD)", row["date_of_birth"])
        db.add(Member(
            first_name=row["first_name"][:255], last_name=row["last_name"][:255],
            email=row.get("email") or None, phone=geo.normalize_phone(row.get("phone"), cc or home) or None,
            gender=gnd, date_of_birth=dob, country_code=cc, region_code=rc,
            city=await _canonical_city(db, rc, row.get("city")), branch_id=admin.branch_id,
        ))
        count += 1
    await db.flush()
    msg = f"{count} member{'s' if count != 1 else ''} imported."
    if skipped:
        msg += f" {skipped} row{'s' if skipped != 1 else ''} without a first and last name skipped."
    if unknown:
        parts = [f"{field} ({', '.join(sorted(vals)[:5])})" for field, vals in unknown.items()]
        msg += " Left blank because they are not in the lists: " + "; ".join(parts) + "."
    return _redirect(request, "/admin/members", msg, "info" if (skipped or unknown) else "success")


# ─── HODs ──────────────────────────────────────────────────────────

@router.get("/hods", name="admin_hods")
async def admin_hods(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.admin import Admin as AdminModel
    hod_q = (
        select(AdminModel)
        .options(selectinload(AdminModel.branch), selectinload(AdminModel.department))
        .where(AdminModel.role == "hod")
        .order_by(AdminModel.created_at.desc())
    )
    from app.models.hod_invitation import HodInvitation
    inv_q = (
        select(HodInvitation)
        .options(selectinload(HodInvitation.branch), selectinload(HodInvitation.department))
        .order_by(HodInvitation.created_at.desc())
        .limit(10)
    )
    if not is_super_role(admin.role):                       # a branch sees only its own department heads
        hod_q = hod_q.where(AdminModel.branch_id == admin.branch_id)
        inv_q = inv_q.where(HodInvitation.branch_id == admin.branch_id)
    hods = list((await db.execute(hod_q)).scalars().all())
    invitations = list((await db.execute(inv_q)).scalars().all())

    from app.models.department import Department
    result = await db.execute(select(Department).order_by(Department.name))
    departments = list(result.scalars().all())

    from app.models.branch import Branch
    branch_q = select(Branch).where(Branch.is_active == True).order_by(Branch.name)
    if not is_super_role(admin.role):
        branch_q = branch_q.where(Branch.id == admin.branch_id)
    branches = list((await db.execute(branch_q)).scalars().all())

    ctx = await _admin_context(request, db, "admin.hods")
    return await render_template(request, "admin/hods.html", {
        "_db": db, "hods": hods, "departments": departments, "branches": branches, "invitations": invitations,
        "title": "Department HODs", "headerIcon": "ri-user-star-line", **ctx,
    })


@router.get("/hods/invite", name="admin_hods_invite_page")
async def admin_hods_invite_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.department import Department
    from app.models.branch import Branch
    departments = list((await db.execute(select(Department).order_by(Department.name))).scalars().all())
    invite_branch_q = select(Branch).where(Branch.is_active == True).order_by(Branch.name)
    if not is_super_role(admin.role):
        invite_branch_q = invite_branch_q.where(Branch.id == admin.branch_id)
    branches = list((await db.execute(invite_branch_q)).scalars().all())

    ctx = await _admin_context(request, db, "admin.hods")
    return await render_template(request, "admin/hod-form.html", {
        "_db": db, "hod": None, "departments": departments, "branches": branches,
        "title": "Invite HOD", "headerIcon": "ri-user-star-line", **ctx,
    })


@router.get("/hods/{hod_id}/edit", name="admin_hods_edit_page")
async def admin_hods_edit_page(hod_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.admin import Admin as AdminModel
    from app.models.department import Department
    hod = (await db.execute(
        select(AdminModel)
        .options(selectinload(AdminModel.branch), selectinload(AdminModel.department))
        .where(AdminModel.id == hod_id, AdminModel.role == "hod")
    )).scalar_one_or_none()
    if not hod:
        return _redirect(request, "/admin/hods", "HOD not found.", "error")
    if _foreign(admin, hod.branch_id):
        return _redirect(request, "/admin/hods", "You can only manage records of your own branch.", "error")

    departments = list((await db.execute(select(Department).order_by(Department.name))).scalars().all())
    ctx = await _admin_context(request, db, "admin.hods")
    return await render_template(request, "admin/hod-form.html", {
        "_db": db, "hod": hod, "departments": departments, "branches": [],
        "title": "Edit HOD", "headerIcon": "ri-user-star-line", **ctx,
    })


@router.post("/hods/invite", name="admin_hods_invite")
async def admin_hods_invite(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), email: str = Form(...),
    department_id: int = Form(...), branch_id: int = Form(...),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.core.access import is_super
    from app.models.hod_invitation import HodInvitation
    if not is_super(admin.role):
        branch_id = admin.branch_id           # never trust the posted branch
    if branch_id is None:
        return _redirect(request, "/admin/hods/invite", "Choose the branch this department head belongs to.", "error")
    from app.models.admin import Admin as _AdminRow
    from app.models.branch import Branch as _BranchRow
    from app.models.department import Department as _DeptRow
    if (await db.execute(select(_AdminRow.id).where(func.lower(_AdminRow.email) == email.strip().lower()))).first():
        return _redirect(request, "/admin/hods/invite", "Someone already has a console account with that email.", "error")
    if (await db.execute(select(_DeptRow.id).where(_DeptRow.id == department_id))).first() is None:
        return _redirect(request, "/admin/hods/invite", "Choose a department.", "error")
    target_branch = (await db.execute(select(_BranchRow).where(_BranchRow.id == branch_id))).scalar_one_or_none()
    if target_branch is None or not target_branch.is_active:
        return _redirect(request, "/admin/hods/invite", "Choose an active branch.", "error")
    token = secrets.token_urlsafe(32)
    invitation = HodInvitation(
        name=name.strip() if name else None,
        email=email.strip().lower(),
        department_id=department_id,
        branch_id=branch_id,
        invited_by_admin_id=admin.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invitation)
    await db.flush()
    _email_invitation(request, email=invitation.email, name=invitation.name, role_line=f"serve as head of a department at {target_branch.name}", path=f"/admin/hod/invitation/{token}", inviter=admin.full_name)
    return _redirect(request, "/admin/hods", "Invitation created and emailed. You can also copy the invite link under Recent invitations.")


# ─── HarvestTrack ──────────────────────────────────────────────────

HARVEST_STAGES = ["new", "contacted", "connected_to_group", "attending_class", "committed", "inactive"]
HARVEST_STAGE_LABELS = {
    "new": "New", "contacted": "Contacted", "connected_to_group": "Connected to Group",
    "attending_class": "Attending Class", "committed": "Committed", "inactive": "Inactive",
}
HARVEST_SOURCES = ["invite", "walk_in", "outreach"]
HARVEST_SOURCE_LABELS = {"invite": "Invite", "walk_in": "Walk-in", "outreach": "Outreach"}
HARVEST_ROLES = ["field_member", "team_lead", "admin"]
HARVEST_ROLE_LABELS = {"field_member": "Field Member", "team_lead": "Team Lead", "admin": "Admin"}
HARVEST_DROPOFF_DAYS = 14
HARVEST_RETENTION_WINDOWS = [30, 60, 90]


def _harvest_date_range(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    try:
        sd = datetime.fromisoformat(start_date) if start_date else now - timedelta(days=30)
    except ValueError:
        sd = now - timedelta(days=30)
    try:
        ed = datetime.fromisoformat(end_date) if end_date else now
    except ValueError:
        ed = now
    if ed < sd:
        sd, ed = ed, sd
    ed = ed + timedelta(days=1) - timedelta(seconds=1)
    return sd, ed


def _harvest_last_activity(contact) -> datetime:
    latest = contact.created_at
    for f in contact.follow_ups:
        if f.created_at and f.created_at > latest:
            latest = f.created_at
    return latest


def _harvest_retained(contact, window_days: int) -> bool:
    if not contact.committed_at:
        return False
    window_end = contact.committed_at + timedelta(days=window_days)
    for f in contact.follow_ups:
        if f.created_at and contact.committed_at <= f.created_at <= window_end:
            return True
    for r in contact.reminders:
        if r.is_completed and r.updated_at and contact.committed_at <= r.updated_at <= window_end:
            return True
    return False


@router.get("/harvesttrack", name="admin_harvesttrack")
async def admin_harvesttrack(
    request: Request, db: AsyncSession = Depends(get_db),
    tab: str = Query("dashboard"),
    start_date: str = Query(""), end_date: str = Query(""),
    team_id: int | None = Query(None), source: str = Query(""),
    stage: str = Query(""), q: str = Query(""),
    branch_id: int | None = Query(None),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.harvest import HarvestWorker, HarvestContact, HarvestTeam
    from app.models.branch import Branch

    is_super = admin.role == "super_admin"
    scope_branch_id = branch_id if (is_super and branch_id) else (None if is_super else admin.branch_id)
    now = datetime.utcnow()
    sd, ed = _harvest_date_range(start_date, end_date)

    branches = []
    if is_super:
        branches = list((await db.execute(select(Branch).order_by(Branch.name))).scalars().all())

    teams_query = select(HarvestTeam).options(selectinload(HarvestTeam.branch)).order_by(HarvestTeam.name)
    if scope_branch_id:
        teams_query = teams_query.where(HarvestTeam.branch_id == scope_branch_id)
    teams = list((await db.execute(teams_query)).scalars().all())

    workers_query = select(HarvestWorker).options(
        selectinload(HarvestWorker.branch), selectinload(HarvestWorker.team), selectinload(HarvestWorker.contacts)
    ).order_by(HarvestWorker.created_at.desc())
    if scope_branch_id:
        workers_query = workers_query.where(HarvestWorker.branch_id == scope_branch_id)
    workers = list((await db.execute(workers_query)).scalars().all())

    contacts_query = select(HarvestContact).options(
        selectinload(HarvestContact.worker).selectinload(HarvestWorker.team),
        selectinload(HarvestContact.follow_ups),
        selectinload(HarvestContact.reminders),
    ).order_by(HarvestContact.created_at.desc())
    if scope_branch_id or team_id:
        scoped_ids = [w.id for w in workers if not team_id or w.team_id == team_id]
        contacts_query = contacts_query.where(HarvestContact.worker_id.in_(scoped_ids)) if scoped_ids else contacts_query.where(False)
    if source:
        contacts_query = contacts_query.where(HarvestContact.source == source)
    all_contacts = list((await db.execute(contacts_query)).scalars().all())

    # 7.1 Dashboard
    total_contacts = len(all_contacts)
    stage_counts = {s: 0 for s in HARVEST_STAGES}
    for c in all_contacts:
        stage_counts[c.current_stage if c.current_stage in HARVEST_STAGES else "new"] += 1
    committed_count = stage_counts["committed"]
    conversion_rate = round(committed_count / total_contacts * 100, 1) if total_contacts else 0.0
    touchpoints_in_range = sum(
        1 for c in all_contacts for f in c.follow_ups if f.created_at and sd <= f.created_at <= ed
    )

    # 7.4 Retention (computed here so the Dashboard summary and Retention tab share one pass)
    committed_contacts = [c for c in all_contacts if c.current_stage == "committed" and c.committed_at]
    retention = {}
    for window in HARVEST_RETENTION_WINDOWS:
        eligible = [c for c in committed_contacts if (now - c.committed_at).days >= window]
        retained = [c for c in eligible if _harvest_retained(c, window)]
        retention[window] = {
            "eligible": len(eligible), "retained": len(retained),
            "pct": round(len(retained) / len(eligible) * 100, 1) if eligible else None,
        }

    # 7.2 Team Performance
    def _worker_metrics(w):
        assigned = [c for c in all_contacts if c.worker_id == w.id]
        touchpoints = sum(1 for c in assigned for f in c.follow_ups if f.worker_id == w.id and f.created_at and sd <= f.created_at <= ed)
        progressed = sum(1 for c in assigned if c.current_stage != "new")
        committed = sum(1 for c in assigned if c.current_stage == "committed")
        return {
            "worker": w, "assigned": len(assigned), "touchpoints": touchpoints,
            "progressed": progressed, "committed": committed,
            "conversion_rate": round(committed / len(assigned) * 100, 1) if assigned else 0.0,
        }
    worker_performance = [_worker_metrics(w) for w in workers]
    team_performance = {}
    for row in worker_performance:
        key = row["worker"].team_id
        bucket = team_performance.setdefault(key, {
            "team": row["worker"].team, "assigned": 0, "touchpoints": 0, "progressed": 0, "committed": 0,
        })
        bucket["assigned"] += row["assigned"]
        bucket["touchpoints"] += row["touchpoints"]
        bucket["progressed"] += row["progressed"]
        bucket["committed"] += row["committed"]
    for bucket in team_performance.values():
        bucket["conversion_rate"] = round(bucket["committed"] / bucket["assigned"] * 100, 1) if bucket["assigned"] else 0.0

    # 7.3 Drop-off Alerts — active contacts (not inactive, not committed — those are tracked via retention)
    dropoffs = []
    for c in all_contacts:
        if c.current_stage in ("inactive", "committed"):
            continue
        last_activity = _harvest_last_activity(c)
        days_since = (now - last_activity).days
        if days_since >= HARVEST_DROPOFF_DAYS:
            dropoffs.append({"contact": c, "last_activity": last_activity, "days_since": days_since})
    dropoffs.sort(key=lambda d: d["days_since"], reverse=True)

    # Contacts tab — search + stage filter (team/source already applied above)
    contacts_view = all_contacts
    if stage:
        contacts_view = [c for c in contacts_view if c.current_stage == stage]
    if q:
        pattern = q.strip().lower()
        contacts_view = [
            c for c in contacts_view
            if pattern in (c.full_name or "").lower() or pattern in (c.phone or "").lower() or pattern in (c.email or "").lower()
        ]

    from urllib.parse import urlencode
    base_params = {
        "start_date": sd.strftime("%Y-%m-%d"),
        "end_date": (ed - timedelta(days=1) + timedelta(seconds=1)).strftime("%Y-%m-%d"),
    }
    if team_id:
        base_params["team_id"] = team_id
    if source:
        base_params["source"] = source
    if branch_id:
        base_params["branch_id"] = branch_id
    tab_urls = {
        t: "/admin/harvesttrack?" + urlencode({**base_params, "tab": t})
        for t in ("dashboard", "team", "dropoff", "retention", "contacts", "teams")
    }
    export_url = "/admin/harvesttrack/export.csv?" + urlencode(base_params)

    ctx = await _admin_context(request, db, "admin.harvesttrack")
    return await render_template(request, "admin/harvesttrack.html", {
        "_db": db,
        "tab_urls": tab_urls, "export_url": export_url,
        "tab": tab if tab in ("dashboard", "team", "dropoff", "retention", "contacts", "teams") else "dashboard",
        "filters": {
            "start_date": sd.strftime("%Y-%m-%d"), "end_date": (ed - timedelta(days=1) + timedelta(seconds=1)).strftime("%Y-%m-%d"),
            "team_id": team_id, "source": source, "stage": stage, "q": q, "branch_id": branch_id,
        },
        "isSuper": is_super, "branches": branches, "teams": teams, "workers": workers,
        "stages": HARVEST_STAGES, "stageLabels": HARVEST_STAGE_LABELS,
        "sources": HARVEST_SOURCES, "sourceLabels": HARVEST_SOURCE_LABELS,
        "roles": HARVEST_ROLES, "roleLabels": HARVEST_ROLE_LABELS,
        "totalContacts": total_contacts, "stageCounts": stage_counts,
        "conversionRate": conversion_rate, "touchpointsInRange": touchpoints_in_range,
        "retention": retention, "retentionWindows": HARVEST_RETENTION_WINDOWS,
        "workerPerformance": worker_performance, "teamPerformance": team_performance,
        "dropoffs": dropoffs, "dropoffDays": HARVEST_DROPOFF_DAYS,
        "contacts": contacts_view,
        "title": "HarvestTrack", "headerIcon": "ri-seedling-line", **ctx,
    })


async def _harvest_form_options(db: AsyncSession, admin):
    """Branches (super admin only) and teams (scoped to the admin's branch) for the worker/team forms."""
    from app.models.harvest import HarvestTeam
    from app.models.branch import Branch

    branches = []
    if admin.role == "super_admin":
        branches = list((await db.execute(select(Branch).order_by(Branch.name))).scalars().all())
    teams_query = select(HarvestTeam).order_by(HarvestTeam.name)
    if admin.role != "super_admin" and admin.branch_id:
        teams_query = teams_query.where(HarvestTeam.branch_id == admin.branch_id)
    teams = list((await db.execute(teams_query)).scalars().all())
    return branches, teams


@router.get("/harvesttrack/teams/create", name="admin_harvesttrack_teams_create_page")
async def admin_harvesttrack_teams_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    branches, _ = await _harvest_form_options(db, admin)
    ctx = await _admin_context(request, db, "admin.harvesttrack")
    return await render_template(request, "admin/harvest-team-form.html", {
        "_db": db, "team": None, "branches": branches,
        "title": "New Team", "headerIcon": "ri-seedling-line", **ctx,
    })


@router.get("/harvesttrack/teams/{team_id}/edit", name="admin_harvesttrack_teams_edit_page")
async def admin_harvesttrack_teams_edit_page(team_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestTeam
    team = (await db.execute(select(HarvestTeam).where(HarvestTeam.id == team_id))).scalar_one_or_none()
    if team is not None and _foreign(admin, team.branch_id):
        return _redirect(request, "/admin/harvesttrack?tab=teams", "You can only manage records of your own branch.", "error")
    if not team:
        return _redirect(request, "/admin/harvesttrack?tab=teams", "Team not found.", "error")

    branches, _ = await _harvest_form_options(db, admin)
    ctx = await _admin_context(request, db, "admin.harvesttrack")
    return await render_template(request, "admin/harvest-team-form.html", {
        "_db": db, "team": team, "branches": branches,
        "title": "Edit Team", "headerIcon": "ri-seedling-line", **ctx,
    })


@router.get("/harvesttrack/workers/create", name="admin_harvesttrack_workers_create_page")
async def admin_harvesttrack_workers_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    branches, teams = await _harvest_form_options(db, admin)
    ctx = await _admin_context(request, db, "admin.harvesttrack")
    return await render_template(request, "admin/harvest-worker-form.html", {
        "_db": db, "worker": None, "branches": branches, "teams": teams,
        "roles": HARVEST_ROLES, "roleLabels": HARVEST_ROLE_LABELS,
        "title": "New Worker", "headerIcon": "ri-seedling-line", **ctx,
    })


@router.get("/harvesttrack/workers/{worker_id}/edit", name="admin_harvesttrack_workers_edit_page")
async def admin_harvesttrack_workers_edit_page(worker_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestWorker
    worker = (await db.execute(select(HarvestWorker).where(HarvestWorker.id == worker_id))).scalar_one_or_none()
    if worker is not None and _foreign(admin, worker.branch_id):
        return _redirect(request, "/admin/harvesttrack?tab=teams", "You can only manage records of your own branch.", "error")
    if not worker:
        return _redirect(request, "/admin/harvesttrack?tab=teams", "Worker not found.", "error")

    branches, teams = await _harvest_form_options(db, admin)
    ctx = await _admin_context(request, db, "admin.harvesttrack")
    return await render_template(request, "admin/harvest-worker-form.html", {
        "_db": db, "worker": worker, "branches": branches, "teams": teams,
        "roles": HARVEST_ROLES, "roleLabels": HARVEST_ROLE_LABELS,
        "title": "Edit Worker", "headerIcon": "ri-seedling-line", **ctx,
    })


@router.post("/harvesttrack/workers", name="admin_harvesttrack_workers_store")
async def admin_harvesttrack_workers_store(
    request: Request, db: AsyncSession = Depends(get_db),
    full_name: str = Form(...), email: str = Form(...),
    phone: str = Form(""), branch_id: int = Form(None),
    role: str = Form("field_member"), team_id: int | None = Form(None),
    password: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestWorker

    if await _value_taken(db, HarvestWorker.email, email):
        return _redirect(request, "/admin/harvesttrack/workers/create", "A worker with that email already exists.", "error")
    generated_password = password.strip() or secrets.token_urlsafe(10)
    worker = HarvestWorker(
        full_name=full_name.strip(),
        email=email.strip().lower(),
        phone=phone.strip() if phone else None,
        branch_id=(branch_id if is_super_role(admin.role) else None) or admin.branch_id,
        role=role if role in HARVEST_ROLES else "field_member",
        team_id=team_id or None,
        password_hash=hash_password(generated_password),
    )
    db.add(worker)
    await db.flush()
    return _redirect(
        request, "/admin/harvesttrack?tab=teams",
        f"Worker created. Temporary password: {generated_password} (share this with them securely — it will not be shown again).",
    )


@router.post("/harvesttrack/workers/{worker_id}/update", name="admin_harvesttrack_workers_update")
async def admin_harvesttrack_workers_update(
    worker_id: int, request: Request, db: AsyncSession = Depends(get_db),
    full_name: str = Form(""), phone: str = Form(""), branch_id: int = Form(None),
    role: str = Form("field_member"), team_id: int | None = Form(None),
    is_active: bool = Form(False),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestWorker
    result = await db.execute(select(HarvestWorker).where(HarvestWorker.id == worker_id))
    worker = result.scalar_one_or_none()
    if not worker:
        return _redirect(request, "/admin/harvesttrack?tab=teams", "Worker not found.", "error")
    if _foreign(admin, getattr(worker, "branch_id", None)):
        return _redirect(request, "/admin/harvesttrack?tab=teams", "You can only manage records of your own branch.", "error")

    worker.full_name = full_name.strip() or worker.full_name
    worker.phone = phone.strip() if phone else None
    if admin.role == "super_admin" and branch_id:
        worker.branch_id = branch_id
    worker.role = role if role in HARVEST_ROLES else worker.role
    worker.team_id = team_id or None
    worker.is_active = is_active
    await db.flush()
    return _redirect(request, "/admin/harvesttrack?tab=teams", "Worker updated successfully.")


@router.post("/harvesttrack/teams", name="admin_harvesttrack_teams_store")
async def admin_harvesttrack_teams_store(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(...), branch_id: int | None = Form(None),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestTeam
    if await _value_taken(db, HarvestTeam.name, name):
        return _redirect(request, "/admin/harvesttrack/teams/create", "A team with that name already exists.", "error")
    team = HarvestTeam(name=name.strip(), branch_id=(branch_id if is_super_role(admin.role) else None) or admin.branch_id)
    db.add(team)
    await db.flush()
    return _redirect(request, "/admin/harvesttrack?tab=teams", "Team created successfully.")


@router.post("/harvesttrack/teams/{team_id}/update", name="admin_harvesttrack_teams_update")
async def admin_harvesttrack_teams_update(
    team_id: int, request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(...), branch_id: int | None = Form(None),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestTeam
    result = await db.execute(select(HarvestTeam).where(HarvestTeam.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        return _redirect(request, "/admin/harvesttrack?tab=teams", "Team not found.", "error")
    if _foreign(admin, getattr(team, "branch_id", None)):
        return _redirect(request, "/admin/harvesttrack?tab=teams", "You can only manage records of your own branch.", "error")

    if name.strip() and await _value_taken(db, HarvestTeam.name, name, exclude_id=team.id):
        return _redirect(request, f"/admin/harvesttrack/teams/{team.id}/edit", "A team with that name already exists.", "error")
    team.name = name.strip() or team.name
    if admin.role == "super_admin":
        team.branch_id = branch_id or None
    await db.flush()
    return _redirect(request, "/admin/harvesttrack?tab=teams", "Team updated successfully.")


@router.post("/harvesttrack/teams/{team_id}/destroy", name="admin_harvesttrack_teams_destroy")
async def admin_harvesttrack_teams_destroy(team_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestTeam
    result = await db.execute(select(HarvestTeam).where(HarvestTeam.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        return _redirect(request, "/admin/harvesttrack?tab=teams", "Team not found.", "error")
    if _foreign(admin, getattr(team, "branch_id", None)):
        return _redirect(request, "/admin/harvesttrack?tab=teams", "You can only manage records of your own branch.", "error")

    await db.delete(team)
    await db.flush()
    return _redirect(request, "/admin/harvesttrack?tab=teams", "Team deleted. Its workers are now unassigned.")


@router.post("/harvesttrack/contacts/{contact_id}/reassign", name="admin_harvesttrack_contacts_reassign")
async def admin_harvesttrack_contacts_reassign(
    contact_id: int, request: Request, db: AsyncSession = Depends(get_db),
    worker_id: int = Form(...),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestContact
    result = await db.execute(select(HarvestContact).where(HarvestContact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        return _redirect(request, "/admin/harvesttrack?tab=contacts", "Contact not found.", "error")
    from app.models.harvest import HarvestWorker as _HW
    old_w = (await db.execute(select(_HW).where(_HW.id == contact.worker_id))).scalar_one_or_none()
    new_w = (await db.execute(select(_HW).where(_HW.id == worker_id))).scalar_one_or_none()
    if new_w is None or _foreign(admin, old_w.branch_id if old_w else None) or _foreign(admin, new_w.branch_id):
        return _redirect(request, "/admin/harvesttrack?tab=contacts", "You can only reassign contacts within your own branch.", "error")

    contact.worker_id = worker_id
    await db.flush()
    return _redirect(request, "/admin/harvesttrack?tab=contacts", "Contact reassigned successfully.")


@router.post("/harvesttrack/contacts/{contact_id}/stage", name="admin_harvesttrack_contacts_stage")
async def admin_harvesttrack_contacts_stage(
    contact_id: int, request: Request, db: AsyncSession = Depends(get_db),
    current_stage: str = Form(...),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    if current_stage not in HARVEST_STAGES:
        return _redirect(request, "/admin/harvesttrack?tab=contacts", "Invalid stage.", "error")

    from app.models.harvest import HarvestContact
    result = await db.execute(select(HarvestContact).where(HarvestContact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        return _redirect(request, "/admin/harvesttrack?tab=contacts", "Contact not found.", "error")
    from app.models.harvest import HarvestWorker as _HW
    owner_w = (await db.execute(select(_HW).where(_HW.id == contact.worker_id))).scalar_one_or_none()
    if _foreign(admin, owner_w.branch_id if owner_w else None):
        return _redirect(request, "/admin/harvesttrack?tab=contacts", "You can only manage records of your own branch.", "error")

    contact.current_stage = current_stage
    if current_stage == "committed" and not contact.committed_at:
        contact.committed_at = datetime.utcnow()
    await db.flush()
    return _redirect(request, "/admin/harvesttrack?tab=contacts", "Contact stage updated successfully.")


@router.get("/harvesttrack/export.csv", name="admin_harvesttrack_export")
async def admin_harvesttrack_export(
    request: Request, db: AsyncSession = Depends(get_db),
    start_date: str = Query(""), end_date: str = Query(""),
    team_id: int | None = Query(None), source: str = Query(""),
    branch_id: int | None = Query(None),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.harvest import HarvestWorker, HarvestContact

    is_super = admin.role == "super_admin"
    scope_branch_id = branch_id if (is_super and branch_id) else (None if is_super else admin.branch_id)

    workers_query = select(HarvestWorker).options(selectinload(HarvestWorker.team))
    if scope_branch_id:
        workers_query = workers_query.where(HarvestWorker.branch_id == scope_branch_id)
    workers = list((await db.execute(workers_query)).scalars().all())

    contacts_query = select(HarvestContact).options(
        selectinload(HarvestContact.worker).selectinload(HarvestWorker.team),
        selectinload(HarvestContact.follow_ups),
    ).order_by(HarvestContact.created_at.desc())
    if scope_branch_id or team_id:
        scoped_ids = [w.id for w in workers if not team_id or w.team_id == team_id]
        contacts_query = contacts_query.where(HarvestContact.worker_id.in_(scoped_ids)) if scoped_ids else contacts_query.where(False)
    if source:
        contacts_query = contacts_query.where(HarvestContact.source == source)
    contacts = list((await db.execute(contacts_query)).scalars().all())

    now = datetime.utcnow()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Contact Name", "Phone", "Email", "Source", "Stage", "Assigned Worker", "Team",
        "Date Added", "Next Follow-up", "Last Activity", "Days Since Activity", "Touchpoint Count",
    ])
    for c in contacts:
        last_activity = _harvest_last_activity(c)
        writer.writerow([
            c.full_name, c.phone or "", c.email or "",
            HARVEST_SOURCE_LABELS.get(c.source, c.source or ""),
            HARVEST_STAGE_LABELS.get(c.current_stage, c.current_stage),
            c.worker.full_name if c.worker else "Unassigned",
            c.worker.team.name if c.worker and c.worker.team else "",
            c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
            c.next_follow_up_at.strftime("%Y-%m-%d") if c.next_follow_up_at else "",
            last_activity.strftime("%Y-%m-%d") if last_activity else "",
            (now - last_activity).days if last_activity else "",
            len(c.follow_ups),
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=harvesttrack_export.csv"},
    )


# ─── Admins (super_admin only) ────────────────────────────────────

@router.get("/admins", name="admin_admins")
async def admin_admins(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.admin import Admin as AdminModel
    result = await db.execute(
        select(AdminModel)
        .options(selectinload(AdminModel.branch))
        .order_by(AdminModel.created_at.desc())
    )
    admins_list = list(result.scalars().all())

    from app.models.branch import Branch
    result = await db.execute(select(Branch).where(Branch.is_active == True).order_by(Branch.name))
    branches = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.admins")
    return await render_template(request, "admin/admins.html", {
        "_db": db, "admins": admins_list, "branches": branches,
        "title": "Admins", "headerIcon": "ri-shield-user-line", **ctx,
    })


@router.get("/admins/create", name="admin_admins_create_page")
async def admin_admins_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.branch import Branch
    branches = list((await db.execute(select(Branch).where(Branch.is_active == True).order_by(Branch.name))).scalars().all())
    ctx = await _admin_context(request, db, "admin.admins")
    return await render_template(request, "admin/admin-form.html", {
        "_db": db, "target": None, "branches": branches, **_admin_role_ctx(None),
        "title": "New Administrator", "headerIcon": "ri-shield-user-line", **ctx,
    })


@router.get("/admins/{admin_id}/edit", name="admin_admins_edit_page")
async def admin_admins_edit_page(admin_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.admin import Admin as AdminModel
    from app.models.branch import Branch
    target = (await db.execute(
        select(AdminModel).options(selectinload(AdminModel.branch)).where(AdminModel.id == admin_id)
    )).scalar_one_or_none()
    if not target:
        return _redirect(request, "/admin/admins", "Admin not found.", "error")

    branches = list((await db.execute(select(Branch).where(Branch.is_active == True).order_by(Branch.name))).scalars().all())
    ctx = await _admin_context(request, db, "admin.admins")
    return await render_template(request, "admin/admin-form.html", {
        "_db": db, "target": target, "branches": branches, **_admin_role_ctx(target),
        "title": "Edit Administrator", "headerIcon": "ri-shield-user-line", **ctx,
    })


@router.post("/admins", name="admin_admins_store")
async def admin_admins_store(
    request: Request, db: AsyncSession = Depends(get_db),
    full_name: str = Form(...), email: str = Form(...),
    role: str = Form("branch_admin"), branch_id: int = Form(None),
    phone: str = Form(""), password: str = Form(""), password_confirmation: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.core.access import BRANCH_ADMIN, FORM_ROLES, normalize_role
    from app.models.admin import Admin as AdminModel
    role = normalize_role(role)
    if role not in FORM_ROLES:
        return _redirect(request, "/admin/admins/create", "Choose a valid role.", "error")
    if role == BRANCH_ADMIN and not branch_id:
        return _redirect(request, "/admin/admins/create", "A branch admin must be assigned to a branch.", "error")
    branch_id = branch_id if role == BRANCH_ADMIN else await get_hq_branch_id(db)
    email_clean = email.strip().lower()
    existing = (await db.execute(select(AdminModel).where(AdminModel.email == email_clean))).scalar_one_or_none()
    if existing:
        return _redirect(request, "/admin/admins/create", "An administrator with that email already exists.", "error")

    if password:
        if password != password_confirmation:
            return _redirect(request, "/admin/admins/create", "Passwords do not match.", "error")
        if len(password) < 8:
            return _redirect(request, "/admin/admins/create", "Password must be at least 8 characters.", "error")
    else:
        # No password supplied: generate an unguessable one; use "Reset password" to set a real one.
        password = secrets.token_urlsafe(12)

    new_admin = AdminModel(
        full_name=full_name.strip(),
        email=email_clean,
        password_hash=hash_password(password),
        role=role,
        branch_id=branch_id,
        phone=phone.strip() or None,
        is_active=True,
        must_change_password=True,
    )
    db.add(new_admin)
    await db.flush()
    return _redirect(request, "/admin/admins", "Admin created successfully.")


# ─── Events ────────────────────────────────────────────────────────

def _visible_event_scope(admin, hq_id):
    """Events a non-HQ-Administrator may SEE: their church's own, plus HQ's published ones (read-only)."""
    from app.models.event import Event

    if admin.branch_id == hq_id:
        return Event.branch_id == admin.branch_id
    return or_(Event.branch_id == admin.branch_id, and_(Event.is_published == True, or_(Event.branch_id == hq_id, Event.branch_id.is_(None))))  # noqa: E712


@router.get("/events", name="admin_events")
async def admin_events(request: Request, db: AsyncSession = Depends(get_db), month: str = Query(None)):
    import calendar as cal_mod
    from datetime import date, timedelta

    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.event import Event
    query = select(Event).order_by(Event.starts_at.desc())
    hq_id = await get_hq_branch_id(db)
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(_visible_event_scope(admin, hq_id))
    result = await db.execute(query)
    events = list(result.scalars().all())
    # HQ's own events are shown to the branches for information only; a branch edits just its own.
    editable_event_ids = {e.id for e in events if admin.role == "super_admin" or e.branch_id == admin.branch_id}

    now = datetime.utcnow()
    if month:
        try:
            parts = month.split("-")
            currentMonth = date(int(parts[0]), int(parts[1]), 1)
        except (ValueError, IndexError):
            currentMonth = date(now.year, now.month, 1)
    else:
        currentMonth = date(now.year, now.month, 1)
    prevMonth = (currentMonth - timedelta(days=1)).replace(day=1)
    nextMonth = (currentMonth + timedelta(days=32)).replace(day=1)

    from app.core.calendar_utils import month_grid_days
    calendarDays = month_grid_days(currentMonth.year, currentMonth.month)
    today_str = date.today().isoformat()

    from app.core.calendar_utils import events_by_date
    eventsByDate = events_by_date(events)

    upcomingPrograms = [
        ev for ev in events
        if ev.starts_at and ev.starts_at.replace(tzinfo=None) >= now
    ][:10]

    ctx = await _admin_context(request, db, "admin.events")
    return await render_template(request, "admin/events-management.html", {
        "_db": db,
        "events": events,
        "calendarDays": calendarDays,
        "eventsByDate": eventsByDate,
        "currentMonth": currentMonth,
        "prevMonth": prevMonth,
        "nextMonth": nextMonth,
        "today": today_str,
        "upcomingPrograms": upcomingPrograms,
        "editable_event_ids": editable_event_ids, "hq_branch_id": hq_id,
        "title": "Calendar",
        "headerIcon": "ri-calendar-event-line",
        **ctx,
    })


@router.get("/events/create", name="admin_events_create")
async def admin_events_create(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    ctx = await _admin_context(request, db, "admin.events")
    return await render_template(request, "admin/events-create.html", {
        "_db": db, "event": None,
        "title": "Create Event", "headerIcon": "ri-calendar-event-line", **ctx,
    })


@router.get("/events/{event_id}/edit", name="admin_events_edit_page")
async def admin_events_edit_page(event_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.event import Event
    query = select(Event).where(Event.id == event_id)
    if admin.role != "super_admin" and admin.branch_id:
        query = query.where(Event.branch_id == admin.branch_id)
    event = (await db.execute(query)).scalar_one_or_none()
    if not event:
        return _redirect(request, "/admin/events", "Event not found.", "error")

    ctx = await _admin_context(request, db, "admin.events")
    return await render_template(request, "admin/events-create.html", {
        "_db": db, "event": event,
        "title": "Edit Event", "headerIcon": "ri-calendar-event-line", **ctx,
    })


@router.post("/events", name="admin_events_store")
async def admin_events_store(
    request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(...), description: str = Form(""),
    location: str = Form(""), start_date: str = Form(...),
    end_date: str = Form(""), is_major: bool = Form(False),
    start_time: str = Form(""), end_time: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.event import Event
    try:
        starts_at = datetime.fromisoformat(f"{start_date}T{start_time}" if start_time else start_date)
        ends_at = (
            datetime.fromisoformat(f"{end_date}T{end_time}" if end_time else end_date) if end_date else None
        )
    except ValueError:
        return _redirect(request, "/admin/events/create", "Please enter valid dates and times.", "error")
    event = Event(
        title=title.strip(),
        description=description.strip() if description else None,
        location=location.strip() if location else None,
        starts_at=starts_at,
        ends_at=ends_at,
        is_major=is_major,
        is_published=True,
        branch_id=admin.branch_id,
        created_by=admin.id,
    )
    db.add(event)
    await db.flush()
    return _redirect(request, "/admin/events", "Event created successfully.")


@router.post("/events/{event_id}/update", name="admin_events_update")
async def admin_events_update(
    event_id: int, request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), description: str = Form(""),
    location: str = Form(""), start_date: str = Form(""),
    end_date: str = Form(""), is_major: bool = Form(False),
    start_time: str = Form(""), end_time: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.event import Event
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        return _redirect(request, "/admin/events", "Event not found.", "error")
    if _foreign(admin, getattr(event, "branch_id", None)):
        return _redirect(request, "/admin/events", "You can only manage records of your own branch.", "error")

    event.title = title.strip() or event.title
    event.description = description.strip() if description else None
    event.location = location.strip() if location else None
    if start_date:
        try:
            event.starts_at = datetime.fromisoformat(f"{start_date}T{start_time}" if start_time else start_date)
        except ValueError:
            pass
    if end_date:
        try:
            event.ends_at = datetime.fromisoformat(f"{end_date}T{end_time}" if end_time else end_date)
        except ValueError:
            pass
    else:
        event.ends_at = None
    event.is_major = is_major
    await db.flush()
    return _redirect(request, "/admin/events", "Event updated successfully.")


@router.post("/events/{event_id}/destroy", name="admin_events_destroy")
async def admin_events_destroy(event_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.event import Event
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        return _redirect(request, "/admin/events", "Event not found.", "error")
    if _foreign(admin, getattr(event, "branch_id", None)):
        return _redirect(request, "/admin/events", "You can only manage records of your own branch.", "error")

    await db.delete(event)
    await db.flush()
    return _redirect(request, "/admin/events", "Event deleted successfully.")


# ─── Departments ───────────────────────────────────────────────────

@router.get("/departments", name="admin_departments")
async def admin_departments(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.department import Department
    result = await db.execute(select(Department).order_by(Department.name))
    departments = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.departments")
    return await render_template(request, "admin/departments.html", {
        "_db": db, "departments": departments,
        "title": "Departments", "headerIcon": "ri-building-2-line", **ctx,
    })


@router.get("/departments/create", name="admin_departments_create_page")
async def admin_departments_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    ctx = await _admin_context(request, db, "admin.departments")
    return await render_template(request, "admin/department-form.html", {
        "_db": db, "department": None,
        "title": "New Department", "headerIcon": "ri-building-2-line", **ctx,
    })


@router.get("/departments/{department_id}/edit", name="admin_departments_edit_page")
async def admin_departments_edit_page(department_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.department import Department
    dept = (await db.execute(select(Department).where(Department.id == department_id))).scalar_one_or_none()
    if not dept:
        return _redirect(request, "/admin/departments", "Department not found.", "error")

    ctx = await _admin_context(request, db, "admin.departments")
    return await render_template(request, "admin/department-form.html", {
        "_db": db, "department": dept,
        "title": "Edit Department", "headerIcon": "ri-building-2-line", **ctx,
    })


@router.post("/departments", name="admin_departments_store")
async def admin_departments_store(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(...), description: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.department import Department
    from slugify import slugify
    if await _value_taken(db, Department.name, name):
        return _redirect(request, "/admin/departments/create", "A department with that name already exists.", "error")
    base_slug = slugify(name.strip())
    slug = base_slug
    suffix = 2
    while (await db.execute(select(Department.id).where(Department.slug == slug))).scalar_one_or_none():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    dept = Department(
        name=name.strip(),
        slug=slug,
        description=description.strip() if description else None,
        created_by_admin_id=admin.id,
    )
    db.add(dept)
    await db.flush()
    return _redirect(request, "/admin/departments", "Department created successfully.")


# ─── Branches ──────────────────────────────────────────────────────

# ─── Places (country / region / city) and money: codes from app/core/geo.py, never free text ───

def _tidy_city(text: str | None) -> str | None:
    city = " ".join((text or "").split())[:120]
    if city and (city.islower() or city.isupper()):
        city = city.title()            # "port harcourt" / "PORT HARCOURT" → "Port Harcourt"
    return city or None


async def _canonical_city(db: AsyncSession, region_code: str | None, city: str | None) -> str | None:
    """Reuse the spelling already recorded for this city in this region, so it is one place in reports."""
    city = _tidy_city(city)
    if not city or not region_code:
        return city
    from app.models.branch import Branch
    from app.models.member import Member

    for model in (Branch, Member):
        known = (await db.execute(select(model.city).where(model.region_code == region_code,
                                                           func.lower(model.city) == city.lower()).limit(1))).scalar_one_or_none()
        if known:
            return known
    return city


async def _city_suggestions(db: AsyncSession, region_code: str | None = None) -> list[str]:
    from app.models.branch import Branch

    q = select(Branch.city).where(Branch.city.isnot(None), Branch.city != "")
    if region_code:
        q = q.where(Branch.region_code == region_code)
    return sorted({c for (c,) in (await db.execute(q.distinct())).all()})


async def _apply_branch_location(db: AsyncSession, branch, form, *, with_country: bool = True) -> str | None:
    """Validate and set a branch's country/region/city/currency/timezone from a form. Returns an error or None.

    ``with_country=False`` (a branch admin): the country, currency and time zone stay as HQ set them.
    """
    from app.core import geo

    cc = str(form.get("country_code") or "").upper() if with_country else (branch.country_code or "")
    if not geo.is_country(cc):
        return "Choose the branch's country from the list."
    rc = str(form.get("region_code") or "").upper()
    if rc and not geo.is_region(cc, rc):
        return f"Choose a state / region from {geo.country_name(cc)}'s list."
    if not rc and geo.regions(cc):
        return "Choose the branch's state / region."
    changes = {"country_code": cc, "country": geo.country_name(cc), "region_code": rc or None,
               "state": geo.region_name(rc) or None, "city": await _canonical_city(db, rc, form.get("city"))}
    if with_country:
        tz = str(form.get("timezone") or "") or geo.timezone_for(cc)
        if not geo.is_timezone(tz):
            return "Choose the branch's time zone from the list."
        changes["timezone"] = tz
        if getattr(branch, "is_hq", False):
            # HQ's currency IS the platform currency: every gift, total and amount on the site uses it.
            cur = str(form.get("currency") or "").upper() or branch.currency or geo.currency_for(cc)
            if not geo.is_currency(cur):
                return "Choose the platform currency from the list."
            changes["currency"] = cur
        else:
            changes["currency"] = None      # branches have no currency of their own
    for k, v in changes.items():
        setattr(branch, k, v)
    return None


@router.get("/data-check", name="admin_data_check")
async def admin_data_check(request: Request, db: AsyncSession = Depends(get_db)):
    """Records the global migration could not match to the official lists exactly: fix them by hand."""
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import geo_migration

    items = await geo_migration.review_items(db)
    links = {"Branch": lambda i: f"/admin/branches/{i['id']}", "Minister": lambda i: f"/admin/ministers/{i['id']}/edit",
             "Giving purpose": lambda i: f"/admin/donations?q={i['name']}", "Member gender": lambda i: "/admin/members"}
    for i in items:
        i["href"] = links.get(i["kind"], lambda _: "")(i)
    ctx = await _admin_context(request, db, "admin.data-check")
    return await render_template(request, "admin/data-check.html", {
        "_db": db, "items": items, "title": "Data check", "headerIcon": "ri-shield-check-line", **ctx})


@router.get("/branches", name="admin_branches")
async def admin_branches(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.branch import Branch
    from app.models.member import Member
    from app.models.event import Event
    from app.models.sermon import Sermon
    from app.core import geo

    country_filter = (request.query_params.get("country") or "").upper()
    q = select(Branch)
    if country_filter:
        q = q.where(Branch.country_code == country_filter)
    branches = list((await db.execute(q)).scalars().all())
    # Grouped by country (HQ's first), then region, then name: how a global HQ reads its churches.
    hq_cc = next((b.country_code for b in branches if b.is_hq), None)
    branches.sort(key=lambda b: (b.country_code != hq_cc, geo.country_name(b.country_code) or "~", not b.is_hq,
                                 geo.region_name(b.region_code) or "~", b.name.lower()))

    branch_counts = {}
    for b in branches:
        try:
            mc = (await db.execute(select(func.count()).select_from(Member).where(Member.branch_id == b.id))).scalar_one() or 0
        except Exception:
            mc = 0
        try:
            ec = (await db.execute(select(func.count()).select_from(Event).where(Event.branch_id == b.id))).scalar_one() or 0
        except Exception:
            ec = 0
        try:
            sc = (await db.execute(select(func.count()).select_from(Sermon).where(Sermon.branch_id == b.id))).scalar_one() or 0
        except Exception:
            sc = 0
        branch_counts[b.id] = {"members": mc, "events": ec, "sermons": sc}

    all_codes = (await db.execute(select(Branch.country_code).where(Branch.country_code.isnot(None)).distinct())).scalars().all()
    country_options = sorted(((c, geo.country_name(c)) for c in all_codes), key=lambda x: x[1])

    ctx = await _admin_context(request, db, "admin.branches")
    return await render_template(request, "admin/branches.html", {
        "_db": db, "branches": branches, "branch_counts": branch_counts,
        "country_options": country_options, "country_filter": country_filter,
        "title": "Branches", "headerIcon": "ri-map-pin-2-line", **ctx,
    })


@router.get("/branches/create", name="admin_branches_create_page")
async def admin_branches_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    ctx = await _admin_context(request, db, "admin.branches")
    from app.models.branch import Branch

    hq = (await db.execute(select(Branch).where(Branch.is_hq == True))).scalar_one_or_none()  # noqa: E712
    return await render_template(request, "admin/branch-form.html", {
        "_db": db, "default_country": (hq.country_code if hq and hq.country_code else "NG"), "cities": await _city_suggestions(db),

        "title": "New Branch", "headerIcon": "ri-map-pin-2-line", **ctx,
    })


@router.post("/branches", name="admin_branches_store")
async def admin_branches_store(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(...), address: str = Form(""),
    phone: str = Form(""), email: str = Form(""),
    super_admin_name: str = Form(""), super_admin_email: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.branch import Branch
    from slugify import slugify
    base_slug = slugify(name.strip())
    slug = base_slug
    suffix = 2
    while (await db.execute(select(Branch.id).where(Branch.slug == slug))).scalar_one_or_none():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    from app.core import geo

    form = await request.form()
    branch = Branch(
        name=name.strip(), slug=slug,
        address=address.strip() if address else None,
        email=email.strip() if email else None,
    )
    if err := await _apply_branch_location(db, branch, form):
        return _redirect(request, "/admin/branches/create", err, "error")
    branch.phone = geo.normalize_phone(phone, branch.country_code) or None
    db.add(branch)
    await db.flush()

    # Optionally invite the branch's first administrator. No email is sent by the
    # system, so send the admin to the branch page where the invite link can be copied.
    if super_admin_email.strip():
        from app.models.branch_admin_invitation import BranchAdminInvitation
        _inv = BranchAdminInvitation(
            branch_id=branch.id,
            invited_by_admin_id=admin.id,
            name=super_admin_name.strip() or super_admin_email.strip(),
            email=super_admin_email.strip().lower(),
            token=secrets.token_urlsafe(32),
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(_inv)
        await db.flush()
        _email_invitation(request, email=_inv.email, name=_inv.name, role_line=f"administer {branch.name}", path=f"/admin/invitation/{_inv.token}", inviter=admin.full_name)
        return _redirect(
            request, f"/admin/branches/{branch.id}",
            "Branch created and the invitation emailed. You can also copy the invite link under Admin invitations.",
        )
    return _redirect(request, "/admin/branches", "Branch created successfully.")


# ─── Messages (Sermons) ───────────────────────────────────────────

@router.get("/messages", name="admin_messages")
async def admin_messages(
    request: Request, db: AsyncSession = Depends(get_db),
    q: str = Query(""), status: str = Query(""), branch_id: str = Query(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    from sqlalchemy.orm import selectinload
    query = select(Sermon).options(selectinload(Sermon.branch)).order_by(Sermon.created_at.desc())
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(Sermon.branch_id == admin.branch_id)
    elif branch_id.isdigit():
        query = query.where(Sermon.branch_id == int(branch_id))
    if q.strip():
        pat = f"%{q.strip()}%"
        query = query.where(or_(Sermon.title.ilike(pat), Sermon.preacher.ilike(pat)))
    if status.strip():
        query = query.where(Sermon.status == status.strip().lower())
    result = await db.execute(query)
    sermons = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.messages")
    
    from app.models.branch import Branch
    branches_result = await db.execute(select(Branch).order_by(Branch.name))
    branches = list(branches_result.scalars().all())
    
    platforms = {
        "Telegram": "telegram",
    }
    feed_url = _public_base_url(request) + "/feed.xml"

    return await render_template(request, "admin/messages.html", {
        "_db": db, "sermons": sermons, "branches": branches, "platforms": platforms,
        "search": q, "filter_status": status, "filter_branch_id": branch_id,
        "current_date_iso": datetime.utcnow().strftime("%Y-%m-%d"),
        "feed_url": feed_url,
        "title": "Audio Messages", "headerIcon": "ri-message-3-line", **ctx,
    })


@router.get("/messages/create", name="admin_messages_create")
async def admin_messages_create(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.branch import Branch

    branches_result = await db.execute(select(Branch).order_by(Branch.name))
    branches = list(branches_result.scalars().all())

    platforms = {"Telegram": "telegram"}

    ctx = await _admin_context(request, db, "admin.messages.create")
    return await render_template(request, "admin/messages_create.html", {**(await _message_form_ctx(db, admin)),
        "_db": db, "branches": branches, "platforms": platforms,
        "current_date_iso": datetime.utcnow().strftime("%Y-%m-%d"),
        "title": "Add Audio Message", "headerIcon": "ri-message-3-line", **ctx,
    })


@router.get("/messages/{sermon_id}/edit", name="admin_messages_edit_page")
async def admin_messages_edit_page(sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    from app.models.branch import Branch
    from sqlalchemy.orm import selectinload

    query = select(Sermon).options(selectinload(Sermon.branch)).where(Sermon.id == sermon_id)
    if admin.role != "super_admin" and admin.branch_id:
        query = query.where(Sermon.branch_id == admin.branch_id)
    sermon = (await db.execute(query)).scalar_one_or_none()
    if not sermon:
        return _redirect(request, "/admin/messages", "Sermon not found.", "error")

    branches = list((await db.execute(select(Branch).order_by(Branch.name))).scalars().all())
    ctx = await _admin_context(request, db, "admin.messages")
    return await render_template(request, "admin/messages_create.html", {**(await _message_form_ctx(db, admin)),
        "_db": db, "sermon": sermon, "branches": branches, "platforms": {"Telegram": "telegram"},
        "current_date_iso": datetime.utcnow().strftime("%Y-%m-%d"),
        "title": "Edit Audio Message", "headerIcon": "ri-message-3-line", **ctx,
    })


@router.post("/messages", name="admin_messages_store")
async def admin_messages_store(
    request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(...), preacher: str = Form(""), speaker: str = Form(""),
    description: str = Form(""), caption: str = Form(""),
    status: str = Form("draft"), published_on: str = Form(""),
    branch_id: int | None = Form(None),
    audio_file: UploadFile | None = File(None),
    platform_targets: list[str] = Form([]),
    series: str = Form(""), scripture: str = Form(""), transcript_public: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    from app.core.telegram import TelegramDistributor
    from app.core.media import media_subdir
    from slugify import slugify

    speaker_val = speaker.strip() or preacher.strip() or None
    base_slug = slugify(title.strip()) or "message"
    slug, n = base_slug, 1
    while (await db.execute(select(Sermon.id).where(Sermon.slug == slug))).first():
        n += 1
        slug = f"{base_slug}-{n}"
    sermon = Sermon(
        title=title.strip(), slug=slug,
        preacher=await _known_spelling(db, Sermon.preacher, speaker_val),
        description=description.strip() if description else None,
        caption=caption.strip() if caption else None,
        series=await _known_spelling(db, Sermon.series, series, 150), scripture=scripture.strip()[:150] or None, transcript_public=transcript_public == "1",
        status=status.strip().lower() or "draft",
        # HQ Administrator: the chosen church, HQ when none is chosen; everyone else: their own church
        branch_id=(branch_id or await get_hq_branch_id(db)) if admin.role == "super_admin" else admin.branch_id,
        created_by=admin.id,
        platform_targets=[p for p in platform_targets if p] or None,
    )

    if published_on:
        try:
            sermon.published_at = datetime.fromisoformat(published_on)
        except ValueError:
            pass
    elif status == "published":
        sermon.published_at = datetime.utcnow()

    queue_distribution = False
    dist_notice = ""

    if audio_file and audio_file.filename:
        ext = (audio_file.filename or "").rsplit(".", 1)[-1].lower() if "." in (audio_file.filename or "") else ""
        if ext not in AUDIO_UPLOAD_EXTENSIONS:
            return _redirect(request, "/admin/messages/create", "Invalid audio format. Use mp3, wav, m4a, ogg, flac, aac, wma or mpga.", "error")

        # Stream to disk in chunks — a multi-gigabyte sermon must never be read
        # into memory, which is what the old audio_file.read() did.
        media_dir = media_subdir("sermons")
        stored_filename = f"{secrets.token_hex(16)}.{ext}"
        stored_abspath = os.path.join(media_dir, stored_filename)
        # The website keeps the full file; when it is over Telegram's cap the worker sends a
        # compressed copy, so Telegram's 50MB cloud limit must not cap the upload itself.
        from app.config import settings as _cfg

        limit = _cfg.telegram_max_upload_bytes
        written = 0

        try:
            with open(stored_abspath, "wb") as f:
                while chunk := await audio_file.read(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        raise ValueError("too large")
                    f.write(chunk)
        except ValueError:
            os.unlink(stored_abspath)
            return _redirect(
                request, "/admin/messages/create",
                f"Audio file is larger than the {limit // 1048576}MB upload limit.",
                "error",
            )
        except Exception:
            if os.path.exists(stored_abspath):
                os.unlink(stored_abspath)
            logging.exception("Failed to store sermon upload")
            return _redirect(request, "/admin/messages/create", "Could not save the audio file.", "error")

        sermon.original_file_name = audio_file.filename
        sermon.original_file_size = written
        sermon.audio_file_path = f"media/sermons/{stored_filename}"
        sermon.media_url = f"/media/sermons/{stored_filename}"

        queue_distribution = bool(sermon.platform_targets and "telegram" in sermon.platform_targets)
        if queue_distribution and not await TelegramDistributor(sermon.branch_id).configured():
            queue_distribution = False            # never borrow another church's bot
            sermon.platform_targets = [t for t in (sermon.platform_targets or []) if t != "telegram"] or None
            dist_notice = " Not sent to Telegram: this branch has no Telegram bot and channel saved yet (see Branch settings)."

        if not sermon.published_at and sermon.status == "published":
            sermon.published_at = datetime.utcnow()

    db.add(sermon)
    await db.flush()

    # Transcription and the Telegram upload both run in the background (at these file sizes they
    # would outlive the request). Transcription reads the local file, so it never waits on Telegram.
    from app.core.jobs import enqueue
    from app.tasks import distribute_audio_task, transcribe_audio_task

    has_audio = bool(sermon.audio_file_path)
    if has_audio:
        sermon.transcription_status = "queued"
    sermon_id = sermon.id
    await db.commit()
    notes, worker_down = [], False
    if has_audio:
        if await enqueue(transcribe_audio_task, sermon_id):
            notes.append("Transcription has started")
        else:
            worker_down = True
            await _set_transcription_failed(db, sermon_id, "The background worker was unavailable when this was uploaded.")
    if queue_distribution:
        if await enqueue(distribute_audio_task, sermon_id):
            notes.append("Telegram distribution is running")
        else:
            worker_down = True
    if worker_down:
        return _redirect(request, "/admin/messages", "Audio message created, but the background worker is unavailable: "
                         "use Retry on the message once it is running.", "error")
    tail = (" " + " and ".join(notes) + " in the background.") if notes else ""
    return _redirect(request, "/admin/messages", "Audio message created." + tail + dist_notice)


# ─── Inquiries ─────────────────────────────────────────────────────

@router.get("/inquiries", name="admin_inquiries")
async def admin_inquiries(
    request: Request, db: AsyncSession = Depends(get_db),
    q: str = Query(""), status: str = Query(""), branch_id: str = Query(""), topic: str = Query(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.core import lists
    from app.models.contact_inquiry import ContactInquiry
    from app.models.branch import Branch
    from sqlalchemy.orm import selectinload
    query = select(ContactInquiry).options(selectinload(ContactInquiry.branch)).order_by(ContactInquiry.created_at.desc())
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(ContactInquiry.branch_id == admin.branch_id)
    elif branch_id.isdigit():
        query = query.where(ContactInquiry.branch_id == int(branch_id))
    if status.strip():
        query = query.where(ContactInquiry.status == status.strip())
    if topic.strip():
        query = query.where(ContactInquiry.topic == topic.strip())
    if q.strip():
        pat = f"%{q.strip()}%"
        query = query.where(or_(
            ContactInquiry.name.ilike(pat), ContactInquiry.email.ilike(pat), ContactInquiry.subject.ilike(pat),
        ))
    result = await db.execute(query)
    inquiries = list(result.scalars().all())
    branches = []
    try:
        br = await db.execute(select(Branch).order_by(Branch.name))
        branches = list(br.scalars().all())
    except Exception:
        logging.exception("Unhandled error")
    ctx = await _admin_context(request, db, "admin.inquiries")
    return await render_template(request, "admin/inquiries.html", {
        "_db": db, "inquiries": inquiries, "branches": branches,
        "search": q, "filter_status": status, "filter_branch_id": branch_id, "filter_topic": topic,
        "topics": lists.items(await get_settings(db), "inquiry_topics") + ["Plan a visit"],
        "title": "Inquiries", "headerIcon": "ri-question-answer-line", **ctx,
    })


@router.get("/inquiries/create", name="admin_inquiries_create_page")
async def admin_inquiries_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.branch import Branch
    branches = list((await db.execute(select(Branch).order_by(Branch.name))).scalars().all())
    ctx = await _admin_context(request, db, "admin.inquiries")
    return await render_template(request, "admin/inquiry-form.html", {
        "_db": db, "branches": branches, "topics": _lists_items(await get_settings(db), "inquiry_topics"),
        "title": "Add Inquiry", "headerIcon": "ri-question-answer-line", **ctx,
    })


@router.get("/inquiries/{inquiry_id}", name="admin_inquiries_show")
async def admin_inquiries_show(inquiry_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.contact_inquiry import ContactInquiry
    from sqlalchemy.orm import selectinload
    query = select(ContactInquiry).options(selectinload(ContactInquiry.branch)).where(ContactInquiry.id == inquiry_id)
    if admin.role != "super_admin" and admin.branch_id:
        query = query.where(ContactInquiry.branch_id == admin.branch_id)
    inquiry = (await db.execute(query)).scalar_one_or_none()
    if not inquiry:
        return _redirect(request, "/admin/inquiries", "Inquiry not found.", "error")

    ctx = await _admin_context(request, db, "admin.inquiries")
    return await render_template(request, "admin/inquiry-show.html", {
        "_db": db, "inquiry": inquiry,
        "title": "Inquiry", "headerIcon": "ri-question-answer-line", **ctx,
    })


@router.post("/inquiries", name="admin_inquiries_store")
async def admin_inquiries_store(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(...), email: str = Form(...), phone: str = Form(""), branch_id: int | None = Form(None),
    subject: str = Form(""), message: str = Form(...), status: str = Form("new"), topic: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    from app.core import geo, lists
    from app.models.contact_inquiry import ContactInquiry

    topic_value = lists.canonical(topic, lists.items(await get_settings(db), "inquiry_topics"))
    if not topic_value:
        return _redirect(request, "/admin/inquiries/create", "Choose what the inquiry is about from the list.", "error")
    if status.strip().lower() not in ("new", "in_progress", "resolved"):
        return _redirect(request, "/admin/inquiries/create", "Choose a status from the list.", "error")
    church = branch_id if admin.role == "super_admin" and branch_id else admin.branch_id
    inquiry = ContactInquiry(
        name=name.strip(), email=email.strip(), phone=geo.normalize_phone(phone, await _branch_country(db, church)) or None,
        branch_id=church, topic=topic_value,
        subject=subject.strip()[:255] or topic_value, message=message.strip(), status=status.strip().lower(),
    )
    db.add(inquiry)
    await db.flush()
    return _redirect(request, "/admin/inquiries", "Inquiry created successfully.")


# ─── Unit Reports ──────────────────────────────────────────────────

async def _resident_pastor_for(db: AsyncSession, branch_id: int | None):
    """The active Resident Pastor of a church (HQ counts as a church), or None."""
    from app.models.admin import Admin

    if not branch_id:
        return None
    return (await db.execute(select(Admin).where(Admin.role == "resident_pastor", Admin.branch_id == branch_id, Admin.is_active == True).limit(1))).scalar_one_or_none()  # noqa: E712


async def _rp_pending_condition(db: AsyncSession, branch_id: int):
    """SQL condition for "still waiting for the Resident Pastor's comment" in this church, or None when
    the church has no Resident Pastor. Reports submitted before the pastor was appointed are not held back."""
    from app.core.branch_settings import get_branch_settings
    from app.models.unit_report import UnitReport

    if await _resident_pastor_for(db, branch_id) is None:
        return None
    since = (await get_branch_settings(db, branch_id)).get("resident_pastor_since") or ""
    try:
        since_dt = datetime.fromisoformat(since) if since else datetime.min
    except ValueError:
        since_dt = datetime.min
    return and_(UnitReport.status == "submitted", or_(UnitReport.resident_pastor_comment.is_(None), UnitReport.resident_pastor_comment == ""),
                or_(UnitReport.submitted_at.is_(None), UnitReport.submitted_at >= since_dt))


async def _unit_report_scope_filter(admin, db: AsyncSession):
    """Which department (unit) reports this admin may read.

    * Nobody reads another church's reports: a branch admin/Resident Pastor their own branch's, the HQ
      Administrator and HQ staff HQ's own. (HQ receives each branch's Branch Report instead.)
    * Drafts are private to the HOD.
    * If the church has a Resident Pastor, a report is invisible to the Super Admin's office until the
      Resident Pastor has appended a comment. The Resident Pastor sees it from the moment it is submitted.
    """
    from app.core.access import RESIDENT_PASTOR, SUPER, normalize_role
    from app.models.admin import Admin
    from app.models.unit_report import UnitReport

    hq_branch_id = await get_hq_branch_id(db)
    role = normalize_role(admin.role)
    branch_id = hq_branch_id if role == SUPER else getattr(admin, "branch_id", None)
    if not branch_id:
        return UnitReport.id == -1

    if branch_id == hq_branch_id:
        hq_hod_ids = select(Admin.id).where(Admin.role == "hod", Admin.branch_id == hq_branch_id)
        base = or_(UnitReport.branch_id == hq_branch_id, UnitReport.hod_admin_id.in_(hq_hod_ids))
    else:
        base = UnitReport.branch_id == branch_id
    cond = and_(base, UnitReport.status != "draft")
    if role != RESIDENT_PASTOR:
        pending = await _rp_pending_condition(db, branch_id)
        if pending is not None:
            cond = and_(cond, not_(pending))
    return cond


async def _can_access_unit_report(admin, report, db: AsyncSession) -> bool:
    from app.models.unit_report import UnitReport

    scope = await _unit_report_scope_filter(admin, db)
    if scope is None:
        return True
    return (await db.execute(select(UnitReport.id).where(UnitReport.id == report.id, scope))).first() is not None


async def _report_church_id(db: AsyncSession, report) -> int | None:
    """The church a report belongs to (legacy HQ reports carry no branch: they belong to HQ)."""
    return report.branch_id or await get_hq_branch_id(db)


@router.get("/unit-reports", name="admin_unit_reports")
async def admin_unit_reports(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.unit_report import UnitReport
    from sqlalchemy.orm import selectinload
    query = (
        select(UnitReport)
        .options(
            selectinload(UnitReport.branch),
            selectinload(UnitReport.department),
            selectinload(UnitReport.hod_admin),
        )
        .order_by(UnitReport.created_at.desc())
    )
    scope_filter = await _unit_report_scope_filter(admin, db)
    if scope_filter is not None:
        query = query.where(scope_filter)
    result = await db.execute(query)
    reports = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.unit-reports")
    return await render_template(request, "admin/unit-reports.html", {
        "_db": db, "reports": reports,
        "title": "Unit Reports", "headerIcon": "ri-file-chart-line", **ctx,
    })


# ─── Sliders ───────────────────────────────────────────────────────

@router.get("/sliders", name="admin_sliders")
async def admin_sliders(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.slider import Slider
    result = await db.execute(select(Slider).order_by(Slider.sort_order.asc()))
    sliders = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.sliders")
    return await render_template(request, "admin/sliders.html", {
        "_db": db, "sliders": sliders,
        "title": "Sliders", "headerIcon": "ri-slideshow-3-line", **ctx,
    })


@router.get("/sliders/create", name="admin_sliders_create_page")
async def admin_sliders_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    ctx = await _admin_context(request, db, "admin.sliders")
    return await render_template(request, "admin/slider-form.html", {
        "_db": db, "slider": None,
        "title": "New Slider", "headerIcon": "ri-slideshow-3-line", **ctx,
    })


@router.get("/sliders/{slider_id}/edit", name="admin_sliders_edit_page")
async def admin_sliders_edit_page(slider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.slider import Slider
    slider = (await db.execute(select(Slider).where(Slider.id == slider_id))).scalar_one_or_none()
    if not slider:
        return _redirect(request, "/admin/sliders", "Slider not found.", "error")

    ctx = await _admin_context(request, db, "admin.sliders")
    return await render_template(request, "admin/slider-form.html", {
        "_db": db, "slider": slider,
        "title": "Edit Slider", "headerIcon": "ri-slideshow-3-line", **ctx,
    })


@router.post("/sliders", name="admin_sliders_store")
async def admin_sliders_store(
    request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), description: str = Form(""),
    cta_label: str = Form(""), cta_url: str = Form(""),
    sort_order: int = Form(0), is_active: bool = Form(True),
    image: UploadFile = File(None),
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    if not image or not image.filename:
        return _redirect(request, "/admin/sliders/create", "Choose a slider image.", "error")
    try:
        image_path = await _save_slider_image(image)
    except ValueError as exc:
        return _redirect(request, "/admin/sliders/create", str(exc), "error")

    from app.models.slider import Slider
    slider = Slider(
        image_path=image_path,
        title=title.strip() if title else None,
        description=description.strip() if description else None,
        cta_label=cta_label.strip() if cta_label else None,
        cta_url=cta_url.strip() if cta_url else None,
        sort_order=sort_order,
        is_active=is_active,
    )
    db.add(slider)
    await db.flush()
    return _redirect(request, "/admin/sliders", "Slider created successfully.")


# ─── Photos (gallery + outreach strips) ────────────────────────────

def _compress_image(raw: bytes, ext: str, max_width: int = 2400, quality: int = 82) -> bytes:
    """
    Compress an uploaded image for web delivery.

    - Converts to WebP (best quality/size trade-off) unless the source is GIF or SVG.
    - Scales down if wider than max_width while preserving aspect ratio.
    - quality=82 is visually lossless for photos; logos/transparent images use lossless WebP.
    - Returns the original bytes unchanged for SVG (vector, no recompression needed).
    - Falls back to original bytes if Pillow is unavailable or encounters an error.
    """
    if ext in {".svg"}:
        return raw
    try:
        from io import BytesIO
        from PIL import Image, UnidentifiedImageError
        try:
            img = Image.open(BytesIO(raw))
        except (UnidentifiedImageError, Exception):
            return raw

        # Preserve EXIF orientation if present
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        # Scale down large images
        w, h = img.size
        if w > max_width:
            new_h = int(h * max_width / w)
            img = img.resize((max_width, new_h), Image.LANCZOS)

        # GIF: keep as GIF (animation support)
        if ext == ".gif":
            buf = BytesIO()
            img.save(buf, format="GIF", save_all=True, optimize=True)
            return buf.getvalue()

        # Everything else → WebP
        buf = BytesIO()
        has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
        if has_alpha:
            img = img.convert("RGBA")
            # Use lossless for images with transparency (logos, icons)
            img.save(buf, format="WEBP", lossless=True, quality=100, method=4)
        else:
            img = img.convert("RGB")
            img.save(buf, format="WEBP", quality=quality, method=4)
        return buf.getvalue()
    except Exception:
        return raw


IMAGE_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# Judged by extension only: the browser-sent content type is the uploader's word, and an
# ".html" accepted as "audio/mpeg" would be served as a page from the church's own domain.
AUDIO_UPLOAD_EXTENSIONS = {"mp3", "wav", "m4a", "ogg", "flac", "aac", "wma", "mpga"}
IMAGE_UPLOAD_MAX_BYTES = 5 * 1024 * 1024


async def _save_photo_upload(upload: UploadFile, collection: str) -> str:
    """Persist an uploaded photo and return its web-relative path."""
    import uuid

    raw = await upload.read()
    ext = os.path.splitext(upload.filename or "")[1].lower() or ".jpg"
    if ext not in IMAGE_UPLOAD_EXTENSIONS:
        raise ValueError("Please upload a JPG, PNG, WebP, or GIF image.")
    if len(raw) > IMAGE_UPLOAD_MAX_BYTES:
        raise ValueError("Image uploads must be 5MB or smaller.")
    compressed = _compress_image(raw, ext)
    # Non-animated, non-SVG images are re-encoded as WebP by _compress_image
    filename = f"{collection}-{uuid.uuid4()}.webp" if ext != ".gif" else f"{collection}-{uuid.uuid4()}{ext}"
    dest_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "images", collection,
    )
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, filename), "wb") as f:
        f.write(compressed)
    return f"images/{collection}/{filename}"


async def _save_program_flyer_upload(upload: UploadFile) -> str:
    return await _save_photo_upload(upload, "program-flyers")


async def _save_slider_image(upload: UploadFile) -> str:
    """Compress and persist a slider image; returns its web-relative path."""
    import uuid

    ext = os.path.splitext(upload.filename or "")[1].lower() or ".jpg"
    if ext not in IMAGE_UPLOAD_EXTENSIONS:
        raise ValueError("Please upload a JPG, PNG, WebP, or GIF image.")
    raw = await upload.read()
    if len(raw) > IMAGE_UPLOAD_MAX_BYTES:
        raise ValueError("Image uploads must be 5MB or smaller.")
    content = _compress_image(raw, ext)
    out_ext = ".webp" if ext not in {".gif", ".svg"} else ext
    filename = f"slide-{uuid.uuid4()}{out_ext}"
    dest_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images", "sliders")
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, filename), "wb") as f:
        f.write(content)
    return f"images/sliders/{filename}"


def _photo_collection(value: str | None) -> str:
    from app.models.photo import Photo

    return value if value in Photo.COLLECTIONS else Photo.GALLERY


@router.get("/photos", name="admin_photos")
async def admin_photos(
    request: Request, db: AsyncSession = Depends(get_db),
    collection: str = Query("gallery"),
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.photo import Photo
    collection = _photo_collection(collection)
    result = await db.execute(
        select(Photo)
        .where(Photo.collection == collection)
        .order_by(Photo.sort_order.asc(), Photo.id.asc())
    )
    photos = list(result.scalars().all())

    counts = {}
    for name in Photo.COLLECTIONS:
        c = await db.execute(
            select(func.count()).select_from(Photo).where(Photo.collection == name)
        )
        counts[name] = c.scalar_one() or 0

    ctx = await _admin_context(request, db, "admin.photos")
    return await render_template(request, "admin/photos.html", {
        "_db": db, "photos": photos, "collection": collection, "counts": counts,
        "title": "Photos", "headerIcon": "ri-image-line", **ctx,
    })


@router.get("/photos/upload", name="admin_photos_upload_page")
async def admin_photos_upload_page(request: Request, db: AsyncSession = Depends(get_db), collection: str = Query("gallery")):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.photo import Photo
    collection = _photo_collection(collection)
    next_order = (await db.execute(
        select(func.count()).select_from(Photo).where(Photo.collection == collection)
    )).scalar_one() or 0
    ctx = await _admin_context(request, db, "admin.photos")
    return await render_template(request, "admin/photo-form.html", {
        "_db": db, "photo": None, "collection": collection, "next_order": next_order,
        "title": "Upload Photos", "headerIcon": "ri-image-line", **ctx,
    })


@router.get("/photos/{photo_id}/edit", name="admin_photos_edit_page")
async def admin_photos_edit_page(photo_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.photo import Photo
    photo = (await db.execute(select(Photo).where(Photo.id == photo_id))).scalar_one_or_none()
    if not photo:
        return _redirect(request, "/admin/photos", "Photo not found.", "error")

    ctx = await _admin_context(request, db, "admin.photos")
    return await render_template(request, "admin/photo-form.html", {
        "_db": db, "photo": photo, "collection": photo.collection, "next_order": photo.sort_order,
        "title": "Edit Photo", "headerIcon": "ri-image-line", **ctx,
    })


@router.post("/photos", name="admin_photos_store")
async def admin_photos_store(
    request: Request, db: AsyncSession = Depends(get_db),
    collection: str = Form("gallery"),
    caption: str = Form(""),
    sort_order: int = Form(0),
    is_active: bool = Form(True),
    images: list[UploadFile] = File(...),
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.photo import Photo
    collection = _photo_collection(collection)

    uploaded = [i for i in (images or []) if i and i.filename]
    if not uploaded:
        return _redirect(request, f"/admin/photos?collection={collection}",
                         "Choose at least one image.", "error")

    # Bulk upload is the norm for a gallery, so caption/order apply to the
    # first file and the rest are appended in order for later editing.
    try:
        for offset, upload in enumerate(uploaded):
            path = await _save_photo_upload(upload, collection)
            db.add(Photo(
                collection=collection,
                image_path=path,
                caption=(caption.strip() or None) if offset == 0 else None,
                sort_order=sort_order + offset,
                is_active=is_active,
            ))
    except ValueError as exc:
        return _redirect(request, f"/admin/photos?collection={collection}", str(exc), "error")
    await db.flush()
    return _redirect(request, f"/admin/photos?collection={collection}",
                     f"{len(uploaded)} photo(s) uploaded.")


@router.post("/photos/{photo_id}/update", name="admin_photos_update")
async def admin_photos_update(
    photo_id: int, request: Request, db: AsyncSession = Depends(get_db),
    caption: str = Form(""), sort_order: int = Form(0),
    is_active: bool = Form(True), image: UploadFile = File(None),
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.photo import Photo
    result = await db.execute(select(Photo).where(Photo.id == photo_id))
    photo = result.scalar_one_or_none()
    if not photo:
        return _redirect(request, "/admin/photos", "Photo not found.", "error")

    photo.caption = caption.strip() or None
    photo.sort_order = sort_order
    photo.is_active = is_active
    if image and image.filename:
        try:
            photo.image_path = await _save_photo_upload(image, photo.collection)
        except ValueError as exc:
            return _redirect(request, f"/admin/photos?collection={photo.collection}", str(exc), "error")

    await db.flush()
    return _redirect(request, f"/admin/photos?collection={photo.collection}",
                     "Photo updated.")


@router.post("/photos/{photo_id}/destroy", name="admin_photos_destroy")
async def admin_photos_destroy(photo_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.photo import Photo
    result = await db.execute(select(Photo).where(Photo.id == photo_id))
    photo = result.scalar_one_or_none()
    if not photo:
        return _redirect(request, "/admin/photos", "Photo not found.", "error")

    collection = photo.collection
    await db.delete(photo)
    await db.flush()
    return _redirect(request, f"/admin/photos?collection={collection}", "Photo deleted.")


@router.get("/program-flyers", name="admin_program_flyers")
async def admin_program_flyers(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.program_flyer import ProgramFlyer

    result = await db.execute(
        select(ProgramFlyer).order_by(ProgramFlyer.created_at.desc(), ProgramFlyer.id.desc())
    )
    flyers = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.program-flyers")
    return await render_template(
        request,
        "admin/program-flyers.html",
        {"_db": db, "flyers": flyers, **ctx},
    )


@router.get("/program-flyers/create", name="admin_program_flyers_create_page")
async def admin_program_flyers_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    ctx = await _admin_context(request, db, "admin.program-flyers")
    return await render_template(request, "admin/program-flyer-form.html", {
        "_db": db, "flyer": None,
        "title": "New Program Flyer", "headerIcon": "ri-gallery-upload-line", **ctx,
    })


@router.get("/program-flyers/{flyer_id}/edit", name="admin_program_flyers_edit_page")
async def admin_program_flyers_edit_page(flyer_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.program_flyer import ProgramFlyer
    flyer = (await db.execute(select(ProgramFlyer).where(ProgramFlyer.id == flyer_id))).scalar_one_or_none()
    if not flyer:
        return _redirect(request, "/admin/program-flyers", "Program flyer not found.", "error")

    ctx = await _admin_context(request, db, "admin.program-flyers")
    return await render_template(request, "admin/program-flyer-form.html", {
        "_db": db, "flyer": flyer,
        "title": "Edit Program Flyer", "headerIcon": "ri-gallery-upload-line", **ctx,
    })


@router.post("/program-flyers", name="admin_program_flyers_store")
async def admin_program_flyers_store(
    request: Request,
    db: AsyncSession = Depends(get_db),
    title: str = Form(""),
    is_active: bool = Form(True),
    image: UploadFile = File(...),
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    if not image or not image.filename:
        return _redirect(request, "/admin/program-flyers", "Choose a flyer image.", "error")

    from app.models.program_flyer import ProgramFlyer

    try:
        image_path = await _save_program_flyer_upload(image)
    except ValueError as exc:
        return _redirect(request, "/admin/program-flyers", str(exc), "error")

    db.add(
        ProgramFlyer(
            title=title.strip() or None,
            image_path=image_path,
            is_active=is_active,
        )
    )
    await db.flush()
    return _redirect(request, "/admin/program-flyers", "Program flyer added.")


@router.post("/program-flyers/{flyer_id}/update", name="admin_program_flyers_update")
async def admin_program_flyers_update(
    flyer_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    title: str = Form(""),
    is_active: bool = Form(True),
    image: UploadFile = File(None),
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.program_flyer import ProgramFlyer

    result = await db.execute(select(ProgramFlyer).where(ProgramFlyer.id == flyer_id))
    flyer = result.scalar_one_or_none()
    if not flyer:
        return _redirect(request, "/admin/program-flyers", "Program flyer not found.", "error")

    flyer.title = title.strip() or None
    flyer.is_active = is_active
    if image and image.filename:
        try:
            flyer.image_path = await _save_program_flyer_upload(image)
        except ValueError as exc:
            return _redirect(request, "/admin/program-flyers", str(exc), "error")

    await db.flush()
    return _redirect(request, "/admin/program-flyers", "Program flyer updated.")


@router.post("/program-flyers/{flyer_id}/destroy", name="admin_program_flyers_destroy")
async def admin_program_flyers_destroy(
    flyer_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.program_flyer import ProgramFlyer

    result = await db.execute(select(ProgramFlyer).where(ProgramFlyer.id == flyer_id))
    flyer = result.scalar_one_or_none()
    if not flyer:
        return _redirect(request, "/admin/program-flyers", "Program flyer not found.", "error")

    await db.delete(flyer)
    await db.flush()
    return _redirect(request, "/admin/program-flyers", "Program flyer deleted.")


# ─── Settings ──────────────────────────────────────────────────────

@router.get("/settings", name="admin_settings")
async def admin_settings(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    settings = await get_settings(db)
    from app.core.settings_cache import LEGACY_SETTING_KEY_ALIASES
    for legacy_key, canonical_key in LEGACY_SETTING_KEY_ALIASES.items():
        if settings.get(canonical_key):
            settings[legacy_key] = settings[canonical_key]
    if settings.get("church_accounts"):
        try:
            settings["church_accounts"] = json.loads(settings["church_accounts"])
        except (json.JSONDecodeError, TypeError):
            settings["church_accounts"] = []

    timezones = ['Africa/Lagos', 'Africa/Accra', 'Africa/Nairobi', 'Africa/Johannesburg',
                 'Europe/London', 'Europe/Paris', 'Europe/Berlin',
                 'America/New_York', 'America/Chicago', 'America/Los_Angeles',
                 'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Dubai',
                 'Australia/Sydney', 'Pacific/Auckland', 'UTC']

    themePresets = ADMIN_THEME_PRESETS

    ctx = await _admin_context(request, db, "admin.settings")
    feed_url = _public_base_url(request) + "/feed.xml"

    # Collect already-uploaded bank logos for the reuse picker
    bank_logos_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "images", "bank-logos",
    )
    bank_logos: list[str] = []
    if os.path.isdir(bank_logos_dir):
        for fname in sorted(os.listdir(bank_logos_dir)):
            if os.path.splitext(fname)[1].lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}:
                bank_logos.append(f"images/bank-logos/{fname}")

    # Normalise giving_purposes for the textarea: stored as JSON array or comma-separated string
    raw_gp = settings.get("giving_purposes", "")
    if raw_gp:
        try:
            gp_list = json.loads(raw_gp) if raw_gp.strip().startswith("[") else [p.strip() for p in raw_gp.split(",") if p.strip()]
        except Exception:
            gp_list = [p.strip() for p in raw_gp.splitlines() if p.strip()]
        settings["giving_purposes"] = "\n".join(gp_list)
    else:
        settings["giving_purposes"] = "Tithe\nOffering\nMissions\nBuilding Fund\nWelfare"

    # Secrets never go back to the browser, not even encrypted: the page only learns
    # whether each one is saved and its last four characters.
    from app.core.settings_cache import ENCRYPTED_SETTING_KEYS, get_secret_status
    secret_status = await get_secret_status(db)
    for secret_key in ENCRYPTED_SETTING_KEYS:
        settings.pop(secret_key, None)
    from app.config import settings as _env
    from app.core import ai as ai_mod
    env_fallback = {"anthropic_api_key": bool(_env.ANTHROPIC_API_KEY.strip()),
                    "paystack_secret_key": bool(_env.PAYSTACK_SECRET_KEY),
                    "flutterwave_secret_key": bool(_env.FLUTTERWAVE_SECRET_KEY)}

    from app.config import settings as _cfg
    from app.core import site_content as _sc
    from app.core.readiness import site_readiness
    from app.models.engagement import EmailLog

    email_log = list((await db.execute(select(EmailLog).order_by(EmailLog.created_at.desc()).limit(12))).scalars().all())
    _raw_site = await get_settings(db)
    from app.core import lists as _lists_mod
    from app.core import telegram_local as _tgl
    tg_large = {"on": _tgl.enabled(_raw_site), "keys_saved": _tgl.credentials_saved(_raw_site),
                "bot_saved": bool(_raw_site.get("telegram_bot_token")),
                "installed": bool(_cfg.TELEGRAM_BOT_API_CONF_DIR and os.path.isdir(_cfg.TELEGRAM_BOT_API_CONF_DIR))}
    svc_rows = _sc.services_to_form(_sc.get_services(_raw_site))
    return await render_template(request, "admin/settings.html", {
        "svc_rows": svc_rows, "expect_rows": _sc.get_expect(_raw_site) or [("", "")], "teach_rows": _sc.get_teaching(_raw_site) or [("", "")],
        "readiness": await site_readiness(db, _raw_site, _cfg.APP_URL), "email_log": email_log,
        "_db": db, "settings": settings, "timezones": timezones,
        "secret_status": secret_status, "env_fallback": env_fallback, "tg_large": tg_large,
        "managed_lists": [(name, meta[0], meta[1], chr(10).join(_lists_mod.items(_raw_site, name)))
                          for name, meta in _lists_mod.LISTS.items() if name != "giving_purposes"],
        "ai_providers": ai_mod.PROVIDERS,
        "ai_model_opts": {k: ai_mod.model_options(k, settings) for k in ai_mod.PROVIDERS},
        "ai_default": (settings.get(ai_mod.DEFAULT_PROVIDER_SETTING) or ai_mod.DEFAULT_PROVIDER),
        "ai_default_models": {"anthropic": _env.ANTHROPIC_MODEL, **{k: v["default_model"] for k, v in ai_mod.PROVIDERS.items() if k != "anthropic"}},
        "secret_key_is_default": _env.SECRET_KEY in ("", "change-me"),
        "themePresets": themePresets, "feed_url": feed_url,
        "bank_logos": bank_logos,
        "title": "Settings", "headerIcon": "ri-settings-4-line", **ctx,
    })


@router.post("/settings", name="admin_settings_update")
async def admin_settings_update(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    form = await request.form()
    settings_dict = {}
    # Map snake_case form keys to camelCase DB keys for compatibility
    from app.core.settings_cache import ENCRYPTED_SETTING_KEYS, LEGACY_SETTING_KEY_ALIASES
    _key_map = LEGACY_SETTING_KEY_ALIASES
    account_pattern = re.compile(r"^church_accounts\[(\d+)\]\[(bank_name|account_number|account_name|bank_logo)\]$")
    from app.core import site_content as _sc
    _row_keys = {"svc_day", "svc_name", "svc_time", "svc_lead", "exp_title", "exp_text", "bel_title", "bel_text", "test_to"}
    church_accounts: dict[int, dict] = {}
    for key in form:
        if key == "csrf_token" or key.startswith("clear_") or key in _row_keys:
            continue
        if key in ENCRYPTED_SETTING_KEYS:
            # Secrets are write-only: a blank box keeps the saved value, an explicit
            # "remove" tick clears it, anything typed replaces it.
            typed = str(form[key]).strip()
            if typed:
                settings_dict[key] = typed
            continue
        m = account_pattern.match(key)
        if m:
            idx, field = int(m.group(1)), m.group(2)
            church_accounts.setdefault(idx, {})[field] = form[key]
            continue
        db_key = _key_map.get(key, key)
        settings_dict[db_key] = form[key]

    if "svc_day" in form:                       # the Website content tab was on the page: rebuild its lists
        settings_dict["serviceTimes"] = _sc.services_from_form(form.getlist("svc_day"), form.getlist("svc_name"), form.getlist("svc_time"), form.getlist("svc_lead"))
        settings_dict["visitExpect"] = _sc.pairs_from_form(form.getlist("exp_title"), form.getlist("exp_text"))
        settings_dict["beliefs"] = _sc.pairs_from_form(form.getlist("bel_title"), form.getlist("bel_text"))
    for _k in ("whatsappChannelUrl", "telegramJoinUrl", "faithStatementUrl", "plausibleSrc", "siteUrl"):
        if _k in settings_dict:
            settings_dict[_k] = _sc.safe_https(settings_dict[_k]) if settings_dict[_k].strip() else ""
    if "plausibleDomain" in settings_dict:
        settings_dict["plausibleDomain"] = re.sub(r"[^a-zA-Z0-9.\-]", "", settings_dict["plausibleDomain"])[:120]
    accounts_list = [church_accounts[i] for i in sorted(church_accounts)]
    accounts_list = [
        a for a in accounts_list
        if any((a.get(f) or "").strip() for f in ("bank_name", "account_number", "account_name", "bank_logo"))
    ]
    settings_dict["church_accounts"] = json.dumps(accounts_list)
    for secret_key in ENCRYPTED_SETTING_KEYS:
        if form.get(f"clear_{secret_key}") == "1" and secret_key not in settings_dict:
            settings_dict[secret_key] = ""

    # Managed lists (Settings → Lists): one item per line → a clean JSON array.
    from app.core import lists as _lists_mod
    for name in _lists_mod.LISTS:
        key = _lists_mod.setting_key(name)
        if name != "giving_purposes" and key in form:
            items = _lists_mod.parse_textarea(str(form[key]))
            settings_dict[key] = json.dumps(items or _lists_mod.LISTS[name][2])

    # Telegram api_id / api_hash: catch a paste of the wrong value before it is encrypted.
    from app.core import telegram_local as _tgl
    tg_errors = []
    for tg_key, check, what in (("telegram_api_id", _tgl.valid_api_id, "App api_id must be the number shown on my.telegram.org (digits only)"),
                                ("telegram_api_hash", _tgl.valid_api_hash, "App api_hash must be the 32-character code shown on my.telegram.org")):
        if settings_dict.get(tg_key):
            settings_dict[tg_key] = re.sub(r"\s", "", settings_dict[tg_key]).lower()
            if not check(settings_dict[tg_key]):
                settings_dict.pop(tg_key)
                tg_errors.append(what)

    # Normalize giving_purposes: textarea sends newline-separated lines → save as JSON array
    if "giving_purposes" in settings_dict:
        raw_purposes = settings_dict["giving_purposes"]
        purposes = [p.strip() for p in raw_purposes.replace(",", "\n").splitlines() if p.strip()]
        # Remove duplicates while preserving order; ensure "Others" not duplicated (public route appends it)
        seen = set()
        deduped = []
        for p in purposes:
            if p not in seen:
                seen.add(p)
                deduped.append(p)
        settings_dict["giving_purposes"] = json.dumps(deduped)

    from app.core import ai as _ai
    if settings_dict.get(_ai.DEFAULT_PROVIDER_SETTING) not in (None, *_ai.PROVIDERS):
        settings_dict.pop(_ai.DEFAULT_PROVIDER_SETTING)
    bad_models = []
    for meta in _ai.PROVIDERS.values():
        mk = meta["model_setting"]
        if mk in settings_dict:
            settings_dict[mk] = settings_dict[mk].strip()
            if settings_dict[mk] and not _ai.valid_model_id(settings_dict[mk]):
                bad_models.append(settings_dict.pop(mk))
    if settings_dict.get("giving_model") not in (None, "central", "branch"):
        settings_dict.pop("giving_model")
    await update_settings(db, settings_dict)
    if {"telegram_api_id", "telegram_api_hash"} & set(settings_dict):
        saved = await get_settings(db)
        if saved.get(_tgl.LARGE_FILES_SETTING) == "on" and not _tgl.credentials_saved(saved):
            await update_settings(db, {_tgl.LARGE_FILES_SETTING: ""})   # nothing left to run it with
        _tgl.sync_credentials(saved)
    if bad_models:
        return _redirect(request, "/admin/settings#ai",
                         "Saved, except model name(s) with invalid characters: " + ", ".join(bad_models)
                         + ". Use letters, digits and . _ : / @ + - only.", "error")
    changed = sorted(k for k in settings_dict if k in ENCRYPTED_SETTING_KEYS)
    if changed:
        await log_admin_action(db, admin.id, "settings.secrets_changed", "settings", None, {"keys": changed}, request)
    if tg_errors:
        return _redirect(request, "/admin/settings#distribution", "Saved, except: " + "; ".join(tg_errors) + ".", "error")
    return _redirect(request, "/admin/settings", "Settings updated successfully.")


@router.post("/settings/test-telegram", name="admin_settings_test_telegram")
async def admin_settings_test_telegram(request: Request, db: AsyncSession = Depends(get_db)):
    """Check HQ's saved bot token and channel with Telegram itself."""
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core.telegram import TelegramDistributor

    ok, message = await TelegramDistributor(None).check()
    return _redirect(request, "/admin/settings#distribution", message, "success" if ok else "error")


@router.post("/settings/telegram-large-files", name="admin_settings_telegram_large_files")
async def admin_settings_telegram_large_files(request: Request, db: AsyncSession = Depends(get_db)):
    """Turn large Telegram uploads (self-hosted Bot API, up to 2 GB) on, off, or just check them.

    Turning on proves the server logs the bot in with the saved api_id/api_hash first, so a
    wrong key can never leave uploads failing. Off is instant: the bot still works on the cloud.
    """
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    import asyncio

    from app.core import telegram_local as tgl

    back = "/admin/settings#distribution"
    action = str((await request.form()).get("action") or "check")
    site = await get_settings(db)
    if action == "off":
        await update_settings(db, {tgl.LARGE_FILES_SETTING: ""})
        await log_admin_action(db, admin.id, "settings.telegram_large_files", "settings", None, {"on": False}, request)
        return _redirect(request, back, "Large files are off. Telegram uploads are limited to 50 MB again.")
    if not tgl.credentials_saved(site):
        return _redirect(request, back, "Save the App api_id and App api_hash first (step 4 of the guide).", "error")
    if not site.get("telegram_bot_token"):
        return _redirect(request, back, "Save the Telegram bot token first.", "error")
    if not tgl.sync_credentials(site):
        return _redirect(request, back, "The large-file service is not installed on this server yet "
                                        "(the telegram-bot-api container). Ask whoever deploys the site to add it.", "error")

    # The server restarts when its credentials change, then logs the bot in: allow it 30s in all.
    import time

    deadline = time.monotonic() + 30
    while True:
        state, message = await tgl.server_check(site["telegram_bot_token"], timeout=min(4.0, max(0.5, deadline - time.monotonic())))
        if state != "down" or time.monotonic() + 2 > deadline:
            break
        await asyncio.sleep(2)
    if state != "ok":
        if state == "rejected":
            message += ". Check that the App api_id and App api_hash were copied exactly."
        on = site.get(tgl.LARGE_FILES_SETTING) == "on"
        return _redirect(request, back, message + (" Large files stay off." if not on else ""), "error")
    if action == "on":
        await update_settings(db, {tgl.LARGE_FILES_SETTING: "on"})
        await log_admin_action(db, admin.id, "settings.telegram_large_files", "settings", None, {"on": True}, request)
        return _redirect(request, back, message + " Large files are on: Telegram now gets every message as uploaded.")
    return _redirect(request, back, message)


@router.post("/settings/test-email", name="admin_settings_test_email")
async def admin_settings_test_email(request: Request, db: AsyncSession = Depends(get_db)):
    """Send one real message with the saved Resend key, and report the provider's answer in plain words."""
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import mailer

    to = str((await request.form()).get("test_to") or "").strip() or admin.email
    if not mailer.valid_email(to):
        return _redirect(request, "/admin/settings#email", "Enter a valid email address to send the test to.", "error")
    ok, msg = await mailer.send_notice(to, "Test email from your website", base_url=_public_base_url(request), kind="test", heading="It works",
                                       paragraphs=["This is a test message from your church website. If you can read it, receipts, confirmations, reminders and newsletters can be delivered."],
                                       footnote="Sent from Admin → Settings → Email delivery.")
    return _redirect(request, "/admin/settings#email", f"Test email sent to {to}." if ok else f"The test email was not sent: {msg}", "success" if ok else "error")


@router.post("/settings/refresh-models", name="admin_settings_refresh_models")
async def admin_settings_refresh_models(request: Request, db: AsyncSession = Depends(get_db)):
    """Replace a provider's suggested models with the list it currently offers (retired models drop off)."""
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import ai

    provider = str((await request.form()).get("provider") or "")
    if provider not in ai.PROVIDERS:
        return _redirect(request, "/admin/settings#ai", "Unknown provider.", "error")
    try:
        models = await ai.fetch_models(provider)
    except ai.AIError as exc:
        return _redirect(request, "/admin/settings#ai", f"Could not refresh models: {exc}", "error")
    payload = json.dumps({"fetched_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "models": [list(m) for m in models]})
    await update_settings(db, {ai.MODELS_CACHE_PREFIX + provider: payload})
    return _redirect(request, "/admin/settings#ai", f"{ai.PROVIDERS[provider]['label']}: {len(models)} current models loaded.")


@router.post("/settings/test-ai", name="admin_settings_test_ai")
async def admin_settings_test_ai(request: Request, db: AsyncSession = Depends(get_db)):
    """Send a tiny request with the *saved* key so a typo is caught before a long job fails."""
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import ai

    form = await request.form()
    provider = str(form.get("provider") or "").strip() or None
    if provider and provider not in ai.PROVIDERS:
        return _redirect(request, "/admin/settings#ai", "Unknown provider.", "error")
    try:
        cfg = await ai.get_config(provider)
        await ai.complete('Reply with a JSON object {"ok": true}.', ["ping"], max_tokens=20, attempts=1, provider=provider)
    except ai.AIError as exc:
        return _redirect(request, "/admin/settings#ai", f"AI check failed: {exc}", "error")
    return _redirect(request, "/admin/settings#ai", f"{cfg.label} works (model {cfg.model}).")


@router.post("/settings/pastor-photo", name="admin_settings_pastor_photo")
async def admin_settings_pastor_photo(request: Request, db: AsyncSession = Depends(get_db), photo: UploadFile = File(...)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    if not photo or not photo.filename:
        return _redirect(request, "/admin/settings", "No file selected.", "error")
    ext = os.path.splitext(photo.filename or "")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return _redirect(request, "/admin/settings", "Invalid image format. Use JPG/PNG/WEBP/GIF.", "error")
    raw = await photo.read()
    if len(raw) > 5 * 1024 * 1024:
        return _redirect(request, "/admin/settings", "Image too large (max 5MB).", "error")
    import uuid
    # Pastor photo is intentionally saved uncompressed to preserve full quality.
    filename = f"pastor-{uuid.uuid4()}{ext}"
    dest_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images", "pastor")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    with open(dest, "wb") as f:
        f.write(raw)
    await update_settings(db, {"pastorPhoto": f"images/pastor/{filename}"})
    return _redirect(request, "/admin/settings", "Pastor photo updated successfully.")


# ─── Page Hero Upload ─────────────────────────────────────────────

@router.post("/settings/page-hero", name="admin_settings_page_hero")
async def admin_settings_page_hero(
    request: Request, db: AsyncSession = Depends(get_db),
    page_key: str = Form(...), image: UploadFile = File(...),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    if not image or not image.filename:
        return _redirect(request, "/admin/settings", "No file selected.", "error")
    ext = os.path.splitext(image.filename or "")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return _redirect(request, "/admin/settings", "Invalid image format.", "error")
    raw = await image.read()
    if len(raw) > 5 * 1024 * 1024:
        return _redirect(request, "/admin/settings", "Image too large (max 5MB).", "error")
    import uuid
    content = _compress_image(raw, ext)
    out_ext = ".webp" if ext not in {".gif"} else ext
    filename = f"hero-{page_key}-{uuid.uuid4()}{out_ext}"
    dest_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images", "heroes")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    with open(dest, "wb") as f:
        f.write(content)
    setting_key = f"pageHero_{page_key}" if page_key != "default" else "defaultPageHero"
    await update_settings(db, {setting_key: f"images/heroes/{filename}"})
    return _redirect(request, "/admin/settings", f"Hero image for {page_key} updated successfully.")


# ─── Bank Logo Upload ─────────────────────────────────────────────

@router.post("/settings/bank-logo", name="admin_settings_bank_logo")
async def admin_settings_bank_logo(
    request: Request, db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Upload a bank logo and return its static path as JSON — called by the accounts JS."""
    from fastapi.responses import JSONResponse
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    form = await request.form()
    # CSRF check
    if form.get("csrf_token") != request.session.get("csrf_token"):
        return JSONResponse({"error": "Invalid CSRF token"}, status_code=403)
    if not file or not file.filename:
        return JSONResponse({"error": "No file provided"}, status_code=400)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}:
        return JSONResponse({"error": "Invalid image format."}, status_code=400)
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        return JSONResponse({"error": "Image too large (max 2 MB)."}, status_code=400)
    import uuid
    # Logos are often small PNGs with transparency — compress but keep SVG as-is
    content = _compress_image(raw, ext, max_width=400, quality=90)
    out_ext = ".webp" if ext not in {".gif", ".svg"} else ext
    filename = f"bank-{uuid.uuid4()}{out_ext}"
    dest_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "images", "bank-logos",
    )
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, filename), "wb") as f:
        f.write(content)
    return JSONResponse({"path": f"images/bank-logos/{filename}"})


# ─── Security Logs ─────────────────────────────────────────────────

# Severity is not stored on AdminAuditLog; it is derived from the action name so the
# UI, the dashboard alert and the filter all agree.
_AUDIT_CRITICAL_MARKERS = ("failed", "denied", "locked", "breach")
_AUDIT_WARN_MARKERS = ("password", "export", "delete", "destroy", "settings", "role", "reset")


def _audit_severity(action: str | None) -> str:
    text = (action or "").lower()
    if any(m in text for m in _AUDIT_CRITICAL_MARKERS):
        return "critical"
    if any(m in text for m in _AUDIT_WARN_MARKERS):
        return "warn"
    return "info"


@router.get("/security-logs", name="admin_security_logs")
async def admin_security_logs(
    request: Request, db: AsyncSession = Depends(get_db),
    reviewed: str = Query(""), severity: str = Query(""),
    action: str = Query(""), ip: str = Query(""),
    admin_id: str = Query(""), assigned_to_admin_id: str = Query(""),
    page: int = Query(1, ge=1),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from urllib.parse import urlencode
    from app.models.admin import AdminAuditLog, Admin as AdminModel

    query = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())

    if reviewed == "no":
        query = query.where(AdminAuditLog.reviewed_at == None)
    elif reviewed == "yes":
        query = query.where(AdminAuditLog.reviewed_at != None)
    if action.strip():
        query = query.where(AdminAuditLog.action.ilike(f"%{action.strip()}%"))
    if ip.strip():
        query = query.where(AdminAuditLog.ip_address.ilike(f"%{ip.strip()}%"))
    if admin_id.isdigit():
        query = query.where(AdminAuditLog.admin_id == int(admin_id))
    if assigned_to_admin_id.isdigit():
        query = query.where(AdminAuditLog.assigned_to_id == int(assigned_to_admin_id))
    if severity == "critical":
        query = query.where(or_(*[AdminAuditLog.action.ilike(f"%{m}%") for m in _AUDIT_CRITICAL_MARKERS]))
    elif severity == "warn":
        query = query.where(
            or_(*[AdminAuditLog.action.ilike(f"%{m}%") for m in _AUDIT_WARN_MARKERS]),
            *[~AdminAuditLog.action.ilike(f"%{m}%") for m in _AUDIT_CRITICAL_MARKERS],
        )
    elif severity == "info":
        query = query.where(
            *[~AdminAuditLog.action.ilike(f"%{m}%") for m in _AUDIT_CRITICAL_MARKERS + _AUDIT_WARN_MARKERS],
        )

    pagination = await paginate(db, query, page=page, per_page=25)
    filters = {
        k: v for k, v in {
            "reviewed": reviewed, "severity": severity, "action": action, "ip": ip,
            "admin_id": admin_id, "assigned_to_admin_id": assigned_to_admin_id,
        }.items() if v
    }

    def _page_url(n: int) -> str:
        return "/admin/security-logs?" + urlencode({**filters, "page": n})

    pagination["prev_url"] = _page_url(page - 1) if pagination["has_prev"] else None
    pagination["next_url"] = _page_url(page + 1) if pagination["has_next"] else None

    ctx = await _admin_context(request, db, "admin.security-logs")

    admins_list = list((await db.execute(
        select(AdminModel).where(AdminModel.is_active == True).order_by(AdminModel.full_name)
    )).scalars().all())
    admin_names = {a.id: a.full_name for a in admins_list}
    log_severity = {log.id: _audit_severity(log.action) for log in pagination["items"]}

    return await render_template(request, "admin/security-logs.html", {
        "_db": db, "logs": pagination["items"], "pagination": pagination,
        "admins": admins_list, "admin_names": admin_names, "log_severity": log_severity,
        "filters": filters, "export_url": "/admin/security-logs/export",
        "title": "Security Logs", "headerIcon": "ri-shield-keyhole-line", **ctx,
    })


@router.post("/security-logs/bulk-review", name="admin_security_logs_bulk_review")
async def admin_security_logs_bulk_review(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    form = await request.form()
    ids = [int(v) for v in form.getlist("log_ids[]") if str(v).isdigit()]
    if not ids:
        return _redirect(request, "/admin/security-logs", "Select at least one log to mark as reviewed.", "error")

    from app.models.admin import AdminAuditLog
    result = await db.execute(
        select(AdminAuditLog).where(AdminAuditLog.id.in_(ids), AdminAuditLog.reviewed_at == None)
    )
    logs = result.scalars().all()
    for log_entry in logs:
        log_entry.reviewed_at = datetime.utcnow()
    await db.flush()
    return _redirect(request, "/admin/security-logs", f"{len(logs)} log(s) marked as reviewed.")


# ─── Profile ───────────────────────────────────────────────────────

@router.get("/profile", name="admin_profile")
async def admin_profile(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    ctx = await _admin_context(request, db, "admin.profile")
    return await render_template(request, "admin/profile.html", {
        "_db": db, "error_messages": {},
        "title": "My Profile", "headerIcon": "ri-user-line", **ctx,
    })


@router.post("/profile", name="admin_profile_update")
async def admin_profile_update(
    request: Request, db: AsyncSession = Depends(get_db),
    full_name: str = Form(...), email: str = Form(...), phone: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.admin import Admin as AdminModel
    email_clean = email.strip().lower()
    clash = (await db.execute(
        select(AdminModel.id).where(AdminModel.email == email_clean, AdminModel.id != admin.id)
    )).scalar_one_or_none()
    if clash:
        return _redirect(request, "/admin/profile", "That email address is already in use.", "error")

    admin.full_name = full_name.strip()
    admin.email = email_clean
    admin.phone = phone.strip() or None
    await db.flush()
    return _redirect(request, "/admin/profile", "Profile updated successfully.")


@router.post("/profile/password", name="admin_profile_password_update")
async def admin_profile_password_update(
    request: Request, db: AsyncSession = Depends(get_db),
    current_password: str = Form(...), password: str = Form(...),
    password_confirmation: str = Form(...),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    if not verify_password(current_password, admin.password_hash):
        ctx = await _admin_context(request, db, "admin.profile")
        return await render_template(request, "admin/profile.html", {
            "_db": db, "error_messages": {"current_password": ["Current password is incorrect."]},
            "title": "My Profile", "headerIcon": "ri-user-line", **ctx,
        })

    if password != password_confirmation:
        ctx = await _admin_context(request, db, "admin.profile")
        return await render_template(request, "admin/profile.html", {
            "_db": db, "error_messages": {"password": ["Passwords do not match."]},
            "title": "My Profile", "headerIcon": "ri-user-line", **ctx,
        })

    if len(password) < 8:
        ctx = await _admin_context(request, db, "admin.profile")
        return await render_template(request, "admin/profile.html", {
            "_db": db, "error_messages": {"password": ["Password must be at least 8 characters."]},
            "title": "My Profile", "headerIcon": "ri-user-line", **ctx,
        })

    if verify_password(password, admin.password_hash):
        ctx = await _admin_context(request, db, "admin.profile")
        return await render_template(request, "admin/profile.html", {
            "_db": db, "error_messages": {"password": ["Choose a password you have not used just now."]},
            "title": "My Profile", "headerIcon": "ri-user-line", **ctx,
        })
    from app.models.admin import Admin as _AdminRow
    row = (await db.execute(select(_AdminRow).where(_AdminRow.id == admin.id))).scalar_one()
    row.password_hash = hash_password(password)
    row.must_change_password = False
    row.session_version = (row.session_version or 0) + 1
    request.session["admin_sv"] = row.session_version
    await db.flush()
    await log_admin_action(db, admin.id, "auth.password.changed", "admin", admin.id, None, request)
    return _redirect(request, "/admin/profile", "Password changed successfully.")


# ─── HOD Reports ───────────────────────────────────────────────────

@router.get("/hod/reports", name="hod_reports_index")
async def hod_reports_index(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.unit_report import UnitReport
    ctx = await _admin_context(request, db, "hod.reports")

    report_query = select(UnitReport).where(UnitReport.hod_admin_id == admin.id).order_by(UnitReport.created_at.desc())
    reports_result = await db.execute(report_query)
    reports = list(reports_result.scalars().all())

    return await render_template(request, "hod/reports/index.html", {
        "_db": db, "reports": reports, "hod": admin,
        "department_name": await _admin_department_name(db, admin),
        "title": "Monthly Reports", "headerIcon": "ri-file-chart-line", **ctx,
    })


@router.get("/hod/reports/create", name="hod_reports_create")
async def hod_reports_create(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    ctx = await _admin_context(request, db, "hod.reports")
    return await render_template(request, "hod/reports/create.html", {
        "_db": db, "report": None, "hod": admin, "report_currency": await _branch_currency(db, admin.branch_id),
        "department_name": await _admin_department_name(db, admin),
        "title": "Create Report", "headerIcon": "ri-file-chart-line", **ctx,
    })


async def _admin_department_name(db: AsyncSession, admin) -> str:
    """Department name for an HOD (queried explicitly; the relationship is lazy)."""
    if not getattr(admin, "department_id", None):
        return ""
    from app.models.department import Department
    dept = (await db.execute(select(Department).where(Department.id == admin.department_id))).scalar_one_or_none()
    return dept.name if dept else ""


HOD_REPORT_WEEKS = ["week1", "week2", "week3", "week4", "week5", "others", "total"]


def _parse_hod_report_form(form) -> dict:
    """Turn the weekly-grid HOD report form into the JSON structures stored on UnitReport.

    attendance -> {"male"|"female"|"total": {"week1": "..", ..., "total": ".."}}
    collections -> {"dues": {...weeks}, "others": {"label": "..", ...weeks}, "total": ".."}
    """
    def _cell(name: str) -> str:
        return str(form.get(name, "") or "").strip()

    attendance = {
        row: {wk: _cell(f"attendance_{row}_{wk}") for wk in HOD_REPORT_WEEKS}
        for row in ("male", "female", "total")
    }
    collections = {
        "dues": {wk: _cell(f"dues_{wk}") for wk in HOD_REPORT_WEEKS},
        "others": {"label": _cell("others_label"), **{wk: _cell(f"others_{wk}") for wk in HOD_REPORT_WEEKS}},
        "total": _cell("collections_total"),
    }
    return {"attendance": attendance, "collections": collections}


def _hod_can_edit_report(admin, report) -> bool:
    return admin.role == "super_admin" or report.hod_admin_id == admin.id


@router.post("/hod/reports", name="hod_reports_store")
async def hod_reports_store(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    form = await request.form()
    report_month = str(form.get("report_month", "") or "").strip()
    introduction = str(form.get("introduction", "") or "").strip()
    if not report_month:
        return _redirect(request, "/admin/hod/reports/create", "Please choose the report month.", "error")
    if not introduction:
        return _redirect(request, "/admin/hod/reports/create", "Please add an introduction before saving the report.", "error")

    from app.models.unit_report import UnitReport

    payload = _parse_hod_report_form(form)
    submit_now = bool(form.get("submit_now"))
    report = UnitReport(
        report_month=report_month,
        introduction=introduction,
        attendance=payload["attendance"],
        attendance_note=str(form.get("attendance_note", "") or "").strip() or None,
        collections=payload["collections"],
        observations=str(form.get("observations", "") or "").strip() or None,
        challenges=str(form.get("challenges", "") or "").strip() or None,
        recommendations=str(form.get("recommendations", "") or "").strip() or None,
        projections=str(form.get("projections", "") or "").strip() or None,
        status="submitted" if submit_now else "draft",
        submitted_at=datetime.utcnow() if submit_now else None,
        branch_id=admin.branch_id,
        department_id=admin.department_id,
        hod_admin_id=admin.id,
    )
    db.add(report)
    await db.flush()
    return _redirect(request, "/admin/hod/reports", "Report submitted successfully." if submit_now else "Draft saved.")


@router.get("/hod/invitation/{token}", name="hod_invitation_accept_page")
async def hod_invitation_accept_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    from app.models.hod_invitation import HodInvitation

    result = await db.execute(
        select(HodInvitation)
        .options(selectinload(HodInvitation.branch), selectinload(HodInvitation.department))
        .where(HodInvitation.token == token)
        .where(HodInvitation.accepted_at == None)
        .where(HodInvitation.expires_at > datetime.utcnow())
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        return RedirectResponse(url="/admin", status_code=303)

    site_name = await _get_site_name(db)
    return await render_template(request, "hod/invitation-accept.html", {
        "_db": db, "invitation": invitation, "token": token,
        "siteName": site_name, "adminThemeCss": "", "error_messages": {},
    })


@router.post("/hod/invitation/{token}", name="hod_invitation_accept")
async def hod_invitation_accept(
    token: str, request: Request,
    password: str = Form(...), password_confirmation: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload
    from app.models.hod_invitation import HodInvitation
    from app.models.admin import Admin

    result = await db.execute(
        select(HodInvitation)
        .options(selectinload(HodInvitation.branch), selectinload(HodInvitation.department))
        .where(HodInvitation.token == token)
        .where(HodInvitation.accepted_at == None)
        .where(HodInvitation.expires_at > datetime.utcnow())
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        return RedirectResponse(url="/admin", status_code=303)

    if password != password_confirmation:
        return await render_template(request, "hod/invitation-accept.html", {
            "_db": db, "invitation": invitation, "token": token,
            "error_messages": {"password": ["Passwords do not match."]},
        })

    admin_user = Admin(
        full_name=invitation.name or "Department HOD",
        email=invitation.email,
        password_hash=hash_password(password),
        role="hod",
        branch_id=invitation.branch_id,
        is_active=True,
    )
    db.add(admin_user)
    await db.flush()

    invitation.accepted_at = datetime.utcnow()
    await db.flush()

    login_admin(request, admin_user.id)
    return RedirectResponse(url="/admin/hod/reports", status_code=303)


# ─── Members: Update & Destroy ────────────────────────────────────

@router.post("/members/{member_id}/update", name="admin_members_update")
async def admin_members_update(
    member_id: int, request: Request, db: AsyncSession = Depends(get_db),
    first_name: str = Form(""), last_name: str = Form(""),
    email: str = Form(""), phone: str = Form(""),
    gender: str = Form(""), membership_status: str = Form("active"),
    date_of_birth: str = Form(""), joined_at: str = Form(""),
    notes: str = Form(""), branch_id: int = Form(None),
    country_code: str = Form(""), region_code: str = Form(""), city: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        return _redirect(request, "/admin/members", "Member not found.", "error")
    if _foreign(admin, getattr(member, "branch_id", None)):
        return _redirect(request, "/admin/members", "You can only manage records of your own branch.", "error")

    member.first_name = first_name.strip() or member.first_name
    member.last_name = last_name.strip() or member.last_name
    from app.core import geo, lists

    if gender.strip() and not lists.gender(gender):
        return _redirect(request, f"/admin/members/{member_id}/edit", "Choose the member's gender from the list.", "error")
    if membership_status not in ("active", "inactive"):
        return _redirect(request, f"/admin/members/{member_id}/edit", "Choose a membership status from the list.", "error")
    if err := await _apply_member_location(db, member, country_code, region_code, city):
        return _redirect(request, f"/admin/members/{member_id}/edit", err, "error")
    member.email = email.strip() if email else None
    member.phone = geo.normalize_phone(phone, await _branch_country(db, member.branch_id)) or None
    member.gender = lists.gender(gender)
    member.membership_status = membership_status
    member.notes = notes.strip() if notes else None

    if date_of_birth:
        try:
            member.date_of_birth = datetime.fromisoformat(date_of_birth).date()
        except ValueError:
            pass
    if joined_at:
        try:
            member.joined_at = datetime.fromisoformat(joined_at).date()
        except ValueError:
            pass

    if admin.role == "super_admin" and branch_id:
        member.branch_id = branch_id

    await db.flush()
    return _redirect(request, "/admin/members", "Member updated successfully.")


@router.post("/members/{member_id}/transfer", name="admin_members_transfer")
async def admin_members_transfer(member_id: int, request: Request, db: AsyncSession = Depends(get_db), to_branch_id: int = Form(...)):
    """Move a member to another branch (HQ Administrator only; recorded in the audit log)."""
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.member import Member

    member = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    target = await _load_branch(db, to_branch_id)
    if member is None or target is None or not target.is_active:
        return _redirect(request, "/admin/members", "Member or destination branch not found.", "error")
    old = member.branch_id
    member.branch_id = target.id
    await log_admin_action(db, admin.id, "member.transferred", "member", member.id, {"from_branch": old, "to_branch": target.id}, request)
    return _redirect(request, f"/admin/members/{member_id}", f"Transferred to {target.name}.")


@router.post("/members/{member_id}/destroy", name="admin_members_destroy")
async def admin_members_destroy(member_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        return _redirect(request, "/admin/members", "Member not found.", "error")
    if _foreign(admin, getattr(member, "branch_id", None)):
        return _redirect(request, "/admin/members", "You can only manage records of your own branch.", "error")

    await db.delete(member)
    await db.flush()
    return _redirect(request, "/admin/members", "Member deleted successfully.")


# ─── Admins: Update & Reset Password ─────────────────────────────

@router.post("/admins/{admin_id}/update", name="admin_admins_update")
async def admin_admins_update(
    admin_id: int, request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), phone: str = Form(""),
    role: str = Form(""),
    branch_id: int = Form(None), is_active: bool = Form(True),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.admin import Admin as AdminModel
    result = await db.execute(select(AdminModel).where(AdminModel.id == admin_id))
    target = result.scalar_one_or_none()
    if not target:
        return _redirect(request, "/admin/admins", "Admin not found.", "error")

    from app.core.access import BRANCH_ADMIN, FORM_ROLES, HOD, RESIDENT_PASTOR, SUPER, normalize_role
    new_role = normalize_role(role) or normalize_role(target.role)
    if new_role == RESIDENT_PASTOR and normalize_role(target.role) != RESIDENT_PASTOR:
        return _redirect(request, f"/admin/admins/{admin_id}/edit", "Resident Pastors are appointed from the Ministers page.", "error")
    if new_role not in FORM_ROLES and new_role not in (HOD, RESIDENT_PASTOR):
        return _redirect(request, f"/admin/admins/{admin_id}/edit", "Choose a valid role.", "error")
    if new_role == BRANCH_ADMIN and not (branch_id or target.branch_id):
        return _redirect(request, f"/admin/admins/{admin_id}/edit", "A branch admin must be assigned to a branch.", "error")
    # Never leave HQ without an administrator.
    if normalize_role(target.role) == SUPER and (new_role != SUPER or not is_active):
        others = (await db.execute(select(func.count()).select_from(AdminModel).where(
            AdminModel.role == SUPER, AdminModel.is_active == True, AdminModel.id != target.id))).scalar_one()  # noqa: E712
        if not others:
            return _redirect(request, f"/admin/admins/{admin_id}/edit", "There must always be at least one active HQ Administrator.", "error")
    if (normalize_role(target.role) != new_role) or (target.is_active and not is_active) or (new_role == BRANCH_ADMIN and branch_id and branch_id != target.branch_id):
        target.session_version = (target.session_version or 0) + 1
    target.full_name = name.strip() or target.full_name
    target.phone = phone.strip() or None
    target.role = new_role
    target.is_active = is_active
    if new_role == BRANCH_ADMIN:
        target.branch_id = branch_id or target.branch_id
    elif new_role not in (HOD, RESIDENT_PASTOR):
        target.branch_id = await get_hq_branch_id(db)
    await db.flush()
    await log_admin_action(db, admin.id, "admin.role_changed", "admin", target.id, {"role": new_role, "active": is_active}, request)
    return _redirect(request, "/admin/admins", "Admin updated successfully.")


@router.post("/admins/{admin_id}/reset-password", name="admin_admins_reset_password")
async def admin_admins_reset_password(
    admin_id: int, request: Request, db: AsyncSession = Depends(get_db),
    password: str = Form(...), password_confirmation: str = Form(...),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    if password != password_confirmation:
        return _redirect(request, "/admin/admins", "Passwords do not match.", "error")
    if len(password) < 8:
        return _redirect(request, "/admin/admins", "Password must be at least 8 characters.", "error")

    from app.models.admin import Admin as AdminModel
    result = await db.execute(select(AdminModel).where(AdminModel.id == admin_id))
    target = result.scalar_one_or_none()
    if not target:
        return _redirect(request, "/admin/admins", "Admin not found.", "error")

    target.password_hash = hash_password(password)
    target.must_change_password = target.id != admin.id
    target.session_version = (target.session_version or 0) + 1
    await db.flush()
    await log_admin_action(db, admin.id, "admin.password_reset", "admin", target.id, None, request)
    return _redirect(request, "/admin/admins", "Password reset. They will be asked to choose their own at next sign-in.")


# ─── Departments: Update & Destroy ───────────────────────────────

@router.post("/departments/{department_id}/update", name="admin_departments_update")
async def admin_departments_update(
    department_id: int, request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), description: str = Form(""),
    is_active: bool = Form(True),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.department import Department
    result = await db.execute(select(Department).where(Department.id == department_id))
    dept = result.scalar_one_or_none()
    if not dept:
        return _redirect(request, "/admin/departments", "Department not found.", "error")

    if name.strip() and await _value_taken(db, Department.name, name, exclude_id=dept.id):
        return _redirect(request, f"/admin/departments/{dept.id}/edit", "A department with that name already exists.", "error")
    dept.name = name.strip() or dept.name
    dept.description = description.strip() if description else None
    dept.is_active = is_active
    await db.flush()
    return _redirect(request, "/admin/departments", "Department updated successfully.")


@router.post("/departments/{department_id}/destroy", name="admin_departments_destroy")
async def admin_departments_destroy(department_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.department import Department
    result = await db.execute(select(Department).where(Department.id == department_id))
    dept = result.scalar_one_or_none()
    if not dept:
        return _redirect(request, "/admin/departments", "Department not found.", "error")

    # Departments are shared by every church: deleting one that has reports or HOD accounts
    # would erase (or orphan) other churches' history, so only unused ones may go.
    from app.models.admin import Admin
    from app.models.unit_report import UnitReport
    reports = (await db.execute(select(func.count(UnitReport.id)).where(UnitReport.department_id == dept.id))).scalar_one()
    hods = (await db.execute(select(func.count(Admin.id)).where(Admin.department_id == dept.id))).scalar_one()
    if reports or hods:
        held = " and ".join(x for x in (f"{reports} report{'s' * (reports != 1)}" if reports else "",
                                         f"{hods} HOD account{'s' * (hods != 1)}" if hods else "") if x)
        return _redirect(request, "/admin/departments",
                         f"{dept.name} has {held} across the churches, so it cannot be deleted. Untick Active on its edit page to retire it instead.",
                         "error")

    await db.delete(dept)
    await db.flush()
    return _redirect(request, "/admin/departments", "Department deleted successfully.")


# ─── Messages (Sermons): Update & Destroy ────────────────────────

@router.post("/messages/{sermon_id}/update", name="admin_messages_update")
async def admin_messages_update(
    sermon_id: int, request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), speaker: str = Form(""),
    caption: str = Form(""), summary: str = Form(""),
    status: str = Form("draft"), published_on: str = Form(""),
    branch_id: int = Form(None),
    platform_targets: list[str] = Form([]),
    series: str = Form(""), scripture: str = Form(""), transcript_public: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
    sermon = result.scalar_one_or_none()
    if not sermon:
        return _redirect(request, "/admin/messages", "Sermon not found.", "error")
    if _foreign(admin, getattr(sermon, "branch_id", None)):
        return _redirect(request, "/admin/messages", "You can only manage records of your own branch.", "error")

    sermon.title = title.strip() or sermon.title
    sermon.preacher = await _known_spelling(db, Sermon.preacher, speaker) if speaker else sermon.preacher
    sermon.caption = caption.strip() if caption else sermon.caption
    sermon.description = summary.strip() if summary else sermon.description
    sermon.status = status.strip() or sermon.status
    sermon.platform_targets = [p for p in platform_targets if p] or None
    sermon.series, sermon.scripture = await _known_spelling(db, Sermon.series, series, 150), scripture.strip()[:150] or None
    sermon.transcript_public = transcript_public == "1"

    if published_on:
        try:
            sermon.published_at = datetime.fromisoformat(published_on)
        except ValueError:
            pass
    elif status == "published" and not sermon.published_at:
        sermon.published_at = datetime.utcnow()

    if admin.role == "super_admin" and branch_id:
        sermon.branch_id = branch_id

    await db.flush()
    return _redirect(request, "/admin/messages", "Audio message updated successfully.")


@router.post("/messages/{sermon_id}/redistribute", name="admin_messages_redistribute")
async def admin_messages_redistribute(sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.core.jobs import enqueue
    from app.core.telegram import TelegramDistributor
    from app.models.sermon import Sermon
    from app.tasks import distribute_audio_task

    sermon = (await db.execute(select(Sermon).where(Sermon.id == sermon_id))).scalar_one_or_none()
    if not sermon:
        return _redirect(request, "/admin/messages", "Message not found.", "error")
    if _foreign(admin, sermon.branch_id):
        return _redirect(request, "/admin/messages", "You can only manage records of your own branch.", "error")
    if not await TelegramDistributor(sermon.branch_id).configured():
        return _redirect(request, "/admin/messages", "This church has no Telegram bot and channel saved yet (see Settings).", "error")

    status = dict(sermon.distribution_status or {})
    status["telegram"] = {"state": "queued", "updated_at": datetime.now(timezone.utc).isoformat()}
    sermon.distribution_status = status
    targets = list(sermon.platform_targets or [])
    if "telegram" not in targets:
        sermon.platform_targets = targets + ["telegram"]
    await db.commit()
    if not await enqueue(distribute_audio_task, sermon_id):
        status["telegram"] = {"state": "failed", "error": "the background worker is unavailable; try again shortly",
                              "updated_at": datetime.now(timezone.utc).isoformat()}
        sermon.distribution_status = dict(status)
        return _redirect(request, "/admin/messages", "The background worker is unavailable. Try again shortly.", "error")
    return _redirect(request, "/admin/messages", f"“{sermon.title}” is being sent to Telegram again.")


@router.post("/messages/{sermon_id}/destroy", name="admin_messages_destroy")
async def admin_messages_destroy(sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
    sermon = result.scalar_one_or_none()
    if not sermon:
        return _redirect(request, "/admin/messages", "Sermon not found.", "error")
    if _foreign(admin, getattr(sermon, "branch_id", None)):
        return _redirect(request, "/admin/messages", "You can only manage records of your own branch.", "error")

    await db.delete(sermon)
    await db.flush()
    return _redirect(request, "/admin/messages", "Audio message deleted successfully.")


# ─── Telegram archive ──────────────────────────────────────────────

async def _archive_super(request: Request):
    admin = await _get_admin(request)
    return admin if admin and admin.role == "super_admin" else None


@router.get("/telegram-archive", name="admin_telegram_archive")
async def admin_telegram_archive(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _archive_super(request)
    if not admin:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    import shutil

    from app.config import settings as _cfg
    from app.core import telegram_archive as ta
    from app.core import telegram_local
    from app.models.telegram_archive import TelegramArchiveItem as Item

    site = await get_settings(db)
    rows = (await db.execute(select(Item.status, func.count(Item.id), func.coalesce(func.sum(Item.size), 0))
                             .group_by(Item.status))).all()
    counts = {s: {"n": n, "bytes": int(b)} for s, n, b in rows}
    imported = (await db.execute(select(func.count(Item.id)).where(Item.sermon_id.isnot(None)))).scalar_one()
    items = list((await db.execute(select(Item).order_by(Item.posted_at.desc()).limit(500))).scalars().all())
    # Sizes of the files still to back up, in backup order, so the page can show what "next N" needs.
    queue_sizes = [int(s or 0) for (s,) in (await db.execute(select(Item.size).where(Item.status.in_(("found", "failed")))
                                                              .order_by(Item.posted_at).limit(10000))).all()]
    archive_dir = _cfg.telegram_archive_dir
    try:
        disk_free = shutil.disk_usage(archive_dir if os.path.isdir(archive_dir) else os.path.dirname(archive_dir) or ".").free
    except OSError:
        disk_free = None
    job = await ta.get_job(db)
    # When the shown result happened (HQ's local time), so an old result never looks current.
    from app.core import geo as _geo
    from app.core.platform import platform_timezone

    job_when = (datetime.fromtimestamp(float(job["heartbeat"]), timezone.utc).astimezone(_geo.zone(await platform_timezone(db)))
                .strftime("%d %b %Y, %H:%M %Z")) if job.get("heartbeat") else ""
    ctx = await _admin_context(request, db, "admin.telegram-archive")
    return await render_template(request, "admin/telegram-archive.html", {
        "_db": db, "login": await ta.login_state(), "job": job, "job_running": ta.job_running(job), "job_when": job_when,
        "keys_saved": telegram_local.credentials_saved(site), "channel_saved": bool((site.get("telegram_audio_chat_id") or "").strip()),
        "counts": counts, "imported": imported, "items": items, "disk_free": disk_free, "disk_margin": ta.DISK_MARGIN,
        "queue_sizes": queue_sizes,
        "title": "Telegram archive", "headerIcon": "ri-archive-drawer-line", **ctx,
    })


@router.get("/telegram-archive/status", name="admin_telegram_archive_status")
async def admin_telegram_archive_status(request: Request, db: AsyncSession = Depends(get_db)):
    if not await _archive_super(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from app.core import telegram_archive as ta

    job = await ta.get_job(db)
    return JSONResponse({"running": ta.job_running(job), "state": job.get("state"), "message": job.get("message"),
                         "done": job.get("done", 0), "total": job.get("total", 0),
                         "bytes_done": job.get("bytes_done", 0), "bytes_total": job.get("bytes_total", 0)})


@router.post("/telegram-archive/login", name="admin_telegram_archive_login")
async def admin_telegram_archive_login(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _archive_super(request)
    if not admin:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import telegram_archive as ta

    form = await request.form()
    step = str(form.get("step") or "")
    back = "/admin/telegram-archive#signin"
    try:
        if step == "phone":
            await ta.start_login(str(form.get("phone") or ""))
            return _redirect(request, back, "Telegram sent a login code to that account: look for a message from “Telegram” in the Telegram app.")
        if step == "code":
            stage = await ta.submit_code(str(form.get("code") or ""))
            if stage == "password":
                return _redirect(request, back, "This account has two-step verification: enter its password to finish.", "info")
        elif step == "password":
            await ta.submit_password(str(form.get("password") or ""))
        elif step == "restart":
            await ta.sign_out()
            return _redirect(request, back, "Sign-in cancelled. You can start again.", "info")
        else:
            return _redirect(request, back, "Unknown step.", "error")
    except ta.ArchiveError as exc:
        return _redirect(request, back, str(exc), "error")
    await log_admin_action(db, admin.id, "telegram_archive.signed_in", "settings", None, {}, request)
    return _redirect(request, "/admin/telegram-archive", "Signed in to Telegram. Next: find the channel's audio.")


@router.post("/telegram-archive/sign-out", name="admin_telegram_archive_sign_out")
async def admin_telegram_archive_sign_out(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _archive_super(request)
    if not admin:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import telegram_archive as ta

    if ta.job_running(await ta.get_job(db)):
        return _redirect(request, "/admin/telegram-archive", "Wait for the running step to finish before signing out.", "error")
    await ta.sign_out()
    await log_admin_action(db, admin.id, "telegram_archive.signed_out", "settings", None, {}, request)
    return _redirect(request, "/admin/telegram-archive", "Signed out. The login was ended at Telegram and deleted from this website.")


@router.post("/telegram-archive/run", name="admin_telegram_archive_run")
async def admin_telegram_archive_run(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _archive_super(request)
    if not admin:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import telegram_archive as ta
    from app.core.jobs import enqueue
    from app.tasks import telegram_archive_task

    form = await request.form()
    action = str(form.get("action") or "")
    if action not in ("scan", "download", "import"):
        return _redirect(request, "/admin/telegram-archive", "Unknown step.", "error")
    limit = None                                  # download: a batch of N files, or continuous
    if action == "download" and form.get("mode") == "batch":
        try:
            limit = int(str(form.get("limit") or ""))
        except ValueError:
            limit = 0
        if not 1 <= limit <= 10000:
            return _redirect(request, "/admin/telegram-archive#backup", "Enter how many files to back up (1 to 10,000).", "error")
    if not await ta.claim_job(action):
        return _redirect(request, "/admin/telegram-archive", "Another archive step is still running.", "error")
    if not await enqueue(telegram_archive_task, action, admin.id, limit):
        from app.core.publications import _run_local

        _run_local(ta.run(action, admin.id, limit))      # no worker: run it in this process
    await log_admin_action(db, admin.id, f"telegram_archive.{action}", "settings", None, {"limit": limit}, request)
    started = {"scan": "Looking for audio in the channel…",
               "download": f"Backing up the next {limit:,} files…" if limit else "Continuous backup started. Press Pause any time.",
               "import": "Creating draft messages…"}[action]
    return _redirect(request, "/admin/telegram-archive", f"{started} This page shows progress; you can leave it and come back.")


@router.post("/telegram-archive/pause", name="admin_telegram_archive_pause")
async def admin_telegram_archive_pause(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _archive_super(request)
    if not admin:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import telegram_archive as ta

    job = await ta.get_job(db)
    if not (ta.job_running(job) and job.get("action") == "download"):
        return _redirect(request, "/admin/telegram-archive", "No backup is running.", "info")
    await ta.request_pause()
    return _redirect(request, "/admin/telegram-archive", "Pausing: the file in progress finishes first, then the backup stops. Press Start to continue later.", "info")


@router.get("/telegram-archive/files/{item_id}", name="admin_telegram_archive_file")
async def admin_telegram_archive_file(item_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if not await _archive_super(request):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core.media import ARCHIVE_PREFIX, to_abspath
    from app.models.telegram_archive import TelegramArchiveItem
    from app.routes.public import _content_disposition, _serve_local_audio

    item = await db.get(TelegramArchiveItem, item_id)
    path = to_abspath(ARCHIVE_PREFIX + item.stored_path) if item and item.stored_path else None
    if not path:
        return HTMLResponse("This file is not in the archive.", status_code=404)
    name = os.path.splitext(item.file_name or "audio")[0] or "audio"
    return _serve_local_audio(request, path, _content_disposition("attachment", f"{item.posted_at:%Y-%m-%d} {name}", os.path.splitext(path)[1]))


@router.get("/telegram-archive/index.csv", name="admin_telegram_archive_index")
async def admin_telegram_archive_index(request: Request, db: AsyncSession = Depends(get_db)):
    if not await _archive_super(request):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import telegram_archive as ta
    from app.models.telegram_archive import TelegramArchiveItem

    items = list((await db.execute(select(TelegramArchiveItem).order_by(TelegramArchiveItem.posted_at))).scalars().all())
    body = ta.manifest_csv(items, (await ta.get_job(db)).get("channel_username"))
    return StreamingResponse(iter([body.encode("utf-8")]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="telegram-archive-index.csv"'})


@router.get("/telegram-archive/archive.zip", name="admin_telegram_archive_zip")
async def admin_telegram_archive_zip(request: Request, db: AsyncSession = Depends(get_db)):
    if not await _archive_super(request):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import telegram_archive as ta
    from app.core.media import ARCHIVE_PREFIX, to_abspath
    from app.models.telegram_archive import TelegramArchiveItem

    items = list((await db.execute(select(TelegramArchiveItem).where(TelegramArchiveItem.status == "saved")
                                   .order_by(TelegramArchiveItem.posted_at))).scalars().all())
    files = []
    for it in items:
        if p := to_abspath(ARCHIVE_PREFIX + it.stored_path):
            name = it.stored_path.split("/", 1)[-1]
            files.append((p, name, it.posted_at))
            if os.path.exists(p + ".json"):                  # the post's caption and details, beside its audio
                files.append((p + ".json", name + ".json", it.posted_at))
    if not files:
        return _redirect(request, "/admin/telegram-archive", "Nothing has been downloaded yet.", "error")
    manifest = ta.manifest_csv(items, (await ta.get_job(db)).get("channel_username"))
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    return StreamingResponse(ta.zip_stream(files, manifest), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="telegram-audio-archive-{stamp}.zip"'})


# ─── Transcriptions ────────────────────────────────────────────────

async def _set_transcription_failed(db: AsyncSession, sermon_id: int, reason: str) -> None:
    from app.models.sermon import Sermon

    sermon = await db.get(Sermon, sermon_id)
    if sermon:
        sermon.transcription_status, sermon.transcription_error = "failed", reason[:1000]


def _transcribable(sermon) -> bool:
    return bool(sermon.audio_file_path or sermon.telegram_file_id)


@router.post("/transcriptions/start-waiting", name="admin_transcriptions_start_waiting")
async def admin_transcriptions_start_waiting(request: Request, db: AsyncSession = Depends(get_db)):
    """Queue every message with audio that was never transcribed (or failed). The transcriber
    works through them one at a time, newest first, so the website stays responsive."""
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    from app.core.jobs import enqueue
    from app.models.sermon import Sermon
    from app.tasks import transcribe_audio_task

    q = select(Sermon).where(or_(Sermon.transcription_status.in_(("none", "failed", "")), Sermon.transcription_status.is_(None)),
                             or_(and_(Sermon.audio_file_path.isnot(None), Sermon.audio_file_path != ""),
                                 and_(Sermon.telegram_file_id.isnot(None), Sermon.telegram_file_id != "")))
    if admin.role != "super_admin" and admin.branch_id:
        q = q.where(Sermon.branch_id == admin.branch_id)
    waiting = list((await db.execute(q.order_by(Sermon.created_at.desc()))).scalars().all())
    if not waiting:
        return _redirect(request, "/admin/transcriptions", "Nothing is waiting: every message with audio has been transcribed or is in the queue.", "info")
    for s in waiting:
        s.transcription_status, s.transcription_error = "queued", None
    await db.commit()
    queued = 0
    for s in waiting:
        if await enqueue(transcribe_audio_task, s.id):
            queued += 1
        else:
            await _set_transcription_failed(db, s.id, "The background worker was unavailable.")
    if not queued:
        return _redirect(request, "/admin/transcriptions", "The background worker is unavailable. Try again shortly.", "error")
    return _redirect(request, "/admin/transcriptions",
                     f"{queued} message{'s' if queued != 1 else ''} queued. They are transcribed one at a time, newest first; "
                     "a one-hour sermon can take about an hour.")


@router.get("/transcriptions/status", name="admin_transcriptions_status")
async def admin_transcriptions_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Tiny JSON the Transcriptions and Audio Messages pages poll: a signature that changes whenever
    any transcription status changes, and whether anything is still queued or transcribing."""
    admin = await _get_admin(request)
    if not admin:
        return JSONResponse({"error": "signed out"}, status_code=401)
    from app.models.sermon import Sermon

    q = select(Sermon.id, Sermon.transcription_status, func.coalesce(func.length(Sermon.transcript), 0)).order_by(Sermon.id)
    if admin.role != "super_admin" and admin.branch_id:
        q = q.where(Sermon.branch_id == admin.branch_id)
    rows = (await db.execute(q)).all()
    return JSONResponse({"signature": transcription_signature(rows),
                         "busy": any((s or "") in ("queued", "processing") for _, s, _ in rows)})


def transcription_signature(rows) -> str:
    return "|".join(f"{i}:{s or 'none'}:{n}" for i, s, n in rows)


@router.get("/transcriptions", name="admin_transcriptions")
async def admin_transcriptions(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    query = select(Sermon).order_by(Sermon.created_at.desc())
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(Sermon.branch_id == admin.branch_id)
    result = await db.execute(query)
    sermons = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.transcriptions")
    return await render_template(request, "admin/transcriptions.html", {
        "_db": db, "sermons": sermons,
        "title": "Transcriptions", "headerIcon": "ri-file-text-line", **ctx,
    })


@router.get("/transcriptions/{sermon_id}", name="admin_transcriptions_show")
async def admin_transcriptions_show(sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
    sermon = result.scalar_one_or_none()
    if not sermon:
        return _redirect(request, "/admin/transcriptions", "Sermon not found.", "error")
    if _foreign(admin, getattr(sermon, "branch_id", None)):
        return _redirect(request, "/admin/transcriptions", "You can only manage records of your own branch.", "error")

    fmt = request.query_params.get("format")
    if fmt == "txt" and sermon.transcript:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(
            sermon.transcript,
            headers={"Content-Disposition": f'attachment; filename="{sermon.title}.txt"'},
        )

    ctx = await _admin_context(request, db, "admin.transcriptions")
    return await render_template(request, "admin/transcription_detail.html", {
        "_db": db, "sermon": sermon,
        "title": f"Transcription - {sermon.title}", "headerIcon": "ri-file-text-line", **ctx,
    })


@router.post("/transcriptions/{sermon_id}/update", name="admin_transcriptions_update")
async def admin_transcriptions_update(sermon_id: int, request: Request, db: AsyncSession = Depends(get_db), transcript: str = Form(...)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    from app.models.sermon import Sermon
    result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
    sermon = result.scalar_one_or_none()
    if not sermon:
        return _redirect(request, "/admin/transcriptions", "Sermon not found.", "error")
    if _foreign(admin, getattr(sermon, "branch_id", None)):
        return _redirect(request, "/admin/transcriptions", "You can only manage records of your own branch.", "error")
    sermon.transcript = transcript.strip() if transcript else None
    sermon.transcription_status = "completed" if sermon.transcript else "none"
    if sermon.transcript:
        sermon.transcription_completed_at = datetime.utcnow()
    await db.flush()
    return _redirect(request, f"/admin/transcriptions/{sermon_id}", "Transcript saved successfully.")

@router.post("/transcriptions/{sermon_id}/retranscribe", name="admin_transcriptions_retranscribe")
async def admin_transcriptions_retranscribe(sermon_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
    sermon = result.scalar_one_or_none()
    if not sermon:
        return _redirect(request, "/admin/transcriptions", "Sermon not found.", "error")
    if _foreign(admin, getattr(sermon, "branch_id", None)):
        return _redirect(request, "/admin/transcriptions", "You can only manage records of your own branch.", "error")

    # The local copy is the primary source now, so a missing Telegram file_id
    # is only fatal when there is no file on disk either.
    from app.core.media import to_abspath

    if not to_abspath(sermon.audio_file_path) and not sermon.telegram_file_id:
        return _redirect(request, "/admin/transcriptions", "No audio file found for this sermon.", "error")

    sermon.transcription_status = "queued"
    sermon.transcription_error = None
    sermon.transcript = None
    await db.flush()

    from app.core.jobs import enqueue
    from app.tasks import transcribe_audio_task

    if not await enqueue(transcribe_audio_task, sermon.id, sermon.telegram_file_id):
        sermon.transcription_status = "failed"
        return _redirect(request, f"/admin/transcriptions/{sermon_id}",
                         "The transcription worker is unavailable. Try again once it is running.", "error")
    return _redirect(request, f"/admin/transcriptions/{sermon_id}", "Retranscription queued.")


# ─── Sliders: Update & Destroy ───────────────────────────────────

@router.post("/sliders/{slider_id}/update", name="admin_sliders_update")
async def admin_sliders_update(
    slider_id: int, request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), description: str = Form(""),
    cta_label: str = Form(""), cta_url: str = Form(""),
    sort_order: int = Form(0), is_active: bool = Form(True),
    image: UploadFile = File(None),
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.slider import Slider
    result = await db.execute(select(Slider).where(Slider.id == slider_id))
    slider = result.scalar_one_or_none()
    if not slider:
        return _redirect(request, "/admin/sliders", "Slider not found.", "error")

    slider.title = title.strip() if title else None
    slider.description = description.strip() if description else None
    slider.cta_label = cta_label.strip() if cta_label else None
    slider.cta_url = cta_url.strip() if cta_url else None
    slider.sort_order = sort_order
    slider.is_active = is_active

    if image and image.filename:
        try:
            slider.image_path = await _save_slider_image(image)
        except ValueError as exc:
            return _redirect(request, f"/admin/sliders/{slider_id}/edit", str(exc), "error")

    await db.flush()
    return _redirect(request, "/admin/sliders", "Slider updated successfully.")


@router.post("/sliders/{slider_id}/destroy", name="admin_sliders_destroy")
async def admin_sliders_destroy(slider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.slider import Slider
    result = await db.execute(select(Slider).where(Slider.id == slider_id))
    slider = result.scalar_one_or_none()
    if not slider:
        return _redirect(request, "/admin/sliders", "Slider not found.", "error")

    await db.delete(slider)
    await db.flush()
    return _redirect(request, "/admin/sliders", "Slider deleted successfully.")


# ─── Inquiries: Update & Destroy ─────────────────────────────────

@router.post("/inquiries/{inquiry_id}/update", name="admin_inquiries_update")
async def admin_inquiries_update(
    inquiry_id: int, request: Request, db: AsyncSession = Depends(get_db),
    status: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.contact_inquiry import ContactInquiry
    result = await db.execute(select(ContactInquiry).where(ContactInquiry.id == inquiry_id))
    inquiry = result.scalar_one_or_none()
    if not inquiry:
        return _redirect(request, "/admin/inquiries", "Inquiry not found.", "error")
    if _foreign(admin, getattr(inquiry, "branch_id", None)):
        return _redirect(request, "/admin/inquiries", "You can only manage records of your own branch.", "error")

    inquiry.status = status.strip() if status else inquiry.status
    await db.flush()
    return _redirect(request, "/admin/inquiries", "Inquiry status updated.")


@router.post("/inquiries/{inquiry_id}/destroy", name="admin_inquiries_destroy")
async def admin_inquiries_destroy(inquiry_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.contact_inquiry import ContactInquiry
    result = await db.execute(select(ContactInquiry).where(ContactInquiry.id == inquiry_id))
    inquiry = result.scalar_one_or_none()
    if not inquiry:
        return _redirect(request, "/admin/inquiries", "Inquiry not found.", "error")
    if _foreign(admin, getattr(inquiry, "branch_id", None)):
        return _redirect(request, "/admin/inquiries", "You can only manage records of your own branch.", "error")

    await db.delete(inquiry)
    await db.flush()
    return _redirect(request, "/admin/inquiries", "Inquiry deleted successfully.")


# ─── Security Logs: Export, Review, Assign ───────────────────────

@router.get("/security-logs/export", name="admin_security_logs_export")
async def admin_security_logs_export(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.admin import AdminAuditLog
    result = await db.execute(select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(1000))
    logs = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "action", "severity", "admin_id", "target_type", "target_id", "ip_address", "created_at", "reviewed_at"])
    for log in logs:
        writer.writerow([
            log.id, log.action, getattr(log, 'severity', ''),
            log.admin_id, log.target_type, log.target_id,
            log.ip_address or '', log.created_at.isoformat() if log.created_at else '',
            log.reviewed_at.isoformat() if log.reviewed_at else '',
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=security_logs.csv"},
    )


@router.post("/security-logs/{log_id}/review", name="admin_security_logs_review")
async def admin_security_logs_review(
    log_id: int, request: Request, db: AsyncSession = Depends(get_db),
    review_note: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.admin import AdminAuditLog
    result = await db.execute(select(AdminAuditLog).where(AdminAuditLog.id == log_id))
    log_entry = result.scalar_one_or_none()
    if log_entry:
        log_entry.reviewed_at = datetime.utcnow()
        await db.flush()
    return _redirect(request, "/admin/security-logs", "Log marked as reviewed.")


@router.post("/security-logs/{log_id}/assign", name="admin_security_logs_assign")
async def admin_security_logs_assign(
    log_id: int, request: Request, db: AsyncSession = Depends(get_db),
    assigned_to_admin_id: int = Form(...),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.admin import AdminAuditLog
    result = await db.execute(select(AdminAuditLog).where(AdminAuditLog.id == log_id))
    log_entry = result.scalar_one_or_none()
    if log_entry:
        log_entry.assigned_to_id = assigned_to_admin_id
        await db.flush()
    return _redirect(request, "/admin/security-logs", "Log assigned successfully.")


# ─── HODs: Update & Invitation Resend ────────────────────────────

@router.post("/hods/{hod_id}/update", name="admin_hods_update")
async def admin_hods_update(
    hod_id: int, request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), phone: str = Form(""),
    department_id: int = Form(None), is_active: bool = Form(True),
    password: str = Form(""), password_confirmation: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.admin import Admin as AdminModel
    result = await db.execute(select(AdminModel).where(AdminModel.id == hod_id))
    hod = result.scalar_one_or_none()
    if not hod:
        return _redirect(request, "/admin/hods", "HOD not found.", "error")
    if _foreign(admin, getattr(hod, "branch_id", None)):
        return _redirect(request, "/admin/hods", "You can only manage records of your own branch.", "error")
    if hod.role != "hod":
        return _redirect(request, "/admin/hods", "That account is not a department head. Administrators are managed by the HQ Administrator.", "error")

    if hod.is_active and not is_active:
        hod.session_version = (hod.session_version or 0) + 1
    hod.full_name = name.strip() or hod.full_name
    hod.is_active = is_active
    if department_id:
        hod.department_id = department_id
    if phone:
        hod.phone = phone.strip()

    if password:
        if password != password_confirmation:
            return _redirect(request, "/admin/hods", "Passwords do not match.", "error")
        if len(password) < 8:
            return _redirect(request, "/admin/hods", "Password must be at least 8 characters.", "error")
        hod.password_hash = hash_password(password)
        hod.must_change_password = True
        hod.session_version = (hod.session_version or 0) + 1

    await db.flush()
    return _redirect(request, "/admin/hods", "HOD updated successfully." if not password else "HOD updated. They must choose a new password at next sign-in.")


@router.post("/hod-invitations/{invitation_id}/resend", name="admin_hod_invitations_resend")
async def admin_hod_invitations_resend(invitation_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.hod_invitation import HodInvitation
    result = await db.execute(select(HodInvitation).where(HodInvitation.id == invitation_id))
    invitation = result.scalar_one_or_none()
    if invitation and _foreign(admin, invitation.branch_id):
        return _redirect(request, "/admin/hods", "You can only manage records of your own branch.", "error")
    if invitation:
        invitation.expires_at = datetime.utcnow() + timedelta(days=7)
        await db.flush()
        _email_invitation(request, email=invitation.email, name=invitation.name, role_line="serve as head of a department", path=f"/admin/hod/invitation/{invitation.token}", inviter=admin.full_name)
    return _redirect(request, "/admin/hods", "Invitation renewed for 7 days and emailed again. You can also copy the invite link.")


# ─── Branches: View, Update, Invitation Resend ───────────────────

@router.get("/branches/{branch_id}", name="admin_branch")
async def admin_branch_view(branch_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.branch import Branch
    from app.models.branch_admin_invitation import BranchAdminInvitation

    result = await db.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if not branch:
        return _redirect(request, "/admin/dashboard", "Branch not found.", "error")
    if _foreign(admin, branch.id):
        return _redirect(request, "/admin/dashboard", "You can only view your own branch.", "error")

    inv_result = await db.execute(
        select(BranchAdminInvitation)
        .where(BranchAdminInvitation.branch_id == branch_id)
        .order_by(BranchAdminInvitation.created_at.desc())
    )
    invitations = list(inv_result.scalars().all())

    from sqlalchemy.orm import selectinload
    from app.models.member import Member
    from app.models.event import Event
    from app.models.sermon import Sermon
    from app.models.department import Department
    from app.models.contact_inquiry import ContactInquiry
    from app.models.unit_report import UnitReport
    from app.models.admin import Admin

    branch_members_count = (await db.execute(select(func.count()).select_from(Member).where(Member.branch_id == branch_id))).scalar_one() or 0
    branch_events_count = (await db.execute(select(func.count()).select_from(Event).where(Event.branch_id == branch_id))).scalar_one() or 0
    branch_sermons_count = (await db.execute(select(func.count()).select_from(Sermon).where(Sermon.branch_id == branch_id))).scalar_one() or 0
    branch_inquiries_count = (await db.execute(select(func.count()).select_from(ContactInquiry).where(ContactInquiry.branch_id == branch_id))).scalar_one() or 0
    departments = list((await db.execute(select(Department).order_by(Department.name))).scalars().all())
    recent_inquiries = list((await db.execute(select(ContactInquiry).where(ContactInquiry.branch_id == branch_id).order_by(ContactInquiry.created_at.desc()).limit(5))).scalars().all())
    sees_dept_reports = admin.role != "super_admin" or branch.is_hq       # HQ never reads a branch's department reports
    recent_reports = list((await db.execute(select(UnitReport).options(selectinload(UnitReport.department)).where(UnitReport.branch_id == branch_id).order_by(UnitReport.created_at.desc()).limit(5))).scalars().all()) if sees_dept_reports else []
    branch_admins = list((await db.execute(select(Admin).options(selectinload(Admin.department)).where(Admin.branch_id == branch_id).order_by(Admin.created_at.desc()))).scalars().all())

    activeMembers = (await db.execute(select(func.count()).select_from(Member).where(Member.branch_id == branch_id, Member.membership_status == "active"))).scalar_one() or 0
    inactiveMembers = branch_members_count - activeMembers
    memberGrowthRate = 0
    givingThisMonth = 0
    givingGrowthRate = 0
    latestAttendanceTotal = 0

    now_utc = datetime.utcnow()
    upcomingEvents = list((await db.execute(select(Event).where(Event.branch_id == branch_id, Event.starts_at >= now_utc, Event.is_published == True).order_by(Event.starts_at).limit(10))).scalars().all())

    from sqlalchemy import literal_column
    latestReportResult = await db.execute(select(UnitReport).where(UnitReport.branch_id == branch_id).order_by(UnitReport.created_at.desc()).limit(1))
    latestReport = latestReportResult.scalar_one_or_none() if sees_dept_reports else None
    from app.models.branch_report import BranchReport as _BR
    recent_branch_reports = list((await db.execute(
        select(_BR).where(_BR.branch_id == branch_id, _BR.status.in_(("submitted", "acknowledged"))).order_by(_BR.report_month.desc()).limit(5))).scalars().all())

    eventStatusCounts = {}
    for status_val in ["published", "draft"]:
        c = (await db.execute(select(func.count()).select_from(Event).where(Event.branch_id == branch_id, Event.is_published == (status_val == "published")))).scalar_one() or 0
        eventStatusCounts[status_val] = c

    inquiryStatusCounts = {}
    for status_val in ["new", "in_progress", "resolved"]:
        c = (await db.execute(select(func.count()).select_from(ContactInquiry).where(ContactInquiry.branch_id == branch_id, ContactInquiry.status == status_val))).scalar_one() or 0
        inquiryStatusCounts[status_val] = c

    sermonStatusCounts = {}
    for status_val in ["published", "draft", "archived"]:
        c = (await db.execute(select(func.count()).select_from(Sermon).where(Sermon.branch_id == branch_id, Sermon.status == status_val))).scalar_one() or 0
        sermonStatusCounts[status_val] = c

    trend = []
    maxTrendAttendance = 1
    maxTrendGiving = 1
    departmentOverview = []
    recentMembers = []

    ctx = await _admin_context(request, db, "admin.branches")
    return await render_template(request, "admin/branch.html", {
        "_db": db, "branch": branch, "invitations": invitations,
        "branch_members_count": branch_members_count, "branch_events_count": branch_events_count,
        "branch_sermons_count": branch_sermons_count, "branch_inquiries_count": branch_inquiries_count,
        "departments": departments,
        "recent_inquiries": recent_inquiries, "recent_reports": recent_reports, "recent_branch_reports": recent_branch_reports, "sees_dept_reports": sees_dept_reports,
        "resident_pastor": await _resident_pastor_for(db, branch.id),
        "admins": branch_admins,
        "activeMembers": activeMembers, "inactiveMembers": inactiveMembers,
        "memberGrowthRate": memberGrowthRate, "givingGrowthRate": givingGrowthRate,
        "givingThisMonth": givingThisMonth, "latestAttendanceTotal": latestAttendanceTotal,
        "upcomingEvents": upcomingEvents, "latestReport": latestReport,
        "eventStatusCounts": eventStatusCounts, "inquiryStatusCounts": inquiryStatusCounts,
        "sermonStatusCounts": sermonStatusCounts,
        "trend": trend, "maxTrendAttendance": maxTrendAttendance, "maxTrendGiving": maxTrendGiving,
        "departmentOverview": departmentOverview, "recentMembers": recentMembers,
        "cities": await _city_suggestions(db, branch.region_code),
        "title": branch.name, "headerIcon": "ri-map-pin-2-line", **ctx,
    })


@router.post("/branches/{branch_id}/update", name="admin_branch_update")
async def admin_branch_update(
    branch_id: int, request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), address: str = Form(""),
    phone: str = Form(""), email: str = Form(""),
    is_active: bool = Form(True),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.branch import Branch
    result = await db.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if not branch:
        return _redirect(request, "/admin/branches", "Branch not found.", "error")

    from app.core import geo

    if err := await _apply_branch_location(db, branch, await request.form()):
        return _redirect(request, f"/admin/branches/{branch_id}#edit", err, "error")
    branch.name = name.strip() or branch.name
    branch.address = address.strip() if address else branch.address
    branch.phone = (geo.normalize_phone(phone, branch.country_code) or None) if phone else branch.phone
    branch.email = email.strip() if email else branch.email
    branch.is_active = is_active
    await db.flush()
    return _redirect(request, f"/admin/branches/{branch_id}", "Branch updated successfully.")


@router.post("/branches/{branch_id}/invitations/{invitation_id}/resend", name="admin_branch_invitations_resend")
async def admin_branch_invitations_resend(
    branch_id: int, invitation_id: int, request: Request, db: AsyncSession = Depends(get_db),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.branch_admin_invitation import BranchAdminInvitation
    result = await db.execute(
        select(BranchAdminInvitation).where(
            BranchAdminInvitation.id == invitation_id,
            BranchAdminInvitation.branch_id == branch_id,
        )
    )
    invitation = result.scalar_one_or_none()
    if invitation:
        invitation.expires_at = datetime.utcnow() + timedelta(days=7)
        await db.flush()
        _b = await _load_branch(db, branch_id)
        _email_invitation(request, email=invitation.email, name=invitation.name, role_line=f"administer {_b.name if _b else 'a branch'}", path=f"/admin/invitation/{invitation.token}", inviter=admin.full_name)
    return _redirect(request, f"/admin/branches/{branch_id}", "Invitation renewed for 7 days and emailed again. You can also copy the invite link.")


# ─── Unit Reports: Show & Update ─────────────────────────────────

@router.get("/unit-reports/{report_id}", name="admin_unit_reports_show")
async def admin_unit_reports_show(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.unit_report import UnitReport
    result = await db.execute(
        select(UnitReport)
        .options(
            selectinload(UnitReport.branch),
            selectinload(UnitReport.department),
            selectinload(UnitReport.hod_admin),
        )
        .where(UnitReport.id == report_id)
    )
    unit_report = result.scalar_one_or_none()
    if not unit_report or not await _can_access_unit_report(admin, unit_report, db):
        return _redirect(request, "/admin/unit-reports", "Report not found.", "error")

    from app.core.access import RESIDENT_PASTOR, normalize_role

    church_id = await _report_church_id(db, unit_report)
    rp = await _resident_pastor_for(db, church_id)
    is_rp = normalize_role(admin.role) == RESIDENT_PASTOR
    # With a Resident Pastor the office reads the report after the pastor's comment and adds only its own;
    # without one the office does both (as before).
    can_rp_comment = is_rp or rp is None
    can_office_comment = not is_rp and (rp is None or bool(unit_report.resident_pastor_comment))
    can_review = can_rp_comment or can_office_comment
    if is_rp:
        review_help = "Read the report, then append your comment. It reaches the Super Admin's office once you save it."
    elif rp is not None:
        review_help = f"{rp.full_name} (Resident Pastor) has reviewed this report. Add the office's comment below."
    else:
        review_help = "Review this monthly report and leave your comments."

    ctx = await _admin_context(request, db, "admin.unit-reports")
    return await render_template(request, "admin/unit-report-show.html", {
        "_db": db, "unitReport": unit_report, "report_currency": await _branch_currency(db, unit_report.branch_id),
        "canReviewHodReport": can_review, "canRpComment": can_rp_comment, "canOfficeComment": can_office_comment,
        "isResidentPastor": is_rp, "residentPastor": rp,
        "reviewHelpText": review_help,
        "title": "Unit Report", "headerIcon": "ri-file-chart-line", **ctx,
    })


@router.post("/unit-reports/{report_id}/update", name="admin_unit_reports_update")
async def admin_unit_reports_update(
    report_id: int, request: Request, db: AsyncSession = Depends(get_db),
    resident_pastor_comment: str = Form(""),
    senior_pastor_comment: str = Form(""),
    status: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.core.access import RESIDENT_PASTOR, normalize_role
    from app.models.unit_report import UnitReport
    result = await db.execute(select(UnitReport).where(UnitReport.id == report_id))
    unit_report = result.scalar_one_or_none()
    if not unit_report or not await _can_access_unit_report(admin, unit_report, db):
        return _redirect(request, "/admin/unit-reports", "Report not found.", "error")

    rp = await _resident_pastor_for(db, await _report_church_id(db, unit_report))
    is_rp = normalize_role(admin.role) == RESIDENT_PASTOR
    back = f"/admin/unit-reports/{report_id}"

    if is_rp:
        text = resident_pastor_comment.strip()
        if not text:
            return _redirect(request, back, "Write your comment before saving: the report only moves on once you have commented.", "error")
        unit_report.resident_pastor_comment = text[:6000]
        unit_report.resident_reviewed_by, unit_report.resident_reviewed_at = admin.id, datetime.utcnow()
        unit_report.status = "reviewed"                       # now forwarded to the Super Admin's office
        await log_admin_action(db, admin.id, "unit_report.resident_reviewed", "unit_report", unit_report.id, None, request)
        return _redirect(request, back, "Comment saved. The report has been forwarded to the Super Admin's office.")

    if rp is not None:
        # The office may only add its own comment, and only after the Resident Pastor's.
        if not unit_report.resident_pastor_comment:
            return _redirect(request, back, "This report is waiting for the Resident Pastor's comment.", "error")
        if senior_pastor_comment.strip():
            unit_report.senior_pastor_comment = senior_pastor_comment.strip()[:6000]
    else:
        if resident_pastor_comment.strip():
            unit_report.resident_pastor_comment = resident_pastor_comment.strip()[:6000]
        if senior_pastor_comment.strip():
            unit_report.senior_pastor_comment = senior_pastor_comment.strip()[:6000]
        if status.strip() in ("submitted", "reviewed"):
            unit_report.status = status.strip()

    await db.flush()
    return _redirect(request, back, "Report updated successfully.")


# ─── HOD Reports: Create, Store, Update ──────────────────────────


@router.post("/hod/reports/{report_id}/update", name="hod_reports_update")
async def admin_hod_reports_update(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.unit_report import UnitReport

    report = (await db.execute(select(UnitReport).where(UnitReport.id == report_id))).scalar_one_or_none()
    if not report or not _hod_can_edit_report(admin, report):
        return _redirect(request, "/admin/hod/reports", "Report not found.", "error")

    form = await request.form()
    introduction = str(form.get("introduction", "") or "").strip()
    if not introduction:
        return _redirect(request, f"/admin/hod/reports/{report_id}/edit", "Please add an introduction before saving the report.", "error")

    report_month = str(form.get("report_month", "") or "").strip()
    if report_month:
        report.report_month = report_month
    report.introduction = introduction
    payload = _parse_hod_report_form(form)
    report.attendance = payload["attendance"]
    report.collections = payload["collections"]
    report.attendance_note = str(form.get("attendance_note", "") or "").strip() or None
    report.observations = str(form.get("observations", "") or "").strip() or None
    report.challenges = str(form.get("challenges", "") or "").strip() or None
    report.recommendations = str(form.get("recommendations", "") or "").strip() or None
    report.projections = str(form.get("projections", "") or "").strip() or None

    submit_now = bool(form.get("submit_now"))
    if submit_now and report.status == "draft":
        report.status = "submitted"
        report.submitted_at = datetime.utcnow()

    await db.flush()
    return _redirect(request, "/admin/hod/reports", "Report submitted successfully." if submit_now else "Report updated successfully.")


@router.get("/hod/reports/{report_id}/edit", name="hod_reports_edit")
async def admin_hod_reports_edit(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.unit_report import UnitReport
    result = await db.execute(select(UnitReport).where(UnitReport.id == report_id))
    report = result.scalar_one_or_none()
    if not report or not _hod_can_edit_report(admin, report):
        return _redirect(request, "/admin/hod/reports", "Report not found.", "error")

    ctx = await _admin_context(request, db, "hod.reports")
    return await render_template(request, "hod/reports/create.html", {
        "_db": db, "report": report, "hod": admin, "report_currency": await _branch_currency(db, report.branch_id if report else admin.branch_id),
        "department_name": await _admin_department_name(db, admin),
        "title": "Edit Report", "headerIcon": "ri-file-chart-line", **ctx,
    })


# --- Stories ---
async def _unique_story_slug(db, base_slug: str, exclude_id: int | None = None) -> str:
    from app.models.story import Story
    slug = base_slug
    suffix = 2
    while True:
        q = select(Story.id).where(Story.slug == slug)
        if exclude_id is not None:
            q = q.where(Story.id != exclude_id)
        existing = (await db.execute(q)).scalar_one_or_none()
        if not existing:
            return slug
        slug = f"{base_slug}-{suffix}"
        suffix += 1
async def _save_story_image(image) -> str:
    import uuid, os
    ext = os.path.splitext(image.filename or "")[1] or ".jpg"
    if ext.lower() not in [".jpg",".jpeg",".png",".webp",".gif"]:
        ext = ".jpg"
    filename = f"story-{uuid.uuid4()}{ext}"
    dest_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images", "stories")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    content = await image.read()
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("too large")
    with open(dest, "wb") as f:
        f.write(content)
    return f"images/stories/{filename}"
@router.get("/stories", name="admin_stories")
async def admin_stories(request: Request, db: AsyncSession = Depends(get_db), q: str = Query("")):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    from app.models.story import Story
    query = select(Story).order_by(Story.created_at.desc())
    if q:
        pat = f"%{q}%"
        query = query.where(or_(Story.title.ilike(pat), Story.excerpt.ilike(pat)))
    result = await db.execute(query)
    stories = list(result.scalars().all())
    ctx = await _admin_context(request, db, "admin.stories")
    return await render_template(request, "admin/stories.html", {"_db": db, "stories": stories, "search": q, "title": "Stories", "headerIcon": "ri-quill-pen-line", **ctx})
@router.get("/stories/create", name="admin_stories_create_page")
async def admin_stories_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    ctx = await _admin_context(request, db, "admin.stories")
    return await render_template(request, "admin/story-form.html", {"_db": db, "story": None, "categories": await _story_categories(db), "title": "New Story", "headerIcon": "ri-quill-pen-line", **ctx})
@router.get("/stories/{story_id}/edit", name="admin_stories_edit_page")
async def admin_stories_edit_page(story_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.story import Story
    story = (await db.execute(select(Story).where(Story.id == story_id))).scalar_one_or_none()
    if not story:
        return _redirect(request, "/admin/stories", "Story not found.", "error")
    ctx = await _admin_context(request, db, "admin.stories")
    return await render_template(request, "admin/story-form.html", {"_db": db, "story": story, "categories": await _story_categories(db), "title": "Edit Story", "headerIcon": "ri-quill-pen-line", **ctx})
async def _story_categories(db: AsyncSession) -> list[str]:
    from app.core import lists

    return lists.items(await get_settings(db), "story_categories")


def _country_name(code: str | None) -> str:
    from app.core import geo

    return geo.country_name(code)


def _lists_items(site: dict, name: str) -> list[str]:
    from app.core import lists

    return lists.items(site, name)


def _lists_canonical(value: str, choices: list[str]) -> str | None:
    from app.core import lists

    return lists.canonical(value, choices)


@router.post("/stories", name="admin_stories_store")
async def admin_stories_store(request: Request, db: AsyncSession = Depends(get_db), title: str = Form(...), excerpt: str = Form(""), content: str = Form(""), category: str = Form(""), author_name: str = Form(""), status: str = Form("draft"), is_featured: str | None = Form(None), image: UploadFile | None = File(None)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.story import Story
    from slugify import slugify
    slug = await _unique_story_slug(db, slugify(title.strip()))
    feat = is_featured in ("1","true","on","True")
    st = status.strip().lower() or "draft"
    cat = _lists_canonical(category, await _story_categories(db))
    if category.strip() and not cat:
        return _redirect(request, "/admin/stories/create", "Choose the category from the list (Settings → Lists).", "error")
    story = Story(title=title.strip(), slug=slug, excerpt=excerpt.strip() if excerpt else None, content=content.strip() if content else None, category=cat, author_name=author_name.strip() if author_name else None, status=st, is_featured=feat, created_by=admin.id)
    if st == "published":
        story.published_at = datetime.utcnow()
    if image and image.filename:
        try:
            story.featured_image = await _save_story_image(image)
        except ValueError:
            return _redirect(request, "/admin/stories", "Image too large (max 5MB).", "error")
    db.add(story)
    await db.flush()
    return _redirect(request, "/admin/stories", "Story created successfully.")
@router.post("/stories/{story_id}/update", name="admin_stories_update")
async def admin_stories_update(story_id: int, request: Request, db: AsyncSession = Depends(get_db), title: str = Form(""), excerpt: str = Form(""), content: str = Form(""), category: str = Form(""), author_name: str = Form(""), status: str = Form("draft"), is_featured: str | None = Form(None), image: UploadFile | None = File(None)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.story import Story
    from slugify import slugify
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        return _redirect(request, "/admin/stories", "Story not found.", "error")
    was_published = story.status == "published"
    if title.strip() and title.strip() != story.title:
        story.title = title.strip()
        story.slug = await _unique_story_slug(db, slugify(story.title), exclude_id=story.id)
    story.excerpt = excerpt.strip() if excerpt else None
    story.content = content.strip() if content else None
    cat = _lists_canonical(category, await _story_categories(db))
    if category.strip() and not cat and category.strip() != (story.category or ""):
        return _redirect(request, f"/admin/stories/{story_id}/edit", "Choose the category from the list (Settings → Lists).", "error")
    story.category = cat or (story.category if category.strip() == (story.category or "") else None)
    story.author_name = author_name.strip() if author_name else None
    story.status = status.strip().lower() or "draft"
    story.is_featured = is_featured in ("1","true","on","True")
    if story.status == "published" and not was_published:
        story.published_at = datetime.utcnow()
    if image and image.filename:
        try:
            story.featured_image = await _save_story_image(image)
        except ValueError:
            return _redirect(request, "/admin/stories", "Image too large.", "error")
    await db.flush()
    return _redirect(request, "/admin/stories", "Story updated successfully.")
@router.post("/stories/{story_id}/destroy", name="admin_stories_destroy")
async def admin_stories_destroy(story_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.story import Story
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        return _redirect(request, "/admin/stories", "Story not found.", "error")
    await db.delete(story)
    await db.flush()
    return _redirect(request, "/admin/stories", "Story deleted successfully.")



# ─── Books Pub: Daily Devotionals & Books ────────────────────────
PUB_AUDIO_MAX_BYTES = 2000 * 1024 * 1024


async def _pub_guard(request: Request, db: AsyncSession, kind: str, pub_id: int | None = None):
    """Books Pub needs the ``media`` permission. Returns ``(admin, publication, error_response)``."""
    from app.core.publications import KINDS, publication_query

    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return None, None, RedirectResponse(url="/admin/dashboard", status_code=303)
    if kind not in KINDS:
        return admin, None, _redirect(request, "/admin/dashboard", "Unknown publication type.", "error")
    pub = None
    if pub_id is not None:
        pub = (await db.execute(publication_query(pub_id, kind))).scalar_one_or_none()
        if not pub:
            return admin, None, _redirect(request, f"/admin/publications/{kind}", "Project not found.", "error")
    return admin, pub, None


async def _pub_render(request, db, template, kind, *, pub=None, title="", **extra):
    from app.core.publications import KINDS, progress

    ctx = await _admin_context(request, db, f"admin.pub-{kind}")
    meta = KINDS[kind]
    return await render_template(request, template, {
        "_db": db, "kind": kind, "kmeta": meta, "pub": pub,
        "prog": progress(pub) if pub else None,
        "title": title or meta["plural"], "headerIcon": meta["icon"], **extra, **ctx,
    })


def _parse_date(value: str):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date() if value and value.strip() else None
    except ValueError:
        return None


def _to_int(value: str):
    try:
        return int(str(value).strip()) if value and str(value).strip() else None
    except ValueError:
        return None


async def _pub_form_sermons(db: AsyncSession, exclude_ids: set[int] | None = None):
    from app.models.sermon import Sermon

    hq_id = await get_hq_branch_id(db)
    rows = (await db.execute(
        select(Sermon).where(or_(Sermon.branch_id == hq_id, Sermon.branch_id.is_(None))).order_by(Sermon.created_at.desc()).limit(500)
    )).scalars().all()
    return [s for s in rows if not exclude_ids or s.id not in exclude_ids]


async def _pub_hq_only(db: AsyncSession, sermon_ids: list[int]) -> list[int]:
    """Books Pub is HQ's publishing house: a branch's messages are never pulled into an HQ publication."""
    from app.models.sermon import Sermon

    if not sermon_ids:
        return []
    hq_id = await get_hq_branch_id(db)
    ok = (await db.execute(select(Sermon.id).where(Sermon.id.in_(sermon_ids), or_(Sermon.branch_id == hq_id, Sermon.branch_id.is_(None))))).scalars().all()
    return [i for i in sermon_ids if i in set(ok)]


def _pub_add_sources(db, pub, sermon_ids: list[int]) -> int:
    existing = {s.sermon_id for s in pub.sources}
    pos = max([s.position for s in pub.sources], default=0)
    added = 0
    from app.models.publication import PublicationSource

    for sid in sermon_ids:
        if sid in existing:
            continue
        pos += 1
        added += 1
        existing.add(sid)
        db.add(PublicationSource(publication_id=pub.id, sermon_id=sid, position=pos))
    return added


async def _pub_fill_titles(db: AsyncSession, pub_id: int) -> None:
    """New sources start out titled after their message."""
    from app.models.publication import PublicationSource
    from app.models.sermon import Sermon

    rows = (await db.execute(
        select(PublicationSource, Sermon).join(Sermon, Sermon.id == PublicationSource.sermon_id)
        .where(PublicationSource.publication_id == pub_id, PublicationSource.main_title.is_(None))
    )).all()
    for src, sermon in rows:
        src.main_title = sermon.title[:255]


def _pub_form_values(form_kind: str, title, description, author_name, duration_type, year, period_index, start_date, end_date):
    """Validate the create/edit form. Returns (values dict, error)."""
    from app.core.publications import compute_period

    title = (title or "").strip()
    if not title:
        return None, "Give the project a title."
    values = {"title": title[:255], "description": (description or "").strip() or None,
              "author_name": (author_name or "").strip() or None,
              "duration_type": None, "year": None, "period_index": None, "start_date": None, "end_date": None}
    if form_kind == "devotionals":
        try:
            start, end, _ = compute_period(duration_type, _to_int(year), _to_int(period_index),
                                           _parse_date(start_date), _parse_date(end_date))
        except ValueError as exc:
            return None, str(exc)
        values.update(duration_type=duration_type, year=_to_int(year), period_index=_to_int(period_index),
                      start_date=start, end_date=end)
    return values, None


@router.get("/publications/{kind}", name="admin_pubs")
async def admin_pubs(kind: str, request: Request, db: AsyncSession = Depends(get_db), q: str = Query("")):
    admin, _, err = await _pub_guard(request, db, kind)
    if err:
        return err
    from app.core.publications import publication_query, progress, period_label
    from app.models.publication import Publication

    stmt = publication_query(kind=kind).order_by(Publication.updated_at.desc())
    pubs = (await db.execute(stmt)).scalars().unique().all()
    if q.strip():
        needle = q.strip().lower()
        pubs = [p for p in pubs if needle in p.title.lower()]
    rows = [{"pub": p, "prog": progress(p), "period": period_label(p)} for p in pubs]
    return await _pub_render(request, db, "admin/pubs.html", kind, rows=rows, search=q)


@router.get("/publications/{kind}/new", name="admin_pubs_new")
async def admin_pubs_new(kind: str, request: Request, db: AsyncSession = Depends(get_db)):
    admin, _, err = await _pub_guard(request, db, kind)
    if err:
        return err
    sermons = await _pub_form_sermons(db)
    return await _pub_render(request, db, "admin/pub-form.html", kind, title="New project",
                             sermons=sermons, selected_ids=set(), form_error=None,
                             this_year=datetime.utcnow().year + (1 if datetime.utcnow().month >= 10 else 0))


@router.post("/publications/{kind}", name="admin_pubs_store")
async def admin_pubs_store(
    kind: str, request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), description: str = Form(""), author_name: str = Form(""),
    duration_type: str = Form("year"), year: str = Form(""), period_index: str = Form(""),
    start_date: str = Form(""), end_date: str = Form(""), sermon_ids: list[int] = Form([]),
):
    admin, _, err = await _pub_guard(request, db, kind)
    if err:
        return err
    from app.core.publications import publication_query
    from app.models.publication import Publication

    values, error = _pub_form_values(kind, title, description, author_name, duration_type, year, period_index, start_date, end_date)
    if error:
        sermons = await _pub_form_sermons(db)
        return await _pub_render(request, db, "admin/pub-form.html", kind, title="New project", sermons=sermons,
                                 selected_ids=set(sermon_ids), form_error=error, this_year=datetime.utcnow().year)
    pub = Publication(kind=kind, created_by=admin.id, **values)
    db.add(pub)
    await db.flush()
    if sermon_ids:
        pub_obj = (await db.execute(publication_query(pub.id))).scalar_one()
        _pub_add_sources(db, pub_obj, await _pub_hq_only(db, sermon_ids))
        await db.flush()
        await _pub_fill_titles(db, pub.id)
    await log_admin_action(db, admin.id, "publication.create", "publication", pub.id, {"kind": kind, "title": pub.title}, request)
    await db.commit()
    if sermon_ids:
        return _redirect(request, f"/admin/publications/{kind}/{pub.id}", "Project created. Next: generate topics from your messages.")
    return _redirect(request, f"/admin/publications/{kind}/{pub.id}/messages/add", "Project created. Now add the messages it will be built from.")


@router.get("/publications/{kind}/{pub_id}", name="admin_pubs_show")
async def admin_pubs_show(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    from app.core import ai
    from app.core.publications import numbered_subtopics, period_label, signature

    return await _pub_render(request, db, "admin/pub-show.html", kind, pub=pub, title=pub.title,
                             period=period_label(pub), ai_ready=await ai.is_configured(), ai_cfg=await ai.get_config(),
                             numbered=numbered_subtopics(pub), sig=signature(pub), timedelta=timedelta)


@router.get("/publications/{kind}/{pub_id}/status", name="admin_pubs_status")
async def admin_pubs_status(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return JSONResponse({"error": "not found"}, status_code=404)
    from app.core.publications import progress, signature

    return JSONResponse({"busy": progress(pub)["busy"], "signature": signature(pub)})


@router.get("/publications/{kind}/{pub_id}/edit", name="admin_pubs_edit")
async def admin_pubs_edit(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    return await _pub_render(request, db, "admin/pub-form.html", kind, pub=pub, title="Edit project",
                             sermons=[], selected_ids=set(), form_error=None, this_year=pub.year or datetime.utcnow().year)


@router.post("/publications/{kind}/{pub_id}/update", name="admin_pubs_update")
async def admin_pubs_update(
    kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), description: str = Form(""), author_name: str = Form(""),
    duration_type: str = Form("year"), year: str = Form(""), period_index: str = Form(""),
    start_date: str = Form(""), end_date: str = Form(""),
):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    values, error = _pub_form_values(kind, title, description, author_name, duration_type, year, period_index, start_date, end_date)
    if error:
        return await _pub_render(request, db, "admin/pub-form.html", kind, pub=pub, title="Edit project", sermons=[],
                                 selected_ids=set(), form_error=error, this_year=pub.year or datetime.utcnow().year)
    for k, v in values.items():
        setattr(pub, k, v)
    await log_admin_action(db, admin.id, "publication.update", "publication", pub.id, {"title": pub.title}, request)
    return _redirect(request, f"/admin/publications/{kind}/{pub.id}", "Project settings saved.")


@router.post("/publications/{kind}/{pub_id}/delete", name="admin_pubs_delete")
async def admin_pubs_delete(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    title = pub.title
    await db.delete(pub)
    await log_admin_action(db, admin.id, "publication.delete", "publication", pub_id, {"title": title}, request)
    return _redirect(request, f"/admin/publications/{kind}", f"“{title}” deleted. The messages themselves are untouched.")


# ── Messages (sources) ──
@router.get("/publications/{kind}/{pub_id}/messages/add", name="admin_pubs_messages_add")
async def admin_pubs_messages_add(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    sermons = await _pub_form_sermons(db, {s.sermon_id for s in pub.sources})
    return await _pub_render(request, db, "admin/pub-messages-add.html", kind, pub=pub, title="Add messages", sermons=sermons)


@router.post("/publications/{kind}/{pub_id}/messages/existing", name="admin_pubs_messages_existing")
async def admin_pubs_messages_existing(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db),
                                       sermon_ids: list[int] = Form([])):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    if not sermon_ids:
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}/messages/add", "Tick at least one message.", "error")
    added = _pub_add_sources(db, pub, await _pub_hq_only(db, sermon_ids))
    await db.flush()
    await _pub_fill_titles(db, pub.id)
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}", f"{added} message{'s' if added != 1 else ''} added.")


async def _pub_new_sermon(db: AsyncSession, admin, title: str, preacher: str):
    from app.models.sermon import Sermon
    from slugify import slugify

    base = slugify(title) or "message"
    slug, n = base, 1
    while (await db.execute(select(Sermon.id).where(Sermon.slug == slug))).first():
        n += 1
        slug = f"{base}-{n}"
    sermon = Sermon(title=title[:255], slug=slug, preacher=await _known_spelling(db, Sermon.preacher, preacher), status="draft", type="audio",
                    branch_id=admin.branch_id, created_by=admin.id)
    db.add(sermon)
    return sermon


@router.post("/publications/{kind}/{pub_id}/messages/upload-audio", name="admin_pubs_messages_upload_audio")
async def admin_pubs_messages_upload_audio(
    kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), preacher: str = Form(""), audio_file: UploadFile = File(...),
):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    back = f"/admin/publications/{kind}/{pub_id}/messages/add#audio"
    if not audio_file or not audio_file.filename:
        return _redirect(request, back, "Choose an audio file.", "error")
    ext = audio_file.filename.rsplit(".", 1)[-1].lower() if "." in audio_file.filename else ""
    if ext not in AUDIO_UPLOAD_EXTENSIONS:
        return _redirect(request, back, "Invalid audio format. Use mp3, wav, m4a, ogg, flac, aac, wma or mpga.", "error")

    from app.core.media import media_subdir

    stored = f"{secrets.token_hex(16)}.{ext}"
    path = os.path.join(media_subdir("sermons"), stored)
    written = 0
    try:
        with open(path, "wb") as f:
            while chunk := await audio_file.read(1024 * 1024):
                written += len(chunk)
                if written > PUB_AUDIO_MAX_BYTES:
                    raise ValueError("too large")
                f.write(chunk)
    except ValueError:
        os.unlink(path)
        return _redirect(request, back, f"Audio file is larger than {PUB_AUDIO_MAX_BYTES // 1048576}MB.", "error")
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        logging.exception("Failed to store publication audio")
        return _redirect(request, back, "Could not save the audio file.", "error")

    name = title.strip() or os.path.splitext(audio_file.filename)[0]
    sermon = await _pub_new_sermon(db, admin, name, preacher)
    sermon.original_file_name = audio_file.filename
    sermon.original_file_size = min(written, 2147483647)
    sermon.audio_file_path = f"media/sermons/{stored}"
    sermon.media_url = f"/media/sermons/{stored}"
    sermon.transcription_status = "queued"
    await db.flush()
    _pub_add_sources(db, pub, [sermon.id])
    await db.flush()
    await _pub_fill_titles(db, pub.id)
    sermon_id = sermon.id
    await db.commit()
    from app.core.jobs import enqueue
    from app.tasks import transcribe_audio_task

    if await enqueue(transcribe_audio_task, sermon_id):
        note = "Uploaded. Transcription has started — it can take a while for long messages."
        kind_flash = "success"
    else:
        from app.models.sermon import Sermon

        s = (await db.execute(select(Sermon).where(Sermon.id == sermon_id))).scalar_one()
        s.transcription_status = "failed"
        note = "Uploaded, but the transcription worker is unavailable. Retry it from Transcriptions once it is running."
        kind_flash = "error"
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}", note, kind_flash)


@router.post("/publications/{kind}/{pub_id}/messages/upload-transcript", name="admin_pubs_messages_upload_transcript")
async def admin_pubs_messages_upload_transcript(
    kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), preacher: str = Form(""), transcript_text: str = Form(""),
    transcript_file: UploadFile | None = File(None),
):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    back = f"/admin/publications/{kind}/{pub_id}/messages/add#transcript"
    text = (transcript_text or "").strip()
    filename = ""
    if transcript_file and transcript_file.filename:
        filename = transcript_file.filename
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        raw = await transcript_file.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            return _redirect(request, back, "Transcript file is larger than 10MB.", "error")
        if ext == "docx":
            try:
                import docx as _docx

                text = "\n\n".join(p.text for p in _docx.Document(io.BytesIO(raw)).paragraphs if p.text.strip())
            except Exception:
                return _redirect(request, back, "Could not read that .docx file.", "error")
        elif ext in ("txt", "md", "text", "srt", "vtt"):
            text = raw.decode("utf-8", errors="replace").strip()
        else:
            return _redirect(request, back, "Upload a .txt or .docx transcript, or paste the text.", "error")
    if len(text.split()) < 50:
        return _redirect(request, back, "The transcript is too short to build from (fewer than 50 words).", "error")

    name = title.strip() or (os.path.splitext(filename)[0] if filename else "") or "Untitled message"
    sermon = await _pub_new_sermon(db, admin, name, preacher)
    sermon.transcript = text
    sermon.transcription_status = "completed"
    sermon.transcription_completed_at = datetime.utcnow()
    await db.flush()
    _pub_add_sources(db, pub, [sermon.id])
    await db.flush()
    await _pub_fill_titles(db, pub.id)
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}", f"Transcript added ({len(text.split()):,} words).")


@router.post("/publications/{kind}/{pub_id}/sources/reorder", name="admin_pubs_sources_reorder")
async def admin_pubs_sources_reorder(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        ids = [int(i) for i in (await request.json()).get("ids", [])]
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    by_id = {s.id: s for s in pub.sources}
    order = [i for i in ids if i in by_id] + [s.id for s in pub.sources if s.id not in ids]
    for pos, sid in enumerate(order, start=1):
        by_id[sid].position = pos
    return JSONResponse({"ok": True})


@router.post("/publications/{kind}/{pub_id}/sources/{source_id}/move", name="admin_pubs_sources_move")
async def admin_pubs_sources_move(kind: str, pub_id: int, source_id: int, request: Request,
                                  db: AsyncSession = Depends(get_db), direction: str = Form("up")):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    order = list(pub.sources)
    idx = next((i for i, s in enumerate(order) if s.id == source_id), None)
    if idx is not None:
        j = idx - 1 if direction == "up" else idx + 1
        if 0 <= j < len(order):
            order[idx], order[j] = order[j], order[idx]
        for pos, s in enumerate(order, start=1):
            s.position = pos
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}#source-{source_id}")


@router.post("/publications/{kind}/{pub_id}/sources/{source_id}/remove", name="admin_pubs_sources_remove")
async def admin_pubs_sources_remove(kind: str, pub_id: int, source_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    src = next((s for s in pub.sources if s.id == source_id), None)
    if not src:
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "Message not found in this project.", "error")
    await db.delete(src)
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "Message removed from the project (the message itself is untouched).")


@router.post("/publications/{kind}/{pub_id}/sources/{source_id}/rename", name="admin_pubs_sources_rename")
async def admin_pubs_sources_rename(kind: str, pub_id: int, source_id: int, request: Request,
                                    db: AsyncSession = Depends(get_db), main_title: str = Form("")):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    src = next((s for s in pub.sources if s.id == source_id), None)
    if not src or not main_title.strip():
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "Enter a title.", "error")
    src.main_title = main_title.strip()[:255]
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}#source-{source_id}", "Title updated.")


# ── AI jobs ──
@router.post("/publications/{kind}/{pub_id}/generate-topics", name="admin_pubs_generate_topics")
async def admin_pubs_generate_topics(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db),
                                     source_id: int | None = Form(None), force: str = Form("")):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    from app.core import ai
    from app.core.publications import enqueue_topics

    back = f"/admin/publications/{kind}/{pub_id}"
    if not await ai.is_configured():
        return _redirect(request, back, "AI drafting is not configured: save an API key for the default provider under Settings → AI drafting.", "error")
    targets, no_transcript = [], 0
    for s in pub.sources:
        if source_id and s.id != source_id:
            continue
        if s.topics_status in ("queued", "processing"):
            continue
        if not (s.sermon and (s.sermon.transcript or "").strip()):
            no_transcript += 1
            continue
        if force == "1" or not s.subtopics:
            targets.append(s)
    if not targets:
        msg = "Nothing to generate: every message with a transcript already has topics."
        if no_transcript:
            msg = f"{no_transcript} message{'s have' if no_transcript != 1 else ' has'} no transcript yet — transcribe or upload it first."
        return _redirect(request, back, msg, "info" if not no_transcript else "error")
    for s in targets:
        s.topics_status, s.topics_error = "queued", None
    await db.commit()
    await enqueue_topics(pub.id)
    note = f"Generating topics for {len(targets)} message{'s' if len(targets) != 1 else ''}. This page updates as they finish."
    if no_transcript:
        note += f" {no_transcript} skipped (no transcript)."
    return _redirect(request, back, note)


@router.post("/publications/{kind}/{pub_id}/generate-contents", name="admin_pubs_generate_contents")
async def admin_pubs_generate_contents(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db),
                                       source_id: int | None = Form(None), subtopic_id: int | None = Form(None)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    from app.core import ai
    from app.core.publications import enqueue_contents

    back = f"/admin/publications/{kind}/{pub_id}"
    if not await ai.is_configured():
        return _redirect(request, back, "AI drafting is not configured: save an API key for the default provider under Settings → AI drafting.", "error")
    targets = []
    for s in pub.sources:
        if source_id and s.id != source_id:
            continue
        if not (s.sermon and (s.sermon.transcript or "").strip()):
            continue
        for t in s.subtopics:
            if t.content_status in ("queued", "processing"):
                continue
            if subtopic_id:
                if t.id == subtopic_id:
                    targets.append(t)
            elif not t.has_content:
                targets.append(t)
    if not targets:
        return _redirect(request, back, "Nothing to generate: every topic already has its content.", "info")
    for t in targets:
        t.content_status, t.content_error = "queued", None
    await db.commit()
    await enqueue_contents(pub.id)
    return _redirect(request, back, f"Writing {len(targets)} {'entry' if len(targets) == 1 else 'entries'}. This page updates as each one finishes.")


@router.post("/publications/{kind}/{pub_id}/reset-jobs", name="admin_pubs_reset_jobs")
async def admin_pubs_reset_jobs(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Release rows stuck in queued/processing (e.g. the worker was down or restarted)."""
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    n = 0
    for s in pub.sources:
        if s.topics_status in ("queued", "processing"):
            s.topics_status, s.topics_error, n = "failed", "Cancelled — try again.", n + 1
        for t in s.subtopics:
            if t.content_status in ("queued", "processing"):
                t.content_status, t.content_error, n = "failed", "Cancelled — try again.", n + 1
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}", f"Released {n} stuck job{'s' if n != 1 else ''}." if n else "No stuck jobs.")


# ── Subtopics ──
def _pub_find_sub(pub, sub_id: int):
    for s in pub.sources:
        for t in s.subtopics:
            if t.id == sub_id:
                return s, t
    return None, None


@router.get("/publications/{kind}/{pub_id}/subtopics/new", name="admin_pubs_subtopics_new")
async def admin_pubs_subtopics_new(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db),
                                   source_id: int = Query(0)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    src = next((s for s in pub.sources if s.id == source_id), None)
    if not src:
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "Choose a message first.", "error")
    return await _pub_render(request, db, "admin/pub-subtopic-form.html", kind, pub=pub, title="New topic", src=src, sub=None)


@router.post("/publications/{kind}/{pub_id}/subtopics", name="admin_pubs_subtopics_store")
async def admin_pubs_subtopics_store(
    kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db),
    source_id: int = Form(...), title: str = Form(""), points: str = Form(""), scripture: str = Form(""),
    body: str = Form(""), action_plan: str = Form(""), prayer_point: str = Form(""),
):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    from app.core.publication_html import word_count
    from app.models.publication import PublicationSubtopic

    src = next((s for s in pub.sources if s.id == source_id), None)
    if not src or not title.strip():
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "A topic needs a title.", "error")
    body = body.strip()
    db.add(PublicationSubtopic(
        source_id=src.id, position=max([t.position for t in src.subtopics], default=0) + 1,
        title=title.strip()[:255], points=[p.strip() for p in points.splitlines() if p.strip()],
        scripture=scripture.strip() or None, body=body or None, action_plan=action_plan.strip() or None,
        prayer_point=prayer_point.strip() or None, word_count=word_count(body),
        content_status="done" if body else "none", edited=bool(body),
    ))
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}#source-{src.id}", "Topic added.")


@router.get("/publications/{kind}/{pub_id}/subtopics/{sub_id}/edit", name="admin_pubs_subtopics_edit")
async def admin_pubs_subtopics_edit(kind: str, pub_id: int, sub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    src, sub = _pub_find_sub(pub, sub_id)
    if not sub:
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "Topic not found.", "error")
    return await _pub_render(request, db, "admin/pub-subtopic-form.html", kind, pub=pub, title="Edit topic", src=src, sub=sub)


@router.post("/publications/{kind}/{pub_id}/subtopics/{sub_id}/update", name="admin_pubs_subtopics_update")
async def admin_pubs_subtopics_update(
    kind: str, pub_id: int, sub_id: int, request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), points: str = Form(""), scripture: str = Form(""), body: str = Form(""),
    action_plan: str = Form(""), prayer_point: str = Form(""),
):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    from app.core.publication_html import word_count

    src, sub = _pub_find_sub(pub, sub_id)
    if not sub or not title.strip():
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "A topic needs a title.", "error")
    body = body.strip()
    sub.title = title.strip()[:255]
    sub.points = [p.strip() for p in points.splitlines() if p.strip()]
    sub.scripture = scripture.strip() or None
    sub.body = body or None
    sub.action_plan = action_plan.strip() or None
    sub.prayer_point = prayer_point.strip() or None
    sub.word_count = word_count(body)
    sub.edited = True
    if sub.content_status not in ("queued", "processing"):
        sub.content_status = "done" if body else "none"
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}#source-{src.id}", "Topic saved. Use Update to carry it into the document.")


@router.post("/publications/{kind}/{pub_id}/subtopics/{sub_id}/delete", name="admin_pubs_subtopics_delete")
async def admin_pubs_subtopics_delete(kind: str, pub_id: int, sub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    src, sub = _pub_find_sub(pub, sub_id)
    if sub:
        await db.delete(sub)
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}" + (f"#source-{src.id}" if src else ""), "Topic deleted.")


@router.post("/publications/{kind}/{pub_id}/subtopics/{sub_id}/move", name="admin_pubs_subtopics_move")
async def admin_pubs_subtopics_move(kind: str, pub_id: int, sub_id: int, request: Request,
                                    db: AsyncSession = Depends(get_db), direction: str = Form("up")):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    src, sub = _pub_find_sub(pub, sub_id)
    if src:
        order = list(src.subtopics)
        idx = order.index(sub)
        j = idx - 1 if direction == "up" else idx + 1
        if 0 <= j < len(order):
            order[idx], order[j] = order[j], order[idx]
        for pos, t in enumerate(order, start=1):
            t.position = pos
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}" + (f"#source-{src.id}" if src else ""))


@router.post("/publications/{kind}/{pub_id}/subtopics/reorder", name="admin_pubs_subtopics_reorder")
async def admin_pubs_subtopics_reorder(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        payload = await request.json()
        source_id, ids = int(payload.get("source_id")), [int(i) for i in payload.get("ids", [])]
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    src = next((s for s in pub.sources if s.id == source_id), None)
    if not src:
        return JSONResponse({"error": "not found"}, status_code=404)
    by_id = {t.id: t for t in src.subtopics}
    order = [i for i in ids if i in by_id] + [t.id for t in src.subtopics if t.id not in ids]
    for pos, tid in enumerate(order, start=1):
        by_id[tid].position = pos
    return JSONResponse({"ok": True})


# ── Document: build, edit, export ──
@router.post("/publications/{kind}/{pub_id}/build", name="admin_pubs_build")
async def admin_pubs_build(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db), mode: str = Form("auto")):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    from app.core.publication_html import sanitize
    from app.core.publications import build_document, numbered_subtopics

    if not numbered_subtopics(pub):
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}",
                         "Nothing to build yet: generate (or write) the content for at least one topic first.", "error")
    updating = bool(pub.document_html) and mode != "rebuild"
    pub.document_html = sanitize(build_document(pub, pub.document_html if updating else None, rebuild=mode == "rebuild"))
    pub.document_built_at = datetime.utcnow()
    await log_admin_action(db, admin.id, "publication.build", "publication", pub.id, {"mode": "update" if updating else "build"}, request)
    await db.commit()
    return _redirect(request, f"/admin/publications/{kind}/{pub_id}/editor",
                     "Document updated. Your edits were kept and days renumbered." if updating else "Document generated. Review and edit it below.")


@router.get("/publications/{kind}/{pub_id}/editor", name="admin_pubs_editor")
async def admin_pubs_editor(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    if not pub.document_html:
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "Generate the document first.", "error")
    return await _pub_render(request, db, "admin/pub-editor.html", kind, pub=pub, title=f"Editor — {pub.title}")


@router.post("/publications/{kind}/{pub_id}/editor/save", name="admin_pubs_editor_save")
async def admin_pubs_editor_save(kind: str, pub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return JSONResponse({"ok": False, "error": "Not found or not allowed."}, status_code=404)
    from app.core.publication_html import plain_text, sanitize, word_count

    try:
        raw = (await request.json()).get("html", "")
    except Exception:
        return JSONResponse({"ok": False, "error": "Bad request."}, status_code=400)
    if not isinstance(raw, str) or len(raw) > 8_000_000:
        return JSONResponse({"ok": False, "error": "Document too large."}, status_code=413)
    clean = sanitize(raw)
    pub.document_html = clean
    pub.document_saved_at = datetime.utcnow()
    return JSONResponse({"ok": True, "words": word_count(plain_text(clean)), "saved_at": pub.document_saved_at.strftime("%H:%M:%S")})


@router.get("/publications/{kind}/{pub_id}/export/{fmt}", name="admin_pubs_export")
async def admin_pubs_export(kind: str, pub_id: int, fmt: str, request: Request, db: AsyncSession = Depends(get_db)):
    admin, pub, err = await _pub_guard(request, db, kind, pub_id)
    if err:
        return err
    from fastapi.responses import Response
    from slugify import slugify

    from app.core.publication_export import build_docx, build_pdf

    if not pub.document_html or fmt not in ("docx", "pdf"):
        return _redirect(request, f"/admin/publications/{kind}/{pub_id}", "Generate the document before exporting.", "error")
    if fmt == "docx":
        data = build_docx(pub.title, pub.document_html, pub.author_name)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        data = build_pdf(pub.title, pub.document_html, pub.author_name)
        media = "application/pdf"
    await log_admin_action(db, admin.id, "publication.export", "publication", pub.id, {"format": fmt}, request)
    name = f"{slugify(pub.title) or 'publication'}.{fmt}"
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ─── Community: Prayer Requests, Newsletter Subscribers, Donations ──
PRAYER_STATUSES = [("new", "New"), ("praying", "Praying"), ("answered", "Answered"), ("archived", "Archived")]


def _csv_cell(value) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @) in exported user-supplied text."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv_response(filename: str, header: list[str], rows: list[list]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_csv_cell(c) for c in r])
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _qs(base: str, **params) -> str:
    from urllib.parse import urlencode

    clean = {k: v for k, v in params.items() if v not in (None, "", 0)}
    return base + ("?" + urlencode(clean) if clean else "")


async def _prayer_scope(db: AsyncSession, admin, query):
    """Prayer requests are pastoral: a branch admin sees only their branch's; HQ staff see HQ's
    (including legacy rows with no branch); the HQ Administrator sees all."""
    from app.core.access import is_super
    from app.models.prayer_request import PrayerRequest

    if is_super(admin.role):
        return query
    hq = await get_hq_branch_id(db)
    if admin.branch_id and admin.branch_id != hq:
        return query.where(PrayerRequest.branch_id == admin.branch_id)
    return query.where(or_(PrayerRequest.branch_id == hq, PrayerRequest.branch_id.is_(None)))


@router.get("/prayer-requests", name="admin_prayer_requests")
async def admin_prayer_requests(request: Request, db: AsyncSession = Depends(get_db),
                                q: str = Query(""), status: str = Query(""), page: int = Query(1, ge=1)):
    admin = await _get_admin(request)
    if not admin or admin.role == "hod":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.prayer_request import PrayerRequest

    query = await _prayer_scope(db, admin, select(PrayerRequest).order_by(PrayerRequest.created_at.desc()))
    if status == "new":
        query = query.where(PrayerRequest.status.in_(("new", "pending")))
    elif status in dict(PRAYER_STATUSES):
        query = query.where(PrayerRequest.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(PrayerRequest.full_name.ilike(like), PrayerRequest.email.ilike(like), PrayerRequest.prayer_request.ilike(like)))
    pagination = await paginate(db, query, page=page, per_page=15)
    pagination["prev_url"] = _qs("/admin/prayer-requests", q=q, status=status, page=page - 1) if pagination["has_prev"] else None
    pagination["next_url"] = _qs("/admin/prayer-requests", q=q, status=status, page=page + 1) if pagination["has_next"] else None
    counts_q = await _prayer_scope(db, admin, select(PrayerRequest.status, func.count()).group_by(PrayerRequest.status))
    counts = dict((await db.execute(counts_q)).all())
    counts["new"] = counts.get("new", 0) + counts.pop("pending", 0)
    ctx = await _admin_context(request, db, "admin.prayer-requests")
    return await render_template(request, "admin/prayer-requests.html", {
        "_db": db, "items": pagination["items"], "pagination": pagination, "search": q, "filter_status": status,
        "statuses": PRAYER_STATUSES, "counts": counts, "title": "Prayer Requests", "headerIcon": "ri-hand-heart-line", **ctx})


@router.post("/prayer-requests/{request_id}/status", name="admin_prayer_requests_status")
async def admin_prayer_requests_status(request_id: int, request: Request, db: AsyncSession = Depends(get_db), status: str = Form("")):
    admin = await _get_admin(request)
    if not admin or admin.role == "hod":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.prayer_request import PrayerRequest

    row = (await db.execute(select(PrayerRequest).where(PrayerRequest.id == request_id))).scalar_one_or_none()
    back = request.headers.get("referer") or "/admin/prayer-requests"
    if not row or status not in dict(PRAYER_STATUSES):
        return _redirect(request, back, "Prayer request not found.", "error")
    if _foreign(admin, row.branch_id or await get_hq_branch_id(db)):
        return _redirect(request, back, "You can only manage prayer requests for your own branch.", "error")
    row.status = status
    told = False
    if status in ("praying", "answered") and row.notify_me and row.email and not row.notified_at:
        from app.core import mailer
        from app.core.settings_cache import get_settings as _gs

        if mailer.valid_email(row.email):
            _s = await _gs(db)
            who = row.full_name.split()[0] if row.full_name and row.full_name != "Anonymous" else "friend"
            told = True
            row.notified_at = datetime.utcnow()
            mailer.fire(mailer.send_notice(
                row.email, "Your prayer request has been lifted up", base_url=_public_base_url(request), kind="prayer_update", heading="We have prayed for you",
                paragraphs=[f"Dear {who}, our prayer team has lifted up the request you sent us. You are not alone in this.",
                            "If you would like to share how things are going, or you need to speak to a pastor, just reply to this email."],
                button=("Listen to a message", _public_base_url(request) + "/messages"), reply_to=(_s.get("siteContactEmail") or None)))
    return _redirect(request, back, f"Marked as {dict(PRAYER_STATUSES)[status].lower()}." + (" The person who asked for an update has been emailed." if told else ""))


@router.post("/prayer-requests/{request_id}/destroy", name="admin_prayer_requests_destroy")
async def admin_prayer_requests_destroy(request_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.prayer_request import PrayerRequest

    row = (await db.execute(select(PrayerRequest).where(PrayerRequest.id == request_id))).scalar_one_or_none()
    if row:
        await db.delete(row)
        await log_admin_action(db, admin.id, "prayer_request.delete", "prayer_request", request_id, None, request)
    return _redirect(request, "/admin/prayer-requests", "Prayer request deleted.")


@router.get("/subscribers", name="admin_subscribers")
async def admin_subscribers(request: Request, db: AsyncSession = Depends(get_db),
                            q: str = Query(""), status: str = Query(""), page: int = Query(1, ge=1)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.newsletter_subscriber import NewsletterSubscriber as NS

    query = select(NS).order_by(NS.created_at.desc())
    if status == "active":
        query = query.where(NS.is_active == True)  # noqa: E712
    elif status == "unsubscribed":
        query = query.where(NS.is_active == False)  # noqa: E712
    if q.strip():
        query = query.where(NS.email.ilike(f"%{q.strip()}%"))
    pagination = await paginate(db, query, page=page, per_page=25)
    pagination["prev_url"] = _qs("/admin/subscribers", q=q, status=status, page=page - 1) if pagination["has_prev"] else None
    pagination["next_url"] = _qs("/admin/subscribers", q=q, status=status, page=page + 1) if pagination["has_next"] else None
    total_active = (await db.execute(select(func.count()).select_from(NS).where(NS.is_active == True))).scalar_one()  # noqa: E712
    ctx = await _admin_context(request, db, "admin.subscribers")
    return await render_template(request, "admin/subscribers.html", {
        "_db": db, "items": pagination["items"], "pagination": pagination, "search": q, "filter_status": status,
        "total_active": total_active, "title": "Subscribers", "headerIcon": "ri-mail-send-line", **ctx})


@router.get("/subscribers/export.csv", name="admin_subscribers_export")
async def admin_subscribers_export(request: Request, db: AsyncSession = Depends(get_db), status: str = Query("active")):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.newsletter_subscriber import NewsletterSubscriber as NS

    query = select(NS).order_by(NS.created_at.asc())
    if status == "active":
        query = query.where(NS.is_active == True)  # noqa: E712
    rows = (await db.execute(query)).scalars().all()
    await log_admin_action(db, admin.id, "subscribers.export", "newsletter", None, {"count": len(rows)}, request)
    return _csv_response("subscribers.csv", ["email", "status", "subscribed"],
                         [[r.email, "active" if r.is_active else "unsubscribed", r.created_at.strftime("%Y-%m-%d")] for r in rows])


@router.post("/subscribers/{sub_id}/toggle", name="admin_subscribers_toggle")
async def admin_subscribers_toggle(sub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.newsletter_subscriber import NewsletterSubscriber as NS

    row = (await db.execute(select(NS).where(NS.id == sub_id))).scalar_one_or_none()
    back = request.headers.get("referer") or "/admin/subscribers"
    if not row:
        return _redirect(request, back, "Subscriber not found.", "error")
    row.is_active = not row.is_active
    row.unsubscribed_at = None if row.is_active else datetime.utcnow()
    return _redirect(request, back, "Subscriber reactivated." if row.is_active else "Subscriber unsubscribed.")


@router.post("/subscribers/{sub_id}/destroy", name="admin_subscribers_destroy")
async def admin_subscribers_destroy(sub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "media"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.newsletter_subscriber import NewsletterSubscriber as NS

    row = (await db.execute(select(NS).where(NS.id == sub_id))).scalar_one_or_none()
    if row:
        await db.delete(row)
    return _redirect(request, "/admin/subscribers", "Subscriber removed.")


PAID_STATUSES = ("completed", "success")
GIFT_METHODS = [("cash", "Cash"), ("transfer", "Bank transfer"), ("cheque", "Cheque"), ("pos", "POS"), ("other", "Other")]


def _sees_all_gifts(admin) -> bool:
    """HQ Administrator and HQ Finance see every branch's giving; a branch admin only their own."""
    from app.core.access import HQ_FINANCE, SUPER, normalize_role

    return normalize_role(admin.role) in (SUPER, HQ_FINANCE)


def _gift_scope(admin, query, model):
    if _sees_all_gifts(admin):
        return query
    return query.where(model.branch_id == admin.branch_id)


def _donation_query(admin, status: str, gateway: str, q: str, start: str, end: str, branch_id: str = "", method: str = "",
                    country: str = "", currency: str = ""):
    from app.models.branch import Branch
    from app.models.giving import Giving

    query = _gift_scope(admin, select(Giving).order_by(Giving.created_at.desc()), Giving)
    if status:
        query = query.where(Giving.status.in_(PAID_STATUSES) if status == "completed" else Giving.status == status)
    if gateway in ("paystack", "flutterwave"):
        query = query.where(Giving.payment_gateway == gateway)
    if method == "online":
        query = query.where(Giving.method == "online")
    elif method == "recorded":
        query = query.where(Giving.method != "online")
    bid = _to_int(branch_id)
    if bid and _sees_all_gifts(admin):
        query = query.where(Giving.branch_id == bid)
    if country and _sees_all_gifts(admin):
        query = query.where(Giving.branch_id.in_(select(Branch.id).where(Branch.country_code == country.upper())))
    if currency:
        query = query.where(Giving.currency == currency.upper())
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(Giving.email.ilike(like), Giving.full_name.ilike(like), Giving.reference.ilike(like), Giving.giving_type.ilike(like)))
    d1, d2 = _parse_date(start), _parse_date(end)
    if d1:
        query = query.where(Giving.created_at >= datetime.combine(d1, datetime.min.time()))
    if d2:
        query = query.where(Giving.created_at < datetime.combine(d2 + timedelta(days=1), datetime.min.time()))
    return query


@router.get("/donations", name="admin_donations")
async def admin_donations(request: Request, db: AsyncSession = Depends(get_db), q: str = Query(""), status: str = Query(""),
                          gateway: str = Query(""), start: str = Query(""), end: str = Query(""), page: int = Query(1, ge=1),
                          branch_id: str = Query(""), method: str = Query(""), country: str = Query(""), currency: str = Query("")):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "donations"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.branch import Branch
    from app.models.giving import Giving

    query = _donation_query(admin, status, gateway, q, start, end, branch_id, method, country, currency)
    pagination = await paginate(db, query, page=page, per_page=20)
    kw = dict(q=q, status=status, gateway=gateway, start=start, end=end, branch_id=branch_id, method=method, country=country, currency=currency)
    pagination["prev_url"] = _qs("/admin/donations", page=page - 1, **kw) if pagination["has_prev"] else None
    pagination["next_url"] = _qs("/admin/donations", page=page + 1, **kw) if pagination["has_next"] else None
    ids_q = query.with_only_columns(Giving.id).order_by(None)
    # One total per currency: amounts in different currencies are never added together.
    paid_rows = (await db.execute(select(Giving.currency, func.coalesce(func.sum(Giving.amount), 0), func.count()).where(
        Giving.id.in_(ids_q), Giving.status.in_(PAID_STATUSES)).group_by(Giving.currency).order_by(func.sum(Giving.amount).desc()))).all()
    paid_totals = [(cur or "NGN", float(total or 0)) for cur, total, _ in paid_rows]
    paid_count = sum(n for _, _, n in paid_rows)
    branches = list((await db.execute(select(Branch).order_by(Branch.is_hq.desc(), Branch.name))).scalars().all())
    branch_names = {b.id: b.name for b in branches}
    from app.core import geo

    from app.core import lists as _lists_mod

    country_options = sorted({(b.country_code, geo.country_name(b.country_code)) for b in branches if b.country_code}, key=lambda x: x[1])
    currency_options = sorted({c for (c,) in (await db.execute(select(Giving.currency).where(Giving.currency.isnot(None)).distinct())).all()})
    ctx = await _admin_context(request, db, "admin.donations")
    return await render_template(request, "admin/donations.html", {
        "_db": db, "items": pagination["items"], "pagination": pagination, "filters": kw,
        "paid_totals": paid_totals, "paid_count": paid_count, "export_url": _qs("/admin/donations/export.csv", **kw),
        "country_options": country_options, "currency_options": currency_options,
        "platform_currency": await _branch_currency(db, None),
        "purposes": _lists_mod.items(await get_settings(db), "giving_purposes") + [_lists_mod.OTHERS],
        "branches": branches, "branch_names": branch_names, "sees_all": _sees_all_gifts(admin), "gift_methods": GIFT_METHODS,
        "recheck_statuses": GIFT_RECHECK_STATUSES,
        "title": "Donations", "headerIcon": "ri-hand-coin-line", **ctx})


@router.get("/donations/export.csv", name="admin_donations_export")
async def admin_donations_export(request: Request, db: AsyncSession = Depends(get_db), q: str = Query(""), status: str = Query(""),
                                 gateway: str = Query(""), start: str = Query(""), end: str = Query(""), branch_id: str = Query(""),
                                 method: str = Query(""), country: str = Query(""), currency: str = Query("")):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "donations"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.branch import Branch

    all_branches = (await db.execute(select(Branch))).scalars().all()
    names = {b.id: b.name for b in all_branches}
    countries = {b.id: (b.country_code or "") for b in all_branches}
    rows = (await db.execute(_donation_query(admin, status, gateway, q, start, end, branch_id, method, country, currency).limit(50000))).scalars().all()
    await log_admin_action(db, admin.id, "donations.export", "giving", None, {"count": len(rows)}, request)
    return _csv_response("donations.csv",
                         ["date", "reference", "status", "branch", "country", "collected_by", "method", "gateway", "amount", "currency", "purpose", "name", "email", "phone", "anonymous", "dedication", "note"],
                         [[r.created_at.strftime("%Y-%m-%d %H:%M"), r.reference, r.status, names.get(r.branch_id, "HQ"), countries.get(r.branch_id, ""), r.gateway_owner, r.method, r.payment_gateway,
                           f"{float(r.amount):.2f}", r.currency or "NGN", r.giving_type, r.full_name, r.email, r.phone, "yes" if r.is_anonymous else "no", r.dedication, r.note] for r in rows])


@router.post("/donations/record", name="admin_donations_record")
async def admin_donations_record(
    request: Request, db: AsyncSession = Depends(get_db),
    amount: str = Form(""), purpose: str = Form("Offering"), method: str = Form("cash"),
    full_name: str = Form(""), given_on: str = Form(""), note: str = Form(""), branch_id: str = Form(""),
    currency: str = Form(""),
):
    """Record a gift that did not come through a payment gateway (cash, transfer, cheque, POS)."""
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "donations"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    import math

    from app.models.giving import Giving

    try:
        value = round(float(amount.replace(",", "")), 2)
    except ValueError:
        value = 0
    if not math.isfinite(value) or value <= 0 or value > 100_000_000:
        return _redirect(request, "/admin/donations", "Enter a valid amount.", "error")
    if method not in dict(GIFT_METHODS):
        method = "other"
    from app.core import geo, lists
    from app.models.branch import Branch

    purposes = lists.items(await get_settings(db), "giving_purposes") + [lists.OTHERS]
    purpose_value = lists.canonical(purpose, purposes)
    if not purpose_value:
        return _redirect(request, "/admin/donations", "Choose the purpose from the list (Settings → Giving gateway → Giving purposes).", "error")
    if purpose_value == lists.OTHERS and not note.strip():
        return _redirect(request, "/admin/donations", "For \"Others\", add a note saying what the gift is for.", "error")
    if _sees_all_gifts(admin):
        target = _to_int(branch_id) or await get_hq_branch_id(db)
    else:
        target = admin.branch_id
    hq_id = await get_hq_branch_id(db)
    cur = await _branch_currency(db, target)      # every gift is recorded in the platform currency (HQ's)
    day = _parse_date(given_on) or datetime.utcnow().date()
    stamp = datetime.combine(day, datetime.min.time())
    db.add(Giving(
        reference=f"manual_{secrets.token_urlsafe(10)}", email=(admin.email or "recorded@local")[:255], full_name=full_name.strip()[:255] or None,
        amount=value, currency=cur, giving_type=purpose_value, payment_gateway="manual", method=method,
        branch_id=target, gateway_owner="hq" if target == hq_id else "branch", status="completed", paid_at=stamp,
        created_at=stamp, note=note.strip()[:1000] or None,
    ))
    await log_admin_action(db, admin.id, "donations.recorded", "giving", None, {"amount": value, "currency": cur, "branch": target, "method": method}, request)
    return _redirect(request, "/admin/donations", "Gift recorded.")


GIFT_RECHECK_STATUSES = ("pending", "failed", "abandoned")


@router.post("/donations/{giving_id}/recheck", name="admin_donations_recheck")
async def admin_donations_recheck(giving_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Ask the payment gateway again about an online gift that is not confirmed (DATA-01)."""
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "donations"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core.giving_reconcile import reconcile
    from app.models.giving import Giving

    back = request.headers.get("referer") or "/admin/donations"
    back = back if "/admin/donations" in back else "/admin/donations"
    giving = (await db.execute(select(Giving).where(Giving.id == giving_id))).scalar_one_or_none()
    if giving is None or (not _sees_all_gifts(admin) and giving.branch_id != admin.branch_id):
        return _redirect(request, back, "Gift not found.", "error")
    if giving.method != "online" or giving.status not in GIFT_RECHECK_STATUSES:
        return _redirect(request, back, "Only online gifts that are not confirmed can be re-checked.", "error")
    before = giving.status
    outcome = await reconcile(db, giving, base_url=_public_base_url(request))
    await log_admin_action(db, admin.id, "donations.rechecked", "giving", giving.id, {"reference": giving.reference, "from": before, "to": giving.status}, request)
    messages = {
        "completed": ("The gateway confirmed this gift. It is now counted, and the giver was emailed a receipt.", "success"),
        "mismatch": ("The gateway says this was paid, but not the full amount. It is marked “Amount mismatch”.", "error"),
        "pending": ("The gateway says this gift has not been paid yet.", "info"),
        "abandoned": ("The gateway says this gift was never paid: the giver left the checkout. Marked “Abandoned”.", "info"),
        "failed": ("The gateway says this payment failed.", "info"),
        "error": ("Could not reach the gateway (or its key in Settings is wrong). Nothing was changed; try again later.", "error"),
    }
    msg, kind = messages.get(outcome, (f"Status: {outcome}.", "info"))
    return _redirect(request, back, msg, kind)


# ─── Remittances: who owes whom between HQ and each branch ──────────

async def _branch_positions(db: AsyncSession, only_branch_id: int | None = None) -> list[dict]:
    """Per branch: gifts recorded, where the money sits, remittances made, and the resulting balance.

    entitlement = hq_share% of ALL gifts recorded for the branch
    hq_holds    = gifts taken through HQ's account for that branch, less what HQ already sent the branch
    branch_owes_hq = entitlement - (hq_holds) - remitted_by_branch      (negative => HQ owes the branch)
    """
    from app.core.branch_settings import get_branch_settings, share_percent
    from app.models.branch import Branch
    from app.models.branch_setting import Remittance
    from app.models.giving import Giving

    branches = list((await db.execute(select(Branch).where(Branch.is_hq == False).order_by(Branch.name))).scalars().all())  # noqa: E712
    if only_branch_id is not None:
        branches = [b for b in branches if b.id == only_branch_id]
    from app.core.finance import branch_position

    platform = await _branch_currency(db, None)
    rows = []
    for b in branches:
        # One position PER CURRENCY: gifts HQ took in its currency and gifts the branch took locally
        # are separate debts, never netted against each other.
        gifts: dict[tuple[str, str], float] = {}
        for cur, owner, total in (await db.execute(
                select(Giving.currency, Giving.gateway_owner, func.coalesce(func.sum(Giving.amount), 0))
                .where(Giving.branch_id == b.id, Giving.status.in_(PAID_STATUSES)).group_by(Giving.currency, Giving.gateway_owner))).all():
            gifts[(cur or "NGN", owner)] = float(total or 0)
        rem: dict[tuple[str, str], float] = {}
        for cur, direction, total in (await db.execute(
                select(Remittance.currency, Remittance.direction, func.coalesce(func.sum(Remittance.amount), 0))
                .where(Remittance.branch_id == b.id).group_by(Remittance.currency, Remittance.direction))).all():
            rem[(cur or "NGN", direction)] = float(total or 0)
        pct = share_percent((await get_branch_settings(db, b.id)).get("hq_share_percent"))
        currencies = sorted({c for c, _ in gifts} | {c for c, _ in rem}) or [platform]
        for cur in currencies:
            held_by_hq, at_branch = gifts.get((cur, "hq"), 0.0), gifts.get((cur, "branch"), 0.0)
            to_hq, to_branch = rem.get((cur, "to_hq"), 0.0), rem.get((cur, "to_branch"), 0.0)
            pos = branch_position(pct, held_by_hq, at_branch, to_hq, to_branch)
            rows.append({"branch": b, "currency": cur, "pct": pct, "held_by_hq": held_by_hq, "at_branch": at_branch,
                         "to_hq": to_hq, "to_branch": to_branch, **pos})
    return rows


@router.get("/remittances", name="admin_remittances")
async def admin_remittances(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "remittances"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.branch import Branch
    from app.models.branch_setting import Remittance

    all_view = _sees_all_gifts(admin)
    positions = await _branch_positions(db, None if all_view else admin.branch_id)
    q = select(Remittance).order_by(Remittance.paid_on.desc(), Remittance.id.desc()).limit(200)
    if not all_view:
        q = q.where(Remittance.branch_id == admin.branch_id)
    items = list((await db.execute(q)).scalars().all())
    branches = list((await db.execute(select(Branch).where(Branch.is_hq == False).order_by(Branch.name))).scalars().all()) if all_view else []  # noqa: E712
    giving_model = ((await get_settings(db)).get("giving_model") or "central")
    ctx = await _admin_context(request, db, "admin.remittances")
    return await render_template(request, "admin/remittances.html", {
        "_db": db, "positions": positions, "items": items, "branches": branches, "can_record": all_view,
        "giving_model": giving_model, "title": "Remittances", "headerIcon": "ri-exchange-funds-line", **ctx})


@router.post("/remittances", name="admin_remittances_store")
async def admin_remittances_store(
    request: Request, db: AsyncSession = Depends(get_db),
    branch_id: int = Form(...), direction: str = Form("to_hq"), amount: str = Form(""), paid_on: str = Form(""),
    reference: str = Form(""), note: str = Form(""), currency: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "remittances") or not _sees_all_gifts(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    import math

    from app.models.branch_setting import Remittance

    branch = await _load_branch(db, branch_id)
    try:
        value = round(float(amount.replace(",", "")), 2)
    except ValueError:
        value = 0
    if branch is None or branch.is_hq or direction not in ("to_hq", "to_branch") or not math.isfinite(value) or value <= 0:
        return _redirect(request, "/admin/remittances", "Choose a branch, a direction and a valid amount.", "error")
    from app.core import geo

    cur = await _branch_currency(db, branch.id)      # the platform currency (HQ's)
    db.add(Remittance(branch_id=branch.id, direction=direction, amount=value, currency=cur, paid_on=_parse_date(paid_on) or datetime.utcnow().date(),
                      reference=reference.strip()[:255] or None, note=note.strip()[:1000] or None, recorded_by=admin.id))
    await log_admin_action(db, admin.id, "remittance.recorded", "branch", branch.id, {"direction": direction, "amount": value, "currency": cur}, request)
    return _redirect(request, "/admin/remittances", "Remittance recorded.")


@router.post("/remittances/{remittance_id}/delete", name="admin_remittances_delete")
async def admin_remittances_delete(remittance_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "remittances") or not _sees_all_gifts(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.branch_setting import Remittance

    row = (await db.execute(select(Remittance).where(Remittance.id == remittance_id))).scalar_one_or_none()
    if row:
        await db.delete(row)
        await log_admin_action(db, admin.id, "remittance.deleted", "branch", row.branch_id, {"amount": float(row.amount)}, request)
    return _redirect(request, "/admin/remittances", "Remittance removed.")



# ─── Branch settings: each branch's own Telegram bot, bank accounts and gateway ──
async def _branch_settings_page(request: Request, db: AsyncSession, admin, branch, *, as_hq: bool, action_base: str):
    from app.core.branch_settings import get_branch_settings, get_secret_status as branch_secret_status, parse_accounts

    values = await get_branch_settings(db, branch.id)
    values.pop("telegram_bot_token", None); values.pop("paystack_secret_key", None)
    values.pop("flutterwave_secret_key", None); values.pop("flutterwave_encryption_key", None)
    ctx = await _admin_context(request, db, "admin.branch-settings" if not as_hq else "admin.branches")
    return await render_template(request, "admin/branch-settings.html", {
        "_db": db, "branch": branch, "values": values, "accounts": parse_accounts(values.get("church_accounts")),
        "secret_status": await branch_secret_status(db, branch.id), "as_hq": as_hq, "action_base": action_base,
        "title": "Branch settings", "headerIcon": "ri-settings-3-line", **ctx})


async def _load_branch(db: AsyncSession, branch_id: int):
    from app.models.branch import Branch

    return (await db.execute(select(Branch).where(Branch.id == branch_id))).scalar_one_or_none()


@router.get("/branch-settings", name="admin_branch_settings")
async def admin_branch_settings(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    branch = await _load_branch(db, admin.branch_id) if admin.branch_id else None
    if branch is None or branch.is_hq:
        return _redirect(request, "/admin/branches" if admin.role == "super_admin" else "/admin/dashboard",
                         "HQ uses the site-wide Settings. Open a branch to edit its own settings.", "info")
    return await _branch_settings_page(request, db, admin, branch, as_hq=False, action_base="/admin/branch-settings")


@router.get("/branches/{branch_id}/settings", name="admin_branch_settings_for")
async def admin_branch_settings_for(branch_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    branch = await _load_branch(db, branch_id)
    if branch is None:
        return _redirect(request, "/admin/branches", "Branch not found.", "error")
    if branch.is_hq:
        return _redirect(request, "/admin/settings", "HQ uses the site-wide Settings.", "info")
    return await _branch_settings_page(request, db, admin, branch, as_hq=True, action_base=f"/admin/branches/{branch_id}/settings")


async def _save_branch_settings(request: Request, db: AsyncSession, admin, branch, as_hq: bool, back: str):
    from app.core.branch_settings import SECRET_KEYS, share_percent, update_branch_settings

    form = await request.form()
    values: dict[str, str] = {}
    for key in ("telegram_audio_chat_id", "paystack_public_key", "flutterwave_public_key", "service_times", "public_about", "public_pastor", "public_whatsapp", "directions_url"):
        if key in form:
            values[key] = str(form[key]).strip()[:2000]
    if "directions_url" in values:
        from app.core.site_content import safe_https

        values["directions_url"] = safe_https(values["directions_url"])
    if as_hq and form.get("public_present") == "1":       # only HQ approves a branch for the public directory
        values["listed_publicly"] = "1" if form.get("listed_publicly") == "1" else "0"
    for key in SECRET_KEYS:                      # write-only: blank keeps, "clear_<key>" removes
        typed = str(form.get(key) or "").strip()
        if typed:
            values[key] = typed
        elif form.get(f"clear_{key}") == "1":
            values[key] = ""
    # Sections are only rewritten when the form really carries them, so a partial post (for example
    # HQ changing just the share %) can never blank a branch's bank accounts or gateway choice.
    if form.get("giving_present") == "1":
        gw = str(form.get("active_giving_gateway") or "")
        values["active_giving_gateway"] = gw if gw in ("paystack", "flutterwave") else "paystack"
        values["collects_own_giving"] = "1" if form.get("collects_own_giving") == "1" else "0"
    if as_hq:                                    # only HQ decides how much of a branch's giving is HQ's
        values["hq_share_percent"] = str(share_percent(str(form.get("hq_share_percent") or "")))
        values["transcription_enabled"] = "0" if form.get("transcription_enabled") == "0" else "1"
    accounts = {}
    pattern = re.compile(r"^accounts\[(\d+)\]\[(bank_name|account_number|account_name)\]$")
    for key in form:
        m = pattern.match(key)
        if m:
            accounts.setdefault(int(m.group(1)), {})[m.group(2)] = str(form[key]).strip()[:120]
    if form.get("accounts_present") == "1":
        values["church_accounts"] = json.dumps([accounts[i] for i in sorted(accounts) if any(accounts[i].values())])
    # Contact details live on the branch itself. The region comes from the country's official list
    # (the country, currency and time zone are HQ's to set, on the branch page).
    if "region_code" in form and (err := await _apply_branch_location(db, branch, form, with_country=False)):
        return _redirect(request, back, err, "error")
    if "public_whatsapp" in values:
        from app.core import geo

        values["public_whatsapp"] = geo.normalize_phone(values["public_whatsapp"], branch.country_code)
    await update_branch_settings(db, branch.id, values)
    for field, limit in (("email", 255), ("address", 500)):
        if field in form:
            setattr(branch, field, str(form[field]).strip()[:limit] or None)
    if "phone" in form:
        from app.core import geo

        branch.phone = geo.normalize_phone(str(form["phone"]), branch.country_code) or None
    await log_admin_action(db, admin.id, "branch.settings_saved", "branch", branch.id,
                           {"secrets_changed": sorted(k for k in SECRET_KEYS if k in values)}, request)
    return _redirect(request, back, "Branch settings saved.")


@router.post("/branch-settings", name="admin_branch_settings_save")
async def admin_branch_settings_save(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    branch = await _load_branch(db, admin.branch_id) if admin.branch_id else None
    if branch is None or branch.is_hq:
        return _redirect(request, "/admin/dashboard", "HQ uses the site-wide Settings.", "info")
    return await _save_branch_settings(request, db, admin, branch, False, "/admin/branch-settings")


@router.post("/branches/{branch_id}/settings", name="admin_branch_settings_for_save")
async def admin_branch_settings_for_save(branch_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    branch = await _load_branch(db, branch_id)
    if branch is None or branch.is_hq:
        return _redirect(request, "/admin/branches", "Branch not found.", "error")
    return await _save_branch_settings(request, db, admin, branch, True, f"/admin/branches/{branch_id}/settings")


async def _test_branch_telegram(request: Request, branch, back: str):
    from app.core.telegram import TelegramDistributor

    ok, message = await TelegramDistributor(branch.id).check()
    return _redirect(request, back, ("Telegram works: " if ok else "Telegram check failed: ") + message, "success" if ok else "error")


@router.post("/branch-settings/test-telegram", name="admin_branch_test_telegram")
async def admin_branch_test_telegram(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    branch = await _load_branch(db, admin.branch_id) if admin.branch_id else None
    if branch is None or branch.is_hq:
        return _redirect(request, "/admin/dashboard", "HQ uses the site-wide Settings.", "info")
    await db.commit()
    return await _test_branch_telegram(request, branch, "/admin/branch-settings")


@router.post("/branches/{branch_id}/settings/test-telegram", name="admin_branch_test_telegram_for")
async def admin_branch_test_telegram_for(branch_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    branch = await _load_branch(db, branch_id)
    if branch is None or branch.is_hq:
        return _redirect(request, "/admin/branches", "Branch not found.", "error")
    await db.commit()
    return await _test_branch_telegram(request, branch, f"/admin/branches/{branch_id}/settings")


@router.post("/branches/{branch_id}/active", name="admin_branch_set_active")
async def admin_branch_set_active(branch_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Deactivate/reactivate a branch. Nothing is deleted: its records stay, it just leaves the public forms."""
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    branch = await _load_branch(db, branch_id)
    if branch is None:
        return _redirect(request, "/admin/branches", "Branch not found.", "error")
    if branch.is_hq:
        return _redirect(request, "/admin/branches", "HQ cannot be deactivated.", "error")
    branch.is_active = not branch.is_active
    await log_admin_action(db, admin.id, "branch.activated" if branch.is_active else "branch.deactivated", "branch", branch.id, None, request)
    return _redirect(request, f"/admin/branches/{branch_id}", "Branch reactivated." if branch.is_active else "Branch deactivated. Its data is kept; it no longer appears on public forms.")


# ─── Branch Reports: the branch admin's monthly report to HQ ─────────
BRANCH_REPORT_METRICS = [
    ("avg_attendance", "Average Sunday attendance"), ("first_timers", "First-time visitors"), ("new_members", "New members"),
    ("converts", "New converts"), ("baptised", "Baptised"),
]
_MONTH_RX = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _is_branch_author(admin, branch=None) -> bool:
    """Only a branch admin of a non-HQ branch writes a branch report (HQ has its own unit reports)."""
    from app.core.access import BRANCH_ADMIN, normalize_role

    return normalize_role(admin.role) == BRANCH_ADMIN and bool(admin.branch_id)


def _is_hq_reader(admin) -> bool:
    from app.core.access import HQ_PASTORAL, SUPER, normalize_role

    return normalize_role(admin.role) in (SUPER, HQ_PASTORAL)


def _current_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


async def _dept_rollup(db: AsyncSession, branch_id: int, month: str) -> dict:
    """How many of the branch's department heads have submitted their unit report for ``month``.

    Only counts and (for the branch itself) department names: HQ never receives the reports' content.
    """
    from app.models.admin import Admin as AdminModel
    from app.models.department import Department
    from app.models.unit_report import UnitReport

    heads = (await db.execute(select(AdminModel).where(AdminModel.role == "hod", AdminModel.branch_id == branch_id, AdminModel.is_active == True))).scalars().all()  # noqa: E712
    dept_ids = {h.department_id for h in heads if h.department_id}
    done = set((await db.execute(select(UnitReport.department_id).where(
        UnitReport.branch_id == branch_id, UnitReport.report_month.like(f"{month}%"), UnitReport.status.in_(("submitted", "reviewed"))))).scalars().all())
    names = {d.id: d.name for d in (await db.execute(select(Department).where(Department.id.in_(dept_ids or {-1})))).scalars().all()}
    missing = sorted(names[i] for i in dept_ids if i not in done and i in names)
    return {"expected": len(dept_ids), "submitted": len(dept_ids & done), "missing": missing}


def _clean_metrics(form) -> dict:
    out = {}
    for key, _ in BRANCH_REPORT_METRICS:
        raw = str(form.get(key, "") or "").strip().replace(",", "")
        out[key] = int(raw) if raw.isdigit() and int(raw) < 10_000_000 else None
    return out


def _report_fields(form) -> dict:
    def t(name, limit=8000):
        return (str(form.get(name, "") or "").strip()[:limit]) or None

    return {"summary": (str(form.get("summary", "") or "").strip()[:8000]), "metrics": _clean_metrics(form), "finance_note": t("finance_note"),
            "highlights": t("highlights"), "challenges": t("challenges"), "requests_to_hq": t("requests_to_hq"), "projections": t("projections")}


@router.get("/branch-reports", name="admin_branch_reports")
async def admin_branch_reports(request: Request, db: AsyncSession = Depends(get_db), branch_id: str = Query(""), month: str = Query(""),
                               country: str = Query("")):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "branch_reports"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.branch import Branch
    from app.models.branch_report import BranchReport

    reader = _is_hq_reader(admin)
    q = select(BranchReport).order_by(BranchReport.report_month.desc(), BranchReport.branch_id)
    if reader:
        q = q.where(BranchReport.status.in_(("submitted", "acknowledged")))          # HQ never sees a branch's drafts
        if _to_int(branch_id):
            q = q.where(BranchReport.branch_id == _to_int(branch_id))
        if country:
            q = q.where(BranchReport.branch_id.in_(select(Branch.id).where(Branch.country_code == country.upper())))
    else:
        q = q.where(BranchReport.branch_id == admin.branch_id)
    if _MONTH_RX.match(month or ""):
        q = q.where(BranchReport.report_month == month)
    items = list((await db.execute(q.limit(200))).scalars().all())

    tracker, branches = [], []
    track_month = month if _MONTH_RX.match(month or "") else _current_month()
    if reader:
        branches = list((await db.execute(select(Branch).where(Branch.is_hq == False, Branch.is_active == True).order_by(Branch.name))).scalars().all())  # noqa: E712
        all_branches = branches
        if country:
            branches = [b for b in branches if b.country_code == country.upper()]
        branches.sort(key=lambda b: (_country_name(b.country_code) or "~", b.region_code or "~", b.name.lower()))   # grouped by country
        have = {r.branch_id: r for r in (await db.execute(select(BranchReport).where(
            BranchReport.report_month == track_month, BranchReport.status.in_(("submitted", "acknowledged"))))).scalars().all()}
        tracker = [{"branch": b, "report": have.get(b.id)} for b in branches]
    ctx = await _admin_context(request, db, "admin.branch-reports")
    return await render_template(request, "admin/branch-reports.html", {
        "_db": db, "items": items, "reader": reader, "tracker": tracker, "branches": branches, "track_month": track_month,
        "filter_branch": branch_id, "filter_month": month, "can_write": _is_branch_author(admin), "metrics_def": BRANCH_REPORT_METRICS,
        "filter_country": country.upper(),
        "country_options": sorted({(b.country_code, _country_name(b.country_code)) for b in (all_branches if reader else []) if b.country_code}, key=lambda x: x[1]),
        "title": "Branch Reports", "headerIcon": "ri-file-list-3-line", **ctx})


async def _report_form_page(request, db, admin, report, month):
    branch_id = report.branch_id if report else admin.branch_id
    rollup = await _dept_rollup(db, branch_id, month)
    ctx = await _admin_context(request, db, "admin.branch-reports")
    return await render_template(request, "admin/branch-report-form.html", {
        "_db": db, "report": report, "month": month, "rollup": rollup, "metrics_def": BRANCH_REPORT_METRICS,
        "title": "Branch report", "headerIcon": "ri-file-list-3-line", **ctx})


@router.get("/branch-reports/create", name="admin_branch_reports_create")
async def admin_branch_reports_create(request: Request, db: AsyncSession = Depends(get_db), month: str = Query("")):
    admin = await _get_admin(request)
    if not admin or not _is_branch_author(admin):
        return RedirectResponse(url="/admin/branch-reports", status_code=303)
    from app.models.branch_report import BranchReport

    month = month if _MONTH_RX.match(month or "") else _current_month()
    existing = (await db.execute(select(BranchReport).where(BranchReport.branch_id == admin.branch_id, BranchReport.report_month == month))).scalar_one_or_none()
    if existing:
        return _redirect(request, f"/admin/branch-reports/{existing.id}/edit", "You already have a report for that month. Continue it here.", "info")
    return await _report_form_page(request, db, admin, None, month)


async def _save_branch_report(request: Request, db: AsyncSession, admin, report, month: str):
    from app.models.branch_report import BranchReport

    form = await request.form()
    fields = _report_fields(form)
    submit_now = bool(form.get("submit_now"))
    if not fields["summary"]:
        return _redirect(request, f"/admin/branch-reports/{report.id}/edit" if report else f"/admin/branch-reports/create?month={month}",
                         "Write a short summary of the month before saving.", "error")
    if report is None:
        report = BranchReport(branch_id=admin.branch_id, author_admin_id=admin.id, report_month=month, status="draft")
        db.add(report)
    for k, v in fields.items():
        setattr(report, k, v)
    if submit_now:
        report.rollup = {k: v for k, v in (await _dept_rollup(db, report.branch_id, report.report_month)).items() if k != "missing"}
        report.status, report.submitted_at = "submitted", datetime.utcnow()
    await db.flush()
    await log_admin_action(db, admin.id, "branch_report.submitted" if submit_now else "branch_report.saved", "branch_report", report.id, {"month": report.report_month}, request)
    return _redirect(request, f"/admin/branch-reports/{report.id}", "Report submitted to HQ." if submit_now else "Draft saved. HQ cannot see it until you submit.")


@router.post("/branch-reports", name="admin_branch_reports_store")
async def admin_branch_reports_store(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not _is_branch_author(admin):
        return RedirectResponse(url="/admin/branch-reports", status_code=303)
    from app.models.branch_report import BranchReport

    month = str((await request.form()).get("report_month", "") or "").strip()
    if not _MONTH_RX.match(month):
        return _redirect(request, "/admin/branch-reports/create", "Choose the report month.", "error")
    dup = (await db.execute(select(BranchReport).where(BranchReport.branch_id == admin.branch_id, BranchReport.report_month == month))).scalar_one_or_none()
    if dup:
        return _redirect(request, f"/admin/branch-reports/{dup.id}/edit", "You already have a report for that month. Continue it here.", "info")
    return await _save_branch_report(request, db, admin, None, month)


async def _load_report(db: AsyncSession, report_id: int):
    from app.models.branch_report import BranchReport

    return (await db.execute(select(BranchReport).where(BranchReport.id == report_id))).scalar_one_or_none()


@router.get("/branch-reports/{report_id}", name="admin_branch_reports_show")
async def admin_branch_reports_show(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "branch_reports"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    report = await _load_report(db, report_id)
    own = bool(report) and admin.branch_id == report.branch_id and _is_branch_author(admin)
    hq_ok = bool(report) and _is_hq_reader(admin) and report.status in ("submitted", "acknowledged")
    if not (own or hq_ok):
        return _redirect(request, "/admin/branch-reports", "Report not found.", "error")
    ctx = await _admin_context(request, db, "admin.branch-reports")
    rollup = report.rollup or {}
    return await render_template(request, "admin/branch-report-show.html", {
        "_db": db, "report": report, "own": own, "can_comment": hq_ok, "can_edit": own and report.status != "acknowledged",
        "rollup": rollup, "metrics_def": BRANCH_REPORT_METRICS, "title": "Branch report", "headerIcon": "ri-file-list-3-line", **ctx})


@router.get("/branch-reports/{report_id}/edit", name="admin_branch_reports_edit")
async def admin_branch_reports_edit(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not _is_branch_author(admin):
        return RedirectResponse(url="/admin/branch-reports", status_code=303)
    report = await _load_report(db, report_id)
    if report is None or report.branch_id != admin.branch_id:
        return _redirect(request, "/admin/branch-reports", "Report not found.", "error")
    if report.status == "acknowledged":
        return _redirect(request, f"/admin/branch-reports/{report_id}", "HQ has acknowledged this report, so it can no longer be edited.", "info")
    return await _report_form_page(request, db, admin, report, report.report_month)


@router.post("/branch-reports/{report_id}/update", name="admin_branch_reports_update")
async def admin_branch_reports_update(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not _is_branch_author(admin):
        return RedirectResponse(url="/admin/branch-reports", status_code=303)
    report = await _load_report(db, report_id)
    if report is None or report.branch_id != admin.branch_id:
        return _redirect(request, "/admin/branch-reports", "Report not found.", "error")
    if report.status == "acknowledged":
        return _redirect(request, f"/admin/branch-reports/{report_id}", "HQ has acknowledged this report, so it can no longer be edited.", "info")
    return await _save_branch_report(request, db, admin, report, report.report_month)


@router.post("/branch-reports/{report_id}/delete", name="admin_branch_reports_delete")
async def admin_branch_reports_delete(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not _is_branch_author(admin):
        return RedirectResponse(url="/admin/branch-reports", status_code=303)
    report = await _load_report(db, report_id)
    if report is None or report.branch_id != admin.branch_id or report.status != "draft":
        return _redirect(request, "/admin/branch-reports", "Only your own drafts can be deleted.", "error")
    await db.delete(report)
    return _redirect(request, "/admin/branch-reports", "Draft deleted.")


@router.post("/branch-reports/{report_id}/comment", name="admin_branch_reports_comment")
async def admin_branch_reports_comment(report_id: int, request: Request, db: AsyncSession = Depends(get_db), hq_comment: str = Form("")):
    admin = await _get_admin(request)
    if not admin or not _is_hq_reader(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    report = await _load_report(db, report_id)
    if report is None or report.status not in ("submitted", "acknowledged"):
        return _redirect(request, "/admin/branch-reports", "Report not found.", "error")
    report.hq_comment = hq_comment.strip()[:4000] or None
    report.hq_commented_by, report.hq_commented_at, report.status = admin.id, datetime.utcnow(), "acknowledged"
    await log_admin_action(db, admin.id, "branch_report.acknowledged", "branch_report", report.id, None, request)
    return _redirect(request, f"/admin/branch-reports/{report_id}", "Report acknowledged. The branch can see your comment.")


# ─── Ministers: bio-data of the ministers at HQ and in each branch ───
MINISTER_TITLES = ["Pastor", "Rev.", "Evangelist", "Prophet", "Apostle", "Minister", "Deacon", "Deaconess", "Elder", "Brother", "Sister"]
MINISTER_STATUSES = [("active", "Active"), ("on_leave", "On leave"), ("retired", "Retired"), ("left", "Left the ministry")]


def _minister_can_manage(admin, minister) -> bool:
    """HQ Administrator: any church. Branch admin / HQ staff with the module: their own church only."""
    return has_perm(admin.role, "ministers") and not _foreign(admin, minister.branch_id)


async def _minister_roles(db: AsyncSession, ministers) -> dict[int, dict]:
    """Per minister: their console role, if they hold one (HOD of which department / Resident Pastor)."""
    from app.models.department import Department

    dep_ids = {m.account.department_id for m in ministers if m.account and m.account.department_id}
    names = {d.id: d.name for d in (await db.execute(select(Department).where(Department.id.in_(dep_ids or {-1})))).scalars().all()}
    out = {}
    for m in ministers:
        a = m.account
        role = a.role if a else None
        out[m.id] = {"role": role, "active": bool(a and a.is_active), "department": names.get(a.department_id) if a and a.department_id else None}
    return out


async def _save_minister_photo(upload) -> str | None:
    import uuid

    if not upload or not upload.filename:
        return None
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise ValueError("Use a JPG, PNG, WEBP or GIF photo.")
    raw = await upload.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("The photo is larger than 5 MB.")
    name = f"minister-{uuid.uuid4()}{ext}"
    dest_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images", "ministers")
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, name), "wb") as f:
        f.write(raw)
    return f"images/ministers/{name}"


async def _minister_branches(db: AsyncSession, admin) -> list:
    from app.models.branch import Branch

    q = select(Branch).where(Branch.is_active == True).order_by(Branch.is_hq.desc(), Branch.name)  # noqa: E712
    if not is_super_role(admin.role):
        q = q.where(Branch.id == admin.branch_id)
    return list((await db.execute(q)).scalars().all())


@router.get("/ministers", name="admin_ministers")
async def admin_ministers(request: Request, db: AsyncSession = Depends(get_db), q: str = Query(""), status: str = Query(""),
                          branch_id: str = Query(""), page: int = Query(1, ge=1), country: str = Query("")):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "ministers.view"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.branch import Branch
    from app.models.minister import Minister

    query = select(Minister).order_by(Minister.full_name)
    if not is_super_role(admin.role):
        query = query.where(Minister.branch_id == admin.branch_id)
    elif _to_int(branch_id):
        query = query.where(Minister.branch_id == _to_int(branch_id))
    if country and is_super_role(admin.role):
        query = query.where(Minister.branch_id.in_(select(Branch.id).where(Branch.country_code == country.upper())))
    if status in dict(MINISTER_STATUSES):
        query = query.where(Minister.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(Minister.full_name.ilike(like), Minister.email.ilike(like), Minister.phone.ilike(like), Minister.ministry_rank.ilike(like)))
    pagination = await paginate(db, query, page=page, per_page=20)
    kw = dict(q=q, status=status, branch_id=branch_id, country=country.upper())
    pagination["prev_url"] = _qs("/admin/ministers", page=page - 1, **kw) if pagination["has_prev"] else None
    pagination["next_url"] = _qs("/admin/ministers", page=page + 1, **kw) if pagination["has_next"] else None
    roles = await _minister_roles(db, pagination["items"])
    branches = list((await db.execute(select(Branch).order_by(Branch.is_hq.desc(), Branch.name))).scalars().all()) if is_super_role(admin.role) else []
    ctx = await _admin_context(request, db, "admin.ministers")
    return await render_template(request, "admin/ministers.html", {
        "_db": db, "items": pagination["items"], "pagination": pagination, "roles": roles, "branches": branches, "filters": kw,
        "statuses": MINISTER_STATUSES, "can_manage": has_perm(admin.role, "ministers"),
        "country_options": sorted({(b.country_code, _country_name(b.country_code)) for b in branches if b.country_code}, key=lambda x: x[1]),
        "title": "Ministers", "headerIcon": "ri-user-voice-line", **ctx})


async def _minister_form_page(request, db, admin, minister):
    from app.core import lists as _lists

    _site = await get_settings(db)
    ctx = await _admin_context(request, db, "admin.ministers")
    return await render_template(request, "admin/minister-form.html", {
        "_db": db, "m": minister, "branches": await _minister_branches(db, admin), "statuses": MINISTER_STATUSES,
        "titles": _lists.items(_site, "minister_titles"), "ranks": _lists.items(_site, "ministry_ranks"),
        "education_levels": _lists.items(_site, "education_levels"), "marital_statuses": _lists.MARITAL_STATUSES,
        "home_country": await _branch_country(db, minister.branch_id if minister else admin.branch_id),
        "title": "Minister", "headerIcon": "ri-user-voice-line", **ctx})


@router.get("/ministers/create", name="admin_ministers_create")
async def admin_ministers_create(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "ministers"):
        return RedirectResponse(url="/admin/ministers", status_code=303)
    return await _minister_form_page(request, db, admin, None)


async def _minister_fields(db: AsyncSession, form, branch_id: int | None) -> tuple[dict, str | None]:
    """Validated minister fields: every category from its list, origin as ISO codes. (fields, error)."""
    from app.core import geo, lists

    site = await get_settings(db)

    def t(name, limit=255):
        return (str(form.get(name, "") or "").strip()[:limit]) or None

    def pick(name, choices, what):
        raw = t(name, 120)
        value = lists.canonical(raw, choices) if raw else None
        return value, (f"Choose the {what} from the list." if raw and not value else None)

    title, e1 = pick("title", lists.items(site, "minister_titles"), "title")
    rank, e2 = pick("ministry_rank", lists.items(site, "ministry_ranks"), "ministry rank")
    level, e3 = pick("education_level", lists.items(site, "education_levels"), "education level")
    marital, e4 = pick("marital_status", lists.MARITAL_STATUSES, "marital status")
    gender = lists.gender(t("gender", 20))
    e5 = "Choose the gender from the list." if t("gender", 20) and not gender else None
    occ, orc = (t("origin_country_code", 2) or "").upper(), (t("origin_region_code", 10) or "").upper()
    e6 = None
    if occ and not geo.is_country(occ):
        e6 = "Choose the country of origin from the list."
    elif orc and not geo.is_region(occ, orc):
        e6 = "Choose the state / region of origin from that country's list."
    error = next((e for e in (e1, e2, e3, e4, e5, e6) if e), None)
    home = await _branch_country(db, branch_id)
    return {
        "title": title, "full_name": (t("full_name") or ""), "gender": gender, "date_of_birth": _parse_date(str(form.get("date_of_birth", "") or "")),
        "marital_status": marital, "spouse_name": t("spouse_name"), "phone": geo.normalize_phone(t("phone", 50), home) or None,
        "email": (t("email") or None) and t("email").lower(), "address": t("address", 500),
        "origin_country_code": occ or None, "origin_region_code": orc or None,
        "state_of_origin": (geo.region_name(orc) or None) if orc else None,
        "education_level": level, "education": t("education", 500), "ministry_rank": rank,
        "date_joined": _parse_date(str(form.get("date_joined", "") or "")), "ordination_date": _parse_date(str(form.get("ordination_date", "") or "")),
        "bio": t("bio", 8000), "notes": t("notes", 4000),
        "status": str(form.get("status", "active")) if str(form.get("status", "active")) in dict(MINISTER_STATUSES) else "active",
    }, error


@router.post("/ministers", name="admin_ministers_store")
async def admin_ministers_store(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "ministers"):
        return RedirectResponse(url="/admin/ministers", status_code=303)
    from app.models.minister import Minister

    form = await request.form()
    branch_id = _to_int(str(form.get("branch_id", ""))) if is_super_role(admin.role) else admin.branch_id
    if not branch_id or (await _load_branch(db, branch_id)) is None:
        return _redirect(request, "/admin/ministers/create", "Choose the church (HQ or a branch) this minister serves.", "error")
    fields, error = await _minister_fields(db, form, branch_id)
    if error:
        return _redirect(request, "/admin/ministers/create", error, "error")
    if not fields["full_name"]:
        return _redirect(request, "/admin/ministers/create", "Enter the minister's full name.", "error")
    try:
        photo = await _save_minister_photo(form.get("photo"))
    except ValueError as exc:
        return _redirect(request, "/admin/ministers/create", str(exc), "error")
    m = Minister(branch_id=branch_id, created_by=admin.id, photo=photo, **fields)
    db.add(m)
    await db.flush()
    await log_admin_action(db, admin.id, "minister.created", "minister", m.id, {"branch": branch_id}, request)
    return _redirect(request, f"/admin/ministers/{m.id}", "Minister recorded.")


async def _load_minister(db: AsyncSession, minister_id: int):
    from app.models.minister import Minister

    return (await db.execute(select(Minister).where(Minister.id == minister_id))).scalar_one_or_none()


@router.get("/ministers/{minister_id}", name="admin_ministers_show")
async def admin_ministers_show(minister_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "ministers.view"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    m = await _load_minister(db, minister_id)
    if m is None or _foreign(admin, m.branch_id):
        return _redirect(request, "/admin/ministers", "Minister not found.", "error")
    from app.models.department import Department

    roles = await _minister_roles(db, [m])
    rp = await _resident_pastor_for(db, m.branch_id)
    departments = list((await db.execute(select(Department).order_by(Department.name))).scalars().all())
    ctx = await _admin_context(request, db, "admin.ministers")
    return await render_template(request, "admin/minister-show.html", {
        "_db": db, "m": m, "role_info": roles[m.id], "branch_rp": rp, "departments": departments,
        "can_manage": _minister_can_manage(admin, m), "can_appoint_rp": admin.role == "super_admin",
        "title": m.full_name, "headerIcon": "ri-user-voice-line", **ctx})


@router.get("/ministers/{minister_id}/edit", name="admin_ministers_edit")
async def admin_ministers_edit(minister_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "ministers"):
        return RedirectResponse(url="/admin/ministers", status_code=303)
    m = await _load_minister(db, minister_id)
    if m is None or not _minister_can_manage(admin, m):
        return _redirect(request, "/admin/ministers", "Minister not found.", "error")
    return await _minister_form_page(request, db, admin, m)


@router.post("/ministers/{minister_id}/update", name="admin_ministers_update")
async def admin_ministers_update(minister_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "ministers"):
        return RedirectResponse(url="/admin/ministers", status_code=303)
    m = await _load_minister(db, minister_id)
    if m is None or not _minister_can_manage(admin, m):
        return _redirect(request, "/admin/ministers", "Minister not found.", "error")
    form = await request.form()
    fields, error = await _minister_fields(db, form, m.branch_id)
    if error:
        return _redirect(request, f"/admin/ministers/{minister_id}/edit", error, "error")
    if not fields["full_name"]:
        return _redirect(request, f"/admin/ministers/{minister_id}/edit", "Enter the minister's full name.", "error")
    try:
        photo = await _save_minister_photo(form.get("photo"))
    except ValueError as exc:
        return _redirect(request, f"/admin/ministers/{minister_id}/edit", str(exc), "error")
    for k, v in fields.items():
        setattr(m, k, v)
    if photo:
        m.photo = photo
    if is_super_role(admin.role) and _to_int(str(form.get("branch_id", ""))) and m.account is None:
        target = await _load_branch(db, _to_int(str(form.get("branch_id", ""))))
        if target is not None:
            m.branch_id = target.id             # an appointed minister's church is changed by moving their account, not here
    await log_admin_action(db, admin.id, "minister.updated", "minister", m.id, None, request)
    return _redirect(request, f"/admin/ministers/{minister_id}", "Minister updated.")


@router.post("/ministers/{minister_id}/delete", name="admin_ministers_delete")
async def admin_ministers_delete(minister_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "ministers"):
        return RedirectResponse(url="/admin/ministers", status_code=303)
    m = await _load_minister(db, minister_id)
    if m is None or not _minister_can_manage(admin, m):
        return _redirect(request, "/admin/ministers", "Minister not found.", "error")
    if m.admin_id:
        return _redirect(request, f"/admin/ministers/{minister_id}", "This minister holds a console role (HOD or Resident Pastor). Remove the role first, or mark them as left.", "error")
    await db.delete(m)
    await log_admin_action(db, admin.id, "minister.deleted", "minister", minister_id, None, request)
    return _redirect(request, "/admin/ministers", "Minister removed from the record.")


async def _account_for_minister(db: AsyncSession, m, email: str, role: str, department_id: int | None):
    """Create the console login for a minister (temporary password returned once). Raises ValueError with a user message."""
    from app.models.admin import Admin as AdminModel

    address = (email or m.email or "").strip().lower()
    if not address or "@" not in address:
        raise ValueError("This minister needs an email address to get a login. Add one on their record first.")
    if (await db.execute(select(AdminModel.id).where(func.lower(AdminModel.email) == address))).first():
        raise ValueError("Someone already has a console account with that email.")
    temp = secrets.token_urlsafe(10)
    acct = AdminModel(full_name=f"{(m.title + ' ') if m.title else ''}{m.full_name}".strip()[:255], email=address, password_hash=hash_password(temp),
                      role=role, branch_id=m.branch_id, department_id=department_id, phone=m.phone, is_active=True, must_change_password=True)
    db.add(acct)
    await db.flush()
    if not m.email:
        m.email = address
    return acct, temp


@router.post("/ministers/{minister_id}/make-hod", name="admin_ministers_make_hod")
async def admin_ministers_make_hod(minister_id: int, request: Request, db: AsyncSession = Depends(get_db),
                                   department_id: int = Form(...), email: str = Form("")):
    """Appoint a minister head of a department (HQ Administrator: any church; branch admin: their own)."""
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "ministers"):
        return RedirectResponse(url="/admin/ministers", status_code=303)
    from app.models.department import Department

    m = await _load_minister(db, minister_id)
    back = f"/admin/ministers/{minister_id}"
    if m is None or not _minister_can_manage(admin, m):
        return _redirect(request, "/admin/ministers", "Minister not found.", "error")
    dept = (await db.execute(select(Department).where(Department.id == department_id))).scalar_one_or_none()
    if dept is None:
        return _redirect(request, back, "Choose a department.", "error")
    if m.account is not None:
        if m.account.role != "hod":
            return _redirect(request, back, "This minister already holds another console role.", "error")
        m.account.department_id = dept.id
        await log_admin_action(db, admin.id, "minister.hod_changed", "minister", m.id, {"department": dept.id}, request)
        return _redirect(request, back, f"Now head of {dept.name}.")
    try:
        acct, temp = await _account_for_minister(db, m, email, "hod", dept.id)
    except ValueError as exc:
        return _redirect(request, back, str(exc), "error")
    m.admin_id = acct.id
    await log_admin_action(db, admin.id, "minister.made_hod", "minister", m.id, {"department": dept.id}, request)
    return _redirect(request, back, f"{m.full_name} is now head of {dept.name}. Login {acct.email} · temporary password {temp} — share it securely; it is shown only once.")


async def _appoint_resident_pastor(db: AsyncSession, actor, account, minister) -> str:
    """Make ``account`` the Resident Pastor of ``minister``'s church. Returns "" or an error message."""
    from app.core.branch_settings import update_branch_settings

    church = minister.branch_id
    existing = await _resident_pastor_for(db, church)
    if existing is not None and existing.id != account.id:
        return f"{existing.full_name} is already the Resident Pastor here. Remove them first."
    account.role, account.branch_id, account.is_active = "resident_pastor", church, True      # department kept so removal can revert to HOD
    account.session_version = (account.session_version or 0) + 1
    minister.admin_id = account.id
    minister.status = "active"
    await update_branch_settings(db, church, {"resident_pastor_since": datetime.utcnow().isoformat(timespec="seconds")})
    return ""


@router.post("/ministers/{minister_id}/make-resident-pastor", name="admin_ministers_make_rp")
async def admin_ministers_make_rp(minister_id: int, request: Request, db: AsyncSession = Depends(get_db), email: str = Form("")):
    """HQ Administrator appoints a minister Resident Pastor of their church."""
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return _redirect(request, "/admin/ministers", "Only the HQ Administrator appoints a Resident Pastor.", "error")
    m = await _load_minister(db, minister_id)
    back = f"/admin/ministers/{minister_id}"
    if m is None:
        return _redirect(request, "/admin/ministers", "Minister not found.", "error")
    temp = ""
    if m.account is None:
        try:
            acct, temp = await _account_for_minister(db, m, email, "resident_pastor", None)
        except ValueError as exc:
            return _redirect(request, back, str(exc), "error")
    elif m.account.role == "hod":
        acct = m.account
    elif m.account.role == "resident_pastor":
        return _redirect(request, back, "Already the Resident Pastor.", "info")
    else:
        return _redirect(request, back, "This minister already holds another console role.", "error")
    err = await _appoint_resident_pastor(db, admin, acct, m)
    if err:
        return _redirect(request, back, err, "error")
    await log_admin_action(db, admin.id, "resident_pastor.appointed", "minister", m.id, {"branch": m.branch_id}, request)
    msg = f"{m.full_name} is now Resident Pastor. Department reports will be reviewed by them before reaching the Super Admin's office."
    if temp:
        msg += f" Login {acct.email} · temporary password {temp} — shown only once."
    return _redirect(request, back, msg)


@router.post("/ministers/{minister_id}/remove-resident-pastor", name="admin_ministers_remove_rp")
async def admin_ministers_remove_rp(minister_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return _redirect(request, "/admin/ministers", "Only the HQ Administrator can change a Resident Pastor.", "error")
    from app.core.branch_settings import update_branch_settings

    m = await _load_minister(db, minister_id)
    if m is None or m.account is None or m.account.role != "resident_pastor":
        return _redirect(request, "/admin/ministers", "That minister is not a Resident Pastor.", "error")
    acct = m.account
    acct.role = "hod" if acct.department_id else "resident_pastor"
    acct.session_version = (acct.session_version or 0) + 1
    if not acct.department_id:
        acct.is_active = False                    # no other role to fall back to: switch the login off, keep the record
    await update_branch_settings(db, m.branch_id, {"resident_pastor_since": ""})
    await log_admin_action(db, admin.id, "resident_pastor.removed", "minister", m.id, {"branch": m.branch_id}, request)
    return _redirect(request, f"/admin/ministers/{minister_id}", "No longer Resident Pastor. Department reports for this church go straight to the Super Admin's office again.")


@router.post("/hods/{hod_id}/make-resident-pastor", name="admin_hods_make_rp")
async def admin_hods_make_rp(hod_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """HQ Administrator promotes an existing department head to Resident Pastor of their church."""
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return _redirect(request, "/admin/hods", "Only the HQ Administrator appoints a Resident Pastor.", "error")
    from app.models.admin import Admin as AdminModel
    from app.models.minister import Minister

    acct = (await db.execute(select(AdminModel).where(AdminModel.id == hod_id, AdminModel.role == "hod"))).scalar_one_or_none()
    if acct is None:
        return _redirect(request, "/admin/hods", "Department head not found.", "error")
    m = (await db.execute(select(Minister).where(Minister.admin_id == acct.id))).scalar_one_or_none()
    if m is None:                                    # keep the record complete: every Resident Pastor is also a Minister on file
        m = Minister(branch_id=acct.branch_id or await get_hq_branch_id(db), admin_id=acct.id, full_name=acct.full_name, email=acct.email,
                     phone=acct.phone, title="Pastor", status="active", created_by=admin.id)
        db.add(m)
        await db.flush()
    err = await _appoint_resident_pastor(db, admin, acct, m)
    if err:
        return _redirect(request, "/admin/hods", err, "error")
    await log_admin_action(db, admin.id, "resident_pastor.appointed", "admin", acct.id, {"branch": m.branch_id, "from": "hod"}, request)
    return _redirect(request, f"/admin/ministers/{m.id}", f"{acct.full_name} is now Resident Pastor. Their department reports are no longer submitted by them; they review the church's reports instead.")


# ─── Announcements: HQ → branches ────────────────────────────────────
async def _announcements_for(db: AsyncSession, admin, limit: int = 50, include_hidden: bool = False) -> list:
    """Announcements this admin may read. The HQ Administrator sees all (incl. expired/hidden, when asked);
    everyone else sees active, unexpired ones addressed to all branches or to their own."""
    from app.models.announcement import Announcement

    rows = list((await db.execute(select(Announcement).order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc()).limit(500))).scalars().all())
    if is_super_role(admin.role) and include_hidden:
        return rows[:limit]
    now = datetime.utcnow()
    out = []
    for a in rows:
        if not a.is_active or (a.expires_at and a.expires_at < now):
            continue
        if a.audience == "selected" and admin.branch_id not in (a.branch_ids or []):
            if not is_super_role(admin.role):
                continue
        out.append(a)
    return out[:limit]


async def _announcement_form_page(request: Request, db: AsyncSession, admin, item):
    from app.models.branch import Branch

    branches = list((await db.execute(select(Branch).where(Branch.is_active == True, Branch.is_hq == False).order_by(Branch.name))).scalars().all())  # noqa: E712
    ctx = await _admin_context(request, db, "admin.announcements")
    return await render_template(request, "admin/announcement-form.html", {
        "_db": db, "item": item, "branches": branches,
        "title": "Announcement", "headerIcon": "ri-megaphone-line", **ctx})


def _announcement_fields(form, branch_ids_valid: set[int]) -> tuple[dict, str]:
    title = str(form.get("title", "") or "").strip()[:255]
    body = str(form.get("body", "") or "").strip()[:8000]
    audience = "selected" if str(form.get("audience", "all")) == "selected" else "all"
    picked = sorted({int(x) for x in form.getlist("branch_ids") if str(x).isdigit() and int(x) in branch_ids_valid})
    if not title or not body:
        return {}, "Give the announcement a title and a message."
    if audience == "selected" and not picked:
        return {}, "Choose at least one branch, or send it to all branches."
    exp = _parse_date(str(form.get("expires_at", "") or ""))
    return {"title": title, "body": body, "audience": audience, "branch_ids": picked if audience == "selected" else None,
            "is_pinned": bool(form.get("is_pinned")), "is_active": not bool(form.get("hidden")),
            "expires_at": datetime.combine(exp, datetime.min.time()) if exp else None}, ""


@router.get("/announcements", name="admin_announcements")
async def admin_announcements(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "announcements.view"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.branch import Branch

    can_manage = has_perm(admin.role, "announcements")
    items = await _announcements_for(db, admin, 200, include_hidden=can_manage)
    names = {b.id: b.name for b in (await db.execute(select(Branch))).scalars().all()}
    ctx = await _admin_context(request, db, "admin.announcements")
    return await render_template(request, "admin/announcements.html", {
        "_db": db, "items": items, "can_manage": can_manage, "branch_names": names, "now": datetime.utcnow(),
        "title": "Announcements", "headerIcon": "ri-megaphone-line", **ctx})


@router.get("/announcements/create", name="admin_announcements_create")
async def admin_announcements_create(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "announcements"):
        return RedirectResponse(url="/admin/announcements", status_code=303)
    return await _announcement_form_page(request, db, admin, None)


@router.post("/announcements", name="admin_announcements_store")
async def admin_announcements_store(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "announcements"):
        return RedirectResponse(url="/admin/announcements", status_code=303)
    from app.models.announcement import Announcement
    from app.models.branch import Branch

    valid = {b.id for b in (await db.execute(select(Branch))).scalars().all()}
    fields, err = _announcement_fields(await request.form(), valid)
    if err:
        return _redirect(request, "/admin/announcements/create", err, "error")
    a = Announcement(created_by=admin.id, **fields)
    db.add(a)
    await db.flush()
    await log_admin_action(db, admin.id, "announcement.created", "announcement", a.id, {"audience": a.audience}, request)
    return _redirect(request, "/admin/announcements", "Announcement published to " + ("all branches." if a.audience == "all" else f"{len(a.branch_ids or [])} branch(es)."))


async def _load_announcement(db: AsyncSession, announcement_id: int):
    from app.models.announcement import Announcement

    return (await db.execute(select(Announcement).where(Announcement.id == announcement_id))).scalar_one_or_none()


@router.get("/announcements/{announcement_id}/edit", name="admin_announcements_edit")
async def admin_announcements_edit(announcement_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "announcements"):
        return RedirectResponse(url="/admin/announcements", status_code=303)
    item = await _load_announcement(db, announcement_id)
    if item is None:
        return _redirect(request, "/admin/announcements", "Announcement not found.", "error")
    return await _announcement_form_page(request, db, admin, item)


@router.post("/announcements/{announcement_id}/update", name="admin_announcements_update")
async def admin_announcements_update(announcement_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "announcements"):
        return RedirectResponse(url="/admin/announcements", status_code=303)
    from app.models.branch import Branch

    item = await _load_announcement(db, announcement_id)
    if item is None:
        return _redirect(request, "/admin/announcements", "Announcement not found.", "error")
    valid = {b.id for b in (await db.execute(select(Branch))).scalars().all()}
    fields, err = _announcement_fields(await request.form(), valid)
    if err:
        return _redirect(request, f"/admin/announcements/{announcement_id}/edit", err, "error")
    for k, v in fields.items():
        setattr(item, k, v)
    await db.flush()
    await log_admin_action(db, admin.id, "announcement.updated", "announcement", item.id, None, request)
    return _redirect(request, "/admin/announcements", "Announcement updated.")


@router.post("/announcements/{announcement_id}/delete", name="admin_announcements_delete")
async def admin_announcements_delete(announcement_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "announcements"):
        return RedirectResponse(url="/admin/announcements", status_code=303)
    item = await _load_announcement(db, announcement_id)
    if item is None:
        return _redirect(request, "/admin/announcements", "Announcement not found.", "error")
    await db.delete(item)
    await log_admin_action(db, admin.id, "announcement.deleted", "announcement", announcement_id, None, request)
    return _redirect(request, "/admin/announcements", "Announcement removed.")


# ─── Activity: who did what, within my church ────────────────────────
@router.get("/activity", name="admin_activity")
async def admin_activity(request: Request, db: AsyncSession = Depends(get_db), q: str = Query("")):
    """A branch's own audit trail: actions by the people of THAT branch. (The HQ Administrator has the full Security Logs.)"""
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "activity"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.admin import Admin as AdminModel, AdminAuditLog

    church = admin.branch_id
    if not church:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    people = {a.id: a for a in (await db.execute(select(AdminModel).where(AdminModel.branch_id == church))).scalars().all()}
    rows = []
    if people:
        query = select(AdminAuditLog).where(AdminAuditLog.admin_id.in_(list(people))).order_by(AdminAuditLog.created_at.desc()).limit(300)
        if q.strip():
            query = query.where(AdminAuditLog.action.ilike(f"%{q.strip()}%"))
        rows = list((await db.execute(query)).scalars().all())
    ctx = await _admin_context(request, db, "admin.activity")
    return await render_template(request, "admin/activity.html", {
        "_db": db, "rows": rows, "people": people, "q": q,
        "title": "Activity", "headerIcon": "ri-history-line", **ctx})


# ─── Email campaigns (to newsletter subscribers) ─────────────────────
def _campaign_guard(admin) -> bool:
    return bool(admin) and has_perm(admin.role, "media")


async def _campaign_row(db: AsyncSession, campaign_id: int):
    from app.models.engagement import EmailCampaign

    return (await db.execute(select(EmailCampaign).where(EmailCampaign.id == campaign_id))).scalar_one_or_none()


async def _campaign_form_ctx(request: Request, db: AsyncSession, item, draft: dict | None) -> dict:
    from app.models.publication import Publication
    from app.models.sermon import Sermon

    hq = await get_hq_branch_id(db)
    messages = list((await db.execute(select(Sermon).where(Sermon.status == "published", or_(Sermon.branch_id == hq, Sermon.branch_id.is_(None)))
                                      .order_by(Sermon.published_at.desc()).limit(8))).scalars().all())
    pubs = list((await db.execute(select(Publication).where(Publication.kind == "devotionals").order_by(Publication.created_at.desc()).limit(20))).scalars().all())
    return {"item": item, "draft": draft or {}, "recent_messages": messages, "devotionals": pubs}


@router.get("/campaigns", name="admin_campaigns")
async def admin_campaigns(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import mailer
    from app.models.engagement import EmailCampaign
    from app.models.newsletter_subscriber import NewsletterSubscriber as NS

    items = list((await db.execute(select(EmailCampaign).order_by(EmailCampaign.created_at.desc()).limit(50))).scalars().all())
    active = (await db.execute(select(func.count()).select_from(NS).where(NS.is_active == True))).scalar_one()  # noqa: E712
    ctx = await _admin_context(request, db, "admin.campaigns")
    return await render_template(request, "admin/campaigns.html", {
        "_db": db, "items": items, "active_subscribers": active, "mail_ready": (await mailer.get_config()) is not None,
        "title": "Email campaigns", "headerIcon": "ri-mail-star-line", **ctx})


@router.get("/campaigns/create", name="admin_campaigns_create")
async def admin_campaigns_create(request: Request, db: AsyncSession = Depends(get_db), message_id: int = Query(0), publication_id: int = Query(0), subtopic_id: int = Query(0)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import campaigns as camp
    from app.models.publication import PublicationSource, PublicationSubtopic
    from app.models.sermon import Sermon

    base = _public_base_url(request)
    draft: dict = {}
    days: list = []
    if message_id:
        s = (await db.execute(select(Sermon).where(Sermon.id == message_id, Sermon.status == "published"))).scalar_one_or_none()
        if s is not None and (s.branch_id in (None, await get_hq_branch_id(db))):
            draft = camp.message_draft(s, base)
    if subtopic_id:
        sub = (await db.execute(select(PublicationSubtopic).where(PublicationSubtopic.id == subtopic_id))).scalar_one_or_none()
        if sub is not None:
            draft = camp.devotional_draft(sub)
    if publication_id:
        srcs = (await db.execute(select(PublicationSource).where(PublicationSource.publication_id == publication_id))).scalars().all()
        for src in srcs:
            for sub in src.subtopics:
                if sub.has_content:
                    days.append(sub)
    ctx = await _admin_context(request, db, "admin.campaigns")
    form_ctx = await _campaign_form_ctx(request, db, None, draft)
    return await render_template(request, "admin/campaign-form.html", {"_db": db, **form_ctx, "days": days[:60], "publication_id": publication_id,
                                                                     "title": "New campaign", "headerIcon": "ri-mail-star-line", **ctx})


def _campaign_fields(form) -> tuple[dict, str]:
    from app.core import campaigns as camp
    from app.core.site_content import safe_https

    subject = str(form.get("subject", "") or "").strip()[:255]
    body_text = str(form.get("body_text", "") or "").strip()[:20000]
    label = str(form.get("button_label", "") or "").strip()[:60]
    url = safe_https(str(form.get("button_url", "") or ""))
    if not subject or not body_text:
        return {}, "Give the email a subject and a message."
    if label and not url:
        return {}, "The button needs a full web address starting with https://."
    kind = str(form.get("kind", "custom"))
    return {"subject": subject, "preheader": str(form.get("preheader", "") or "").strip()[:255] or None, "body_text": body_text, "body_html": camp.text_to_html(body_text),
            "button_label": label or None, "button_url": url or None, "kind": kind if kind in ("custom", "message", "devotional") else "custom"}, ""


@router.post("/campaigns", name="admin_campaigns_store")
async def admin_campaigns_store(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.engagement import EmailCampaign

    fields, err = _campaign_fields(await request.form())
    if err:
        return _redirect(request, "/admin/campaigns/create", err, "error")
    c = EmailCampaign(created_by=admin.id, **fields)
    db.add(c)
    await db.flush()
    await log_admin_action(db, admin.id, "campaign.created", "campaign", c.id, None, request)
    return _redirect(request, f"/admin/campaigns/{c.id}", "Draft saved. Preview it, send yourself a test, then send it.")


@router.get("/campaigns/{campaign_id}", name="admin_campaigns_show")
async def admin_campaigns_show(campaign_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import mailer
    from app.core.campaigns import render_campaign
    from app.models.newsletter_subscriber import NewsletterSubscriber as NS

    c = await _campaign_row(db, campaign_id)
    if c is None:
        return _redirect(request, "/admin/campaigns", "Campaign not found.", "error")
    html, _text, _h = await render_campaign({"subject": c.subject, "preheader": c.preheader or "", "body_html": c.body_html,
                                             "button": (c.button_label, c.button_url) if c.button_label and c.button_url else None}, "preview", _public_base_url(request))
    active = (await db.execute(select(func.count()).select_from(NS).where(NS.is_active == True))).scalar_one()  # noqa: E712
    ctx = await _admin_context(request, db, "admin.campaigns")
    return await render_template(request, "admin/campaign-show.html", {
        "_db": db, "c": c, "preview_html": html, "active_subscribers": active, "mail_ready": (await mailer.get_config()) is not None,
        "title": c.subject, "headerIcon": "ri-mail-star-line", **ctx})


@router.get("/campaigns/{campaign_id}/edit", name="admin_campaigns_edit")
async def admin_campaigns_edit(campaign_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    c = await _campaign_row(db, campaign_id)
    if c is None or c.status != "draft":
        return _redirect(request, "/admin/campaigns", "Only a draft can be edited.", "error")
    ctx = await _admin_context(request, db, "admin.campaigns")
    return await render_template(request, "admin/campaign-form.html", {"_db": db, **(await _campaign_form_ctx(request, db, c, None)), "days": [], "publication_id": 0,
                                                                     "title": "Edit campaign", "headerIcon": "ri-mail-star-line", **ctx})


@router.post("/campaigns/{campaign_id}/update", name="admin_campaigns_update")
async def admin_campaigns_update(campaign_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    c = await _campaign_row(db, campaign_id)
    if c is None or c.status != "draft":
        return _redirect(request, "/admin/campaigns", "Only a draft can be edited.", "error")
    fields, err = _campaign_fields(await request.form())
    if err:
        return _redirect(request, f"/admin/campaigns/{campaign_id}/edit", err, "error")
    for k, v in fields.items():
        setattr(c, k, v)
    await db.flush()
    return _redirect(request, f"/admin/campaigns/{campaign_id}", "Draft updated.")


@router.post("/campaigns/{campaign_id}/test", name="admin_campaigns_test")
async def admin_campaigns_test(campaign_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import mailer
    from app.core.campaigns import render_campaign

    c = await _campaign_row(db, campaign_id)
    if c is None:
        return _redirect(request, "/admin/campaigns", "Campaign not found.", "error")
    html, text, headers = await render_campaign({"subject": "[TEST] " + c.subject, "preheader": c.preheader or "", "body_html": c.body_html,
                                                 "button": (c.button_label, c.button_url) if c.button_label and c.button_url else None}, "", _public_base_url(request))
    ok, msg = await mailer.send_email(admin.email, "[TEST] " + c.subject, html, text, kind="campaign_test", headers=headers or None)
    return _redirect(request, f"/admin/campaigns/{campaign_id}", f"A test was sent to {admin.email}." if ok else f"The test was not sent: {msg}", "success" if ok else "error")


@router.post("/campaigns/{campaign_id}/send", name="admin_campaigns_send")
async def admin_campaigns_send(campaign_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import mailer
    from app.models.newsletter_subscriber import NewsletterSubscriber as NS

    c = await _campaign_row(db, campaign_id)
    if c is None or c.status != "draft":
        return _redirect(request, "/admin/campaigns", "Only a draft can be sent.", "error")
    if await mailer.get_config() is None:
        return _redirect(request, f"/admin/campaigns/{campaign_id}", "Email delivery is not set up yet (Settings → Email delivery).", "error")
    total = (await db.execute(select(func.count()).select_from(NS).where(NS.is_active == True))).scalar_one()  # noqa: E712
    if not total:
        return _redirect(request, f"/admin/campaigns/{campaign_id}", "There are no active subscribers to send to.", "error")
    c.status, c.total, c.cursor, c.sent_count, c.failed_count, c.started_at = "sending", total, 0, 0, 0, datetime.utcnow()
    await log_admin_action(db, admin.id, "campaign.sent", "campaign", c.id, {"recipients": total}, request)
    return _redirect(request, f"/admin/campaigns/{campaign_id}", f"Sending to {total} subscriber(s). It goes out in the background — this page shows progress.")


@router.post("/campaigns/{campaign_id}/cancel", name="admin_campaigns_cancel")
async def admin_campaigns_cancel(campaign_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    c = await _campaign_row(db, campaign_id)
    if c is None or c.status != "sending":
        return _redirect(request, "/admin/campaigns", "That campaign is not being sent.", "error")
    c.status, c.finished_at = "cancelled", datetime.utcnow()
    return _redirect(request, f"/admin/campaigns/{campaign_id}", f"Stopped after {c.sent_count} email(s).")


@router.post("/campaigns/{campaign_id}/delete", name="admin_campaigns_delete")
async def admin_campaigns_delete(campaign_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not _campaign_guard(admin):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    c = await _campaign_row(db, campaign_id)
    if c is None or c.status == "sending":
        return _redirect(request, "/admin/campaigns", "Stop a campaign before deleting it.", "error")
    await db.delete(c)
    return _redirect(request, "/admin/campaigns", "Campaign deleted.")


# ─── Site traffic (built-in, privacy-friendly analytics) ─────────────
@router.get("/analytics", name="admin_analytics")
async def admin_analytics(request: Request, db: AsyncSession = Depends(get_db), days: int = Query(30)):
    admin = await _get_admin(request)
    if not admin or not has_perm(admin.role, "analytics"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.core import analytics

    await analytics.flush()                       # include what is still in memory
    days = days if days in (7, 30, 90, 365) else 30
    data = await analytics.summary(db, days)
    ctx = await _admin_context(request, db, "admin.analytics")
    return await render_template(request, "admin/analytics.html", {
        "_db": db, "data": data, "peak": max([p["views"] for p in data["series"]] or [1]) or 1,
        "title": "Site traffic", "headerIcon": "ri-bar-chart-2-line", **ctx})
