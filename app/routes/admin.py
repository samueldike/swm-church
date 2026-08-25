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
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.core.auth import get_current_admin, login_admin, logout_admin
from app.core.pagination import paginate
from app.core.security import hash_password, verify_password
from app.core.audit import log_admin_action
from app.core.template import render_template
from app.core.settings_cache import get_settings, update_settings
from app.core.hq_branch import get_hq_branch_id

router = APIRouter(tags=["admin"])


def _redirect(request: Request, url: str, flash_msg: str | None = None, flash_type: str = "success") -> RedirectResponse:
    if flash_msg:
        request.session["flash_messages"] = [{"type": flash_type, "message": flash_msg}]
        request.session[flash_type] = flash_msg
    resp = RedirectResponse(url=url, status_code=303)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


async def _get_admin(request: Request):
    admin = await get_current_admin(request)
    if not admin:
        return None
    return admin


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
    badges = {"members": 0, "events": 0, "inquiries": 0, "messages": 0, "unit_reports": 0}

    try:
        q = select(func.count()).select_from(Member)
        if not is_super and branch_id:
            q = q.where(Member.branch_id == branch_id)
        q = q.where(Member.created_at >= seven_days_ago)
        badges["members"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        q = select(func.count()).select_from(Event)
        if not is_super and branch_id:
            q = q.where(Event.branch_id == branch_id)
        q = q.where(Event.is_published == True, Event.starts_at >= datetime.utcnow())
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
        q = select(func.count()).select_from(Sermon).where(Sermon.status == "draft")
        if not is_super and branch_id:
            q = q.where(Sermon.branch_id == branch_id)
        badges["messages"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    try:
        q = select(func.count()).select_from(UnitReport).where(UnitReport.status == "submitted")
        if branch_id:
            q = q.where(UnitReport.branch_id == branch_id)
            q = q.where(
                or_(UnitReport.resident_pastor_comment == None, UnitReport.resident_pastor_comment == "")
            )
        badges["unit_reports"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        logging.exception("Unhandled error")

    role_label = "Super Admin" if is_super else ("Department HOD" if is_hod else "Branch Admin")
    name = admin.full_name if hasattr(admin, "full_name") else (admin.name if hasattr(admin, "name") else "Admin")
    parts = name.split() if name else ["A"]
    initials = "".join(p[0].upper() for p in parts[:2])

    branch_name = site_name = "Scent of Water Ministry"
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

    result = await db.execute(select(Admin).where(Admin.email == email.lower().strip()))
    admin = result.scalar_one_or_none()

    if not admin or not verify_password(password, admin.password_hash):
        await log_admin_action(
            db, admin_id=admin.id if admin else 0,
            action="auth.login.failed",
            request=request,
        )
        request.session["flash_error"] = "Invalid email or password."
        return RedirectResponse(url="/admin", status_code=303)

    if not admin.is_active:
        request.session["flash_error"] = "Your account has been deactivated."
        return RedirectResponse(url="/admin", status_code=303)

    login_admin(request, admin.id)

    await log_admin_action(
        db, admin_id=admin.id, action="auth.login.success",
        request=request,
    )

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

    login_admin(request, admin_user.id)
    return RedirectResponse(url="/admin/dashboard", status_code=303)


async def _giving_trend_data(db: AsyncSession):
    """Real 6-period giving trend from the `givings` table (tithe/offering/special buckets)."""
    from app.models.giving import Giving

    now_naive = datetime.utcnow()
    givingTrend: list[dict] = []
    maxGiving = 0.0
    try:
        for i in range(6):
            window_end = now_naive - timedelta(days=30 * (5 - i))
            window_start = window_end - timedelta(days=30)
            label = window_end.strftime("%b")
            q = (
                select(Giving.giving_type, func.sum(Giving.amount))
                .where(
                    Giving.status == "success",
                    Giving.paid_at >= window_start,
                    Giving.paid_at < window_end,
                )
                .group_by(Giving.giving_type)
            )
            rows = (await db.execute(q)).all()
            tithe = offering = special = 0.0
            for giving_type, total in rows:
                gt = (giving_type or "").strip().lower()
                total = float(total or 0)
                if gt == "tithe":
                    tithe += total
                elif gt == "offering":
                    offering += total
                else:
                    special += total
            givingTrend.append({"label": label, "tithe": tithe, "offering": offering, "special": special})
            maxGiving = max(maxGiving, tithe, offering, special)
    except Exception:
        logging.exception("Failed to compute giving trend")
    totalThisMonth = sum(g["tithe"] + g["offering"] + g["special"] for g in givingTrend[-1:])
    return givingTrend, maxGiving, totalThisMonth


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
            q = q.where(Event.branch_id == admin.branch_id)
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

    # Department distribution
    try:
        q = select(Department.name, func.count(Member.id).label("cnt")).join(Member, Member.branch_id == Department.id, isouter=True).group_by(Department.name).order_by(func.count(Member.id).desc())
        result = await db.execute(q)
        rows = result.all()
        total_dept_members = sum(r.cnt for r in rows) if rows else 1
        for i, row in enumerate(rows):
            pct = round(row.cnt / total_dept_members * 100) if total_dept_members else 0
            departmentDistribution[i] = {"name": row.name, "members_count": row.cnt, "pct": pct}
    except Exception:
        logging.exception("Unhandled error")

    # Branch performance
    try:
        q = select(Branch.name, func.count(Member.id).label("cnt")).join(Member, Member.branch_id == Branch.id, isouter=True).group_by(Branch.name).order_by(func.count(Member.id).desc())
        result = await db.execute(q)
        rows = result.all()
        max_branch = max((r.cnt for r in rows), default=1) if rows else 1
        for row in rows:
            bar_pct = round(row.cnt / max_branch * 100) if max_branch else 0
            branchPerformance.append({"name": row.name, "members_count": row.cnt, "growth": 0, "bar_pct": bar_pct})
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

    givingTrend, maxGiving, totalThisMonth = await _giving_trend_data(db)

    return await render_template(request, "admin/dashboard.html", {
        "_db": db,
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
        "totalThisMonth": totalThisMonth,
        "recentMembers": recentMembers,
        "departmentDistribution": departmentDistribution,
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
        branches_list = list((await db.execute(select(Branch).order_by(Branch.name))).scalars().all())
    except: branches_list = []
    palette = ["#c9a227","#34d399","#38bdf8","#a78bfa","#fb923c"]
    attendanceTrend = []; maxAttendance = 0
    try:
        for i in range(6):
            label_dt = now_naive - timedelta(days=30 * (5 - i)); label = label_dt.strftime("%b"); br_vals = {}
            for b in branches_list:
                cnt = 0
                try:
                    qr = select(func.count()).select_from(UnitReport).where(UnitReport.branch_id == b.id)
                    if sd: qr = qr.where(UnitReport.created_at >= sd)
                    if ed: qr = qr.where(UnitReport.created_at <= ed)
                    cnt = (await db.execute(qr)).scalar_one() or 0; cnt = cnt * 10
                except: cnt = 0
                br_vals[b.name] = cnt; maxAttendance = max(maxAttendance, cnt)
            attendanceTrend.append({"label": label, "branches": br_vals})
    except: pass
    weeklyAttendance = []
    try:
        for d in range(7):
            day = now_naive - timedelta(days=6 - d); label = day.strftime("%a"); att = 0
            try:
                q = select(func.count()).select_from(Member).where(func.date(Member.created_at) == day.date())
                q = _scope(q, Member); att = (await db.execute(q)).scalar_one() or 0
            except: att = 0
            cap = max(100, att * 2 or 100); pct = int(att / cap * 100) if cap else 0
            weeklyAttendance.append({"label": label, "attendance": att, "capacity": cap, "pct": pct})
    except: pass
    givingTrend, maxGiving, totalThisMonth = await _giving_trend_data(db)
    return JSONResponse({"stats": {"members": {"value": membersCount, "delta": membersDelta, "suffix": ""}, "events": {"value": eventsCount, "delta": eventsDelta, "suffix": ""}, "inquiries": {"value": inquiriesCount, "delta": inquiriesDelta, "suffix": ""}, "departments": {"value": departmentsCount, "delta": 0, "suffix": ""}, "branches": {"value": branchesCount, "delta": 0, "suffix": ""}, "messages": {"value": sermonsCount, "delta": messagesDelta, "suffix": ""}}, "attendanceTrend": attendanceTrend, "branches": [b.name for b in branches_list], "palette": palette, "maxAttendance": maxAttendance, "weeklyAttendance": weeklyAttendance, "givingTrend": givingTrend, "maxGiving": maxGiving, "totalThisMonth": totalThisMonth, "range": {"mode": mode}})


# ─── Members ───────────────────────────────────────────────────────

@router.get("/members", name="admin_members")
async def admin_members(request: Request, db: AsyncSession = Depends(get_db), q: str = Query(""), page: int = Query(1, ge=1)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    from sqlalchemy.orm import selectinload
    query = select(Member).options(selectinload(Member.branch))

    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(Member.branch_id == admin.branch_id)

    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Member.first_name.ilike(pattern), Member.last_name.ilike(pattern), Member.email.ilike(pattern)))

    query = query.order_by(Member.created_at.desc())
    pagination = await paginate(db, query, page=page, per_page=20)
    pagination["prev_url"] = f"/admin/members?q={q}&page={page - 1}" if pagination["has_prev"] else None
    pagination["next_url"] = f"/admin/members?q={q}&page={page + 1}" if pagination["has_next"] else None

    ctx = await _admin_context(request, db, "admin.members")
    return await render_template(request, "admin/members.html", {
        "_db": db, "members": pagination["items"], "pagination": pagination, "search": q,
        "title": "Members", "headerIcon": "ri-team-line", **ctx,
    })


@router.post("/members", name="admin_members_store")
async def admin_members_store(
    request: Request, db: AsyncSession = Depends(get_db),
    first_name: str = Form(...), last_name: str = Form(...),
    email: str = Form(""), phone: str = Form(""), gender: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    member = Member(
        first_name=first_name.strip(), last_name=last_name.strip(),
        email=email.strip() if email else None, phone=phone.strip() if phone else None,
        gender=gender.strip() if gender else None,
        branch_id=admin.branch_id,
    )
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
    writer.writerow(["first_name", "last_name", "email", "phone", "gender", "date_of_birth", "address"])
    writer.writerow(["John", "Doe", "john@example.com", "08012345678", "male", "1990-01-15", "123 Main St"])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=member_import_template.csv"},
    )


@router.post("/members/import", name="admin_members_import")
async def admin_members_import(
    request: Request, db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    content = (await file.read()).decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    count = 0
    for row in reader:
        member = Member(
            first_name=row.get("first_name", "").strip(),
            last_name=row.get("last_name", "").strip(),
            email=row.get("email", "").strip() or None,
            phone=row.get("phone", "").strip() or None,
            gender=row.get("gender", "").strip() or None,
            branch_id=admin.branch_id,
        )
        db.add(member)
        count += 1
    await db.flush()
    return _redirect(request, "/admin/members", f"{count} members imported successfully.")


# ─── HODs ──────────────────────────────────────────────────────────

@router.get("/hods", name="admin_hods")
async def admin_hods(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.admin import Admin as AdminModel
    result = await db.execute(
        select(AdminModel)
        .options(selectinload(AdminModel.branch), selectinload(AdminModel.department))
        .where(AdminModel.role == "hod")
        .order_by(AdminModel.created_at.desc())
    )
    hods = list(result.scalars().all())

    from app.models.hod_invitation import HodInvitation
    inv_result = await db.execute(
        select(HodInvitation)
        .options(selectinload(HodInvitation.branch), selectinload(HodInvitation.department))
        .order_by(HodInvitation.created_at.desc())
        .limit(10)
    )
    invitations = list(inv_result.scalars().all())

    from app.models.department import Department
    result = await db.execute(select(Department).order_by(Department.name))
    departments = list(result.scalars().all())

    from app.models.branch import Branch
    result = await db.execute(select(Branch).where(Branch.is_active == True).order_by(Branch.name))
    branches = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.hods")
    return await render_template(request, "admin/hods.html", {
        "_db": db, "hods": hods, "departments": departments, "branches": branches, "invitations": invitations,
        "title": "Department HODs", "headerIcon": "ri-user-star-line", **ctx,
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

    from app.models.hod_invitation import HodInvitation
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
    return _redirect(request, "/admin/hods", "HOD invitation sent successfully.")


# ─── HarvestTrack ──────────────────────────────────────────────────

@router.get("/harvesttrack", name="admin_harvesttrack")
async def admin_harvesttrack(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from sqlalchemy.orm import selectinload
    from app.models.harvest import HarvestWorker, HarvestContact
    query = (
        select(HarvestWorker)
        .options(selectinload(HarvestWorker.branch), selectinload(HarvestWorker.contacts))
        .order_by(HarvestWorker.created_at.desc())
    )
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(HarvestWorker.branch_id == admin.branch_id)
    result = await db.execute(query)
    workers = list(result.scalars().all())

    stats = {
        'total_workers': len(workers) if workers else 0,
        'active_workers': len([w for w in workers if w.is_active]) if workers else 0,
        'inactive_workers': len([w for w in workers if not w.is_active]) if workers else 0,
    }

    contacts_query = (
        select(HarvestContact)
        .options(selectinload(HarvestContact.worker))
        .order_by(HarvestContact.created_at.desc())
        .limit(20)
    )
    if not admin.role == "super_admin" and admin.branch_id:
        worker_ids = [w.id for w in workers]
        contacts_query = contacts_query.where(HarvestContact.worker_id.in_(worker_ids)) if worker_ids else contacts_query.where(False)
    contacts = list((await db.execute(contacts_query)).scalars().all())

    ctx = await _admin_context(request, db, "admin.harvesttrack")
    return await render_template(request, "admin/harvesttrack.html", {
        "_db": db, "workers": workers, "stats": stats, "contacts": contacts,
        "title": "HarvestTrack", "headerIcon": "ri-seedling-line", **ctx,
    })


@router.post("/harvesttrack/workers", name="admin_harvesttrack_workers_store")
async def admin_harvesttrack_workers_store(
    request: Request, db: AsyncSession = Depends(get_db),
    full_name: str = Form(...), email: str = Form(...),
    phone: str = Form(""), branch_id: int = Form(None),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.harvest import HarvestWorker
    from app.core.security import generate_token

    password = secrets.token_urlsafe(12)
    worker = HarvestWorker(
        full_name=full_name.strip(),
        email=email.strip().lower(),
        phone=phone.strip() if phone else None,
        branch_id=branch_id or admin.branch_id,
        password_hash=hash_password(password),
    )
    db.add(worker)
    await db.flush()
    return _redirect(request, "/admin/harvesttrack", "Worker created successfully.")


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


@router.post("/admins", name="admin_admins_store")
async def admin_admins_store(
    request: Request, db: AsyncSession = Depends(get_db),
    full_name: str = Form(...), email: str = Form(...),
    role: str = Form("branch_super_admin"), branch_id: int = Form(None),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.admin import Admin as AdminModel
    password = secrets.token_urlsafe(12)
    new_admin = AdminModel(
        full_name=full_name.strip(),
        email=email.strip().lower(),
        password_hash=hash_password(password),
        role=role,
        branch_id=branch_id,
        is_active=True,
    )
    db.add(new_admin)
    await db.flush()
    return _redirect(request, "/admin/admins", "Admin created successfully.")


# ─── Events ────────────────────────────────────────────────────────

@router.get("/events", name="admin_events")
async def admin_events(request: Request, db: AsyncSession = Depends(get_db), month: str = Query(None)):
    import calendar as cal_mod
    from datetime import date, timedelta

    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.event import Event
    query = select(Event).order_by(Event.starts_at.desc())
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(Event.branch_id == admin.branch_id)
    result = await db.execute(query)
    events = list(result.scalars().all())

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

    cal = cal_mod.Calendar(firstweekday=0)
    raw_days = cal.monthdatescalendar(currentMonth.year, currentMonth.month)
    calendarDays = [d for week in raw_days for d in week]
    today_str = date.today().isoformat()

    eventsByDate: dict[str, list] = {}
    for ev in events:
        if ev.starts_at:
            key = ev.starts_at.strftime("%Y-%m-%d")
            eventsByDate.setdefault(key, []).append(ev)

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


@router.post("/events", name="admin_events_store")
async def admin_events_store(
    request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(...), description: str = Form(""),
    location: str = Form(""), start_date: str = Form(...),
    end_date: str = Form(""), is_major: bool = Form(False),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.event import Event
    event = Event(
        title=title.strip(),
        description=description.strip() if description else None,
        location=location.strip() if location else None,
        starts_at=datetime.fromisoformat(start_date) if start_date else datetime.utcnow(),
        ends_at=datetime.fromisoformat(end_date) if end_date else None,
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
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.event import Event
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        return _redirect(request, "/admin/events", "Event not found.", "error")

    event.title = title.strip() or event.title
    event.description = description.strip() if description else None
    event.location = location.strip() if location else None
    if start_date:
        try:
            event.starts_at = datetime.fromisoformat(start_date)
        except ValueError:
            pass
    if end_date:
        try:
            event.ends_at = datetime.fromisoformat(end_date)
        except ValueError:
            pass
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

@router.get("/branches", name="admin_branches")
async def admin_branches(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.branch import Branch
    from app.models.member import Member
    from app.models.event import Event
    from app.models.sermon import Sermon
    result = await db.execute(select(Branch).order_by(Branch.name))
    branches = list(result.scalars().all())

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

    countries = {
        'Nigeria': 'NG', 'United States': 'US', 'United Kingdom': 'GB',
        'Ghana': 'GH', 'South Africa': 'ZA', 'Kenya': 'KE',
        'Canada': 'CA', 'Germany': 'DE', 'France': 'FR',
        'China': 'CN', 'Japan': 'JP', 'Australia': 'AU',
        'Brazil': 'BR', 'India': 'IN', 'Mexico': 'MX',
    }

    ctx = await _admin_context(request, db, "admin.branches")
    return await render_template(request, "admin/branches.html", {
        "_db": db, "branches": branches, "branch_counts": branch_counts, "countries": countries,
        "title": "Branches", "headerIcon": "ri-map-pin-2-line", **ctx,
    })


@router.post("/branches", name="admin_branches_store")
async def admin_branches_store(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(...), address: str = Form(""),
    phone: str = Form(""), email: str = Form(""),
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
    branch = Branch(
        name=name.strip(), slug=slug,
        address=address.strip() if address else None,
        phone=phone.strip() if phone else None,
        email=email.strip() if email else None,
    )
    db.add(branch)
    await db.flush()
    return _redirect(request, "/admin/branches", "Branch created successfully.")


# ─── Messages (Sermons) ───────────────────────────────────────────

@router.get("/messages", name="admin_messages")
async def admin_messages(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    from sqlalchemy.orm import selectinload
    query = select(Sermon).options(selectinload(Sermon.branch)).order_by(Sermon.created_at.desc())
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(Sermon.branch_id == admin.branch_id)
    result = await db.execute(query)
    sermons = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.messages")
    
    from app.models.branch import Branch
    branches_result = await db.execute(select(Branch).order_by(Branch.name))
    branches = list(branches_result.scalars().all())
    
    platforms = {
        "Telegram": "telegram",
    }
    feed_url = str(request.base_url).rstrip("/") + "/feed.xml"

    return await render_template(request, "admin/messages.html", {
        "_db": db, "sermons": sermons, "branches": branches, "platforms": platforms,
        "current_date_iso": datetime.utcnow().strftime("%Y-%m-%d"),
        "feed_url": feed_url,
        "title": "Audio Messages", "headerIcon": "ri-message-3-line", **ctx,
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
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    from app.core.telegram import TelegramDistributor
    from slugify import slugify

    speaker_val = speaker.strip() or preacher.strip() or None
    slug = slugify(title.strip())
    sermon = Sermon(
        title=title.strip(), slug=slug,
        preacher=speaker_val,
        description=description.strip() if description else None,
        caption=caption.strip() if caption else None,
        status=status.strip().lower() or "draft",
        branch_id=branch_id if admin.role == "super_admin" and branch_id else admin.branch_id,
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

    if audio_file and audio_file.filename:
        file_bytes = await audio_file.read()
        if len(file_bytes) > 50 * 1024 * 1024:
            return _redirect(request, "/admin/messages", "Audio file too large (max 50MB).", "error")
        ext = (audio_file.filename or "").rsplit(".", 1)[-1].lower() if "." in (audio_file.filename or "") else ""
        if audio_file.content_type and not audio_file.content_type.startswith("audio/") and ext not in ["mp3","wav","m4a","ogg","flac","aac","wma","mpga"]:
            return _redirect(request, "/admin/messages", "Invalid audio format.", "error")
        sermon.original_file_name = audio_file.filename
        sermon.original_file_size = len(file_bytes)

        media_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media", "sermons")
        os.makedirs(media_dir, exist_ok=True)
        stored_filename = f"{secrets.token_hex(16)}.{ext or 'mp3'}"
        with open(os.path.join(media_dir, stored_filename), "wb") as f:
            f.write(file_bytes)
        sermon.audio_file_path = f"media/sermons/{stored_filename}"
        sermon.media_url = f"/media/sermons/{stored_filename}"

        if sermon.platform_targets and "telegram" in sermon.platform_targets:
            tg = TelegramDistributor()
            if await tg.configured():
                tg_result = await tg.send_audio(
                    file_bytes=file_bytes,
                    filename=audio_file.filename,
                    title=title.strip(),
                    caption=caption.strip() if caption else title.strip(),
                )
                if tg_result:
                    sermon.telegram_file_id = tg_result.get("file_id")
                    sermon.telegram_message_id = str(tg_result.get("message_id", ""))

                    try:
                        from app.tasks import transcribe_audio_task
                        transcribe_audio_task.delay(sermon.id, tg_result["file_id"])
                    except Exception:
                        logging.exception("Unhandled error")

        if not sermon.published_at and sermon.status == "published":
            sermon.published_at = datetime.utcnow()

    db.add(sermon)
    await db.flush()

    return _redirect(request, "/admin/messages", "Audio message created successfully.")


# ─── Inquiries ─────────────────────────────────────────────────────

@router.get("/inquiries", name="admin_inquiries")
async def admin_inquiries(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.contact_inquiry import ContactInquiry
    from app.models.branch import Branch
    from sqlalchemy.orm import selectinload
    query = select(ContactInquiry).options(selectinload(ContactInquiry.branch)).order_by(ContactInquiry.created_at.desc())
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(ContactInquiry.branch_id == admin.branch_id)
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
        "title": "Inquiries", "headerIcon": "ri-question-answer-line", **ctx,
    })


@router.post("/inquiries", name="admin_inquiries_store")
async def admin_inquiries_store(
    request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(...), email: str = Form(...), phone: str = Form(""), branch_id: int | None = Form(None),
    subject: str = Form(...), message: str = Form(...), status: str = Form("new"),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)
    from app.models.contact_inquiry import ContactInquiry
    inquiry = ContactInquiry(
        name=name.strip(), email=email.strip(), phone=phone.strip() if phone else None,
        branch_id=branch_id if admin.role == "super_admin" and branch_id else admin.branch_id,
        subject=subject.strip(), message=message.strip(), status=status.strip().lower() or "new",
    )
    db.add(inquiry)
    await db.flush()
    return _redirect(request, "/admin/inquiries", "Inquiry created successfully.")


# ─── Unit Reports ──────────────────────────────────────────────────

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
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(UnitReport.branch_id == admin.branch_id)
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
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.slider import Slider
    result = await db.execute(select(Slider).order_by(Slider.sort_order.asc()))
    sliders = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.sliders")
    return await render_template(request, "admin/sliders.html", {
        "_db": db, "sliders": sliders,
        "title": "Sliders", "headerIcon": "ri-slideshow-3-line", **ctx,
    })


@router.post("/sliders", name="admin_sliders_store")
async def admin_sliders_store(
    request: Request, db: AsyncSession = Depends(get_db),
    title: str = Form(""), description: str = Form(""),
    cta_label: str = Form(""), cta_url: str = Form(""),
    sort_order: int = Form(0), is_active: bool = Form(True),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.slider import Slider
    slider = Slider(
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

    themePresets = {
        'default': {
            'name': 'Default Gold', 'description': 'Warm gold accent on a light cream background.',
            'primary': '#c9a227', 'primary_dark': '#a5831f', 'primary_light': '#e8d5a3',
            'secondary': '#1a1a1a', 'background': '#f5f3ee', 'sidebar': '#1a1814',
        },
        'dark': {
            'name': 'Dark Mode', 'description': 'Gold accent on a dark charcoal background.',
            'primary': '#c9a227', 'primary_dark': '#a5831f', 'primary_light': '#e8d5a3',
            'secondary': '#f5f3ee', 'background': '#1a1a1a', 'sidebar': '#0f0d0a',
        },
        'blue': {
            'name': 'Royal Blue', 'description': 'Cool royal blue accent for a modern look.',
            'primary': '#2563eb', 'primary_dark': '#1d4ed8', 'primary_light': '#93c5fd',
            'secondary': '#1e293b', 'background': '#f0f4ff', 'sidebar': '#0f172a',
        },
        'green': {
            'name': 'Nature Green', 'description': 'Fresh green accent inspired by nature.',
            'primary': '#16a34a', 'primary_dark': '#15803d', 'primary_light': '#86efac',
            'secondary': '#14532d', 'background': '#f0fdf4', 'sidebar': '#0f2417',
        },
    }

    ctx = await _admin_context(request, db, "admin.settings")
    feed_url = str(request.base_url).rstrip("/") + "/feed.xml"
    return await render_template(request, "admin/settings.html", {
        "_db": db, "settings": settings, "timezones": timezones,
        "themePresets": themePresets, "feed_url": feed_url,
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
    from app.core.settings_cache import LEGACY_SETTING_KEY_ALIASES
    _key_map = LEGACY_SETTING_KEY_ALIASES
    account_pattern = re.compile(r"^church_accounts\[(\d+)\]\[(bank_name|account_number|account_name)\]$")
    church_accounts: dict[int, dict] = {}
    for key in form:
        if key == "csrf_token":
            continue
        m = account_pattern.match(key)
        if m:
            idx, field = int(m.group(1)), m.group(2)
            church_accounts.setdefault(idx, {})[field] = form[key]
            continue
        db_key = _key_map.get(key, key)
        settings_dict[db_key] = form[key]

    accounts_list = [church_accounts[i] for i in sorted(church_accounts)]
    accounts_list = [
        a for a in accounts_list
        if any((a.get(f) or "").strip() for f in ("bank_name", "account_number", "account_name"))
    ]
    settings_dict["church_accounts"] = json.dumps(accounts_list)

    await update_settings(db, settings_dict)
    return _redirect(request, "/admin/settings", "Settings updated successfully.")


@router.post("/settings/pastor-photo", name="admin_settings_pastor_photo")
async def admin_settings_pastor_photo(request: Request, db: AsyncSession = Depends(get_db), photo: UploadFile = File(...)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    if not photo or not photo.filename:
        return _redirect(request, "/admin/settings", "No file selected.", "error")
    ext = os.path.splitext(photo.filename or "")[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        return _redirect(request, "/admin/settings", "Invalid image format. Use JPG/PNG/WEBP/GIF.", "error")
    content = await photo.read()
    if len(content) > 5 * 1024 * 1024:
        return _redirect(request, "/admin/settings", "Image too large (max 5MB).", "error")
    import uuid
    filename = f"pastor-{uuid.uuid4()}{ext}"
    dest_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images", "pastor")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    with open(dest, "wb") as f:
        f.write(content)
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
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        return _redirect(request, "/admin/settings", "Invalid image format.", "error")
    content = await image.read()
    if len(content) > 5 * 1024 * 1024:
        return _redirect(request, "/admin/settings", "Image too large (max 5MB).", "error")
    import uuid
    filename = f"hero-{page_key}-{uuid.uuid4()}{ext}"
    dest_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images", "heroes")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    with open(dest, "wb") as f:
        f.write(content)
    setting_key = f"pageHero_{page_key}" if page_key != "default" else "defaultPageHero"
    await update_settings(db, {setting_key: f"images/heroes/{filename}"})
    return _redirect(request, "/admin/settings", f"Hero image for {page_key} updated successfully.")


# ─── Security Logs ─────────────────────────────────────────────────

@router.get("/security-logs", name="admin_security_logs")
async def admin_security_logs(
    request: Request, db: AsyncSession = Depends(get_db),
    reviewed: str = Query(""), severity: str = Query(""),
    page: int = Query(1, ge=1),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.admin import AdminAuditLog
    query = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())

    if reviewed == "no":
        query = query.where(AdminAuditLog.reviewed_at == None)

    pagination = await paginate(db, query, page=page, per_page=25)
    pagination["prev_url"] = f"/admin/security-logs?page={page - 1}" if pagination["has_prev"] else None
    pagination["next_url"] = f"/admin/security-logs?page={page + 1}" if pagination["has_next"] else None

    ctx = await _admin_context(request, db, "admin.security-logs")

    from app.models.admin import Admin as AdminModel
    admins_result = await db.execute(select(AdminModel).where(AdminModel.is_active == True).order_by(AdminModel.full_name))
    admins_list = list(admins_result.scalars().all())

    return await render_template(request, "admin/security-logs.html", {
        "_db": db, "logs": pagination["items"], "pagination": pagination,
        "admins": admins_list,
        "title": "Security Logs", "headerIcon": "ri-shield-keyhole-line", **ctx,
    })


@router.post("/security-logs/bulk-review", name="admin_security_logs_bulk_review")
async def admin_security_logs_bulk_review(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.admin import AdminAuditLog
    result = await db.execute(
        select(AdminAuditLog).where(AdminAuditLog.reviewed_at == None)
    )
    logs = result.scalars().all()
    for log_entry in logs:
        log_entry.reviewed_at = datetime.utcnow()
    await db.flush()
    return _redirect(request, "/admin/security-logs", f"{len(logs)} logs marked as reviewed.")


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
    full_name: str = Form(...), email: str = Form(...),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    admin.full_name = full_name.strip()
    admin.email = email.strip().lower()
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

    admin.password_hash = hash_password(password)
    await db.flush()
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
        "title": "Monthly Reports", "headerIcon": "ri-file-chart-line", **ctx,
    })


@router.get("/hod/reports/create", name="hod_reports_create")
async def hod_reports_create(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    ctx = await _admin_context(request, db, "hod.reports")
    return await render_template(request, "hod/reports/create.html", {
        "_db": db, "report": None, "hod": admin,
        "title": "Create Report", "headerIcon": "ri-file-chart-line", **ctx,
    })


@router.post("/hod/reports", name="hod_reports_store")
async def hod_reports_store(
    request: Request, db: AsyncSession = Depends(get_db),
    report_month: str = Form(...), introduction: str = Form(""),
    attendance: str = Form("{}"), collections: str = Form("{}"),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    if not introduction or not introduction.strip():
        return _redirect(request, "/hod/reports", "Please add an introduction before submitting the report.", "error")

    from app.models.unit_report import UnitReport

    try:
        attendance_data = json.loads(attendance) if attendance else {}
        collections_data = json.loads(collections) if collections else {}
    except json.JSONDecodeError:
        return _redirect(request, "/hod/reports", "Invalid data format in attendance or collections.", "error")

    report = UnitReport(
        report_month=report_month.strip(),
        introduction=introduction.strip(),
        attendance=attendance_data,
        collections=collections_data,
        status="submitted",
        branch_id=admin.branch_id,
        department_id=admin.department_id,
        hod_admin_id=admin.id,
    )
    db.add(report)
    await db.flush()
    return _redirect(request, "/hod/reports", "Report submitted successfully.")


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
    return RedirectResponse(url="/hod/reports", status_code=303)


# ─── Members: Update & Destroy ────────────────────────────────────

@router.post("/members/{member_id}/update", name="admin_members_update")
async def admin_members_update(
    member_id: int, request: Request, db: AsyncSession = Depends(get_db),
    first_name: str = Form(""), last_name: str = Form(""),
    email: str = Form(""), phone: str = Form(""),
    gender: str = Form(""), membership_status: str = Form("active"),
    date_of_birth: str = Form(""), joined_at: str = Form(""),
    notes: str = Form(""), branch_id: int = Form(None),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        return _redirect(request, "/admin/members", "Member not found.", "error")

    member.first_name = first_name.strip() or member.first_name
    member.last_name = last_name.strip() or member.last_name
    member.email = email.strip() if email else None
    member.phone = phone.strip() if phone else None
    member.gender = gender.strip() if gender else None
    member.membership_status = membership_status.strip() or "active"
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

    await db.delete(member)
    await db.flush()
    return _redirect(request, "/admin/members", "Member deleted successfully.")


# ─── Admins: Update & Reset Password ─────────────────────────────

@router.post("/admins/{admin_id}/update", name="admin_admins_update")
async def admin_admins_update(
    admin_id: int, request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), phone: str = Form(""),
    role: str = Form("branch_super_admin"),
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

    target.full_name = name.strip() or target.full_name
    target.role = role.strip() or target.role
    target.is_active = is_active
    target.branch_id = branch_id if branch_id else None
    await db.flush()
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
    await db.flush()
    return _redirect(request, "/admin/admins", "Password reset successfully.")


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
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.sermon import Sermon
    result = await db.execute(select(Sermon).where(Sermon.id == sermon_id))
    sermon = result.scalar_one_or_none()
    if not sermon:
        return _redirect(request, "/admin/messages", "Sermon not found.", "error")

    sermon.title = title.strip() or sermon.title
    sermon.preacher = speaker.strip() if speaker else sermon.preacher
    sermon.caption = caption.strip() if caption else sermon.caption
    sermon.description = summary.strip() if summary else sermon.description
    sermon.status = status.strip() or sermon.status
    sermon.platform_targets = [p for p in platform_targets if p] or None

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

    await db.delete(sermon)
    await db.flush()
    return _redirect(request, "/admin/messages", "Audio message deleted successfully.")


# ─── Transcriptions ────────────────────────────────────────────────

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

    if not sermon.telegram_file_id:
        return _redirect(request, "/admin/transcriptions", "No Telegram audio file found for this sermon.", "error")

    sermon.transcription_status = "queued"
    sermon.transcript = None
    await db.flush()

    try:
        from app.tasks import transcribe_audio_task
        transcribe_audio_task.delay(sermon.id, sermon.telegram_file_id)
    except Exception:
        logging.exception("Unhandled error")

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
    if not admin or admin.role != "super_admin":
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
        import uuid
        ext = os.path.splitext(image.filename)[1] or ".jpg"
        filename = f"slide-{uuid.uuid4()}{ext}"
        dest_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "images", "sliders")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, filename)
        content = await image.read()
        with open(dest, "wb") as f:
            f.write(content)
        slider.image_path = f"images/sliders/{filename}"

    await db.flush()
    return _redirect(request, "/admin/sliders", "Slider updated successfully.")


@router.post("/sliders/{slider_id}/destroy", name="admin_sliders_destroy")
async def admin_sliders_destroy(slider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
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

    await db.flush()
    return _redirect(request, "/admin/hods", "HOD updated successfully.")


@router.post("/hod-invitations/{invitation_id}/resend", name="admin_hod_invitations_resend")
async def admin_hod_invitations_resend(invitation_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.hod_invitation import HodInvitation
    result = await db.execute(select(HodInvitation).where(HodInvitation.id == invitation_id))
    invitation = result.scalar_one_or_none()
    if invitation:
        invitation.expires_at = datetime.utcnow() + timedelta(days=7)
        await db.flush()
    return _redirect(request, "/admin/hods", "Invitation resent successfully.")


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
        return _redirect(request, "/admin/branches", "Branch not found.", "error")

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
    recent_reports = list((await db.execute(select(UnitReport).options(selectinload(UnitReport.department)).where(UnitReport.branch_id == branch_id).order_by(UnitReport.created_at.desc()).limit(5))).scalars().all())
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
    latestReport = latestReportResult.scalar_one_or_none()

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

    countries = {
        'Nigeria': 'NG', 'United States': 'US', 'United Kingdom': 'GB',
        'Ghana': 'GH', 'South Africa': 'ZA', 'Kenya': 'KE',
        'Canada': 'CA', 'Germany': 'DE', 'France': 'FR',
    }

    ctx = await _admin_context(request, db, "admin.branches")
    return await render_template(request, "admin/branch.html", {
        "_db": db, "branch": branch, "invitations": invitations,
        "branch_members_count": branch_members_count, "branch_events_count": branch_events_count,
        "branch_sermons_count": branch_sermons_count, "branch_inquiries_count": branch_inquiries_count,
        "departments": departments,
        "recent_inquiries": recent_inquiries, "recent_reports": recent_reports,
        "admins": branch_admins,
        "activeMembers": activeMembers, "inactiveMembers": inactiveMembers,
        "memberGrowthRate": memberGrowthRate, "givingGrowthRate": givingGrowthRate,
        "givingThisMonth": givingThisMonth, "latestAttendanceTotal": latestAttendanceTotal,
        "upcomingEvents": upcomingEvents, "latestReport": latestReport,
        "eventStatusCounts": eventStatusCounts, "inquiryStatusCounts": inquiryStatusCounts,
        "sermonStatusCounts": sermonStatusCounts,
        "trend": trend, "maxTrendAttendance": maxTrendAttendance, "maxTrendGiving": maxTrendGiving,
        "departmentOverview": departmentOverview, "recentMembers": recentMembers,
        "countries": countries,
        "title": branch.name, "headerIcon": "ri-map-pin-2-line", **ctx,
    })


@router.post("/branches/{branch_id}/update", name="admin_branch_update")
async def admin_branch_update(
    branch_id: int, request: Request, db: AsyncSession = Depends(get_db),
    name: str = Form(""), address: str = Form(""),
    phone: str = Form(""), email: str = Form(""),
    city: str = Form(""), state: str = Form(""),
    country: str = Form(""), is_active: bool = Form(True),
):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    from app.models.branch import Branch
    result = await db.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if not branch:
        return _redirect(request, "/admin/branches", "Branch not found.", "error")

    branch.name = name.strip() or branch.name
    branch.address = address.strip() if address else branch.address
    branch.phone = phone.strip() if phone else branch.phone
    branch.email = email.strip() if email else branch.email
    branch.city = city.strip() if city else branch.city
    branch.state = state.strip() if state else branch.state
    branch.country = country.strip() if country else branch.country
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
    return _redirect(request, f"/admin/branches/{branch_id}", "Invitation resent successfully.")


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
    if not unit_report:
        return _redirect(request, "/admin/unit-reports", "Report not found.", "error")

    can_review = admin.role == "super_admin" or (admin.branch_id and admin.branch_id == unit_report.branch_id)
    review_help = "Review this monthly report and leave your comments." if can_review else "You can view this report but only the assigned admin can review it."

    ctx = await _admin_context(request, db, "admin.unit-reports")
    return await render_template(request, "admin/unit-report-show.html", {
        "_db": db, "unitReport": unit_report,
        "canReviewHodReport": can_review,
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

    from app.models.unit_report import UnitReport
    result = await db.execute(select(UnitReport).where(UnitReport.id == report_id))
    unit_report = result.scalar_one_or_none()
    if not unit_report:
        return _redirect(request, "/admin/unit-reports", "Report not found.", "error")

    can_review = admin.role == "super_admin" or (admin.branch_id and admin.branch_id == unit_report.branch_id)
    if not can_review:
        return _redirect(request, "/admin/unit-reports", "You do not have permission to review this report.", "error")

    if resident_pastor_comment:
        unit_report.resident_pastor_comment = resident_pastor_comment.strip()
    if senior_pastor_comment:
        unit_report.senior_pastor_comment = senior_pastor_comment.strip()
    if status:
        unit_report.status = status.strip()

    await db.flush()
    return _redirect(request, f"/admin/unit-reports/{report_id}", "Report updated successfully.")


# ─── HOD Reports: Create, Store, Update ──────────────────────────


@router.post("/hod/reports/{report_id}/update", name="hod_reports_update")
async def admin_hod_reports_update(
    report_id: int, request: Request, db: AsyncSession = Depends(get_db),
    report_month: str = Form(""), introduction: str = Form(""),
    attendance: str = Form("{}"), collections: str = Form("{}"),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.unit_report import UnitReport

    result = await db.execute(select(UnitReport).where(UnitReport.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        return _redirect(request, "/hod/reports", "Report not found.", "error")

    if not introduction or not introduction.strip():
        return _redirect(request, "/hod/reports", "Please add an introduction before saving the report.", "error")

    if report_month:
        report.report_month = report_month.strip()
    report.introduction = introduction.strip()
    try:
        if attendance:
            report.attendance = json.loads(attendance)
        if collections:
            report.collections = json.loads(collections)
    except json.JSONDecodeError:
        return _redirect(request, "/hod/reports", "Invalid data format in attendance or collections.", "error")

    await db.flush()
    return _redirect(request, "/hod/reports", "Report updated successfully.")


@router.get("/hod/reports/{report_id}/edit", name="hod_reports_edit")
async def admin_hod_reports_edit(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.unit_report import UnitReport
    result = await db.execute(select(UnitReport).where(UnitReport.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        return _redirect(request, "/hod/reports", "Report not found.", "error")

    ctx = await _admin_context(request, db, "hod.reports")
    return await render_template(request, "hod/reports/create.html", {
        "_db": db, "report": report, "hod": admin,
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
@router.post("/stories", name="admin_stories_store")
async def admin_stories_store(request: Request, db: AsyncSession = Depends(get_db), title: str = Form(...), excerpt: str = Form(""), content: str = Form(""), category: str = Form(""), author_name: str = Form(""), status: str = Form("draft"), is_featured: str | None = Form(None), image: UploadFile | None = File(None)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.story import Story
    from slugify import slugify
    slug = await _unique_story_slug(db, slugify(title.strip()))
    feat = is_featured in ("1","true","on","True")
    st = status.strip().lower() or "draft"
    story = Story(title=title.strip(), slug=slug, excerpt=excerpt.strip() if excerpt else None, content=content.strip() if content else None, category=category.strip() if category else None, author_name=author_name.strip() if author_name else None, status=st, is_featured=feat, created_by=admin.id)
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
    if not admin or admin.role != "super_admin":
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
    story.category = category.strip() if category else None
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
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    from app.models.story import Story
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        return _redirect(request, "/admin/stories", "Story not found.", "error")
    await db.delete(story)
    await db.flush()
    return _redirect(request, "/admin/stories", "Story deleted successfully.")
