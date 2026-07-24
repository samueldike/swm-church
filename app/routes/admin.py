from __future__ import annotations

import csv
import io
import json
import os
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
    return RedirectResponse(url=url, status_code=303)


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

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    badges = {"members": 0, "events": 0, "inquiries": 0, "messages": 0, "unit_reports": 0}

    try:
        q = select(func.count()).select_from(Member)
        if not is_super and branch_id:
            q = q.where(Member.branch_id == branch_id)
        q = q.where(Member.created_at >= seven_days_ago)
        badges["members"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        pass

    try:
        q = select(func.count()).select_from(Event)
        if not is_super and branch_id:
            q = q.where(Event.branch_id == branch_id)
        q = q.where(Event.is_published == True, Event.starts_at >= datetime.now(timezone.utc))
        badges["events"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        pass

    try:
        q = select(func.count()).select_from(ContactInquiry).where(ContactInquiry.status == "new")
        if not is_super and branch_id:
            q = q.where(ContactInquiry.branch_id == branch_id)
        badges["inquiries"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        pass

    try:
        q = select(func.count()).select_from(Sermon).where(Sermon.status == "draft")
        if not is_super and branch_id:
            q = q.where(Sermon.branch_id == branch_id)
        badges["messages"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        pass

    try:
        q = select(func.count()).select_from(UnitReport).where(UnitReport.status == "submitted")
        if branch_id:
            q = q.where(UnitReport.branch_id == branch_id)
            q = q.where(
                or_(UnitReport.resident_pastor_comment == None, UnitReport.resident_pastor_comment == "")
            )
        badges["unit_reports"] = (await db.execute(q)).scalar_one() or 0
    except Exception:
        pass

    role_label = "Super Admin" if is_super else ("Department HOD" if is_hod else "Branch Admin")
    name = admin.full_name if hasattr(admin, "full_name") else (admin.name if hasattr(admin, "name") else "Admin")
    parts = name.split() if name else ["A"]
    initials = "".join(p[0].upper() for p in parts[:2])

    branch_name = site_name = "Scent of Water Ministry"
    try:
        settings = await get_settings(db)
        branch_name = settings.get("siteName", site_name)
    except Exception:
        pass

    try:
        from app.models.branch import Branch
        if branch_id:
            br = (await db.execute(select(Branch).where(Branch.id == branch_id))).scalar_one_or_none()
            if br:
                branch_name = br.name
    except Exception:
        pass

    critical_notifications = []
    if is_super:
        try:
            from app.models.admin import AdminAuditLog
            q = (
                select(AdminAuditLog)
                .where(AdminAuditLog.reviewed_at == None)
                .where(AdminAuditLog.created_at >= datetime.now(timezone.utc) - timedelta(days=1))
                .order_by(AdminAuditLog.created_at.desc())
                .limit(6)
            )
            critical_notifications = list((await db.execute(q)).scalars().all())
        except Exception:
            pass

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
    from app.models.branch_admin_invitation import BranchAdminInvitation

    result = await db.execute(
        select(BranchAdminInvitation)
        .where(BranchAdminInvitation.token == token)
        .where(BranchAdminInvitation.accepted_at == None)
        .where(BranchAdminInvitation.expires_at > datetime.now(timezone.utc))
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
    from app.models.branch_admin_invitation import BranchAdminInvitation
    from app.models.admin import Admin

    result = await db.execute(
        select(BranchAdminInvitation)
        .where(BranchAdminInvitation.token == token)
        .where(BranchAdminInvitation.accepted_at == None)
        .where(BranchAdminInvitation.expires_at > datetime.now(timezone.utc))
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

    invitation.accepted_at = datetime.now(timezone.utc)
    await db.flush()

    login_admin(request, admin_user.id)
    return RedirectResponse(url="/admin/dashboard", status_code=303)


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
        pass

    try:
        q = (
            select(Event)
            .where(Event.is_published == True, Event.starts_at >= datetime.now(timezone.utc))
            .order_by(Event.starts_at.asc())
            .limit(5)
        )
        if not ctx["is_super"] and admin.branch_id:
            q = q.where(Event.branch_id == admin.branch_id)
        upcoming_events = list((await db.execute(q)).scalars().all())
    except Exception:
        pass

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
        pass

    return await render_template(request, "admin/dashboard.html", {
        "_db": db,
        "title": "Dashboard",
        "headerIcon": "ri-dashboard-3-line",
        "total_members": total_members,
        "upcoming_events": upcoming_events,
        "recent_inquiries": recent_inquiries,
        "current_date": datetime.now(timezone.utc).strftime("%A, %b %d, %Y"),
        "membersCount": total_members,
        "eventsCount": len(upcoming_events),
        "newInquiries": len(recent_inquiries),
        "departmentsCount": 0,
        "branchesCount": 0,
        "sermonsCount": 0,
        "membersDelta": 0,
        "eventsDelta": 0,
        "inquiriesDelta": 0,
        "messagesDelta": 0,
        "attendanceTrend": [],
        "branchesForChart": {},
        "maxAttendance": 0,
        "weeklyAttendance": [],
        "givingTrend": [],
        "maxGiving": 0,
        "totalThisMonth": 0,
        "recentMembers": [],
        "departmentDistribution": {},
        "branchPerformance": [],
        "activityFeed": [],
        "upcomingEvents": upcoming_events,
        "recentMessages": [],
        "recentInquiries": recent_inquiries,
        "isSuper": ctx.get("is_super", False),
        "securityAlerts": [],
        **ctx,
    })


@router.get("/metrics", name="admin_dashboard_metrics")
async def admin_dashboard_metrics(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return JSONResponse({"ok": True})


# ─── Members ───────────────────────────────────────────────────────

@router.get("/members", name="admin_members")
async def admin_members(request: Request, db: AsyncSession = Depends(get_db), q: str = Query(""), page: int = Query(1, ge=1)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.member import Member
    query = select(Member)

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

    from app.models.admin import Admin as AdminModel
    result = await db.execute(select(AdminModel).where(AdminModel.role == "hod").order_by(AdminModel.created_at.desc()))
    hods = list(result.scalars().all())

    from app.models.hod_invitation import HodInvitation
    inv_result = await db.execute(
        select(HodInvitation).order_by(HodInvitation.created_at.desc()).limit(10)
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
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
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

    from app.models.harvest import HarvestWorker
    query = select(HarvestWorker).order_by(HarvestWorker.created_at.desc())
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(HarvestWorker.branch_id == admin.branch_id)
    result = await db.execute(query)
    workers = list(result.scalars().all())

    stats = {
        'total_workers': len(workers) if workers else 0,
        'active_workers': len([w for w in workers if w.is_active]) if workers else 0,
        'inactive_workers': len([w for w in workers if not w.is_active]) if workers else 0,
    }

    ctx = await _admin_context(request, db, "admin.harvesttrack")
    return await render_template(request, "admin/harvesttrack.html", {
        "_db": db, "workers": workers, "stats": stats,
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

    from app.models.admin import Admin as AdminModel
    result = await db.execute(select(AdminModel).order_by(AdminModel.created_at.desc()))
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
async def admin_events(request: Request, db: AsyncSession = Depends(get_db)):
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
        starts_at=datetime.fromisoformat(start_date) if start_date else datetime.now(timezone.utc),
        ends_at=datetime.fromisoformat(end_date) if end_date else None,
        is_major=is_major,
        is_published=True,
        branch_id=admin.branch_id,
        created_by=admin.id,
    )
    db.add(event)
    await db.flush()
    return _redirect(request, "/admin/events", "Event created successfully.")


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
    dept = Department(
        name=name.strip(),
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
    slug = name.strip().lower().replace(" ", "-")
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
    query = select(Sermon).order_by(Sermon.created_at.desc())
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
        "YouTube": "youtube",
        "Spotify": "spotify",
        "Apple Podcasts": "apple_podcasts",
        "SoundCloud": "soundcloud",
    }
    
    return await render_template(request, "admin/messages.html", {
        "_db": db, "sermons": sermons, "branches": branches, "platforms": platforms,
        "current_date_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
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
        status=status.strip() or "draft",
        branch_id=branch_id if admin.role == "super_admin" and branch_id else admin.branch_id,
        created_by=admin.id,
    )

    if published_on:
        try:
            sermon.published_at = datetime.fromisoformat(published_on)
        except ValueError:
            pass
    elif status == "published":
        sermon.published_at = datetime.now(timezone.utc)

    if audio_file and audio_file.filename:
        file_bytes = await audio_file.read()
        sermon.original_file_name = audio_file.filename
        sermon.original_file_size = len(file_bytes)

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
                sermon.status = "published"
                if not sermon.published_at:
                    sermon.published_at = datetime.now(timezone.utc)

                try:
                    from app.tasks import transcribe_audio_task
                    transcribe_audio_task.delay(sermon.id, tg_result["file_id"])
                except Exception:
                    pass

    db.add(sermon)
    await db.flush()

    flash = "Audio message created and published to Telegram." if sermon.telegram_file_id else "Audio message created."
    return _redirect(request, "/admin/messages", flash)


# ─── Inquiries ─────────────────────────────────────────────────────

@router.get("/inquiries", name="admin_inquiries")
async def admin_inquiries(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.contact_inquiry import ContactInquiry
    query = select(ContactInquiry).order_by(ContactInquiry.created_at.desc())
    if not admin.role == "super_admin" and admin.branch_id:
        query = query.where(ContactInquiry.branch_id == admin.branch_id)
    result = await db.execute(query)
    inquiries = list(result.scalars().all())

    ctx = await _admin_context(request, db, "admin.inquiries")
    return await render_template(request, "admin/inquiries.html", {
        "_db": db, "inquiries": inquiries,
        "title": "Inquiries", "headerIcon": "ri-question-answer-line", **ctx,
    })


@router.post("/inquiries", name="admin_inquiries_store")
async def admin_inquiries_store(
    request: Request, db: AsyncSession = Depends(get_db),
    inquiry_id: int = Form(...), status: str = Form(""),
):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.contact_inquiry import ContactInquiry
    result = await db.execute(select(ContactInquiry).where(ContactInquiry.id == inquiry_id))
    inquiry = result.scalar_one_or_none()
    if inquiry:
        inquiry.status = status
        await db.flush()
    return _redirect(request, "/admin/inquiries", "Inquiry updated.")


# ─── Unit Reports ──────────────────────────────────────────────────

@router.get("/unit-reports", name="admin_unit_reports")
async def admin_unit_reports(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.unit_report import UnitReport
    query = select(UnitReport).order_by(UnitReport.created_at.desc())
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

    timezones = ['Africa/Lagos', 'Africa/Accra', 'Africa/Nairobi', 'Africa/Johannesburg',
                 'Europe/London', 'Europe/Paris', 'Europe/Berlin',
                 'America/New_York', 'America/Chicago', 'America/Los_Angeles',
                 'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Dubai',
                 'Australia/Sydney', 'Pacific/Auckland', 'UTC']

    themePresets = {
        'default': {'name': 'Default Gold', 'primary': '#c9a227', 'bg': '#f5f3ee', 'text': '#1a1a1a'},
        'dark': {'name': 'Dark Mode', 'primary': '#c9a227', 'bg': '#1a1a1a', 'text': '#f5f3ee'},
        'blue': {'name': 'Royal Blue', 'primary': '#2563eb', 'bg': '#f0f4ff', 'text': '#1e293b'},
        'green': {'name': 'Nature Green', 'primary': '#16a34a', 'bg': '#f0fdf4', 'text': '#14532d'},
    }

    ctx = await _admin_context(request, db, "admin.settings")
    return await render_template(request, "admin/settings.html", {
        "_db": db, "settings": settings, "timezones": timezones,
        "themePresets": themePresets,
        "title": "Settings", "headerIcon": "ri-settings-4-line", **ctx,
    })


@router.post("/settings", name="admin_settings_update")
async def admin_settings_update(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin or admin.role != "super_admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    form = await request.form()
    settings_dict = {}
    for key in form:
        if key != "csrf_token":
            settings_dict[key] = form[key]

    await update_settings(db, settings_dict)
    return _redirect(request, "/admin/settings", "Settings updated successfully.")


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
        log_entry.reviewed_at = datetime.now(timezone.utc)
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

    from app.models.unit_report import UnitReport

    try:
        attendance_data = json.loads(attendance) if attendance else {}
        collections_data = json.loads(collections) if collections else {}
    except json.JSONDecodeError:
        return _redirect(request, "/hod/reports", "Invalid data format in attendance or collections.", "error")

    report = UnitReport(
        report_month=report_month.strip(),
        introduction=introduction.strip() if introduction else None,
        attendance=attendance_data,
        collections=collections_data,
        status="submitted",
        branch_id=admin.branch_id,
        department_id=None,
        hod_admin_id=admin.id,
    )
    db.add(report)
    await db.flush()
    return _redirect(request, "/hod/reports", "Report submitted successfully.")


@router.get("/hod/invitation/{token}", name="hod_invitation_accept_page")
async def hod_invitation_accept_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.hod_invitation import HodInvitation

    result = await db.execute(
        select(HodInvitation)
        .where(HodInvitation.token == token)
        .where(HodInvitation.accepted_at == None)
        .where(HodInvitation.expires_at > datetime.now(timezone.utc))
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
    from app.models.hod_invitation import HodInvitation
    from app.models.admin import Admin

    result = await db.execute(
        select(HodInvitation)
        .where(HodInvitation.token == token)
        .where(HodInvitation.accepted_at == None)
        .where(HodInvitation.expires_at > datetime.now(timezone.utc))
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

    invitation.accepted_at = datetime.now(timezone.utc)
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

    if published_on:
        try:
            sermon.published_at = datetime.fromisoformat(published_on)
        except ValueError:
            pass
    elif status == "published" and not sermon.published_at:
        sermon.published_at = datetime.now(timezone.utc)

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
        pass

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
        log_entry.reviewed_at = datetime.now(timezone.utc)
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
        invitation.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
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
    recent_reports = list((await db.execute(select(UnitReport).where(UnitReport.branch_id == branch_id).order_by(UnitReport.created_at.desc()).limit(5))).scalars().all())
    branch_admins = list((await db.execute(select(Admin).where(Admin.branch_id == branch_id).order_by(Admin.created_at.desc()))).scalars().all())

    activeMembers = (await db.execute(select(func.count()).select_from(Member).where(Member.branch_id == branch_id, Member.membership_status == "active"))).scalar_one() or 0
    inactiveMembers = branch_members_count - activeMembers
    memberGrowthRate = 0
    givingThisMonth = 0
    givingGrowthRate = 0
    latestAttendanceTotal = 0

    now_utc = datetime.now(timezone.utc)
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
        invitation.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        await db.flush()
    return _redirect(request, f"/admin/branches/{branch_id}", "Invitation resent successfully.")


# ─── Unit Reports: Show & Update ─────────────────────────────────

@router.get("/unit-reports/{report_id}", name="admin_unit_reports_show")
async def admin_unit_reports_show(report_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _get_admin(request)
    if not admin:
        return RedirectResponse(url="/admin", status_code=303)

    from app.models.unit_report import UnitReport
    result = await db.execute(
        select(UnitReport).where(UnitReport.id == report_id)
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

    if report_month:
        report.report_month = report_month.strip()
    report.introduction = introduction.strip() if introduction else None
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
