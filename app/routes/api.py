from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.audit import log_admin_action
from app.core.auth import get_current_admin
from app.core.pagination import paginate
from app.core.security import (
    generate_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.database import get_db

router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


def _json(data: dict | list, status: int = 200) -> JSONResponse:
    return JSONResponse(content=data, status_code=status)


def _error(message: str, status: int = 400) -> JSONResponse:
    return _json({"error": message}, status=status)


async def _get_event_or_404(db: AsyncSession, event_id: int):
    from app.models.event import Event

    result = await db.execute(select(Event).where(Event.id == event_id))
    return result.scalar_one_or_none()


async def _require_harvest_worker(request: Request, db: AsyncSession):
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]
    token_hash = hash_token(token)

    from app.models.harvest import HarvestWorker, HarvestWorkerToken

    result = await db.execute(
        select(HarvestWorkerToken).where(
            HarvestWorkerToken.token_hash == token_hash,
            HarvestWorkerToken.expires_at > datetime.now(timezone.utc),
        )
    )
    token_row = result.scalar_one_or_none()
    if not token_row:
        return None

    result = await db.execute(
        select(HarvestWorker).where(
            HarvestWorker.id == token_row.worker_id,
            HarvestWorker.is_active == True,
        )
    )
    return result.scalar_one_or_none()


def _serialize_worker(worker) -> dict:
    return {
        "id": worker.id,
        "full_name": worker.full_name,
        "email": worker.email,
        "phone": worker.phone,
        "branch_id": worker.branch_id,
        "is_active": worker.is_active,
        "created_at": worker.created_at.isoformat() if worker.created_at else None,
    }


def _serialize_contact(contact, include_follow_ups: bool = False) -> dict:
    data = {
        "id": contact.id,
        "worker_id": contact.worker_id,
        "full_name": contact.full_name,
        "email": contact.email,
        "phone": contact.phone,
        "address": contact.address,
        "notes": contact.notes,
        "status": contact.status,
        "created_at": contact.created_at.isoformat() if contact.created_at else None,
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
    }
    if include_follow_ups:
        data["follow_ups"] = [
            {
                "id": fu.id,
                "follow_type": fu.follow_type,
                "notes": fu.notes,
                "created_at": fu.created_at.isoformat() if fu.created_at else None,
            }
            for fu in (contact.follow_ups or [])
        ]
    return data


def _serialize_reminder(reminder) -> dict:
    return {
        "id": reminder.id,
        "worker_id": reminder.worker_id,
        "contact_id": reminder.contact_id,
        "title": reminder.title,
        "notes": reminder.notes,
        "due_at": reminder.due_at.isoformat() if reminder.due_at else None,
        "is_completed": reminder.is_completed,
        "created_at": reminder.created_at.isoformat() if reminder.created_at else None,
        "updated_at": reminder.updated_at.isoformat() if reminder.updated_at else None,
    }


def _serialize_event(event, calendar: bool = False) -> dict:
    data = {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "location": event.location,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "is_published": event.is_published,
        "is_major": event.is_major,
        "cover_image": event.cover_image,
        "branch_id": event.branch_id,
        "created_by": event.created_by,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    }
    if calendar:
        data = {
            "id": event.id,
            "title": event.title,
            "description": event.description or '',
            "starts_at": event.starts_at.isoformat() if event.starts_at else None,
            "ends_at": event.ends_at.isoformat() if event.ends_at else None,
            "location": event.location,
            "is_major": event.is_major,
            "is_published": event.is_published,
        }
    return data


# ===========================================================================
# EVENTS API
# ===========================================================================

@router.get("/events/calendar")
async def events_calendar(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start: str | None = Query(None, description="ISO start date filter"),
    end: str | None = Query(None, description="ISO end date filter"),
):
    from app.models.event import Event

    admin = await get_current_admin(request)

    query = select(Event)

    if admin:
        pass
    else:
        query = query.where(Event.is_published == True)

    if start:
        try:
            start_dt = datetime.fromisoformat(start)
            query = query.where(Event.starts_at >= start_dt)
        except ValueError:
            pass

    if end:
        try:
            end_dt = datetime.fromisoformat(end)
            query = query.where(Event.starts_at <= end_dt)
        except ValueError:
            pass

    query = query.order_by(Event.starts_at.asc())

    result = await db.execute(query)
    events = result.scalars().all()

    return _json([_serialize_event(e, calendar=True) for e in events])


