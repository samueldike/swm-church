import asyncio
import json
import re
import os
from datetime import datetime, date, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://swm_user:swm_password@localhost:5433/swm_db"
)
SQL_DUMP = Path(__file__).parent / "swm_database.sql"

TABLE_COLS = {
    "admins": [
        "id",
        "branch_id",
        "department_id",
        "name",
        "email",
        "phone",
        "password",
        "role",
        "is_active",
        "must_change_password",
        "last_login_at",
        "created_at",
        "updated_at",
    ],
    "branches": [
        "id",
        "name",
        "slug",
        "email",
        "phone",
        "city",
        "state",
        "country",
        "address",
        "is_active",
        "created_at",
        "updated_at",
    ],
    "departments": [
        "id",
        "created_by_admin_id",
        "name",
        "slug",
        "description",
        "is_active",
        "created_at",
        "updated_at",
    ],
    "members": [
        "id",
        "branch_id",
        "department_id",
        "first_name",
        "last_name",
        "email",
        "phone",
        "gender",
        "date_of_birth",
        "joined_at",
        "membership_status",
        "role",
        "notes",
        "created_at",
        "updated_at",
    ],
    "events": [
        "id",
        "branch_id",
        "title",
        "description",
        "start_date",
        "end_date",
        "start_time",
        "end_time",
        "daily_schedule",
        "location",
        "expected_attendance",
        "event_type",
        "status",
        "featured_image",
        "created_at",
        "updated_at",
    ],
    "sermons": [
        "id",
        "branch_id",
        "title",
        "caption",
        "speaker",
        "summary",
        "media_url",
        "original_file_name",
        "original_file_size",
        "telegram_file_id",
        "telegram_message_id",
        "platform_targets",
        "platform_links",
        "distribution_status",
        "type",
        "published_on",
        "status",
        "created_at",
        "updated_at",
    ],
    "sliders": [
        "id",
        "image_path",
        "title",
        "description",
        "cta_label",
        "cta_url",
        "sort_order",
        "is_active",
        "created_at",
        "updated_at",
    ],
    "contact_inquiries": [
        "id",
        "branch_id",
        "name",
        "email",
        "phone",
        "subject",
        "message",
        "status",
        "resolved_at",
        "created_at",
        "updated_at",
    ],
    "site_settings": ["id", "key", "value", "created_at", "updated_at"],
    "prayer_requests": [
        "id",
        "name",
        "email",
        "phone",
        "request",
        "anonymous",
        "notify_me",
        "status",
        "created_at",
        "updated_at",
    ],
    "harvest_workers": [
        "id",
        "branch_id",
        "name",
        "email",
        "phone",
        "password",
        "role",
        "is_active",
        "last_login_at",
        "created_at",
        "updated_at",
    ],
    "unit_reports": [
        "id",
        "branch_id",
        "department_id",
        "hod_admin_id",
        "report_month",
        "introduction",
        "attendance",
        "attendance_note",
        "collections",
        "observations",
        "challenges",
        "recommendations",
        "projections",
        "resident_pastor_comment",
        "senior_pastor_comment",
        "status",
        "submitted_at",
        "created_at",
        "updated_at",
    ],
    "admin_audit_logs": [
        "id",
        "admin_id",
        "reviewed_by_admin_id",
        "assigned_to_admin_id",
        "assigned_at",
        "action",
        "target_type",
        "target_id",
        "metadata",
        "reviewed_at",
        "review_note",
        "ip_address",
        "user_agent",
        "created_at",
        "updated_at",
    ],
}


def parse_dt(s):
    if s is None:
        return None
    s = str(s).strip()
    if not s or s == "NULL":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=None)
        except:
            pass
    return None


def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    if not s or s == "NULL":
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except:
        return None


def unescape_mysql(v):
    if v is None:
        return None
    v = (
        v.replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
    )
    v = v.replace("\\0", "\0")
    return v


def split_fields(row):
    fields = []
    buf = ""
    in_s = False
    esc = False
    i = 0
    while i < len(row):
        c = row[i]
        if esc:
            buf += c
            esc = False
            i += 1
            continue
        if c == "\\" and in_s:
            esc = True
            buf += c
            i += 1
            continue
        if c == "'" and not esc:
            if in_s and i + 1 < len(row) and row[i + 1] == "'":
                buf += "'"
                i += 2
                continue
            in_s = not in_s
            buf += c
            i += 1
            continue
        if c == "," and not in_s:
            fields.append(buf.strip())
            buf = ""
            i += 1
            continue
        buf += c
        i += 1
    fields.append(buf.strip())
    out = []
    for f in fields:
        f = f.strip()
        if f == "NULL":
            out.append(None)
        elif len(f) >= 2 and f[0] == "'" and f[-1] == "'":
            inner = f[1:-1]
            inner = unescape_mysql(inner)
            inner = inner.replace("\\'", "'")
            out.append(inner)
        elif f == "":
            out.append(None)
        else:
            try:
                if "." in f:
                    out.append(float(f))
                else:
                    out.append(int(f))
            except:
                out.append(f)
    return out


def extract_rows(values_str):
    rows = []
    depth = 0
    in_s = False
    esc = False
    cur = ""
    for idx, c in enumerate(values_str):
        if esc:
            cur += c
            esc = False
            continue
        if c == "\\" and in_s:
            esc = True
            cur += c
            continue
        if c == "'" and not esc:
            if in_s and idx + 1 < len(values_str) and values_str[idx + 1] == "'":
                cur += c
                continue
            in_s = not in_s
            cur += c
            continue
        if not in_s:
            if c == "(":
                if depth == 0:
                    cur = ""
                    depth = 1
                    continue
                else:
                    depth += 1
                    cur += c
                    continue
            elif c == ")":
                depth -= 1
                if depth == 0:
                    rows.append(cur)
                    cur = ""
                    continue
                else:
                    cur += c
                    continue
            elif c == "," and depth == 0:
                continue
            else:
                if depth > 0:
                    cur += c
                continue
        else:
            if depth > 0:
                cur += c
    return rows


def parse_dump(path):
    content = Path(path).read_text(encoding="utf-8", errors="ignore")
    data = {}
    for table, cols in TABLE_COLS.items():
        needle = f"INSERT INTO `{table}` VALUES "
        pos = content.find(needle)
        if pos == -1:
            data[table] = []
            continue
        start = pos + len(needle)
        in_s = False
        esc = False
        end = -1
        for i in range(start, len(content)):
            c = content[i]
            if esc:
                esc = False
                continue
            if c == "\\" and in_s:
                esc = True
                continue
            if c == "'" and not esc:
                if in_s and i + 1 < len(content) and content[i + 1] == "'":
                    continue
                in_s = not in_s
                continue
            if not in_s and c == ";":
                end = i
                break
        if end == -1:
            data[table] = []
            continue
        values_str = content[start:end]
        raw_rows = extract_rows(values_str)
        parsed = []
        for r in raw_rows:
            fields = split_fields(r)
            if len(fields) != len(cols):
                if len(fields) < len(cols):
                    fields += [None] * (len(cols) - len(fields))
                else:
                    fields = fields[: len(cols)]
            d = dict(zip(cols, fields))
            parsed.append(d)
        data[table] = parsed
        print(f"{table}: {len(parsed)} rows")
    return data


def slugify(s):
    import re as _re

    s = str(s).lower()
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "n-a"