@router.get("/events")
async def list_events(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query("", alias="search"),
    status: str = Query("", alias="status"),
):
    from app.models.event import Event

    admin = await get_current_admin(request)

    query = select(Event)

    if admin:
        if status == "published":
            query = query.where(Event.is_published == True)
        elif status == "draft":
            query = query.where(Event.is_published == False)
    else:
        query = query.where(Event.is_published == True)

    if search:
        pattern = f"%{search}%"
        query = query.where(Event.title.ilike(pattern))

    query = query.order_by(Event.starts_at.desc())

    pagination = await paginate(db, query, page=page, per_page=per_page)
    events = pagination["items"]

    return _json({
        "items": [_serialize_event(e) for e in events],
        "page": pagination["page"],
        "per_page": pagination["per_page"],
        "total": pagination["total"],
        "pages": pagination["pages"],
        "has_prev": pagination["has_prev"],
        "has_next": pagination["has_next"],
    })


@router.get("/events/{event_id}")
async def get_event(event_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.event import Event

    admin = await get_current_admin(request)

    query = select(Event).where(Event.id == event_id)
    if not admin:
        query = query.where(Event.is_published == True)

    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        return _error("Event not found", 404)

    return _json(_serialize_event(event))


@router.post("/events")
async def create_event(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await get_current_admin(request)
    if not admin:
        return _error("Authentication required", 403)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    title = (body.get("title") or "").strip()
    if not title:
        return _error("Title is required")

    starts_at = body.get("starts_at")
    if not starts_at:
        return _error("starts_at is required")

    try:
        starts_at_dt = datetime.fromisoformat(starts_at)
    except (ValueError, TypeError):
        return _error("Invalid starts_at format. Use ISO 8601.")

    ends_at_dt = None
    if body.get("ends_at"):
        try:
            ends_at_dt = datetime.fromisoformat(body["ends_at"])
        except (ValueError, TypeError):
            return _error("Invalid ends_at format. Use ISO 8601.")

    from app.models.event import Event

    event = Event(
        title=title,
        description=body.get("description", ""),
        location=body.get("location", ""),
        starts_at=starts_at_dt,
        ends_at=ends_at_dt,
        is_published=body.get("is_published", False),
        is_major=body.get("is_major", False),
        cover_image=body.get("cover_image"),
        branch_id=body.get("branch_id"),
        created_by=admin.id,
    )
    db.add(event)
    await db.flush()

    await log_admin_action(
        db, admin.id, "event_create", "event", event.id, {"title": title}, request
    )

    return _json(_serialize_event(event), 201)


@router.put("/events/{event_id}")
async def update_event(event_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await get_current_admin(request)
    if not admin:
        return _error("Authentication required", 403)

    event = await _get_event_or_404(db, event_id)
    if not event:
        return _error("Event not found", 404)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    if "title" in body:
        title = (body["title"] or "").strip()
        if not title:
            return _error("Title cannot be empty")
        event.title = title

    if "starts_at" in body and body["starts_at"]:
        try:
            event.starts_at = datetime.fromisoformat(body["starts_at"])
        except (ValueError, TypeError):
            return _error("Invalid starts_at format")

    if "ends_at" in body:
        if body["ends_at"]:
            try:
                event.ends_at = datetime.fromisoformat(body["ends_at"])
            except (ValueError, TypeError):
                return _error("Invalid ends_at format")
        else:
            event.ends_at = None

    for field in ("description", "location", "cover_image"):
        if field in body:
            setattr(event, field, body[field])

    if "branch_id" in body:
        event.branch_id = body["branch_id"]

    for bool_field in ("is_published", "is_major"):
        if bool_field in body:
            setattr(event, bool_field, bool(body[bool_field]))

    await db.flush()

    await log_admin_action(
        db, admin.id, "event_update", "event", event.id, request=request
    )

    return _json(_serialize_event(event))


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await get_current_admin(request)
    if not admin:
        return _error("Authentication required", 403)

    event = await _get_event_or_404(db, event_id)
    if not event:
        return _error("Event not found", 404)

    title = event.title
    await db.delete(event)
    await db.flush()

    await log_admin_action(
        db, admin.id, "event_delete", "event", event_id, {"title": title}, request
    )

    return _json({"message": "Event deleted"})


# ===========================================================================
# HARVESTTRACK API
# ===========================================================================

@router.post("/harvesttrack/register")
async def ht_register(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.harvest import HarvestWorker

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    from app.core import throttle

    reg_key = f"ht-register:{_client_ip(request)}"
    if throttle.is_blocked(reg_key, limit=10, window=3600):
        return _error("Too many registrations from this address. Try again later.", 429)
    throttle.record_failure(reg_key, window=3600)

    full_name = (body.get("full_name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not full_name:
        return _error("Full name is required")
    if len(full_name) > 255 or len(email) > 255 or "@" not in email:
        return _error("Please provide a valid name and email")
    if not email:
        return _error("Email is required")
    if len(password) < 6:
        return _error("Password must be at least 6 characters")

    existing = await db.execute(
        select(HarvestWorker).where(HarvestWorker.email == email)
    )
    if existing.scalar_one_or_none():
        return _error("An account with this email already exists", 409)

    worker = HarvestWorker(
        full_name=full_name,
        email=email,
        phone=(body.get("phone") or "").strip() or None,
        password_hash=hash_password(password),
        branch_id=body.get("branch_id"),
    )
    db.add(worker)
    await db.flush()

    return _json(_serialize_worker(worker), 201)


@router.post("/harvesttrack/login")
async def ht_login(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.harvest import HarvestWorker, HarvestWorkerToken

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return _error("Email and password are required")

    from app.core import throttle

    keys = (f"ht-login:ip:{_client_ip(request)}", f"ht-login:email:{email}")
    if any(throttle.is_blocked(k, limit=10 if "ip" in k else 5) for k in keys):
        return _error("Too many failed attempts. Try again in a few minutes.", 429)

    result = await db.execute(
        select(HarvestWorker).where(HarvestWorker.email == email)
    )
    worker = result.scalar_one_or_none()

    if not worker or not verify_password(password, worker.password_hash):
        for k in keys:
            throttle.record_failure(k)
        return _error("Invalid email or password", 401)
    for k in keys:
        throttle.clear(k)

    if not worker.is_active:
        return _error("Account is deactivated", 403)

    plain_token = generate_token(80)
    token_hash_val = hash_token(plain_token)

    token_record = HarvestWorkerToken(
        worker_id=worker.id,
        token_hash=token_hash_val,
        expires_at=datetime.now(timezone.utc) + timedelta(days=90),
    )
    db.add(token_record)
    await db.flush()

    return _json({
        "token": plain_token,
        "worker": _serialize_worker(worker),
    })


@router.get("/harvesttrack/me")
async def ht_me(request: Request, db: AsyncSession = Depends(get_db)):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    return _json({"worker": _serialize_worker(worker)})


@router.post("/harvesttrack/logout")
async def ht_logout(request: Request, db: AsyncSession = Depends(get_db)):
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return _error("Invalid or missing token", 401)

    token = auth_header[7:]
    token_hash_val = hash_token(token)

    from app.models.harvest import HarvestWorkerToken

    result = await db.execute(
        select(HarvestWorkerToken).where(
            HarvestWorkerToken.token_hash == token_hash_val,
        )
    )
    token_record = result.scalar_one_or_none()

    if token_record:
        await db.delete(token_record)
        await db.flush()

    return _json({"message": "Logged out successfully"})


@router.get("/harvesttrack/dashboard")
async def ht_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    from app.models.harvest import HarvestContact, HarvestReminder

    total_contacts_result = await db.execute(
        select(func.count()).select_from(HarvestContact).where(
            HarvestContact.worker_id == worker.id
        )
    )
    total_contacts = total_contacts_result.scalar_one() or 0

    active_contacts_result = await db.execute(
        select(func.count()).select_from(HarvestContact).where(
            HarvestContact.worker_id == worker.id,
            HarvestContact.status == "active",
        )
    )
    active_contacts = active_contacts_result.scalar_one() or 0

    ended_contacts_result = await db.execute(
        select(func.count()).select_from(HarvestContact).where(
            HarvestContact.worker_id == worker.id,
            HarvestContact.status == "ended",
        )
    )
    ended_contacts = ended_contacts_result.scalar_one() or 0

    pending_reminders_result = await db.execute(
        select(func.count()).select_from(HarvestReminder).where(
            HarvestReminder.worker_id == worker.id,
            HarvestReminder.is_completed == False,
            HarvestReminder.due_at >= datetime.now(timezone.utc),
        )
    )
    pending_reminders = pending_reminders_result.scalar_one() or 0

    overdue_reminders_result = await db.execute(
        select(func.count()).select_from(HarvestReminder).where(
            HarvestReminder.worker_id == worker.id,
            HarvestReminder.is_completed == False,
            HarvestReminder.due_at < datetime.now(timezone.utc),
        )
    )
    overdue_reminders = overdue_reminders_result.scalar_one() or 0

    recent_contacts_result = await db.execute(
        select(HarvestContact)
        .where(HarvestContact.worker_id == worker.id)
        .order_by(HarvestContact.created_at.desc())
        .limit(5)
    )
    recent_contacts = recent_contacts_result.scalars().all()

    upcoming_reminders_result = await db.execute(
        select(HarvestReminder)
        .where(
            HarvestReminder.worker_id == worker.id,
            HarvestReminder.is_completed == False,
            HarvestReminder.due_at >= datetime.now(timezone.utc),
        )
        .order_by(HarvestReminder.due_at.asc())
        .limit(5)
    )
    upcoming_reminders = upcoming_reminders_result.scalars().all()

    return _json({
        "stats": {
            "total_contacts": total_contacts,
            "active_contacts": active_contacts,
            "ended_contacts": ended_contacts,
            "pending_reminders": pending_reminders,
            "overdue_reminders": overdue_reminders,
        },
        "recent_contacts": [_serialize_contact(c) for c in recent_contacts],
        "upcoming_reminders": [_serialize_reminder(r) for r in upcoming_reminders],
    })


@router.get("/harvesttrack/contacts")
async def ht_list_contacts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    status: str = Query(""),
):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    from app.models.harvest import HarvestContact

    query = select(HarvestContact).where(HarvestContact.worker_id == worker.id)

    if status:
        query = query.where(HarvestContact.status == status)
    else:
        query = query.where(HarvestContact.status != "ended")

    if search:
        pattern = f"%{search}%"
        query = query.where(
            HarvestContact.full_name.ilike(pattern)
            | HarvestContact.email.ilike(pattern)
            | HarvestContact.phone.ilike(pattern)
        )

    query = query.order_by(HarvestContact.created_at.desc())

    pagination = await paginate(db, query, page=page, per_page=per_page)
    contacts = pagination["items"]

    return _json({
        "items": [_serialize_contact(c) for c in contacts],
        "page": pagination["page"],
        "per_page": pagination["per_page"],
        "total": pagination["total"],
        "pages": pagination["pages"],
        "has_prev": pagination["has_prev"],
        "has_next": pagination["has_next"],
    })


@router.post("/harvesttrack/contacts")
async def ht_create_contact(request: Request, db: AsyncSession = Depends(get_db)):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    full_name = (body.get("full_name") or "").strip()
    if not full_name:
        return _error("Full name is required")

    from app.models.harvest import HarvestContact

    contact = HarvestContact(
        worker_id=worker.id,
        full_name=full_name,
        email=(body.get("email") or "").strip() or None,
        phone=(body.get("phone") or "").strip() or None,
        address=(body.get("address") or "").strip() or None,
        notes=(body.get("notes") or "").strip() or None,
        status="active",
    )
    db.add(contact)
    await db.flush()

    return _json(_serialize_contact(contact), 201)


@router.get("/harvesttrack/contacts/{contact_id}")
async def ht_get_contact(
    contact_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    from app.models.harvest import HarvestContact

    result = await db.execute(
        select(HarvestContact)
        .options(selectinload(HarvestContact.follow_ups))
        .where(
            HarvestContact.id == contact_id,
            HarvestContact.worker_id == worker.id,
        )
    )
    contact = result.scalar_one_or_none()

    if not contact:
        return _error("Contact not found", 404)

    return _json(_serialize_contact(contact, include_follow_ups=True))


@router.put("/harvesttrack/contacts/{contact_id}")
async def ht_update_contact(
    contact_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    from app.models.harvest import HarvestContact

    result = await db.execute(
        select(HarvestContact).where(
            HarvestContact.id == contact_id,
            HarvestContact.worker_id == worker.id,
        )
    )
    contact = result.scalar_one_or_none()

    if not contact:
        return _error("Contact not found", 404)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    if "full_name" in body:
        name = (body["full_name"] or "").strip()
        if not name:
            return _error("Full name cannot be empty")
        contact.full_name = name

    for field in ("email", "phone", "address", "notes"):
        if field in body:
            val = body[field]
            setattr(contact, field, val.strip() if isinstance(val, str) and val.strip() else (val or None))

    if "status" in body:
        contact.status = body["status"]

    await db.flush()

    return _json(_serialize_contact(contact))


@router.delete("/harvesttrack/contacts/{contact_id}")
async def ht_delete_contact(
    contact_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    from app.models.harvest import HarvestContact

    result = await db.execute(
        select(HarvestContact).where(
            HarvestContact.id == contact_id,
            HarvestContact.worker_id == worker.id,
        )
    )
    contact = result.scalar_one_or_none()

    if not contact:
        return _error("Contact not found", 404)

    contact.status = "ended"
    await db.flush()

    return _json({"message": "Contact deleted"})


@router.post("/harvesttrack/contacts/{contact_id}/follow-ups")
async def ht_create_follow_up(
    contact_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    from app.models.harvest import HarvestContact, HarvestFollowUp

    result = await db.execute(
        select(HarvestContact).where(
            HarvestContact.id == contact_id,
            HarvestContact.worker_id == worker.id,
        )
    )
    contact = result.scalar_one_or_none()

    if not contact:
        return _error("Contact not found", 404)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    follow_type = (body.get("follow_type") or "").strip()
    if not follow_type:
        return _error("follow_type is required (e.g. call, visit, text, prayer)")

    follow_up = HarvestFollowUp(
        contact_id=contact.id,
        worker_id=worker.id,
        follow_type=follow_type,
        notes=(body.get("notes") or "").strip() or None,
    )
    db.add(follow_up)
    await db.flush()

    return _json({
        "id": follow_up.id,
        "contact_id": follow_up.contact_id,
        "follow_type": follow_up.follow_type,
        "notes": follow_up.notes,
        "created_at": follow_up.created_at.isoformat() if follow_up.created_at else None,
    }, 201)


@router.get("/harvesttrack/reminders")
async def ht_list_reminders(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    completed: str = Query(""),
):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    from app.models.harvest import HarvestReminder

    query = select(HarvestReminder).where(HarvestReminder.worker_id == worker.id)

    if completed == "true":
        query = query.where(HarvestReminder.is_completed == True)
    elif completed == "false":
        query = query.where(HarvestReminder.is_completed == False)

    query = query.order_by(HarvestReminder.due_at.asc())

    pagination = await paginate(db, query, page=page, per_page=per_page)
    reminders = pagination["items"]

    return _json({
        "items": [_serialize_reminder(r) for r in reminders],
        "page": pagination["page"],
        "per_page": pagination["per_page"],
        "total": pagination["total"],
        "pages": pagination["pages"],
        "has_prev": pagination["has_prev"],
        "has_next": pagination["has_next"],
    })


@router.post("/harvesttrack/reminders")
async def ht_create_reminder(request: Request, db: AsyncSession = Depends(get_db)):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    title = (body.get("title") or "").strip()
    if not title:
        return _error("Title is required")

    due_at = body.get("due_at")
    if not due_at:
        return _error("due_at is required")

    try:
        due_at_dt = datetime.fromisoformat(due_at)
    except (ValueError, TypeError):
        return _error("Invalid due_at format. Use ISO 8601.")

    from app.models.harvest import HarvestReminder

    contact_id = body.get("contact_id")
    if contact_id:
        from app.models.harvest import HarvestContact

        contact_check = await db.execute(
            select(HarvestContact).where(
                HarvestContact.id == contact_id,
                HarvestContact.worker_id == worker.id,
            )
        )
        if not contact_check.scalar_one_or_none():
            return _error("Contact not found", 404)

    reminder = HarvestReminder(
        worker_id=worker.id,
        contact_id=contact_id,
        title=title,
        notes=(body.get("notes") or "").strip() or None,
        due_at=due_at_dt,
        is_completed=False,
    )
    db.add(reminder)
    await db.flush()

    return _json(_serialize_reminder(reminder), 201)


@router.put("/harvesttrack/reminders/{reminder_id}")
async def ht_update_reminder(
    reminder_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    worker = await _require_harvest_worker(request, db)
    if not worker:
        return _error("Invalid or missing token", 401)

    from app.models.harvest import HarvestReminder

    result = await db.execute(
        select(HarvestReminder).where(
            HarvestReminder.id == reminder_id,
            HarvestReminder.worker_id == worker.id,
        )
    )
    reminder = result.scalar_one_or_none()

    if not reminder:
        return _error("Reminder not found", 404)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    if "title" in body:
        title = (body["title"] or "").strip()
        if not title:
            return _error("Title cannot be empty")
        reminder.title = title

    if "due_at" in body and body["due_at"]:
        try:
            reminder.due_at = datetime.fromisoformat(body["due_at"])
        except (ValueError, TypeError):
            return _error("Invalid due_at format")

    if "notes" in body:
        notes = body["notes"]
        reminder.notes = notes.strip() if isinstance(notes, str) and notes.strip() else None

    if "is_completed" in body:
        reminder.is_completed = bool(body["is_completed"])

    if "contact_id" in body:
        reminder.contact_id = body["contact_id"]

    await db.flush()

    return _json(_serialize_reminder(reminder))