async def migrate():
    from app.database import Base
    import app.models

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    data = parse_dump(SQL_DUMP)
    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        for tbl in [
            "admin_audit_logs",
            "unit_reports",
            "members",
            "harvest_workers",
            "prayer_requests",
            "contact_inquiries",
            "sliders",
            "sermons",
            "events",
            "departments",
            "branches",
            "admins",
            "site_settings",
        ]:
            try:
                await db.execute(text(f'DELETE FROM "{tbl}"'))
            except Exception as e:
                print(f"delete {tbl} failed: {e}")
        await db.flush()

        from app.models.branch import Branch
        from app.models.department import Department
        from app.models.admin import Admin
        from app.models.member import Member
        from app.models.site_settings import SiteSetting
        from app.models.event import Event as Evt
        from app.models.sermon import Sermon
        from app.models.slider import Slider
        from app.models.contact_inquiry import ContactInquiry
        from app.models.prayer_request import PrayerRequest
        from app.models.harvest import HarvestWorker
        from app.models.unit_report import UnitReport
        from app.models.admin import AdminAuditLog

        for r in data["branches"]:
            db.add(
                Branch(
                    id=int(r["id"]),
                    name=r["name"],
                    slug=r["slug"],
                    address=r["address"],
                    city=r["city"],
                    state=r["state"],
                    country=r["country"] or "Nigeria",
                    phone=str(r["phone"]) if r["phone"] else None,
                    email=r["email"],
                    is_active=bool(int(r["is_active"]))
                    if r["is_active"] is not None
                    else True,
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["departments"]:
            db.add(
                Department(
                    id=int(r["id"]),
                    name=r["name"],
                    slug=r["slug"],
                    description=r["description"],
                    is_active=bool(int(r["is_active"]))
                    if r["is_active"] is not None
                    else True,
                    created_by_admin_id=int(r["created_by_admin_id"])
                    if r["created_by_admin_id"]
                    else None,
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["admins"]:
            ph = str(r["password"] or "")
            ph = ph.replace("$2y$", "$2b$")
            db.add(
                Admin(
                    id=int(r["id"]),
                    full_name=r["name"],
                    email=str(r["email"]).lower().strip(),
                    password_hash=ph,
                    role=r["role"] or "admin",
                    branch_id=int(r["branch_id"]) if r["branch_id"] else None,
                    department_id=int(r["department_id"])
                    if r["department_id"]
                    else None,
                    phone=str(r["phone"]) if r["phone"] else None,
                    is_active=bool(int(r["is_active"]))
                    if r["is_active"] is not None
                    else True,
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["members"]:
            db.add(
                Member(
                    id=int(r["id"]),
                    branch_id=int(r["branch_id"]) if r["branch_id"] else None,
                    first_name=r["first_name"] or "Unknown",
                    last_name=r["last_name"] or "Member",
                    email=r["email"],
                    phone=str(r["phone"]) if r["phone"] else None,
                    gender=r["gender"],
                    date_of_birth=parse_date(str(r["date_of_birth"]))
                    if r["date_of_birth"]
                    else None,
                    joined_at=parse_date(str(r["joined_at"]))
                    if r["joined_at"]
                    else None,
                    membership_status=r["membership_status"] or "active",
                    notes=r["notes"],
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["site_settings"]:
            db.add(
                SiteSetting(
                    id=int(r["id"]),
                    key=r["key"],
                    value=str(r["value"]) if r["value"] is not None else None,
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["events"]:
            sd = str(r["start_date"]) if r["start_date"] else None
            st = str(r["start_time"]) if r["start_time"] else "00:00:00"
            ed = str(r["end_date"]) if r["end_date"] else None
            et = str(r["end_time"]) if r["end_time"] else None
            starts = None
            ends = None
            try:
                if sd:
                    starts = datetime.strptime(
                        f"{sd} {st}", "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=None)
            except:
                starts = parse_dt(r["created_at"])
            try:
                if ed:
                    t = et or st or "00:00:00"
                    ends = datetime.strptime(f"{ed} {t}", "%Y-%m-%d %H:%M:%S")
            except:
                ends = None
            is_pub = str(r["status"]) == "published"
            is_major = str(r["event_type"]) == "major"
            db.add(
                Evt(
                    id=int(r["id"]),
                    title=r["title"],
                    description=r["description"],
                    location=r["location"],
                    starts_at=starts or datetime.utcnow(),
                    ends_at=ends,
                    is_published=is_pub,
                    is_major=is_major,
                    cover_image=r["featured_image"],
                    branch_id=int(r["branch_id"]) if r["branch_id"] else None,
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        seen_slugs = set()
        for r in data["sermons"]:
            base = slugify(r["title"] or f"sermon-{r['id']}")
            slug = base
            c = 2
            while slug in seen_slugs:
                slug = f"{base}-{c}"
                c += 1
            seen_slugs.add(slug)
            pub = None
            if r["published_on"]:
                try:
                    pub = datetime.strptime(str(r["published_on"]), "%Y-%m-%d")
                except:
                    pub = parse_dt(r["created_at"])
            pt = None
            pl = None
            ds = None
            for k in ["platform_targets", "platform_links", "distribution_status"]:
                v = r.get(k)
                if v:
                    try:
                        pt = json.loads(v) if k == "platform_targets" else pt
                    except:
                        pass
            try:
                if r["platform_targets"]:
                    pt = json.loads(r["platform_targets"])
            except:
                pt = None
            try:
                if r["platform_links"]:
                    pl = json.loads(r["platform_links"])
            except:
                pl = None
            try:
                if r["distribution_status"]:
                    ds = json.loads(r["distribution_status"])
            except:
                ds = None
            db.add(
                Sermon(
                    id=int(r["id"]),
                    branch_id=int(r["branch_id"]) if r["branch_id"] else None,
                    title=r["title"],
                    slug=slug,
                    caption=r["caption"],
                    description=r["summary"],
                    preacher=r["speaker"],
                    media_url=r["media_url"],
                    type=r["type"] or "audio",
                    published_at=pub,
                    status=r["status"] or "draft",
                    original_file_name=r["original_file_name"],
                    original_file_size=int(r["original_file_size"])
                    if r["original_file_size"]
                    else None,
                    telegram_file_id=r["telegram_file_id"],
                    telegram_message_id=str(r["telegram_message_id"])
                    if r["telegram_message_id"]
                    else None,
                    platform_targets=pt,
                    platform_links=pl,
                    distribution_status=ds,
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["sliders"]:
            db.add(
                Slider(
                    id=int(r["id"]),
                    image_path=r["image_path"],
                    title=r["title"],
                    description=r["description"],
                    cta_label=r["cta_label"],
                    cta_url=r["cta_url"],
                    sort_order=int(r["sort_order"])
                    if r["sort_order"] is not None
                    else 0,
                    is_active=bool(int(r["is_active"]))
                    if r["is_active"] is not None
                    else True,
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["contact_inquiries"]:
            db.add(
                ContactInquiry(
                    id=int(r["id"]),
                    branch_id=int(r["branch_id"]) if r["branch_id"] else None,
                    name=r["name"],
                    email=r["email"],
                    phone=str(r["phone"]) if r["phone"] else None,
                    subject=r["subject"],
                    message=r["message"],
                    status=r["status"] or "new",
                    resolved_at=parse_dt(r["resolved_at"]),
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["prayer_requests"]:
            is_pub = (
                not bool(int(r["anonymous"])) if r["anonymous"] is not None else False
            )
            db.add(
                PrayerRequest(
                    id=int(r["id"]),
                    full_name=r["name"],
                    email=r["email"],
                    phone=str(r["phone"]) if r["phone"] else None,
                    prayer_request=r["request"],
                    is_public=is_pub,
                    notify_me=bool(int(r["notify_me"]))
                    if r["notify_me"] is not None
                    else False,
                    status=r["status"] or "new",
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["harvest_workers"]:
            ph = str(r["password"] or "").replace("$2y$", "$2b$")
            db.add(
                HarvestWorker(
                    id=int(r["id"]),
                    full_name=r["name"],
                    email=str(r["email"]).lower().strip(),
                    phone=str(r["phone"]) if r["phone"] else None,
                    password_hash=ph,
                    branch_id=int(r["branch_id"]) if r["branch_id"] else None,
                    is_active=bool(int(r["is_active"]))
                    if r["is_active"] is not None
                    else True,
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["unit_reports"]:
            att = None
            coll = None
            try:
                if r["attendance"]:
                    att = json.loads(r["attendance"])
            except:
                att = None
            try:
                if r["collections"]:
                    coll = json.loads(r["collections"])
            except:
                coll = None
            rm = str(r["report_month"]) if r["report_month"] else None
            if rm and len(rm) >= 7:
                try:
                    rm = datetime.strptime(rm, "%Y-%m-%d").strftime("%Y-%m")
                except:
                    pass
            db.add(
                UnitReport(
                    id=int(r["id"]),
                    branch_id=int(r["branch_id"]) if r["branch_id"] else None,
                    department_id=int(r["department_id"])
                    if r["department_id"]
                    else None,
                    hod_admin_id=int(r["hod_admin_id"]) if r["hod_admin_id"] else None,
                    report_month=rm,
                    introduction=r["introduction"] or "",
                    attendance=att,
                    attendance_note=r["attendance_note"],
                    collections=coll,
                    observations=r["observations"],
                    challenges=r["challenges"],
                    recommendations=r["recommendations"],
                    projections=r["projections"],
                    resident_pastor_comment=r["resident_pastor_comment"],
                    senior_pastor_comment=r["senior_pastor_comment"],
                    status=r["status"] or "draft",
                    submitted_at=parse_dt(r["submitted_at"]),
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                    updated_at=parse_dt(r["updated_at"]) or datetime.utcnow(),
                )
            )

        for r in data["admin_audit_logs"]:
            meta = None
            try:
                if r["metadata"]:
                    meta = json.loads(r["metadata"])
            except:
                meta = None
            db.add(
                AdminAuditLog(
                    id=int(r["id"]),
                    admin_id=int(r["admin_id"]) if r["admin_id"] else 1,
                    action=r["action"],
                    target_type=r["target_type"],
                    target_id=int(r["target_id"]) if r["target_id"] else None,
                    extra_data=meta,
                    ip_address=str(r["ip_address"]) if r["ip_address"] else None,
                    user_agent=r["user_agent"],
                    assigned_to_id=int(r["assigned_to_admin_id"])
                    if r["assigned_to_admin_id"]
                    else None,
                    reviewed_at=parse_dt(r["reviewed_at"]),
                    created_at=parse_dt(r["created_at"]) or datetime.utcnow(),
                )
            )

        await db.flush()
        from sqlalchemy import select
        from app.core.security import hash_password

        res = await db.execute(
            select(Admin).where(Admin.email == "admin@scentofwater.org")
        )
        adm = res.scalar_one_or_none()
        if not adm:
            res2 = await db.execute(
                select(Admin).where(Admin.email == "admin@swm.local")
            )
            local = res2.scalar_one_or_none()
            if local:
                local.email = "admin@scentofwater.org"
                local.password_hash = hash_password("admin123")
                print("Updated admin@swm.local -> admin@scentofwater.org with admin123")
            else:
                db.add(
                    Admin(
                        full_name="Super Admin",
                        email="admin@scentofwater.org",
                        password_hash=hash_password("admin123"),
                        role="super_admin",
                        is_active=True,
                    )
                )
                print("Created admin@scentofwater.org")
        else:
            from app.core.security import verify_password

            if not verify_password("admin123", adm.password_hash):
                adm.password_hash = hash_password("admin123")
                print("Reset admin@scentofwater.org password")
        await db.commit()
        print("Migration committed")
        await db.execute(text("SET session_replication_role = 'origin'"))
        await engine.dispose()
        for seq in [
            "admins_id_seq",
            "branches_id_seq",
            "departments_id_seq",
            "members_id_seq",
            "events_id_seq",
            "sermons_id_seq",
            "sliders_id_seq",
            "contact_inquiries_id_seq",
            "site_settings_id_seq",
            "prayer_requests_id_seq",
            "harvest_workers_id_seq",
            "unit_reports_id_seq",
            "admin_audit_logs_id_seq",
        ]:
            async with Session() as s:
                try:
                    await s.execute(
                        text(
                            f"SELECT setval('{seq}', (SELECT COALESCE(MAX(id),1) FROM {seq.replace('_id_seq', 's')}))"
                        )
                    )
                except:
                    pass
                try:
                    await s.commit()
                except:
                    pass
        print("Sequences updated")


if __name__ == "__main__":
    asyncio.run(migrate())
