"""Build a fresh LOCAL database with realistic data for the launch audit.

    py audit/seed_local.py            # creates swm_launch on localhost:5433 and seeds it

Safety: refuses any host other than localhost/127.0.0.1 and refuses the names swm_db/production.
No real personal data: every person, email and phone here is invented (emails on example.org,
which the mailer never sends to).
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

DB_NAME = "swm_launch"
BASE = "postgresql+asyncpg://swm_user:swm_password@localhost:5433"

url = os.environ.get("SEED_DATABASE_URL", f"{BASE}/{DB_NAME}")
host = url.split("@", 1)[1].split("/", 1)[0].split(":", 1)[0]
name = url.rsplit("/", 1)[1]
if host not in ("localhost", "127.0.0.1") or name in ("swm_db", "production", "swm_audit"):
    sys.exit(f"Refusing to seed {host}/{name}: only a fresh local database is allowed.")

os.environ["DATABASE_URL"] = url
os.environ["SWM_DISABLE_SCHEDULER"] = "1"
os.environ["SWM_EMAIL_DRY_RUN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

random.seed(7)
NOW = datetime.utcnow().replace(microsecond=0)
TODAY = NOW.date()


async def create_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(BASE.replace("+asyncpg", "") + "/postgres")
    try:
        exists = await conn.fetchval("select 1 from pg_database where datname=$1", name)
        if exists:
            await conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


def startup() -> None:
    """Run the real app startup (create_all, ensure_schema, defaults, geo migration)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        assert c.get("/").status_code == 200


FIRST_M = ["Chukwuemeka", "Ifeanyi", "Oluwaseun", "Emeka", "Tunde", "Ibrahim", "Kelechi", "Uche", "Femi", "Nnamdi", "Segun", "Obinna", "Kwame", "Kofi"]
FIRST_F = ["Ngozi", "Adaeze", "Funmilayo", "Chiamaka", "Blessing", "Amaka", "Yetunde", "Halima", "Ifeoma", "Chinwe", "Esther", "Grace", "Ama", "Akosua"]
LAST = ["Okafor", "Nwosu", "Adeyemi", "Eze", "Okonkwo", "Balogun", "Uchenna", "Nwankwo", "Ogbu", "Ibe", "Mensah", "Bello", "Ajayi", "Onu", "Agbo", "Nweke"]


def person(i: int, gender: str | None = None):
    gender = gender or random.choice(["male", "female"])
    first = random.choice(FIRST_M if gender == "male" else FIRST_F)
    last = random.choice(LAST)
    return first, last, gender, f"{first.lower()}.{last.lower()}{i}@example.org"


async def seed() -> None:
    from sqlalchemy import select

    from app.core import geo
    from app.core.security import hash_password
    from app.database import async_session
    from app.models.admin import Admin
    from app.models.announcement import Announcement
    from app.models.branch import Branch
    from app.models.branch_report import BranchReport
    from app.models.contact_inquiry import ContactInquiry
    from app.models.department import Department
    from app.models.event import Event
    from app.models.branch_setting import Remittance
    from app.models.giving import Giving
    from app.models.harvest import HarvestContact, HarvestFollowUp, HarvestWorker
    from app.models.member import Member
    from app.models.minister import Minister
    from app.models.newsletter_subscriber import NewsletterSubscriber
    from app.models.prayer_request import PrayerRequest
    from app.models.sermon import Sermon
    from app.models.site_settings import SiteSetting
    from app.models.story import Story
    from app.models.unit_report import UnitReport

    from app.database import engine

    await engine.dispose(close=False)   # the startup ran on another event loop; drop its pooled connections
    async with async_session() as db:
        hq =(await db.execute(select(Branch).where(Branch.is_hq.is_(True)))).scalar_one_or_none()
        if hq is None:
            hq = Branch(name="Scent of Water Ministry HQ", slug="hq", is_hq=True)
            db.add(hq)
        hq.address, hq.city, hq.state = "#1 Scent Close, Mile 50", "Abakaliki", "Ebonyi"
        hq.country, hq.country_code, hq.region_code, hq.currency, hq.timezone = "Nigeria", "NG", "NG-EB", "NGN", "Africa/Lagos"
        hq.phone, hq.email, hq.is_active = "+2347037736698", "office@example.org", True

        specs = [("Abuja Branch", "Abuja", "NG", "NG-FC", "Africa/Lagos"), ("Enugu Branch", "Enugu", "NG", "NG-EN", "Africa/Lagos"),
                 ("Port Harcourt Branch", "Port Harcourt", "NG", "NG-RI", "Africa/Lagos"), ("Accra Branch", "Accra", "GH", "GH-AA", "Africa/Accra"),
                 ("London Branch", "London", "GB", None, "Europe/London")]
        branches = [hq]
        for i, (bname, city, cc, rc, tz) in enumerate(specs):
            b = Branch(name=bname, slug=bname.lower().replace(" ", "-"), city=city, country=geo.country_name(cc), country_code=cc,
                       region_code=rc, timezone=tz, address=f"{12 + i} Church Road, {city}", phone=None, is_active=True, is_hq=False)
            db.add(b)
            branches.append(b)
        await db.flush()

        depts = []
        for dn in ["Choir", "Ushering", "Children Church", "Media", "Evangelism", "Prayer Band", "Protocol"]:
            d = Department(name=dn, slug=dn.lower().replace(" ", "-"), description=f"The {dn.lower()} unit.", is_active=True)
            db.add(d)
            depts.append(d)
        await db.flush()

        pw = hash_password("Launch-Audit-2026")
        def adm(email, full, role, branch=None, dept=None):
            a = Admin(email=email, full_name=full, password_hash=pw, role=role, branch_id=branch.id if branch else None,
                      department_id=dept.id if dept else None, is_active=True, must_change_password=False)
            db.add(a)
            return a
        sup = adm("admin@example.org", "Pastor Samuel Okeke", "super_admin", hq)
        adm("media@example.org", "Chidi Media", "hq_media", hq)
        adm("finance@example.org", "Ada Finance", "hq_finance", hq)
        adm("pastoral@example.org", "Rev. Grace Pastoral", "hq_pastoral", hq)
        ba = adm("abuja.admin@example.org", "Ifeanyi Abuja", "branch_admin", branches[1])
        adm("accra.admin@example.org", "Kwame Accra", "branch_admin", branches[4])
        rp = adm("rp.abuja@example.org", "Rev. Tunde Bello", "resident_pastor", branches[1])
        hods = [adm(f"hod.{d.slug}@example.org", f"HOD {d.name}", "hod", branches[1] if i % 2 else hq, d) for i, d in enumerate(depts[:4])]
        await db.flush()

        # Members across branches (incl. diaspora)
        n = 0
        for b in branches:
            for _ in range(random.randint(180, 220) if b.is_hq else random.randint(25, 60)):
                n += 1
                first, last, g, email = person(n)
                db.add(Member(branch_id=b.id, first_name=first, last_name=last, email=email if n % 3 else None,
                              phone=geo.normalize_phone(f"080{random.randint(10000000, 99999999)}", b.country_code) if b.country_code == "NG" else None,
                              country_code=b.country_code, region_code=b.region_code, city=b.city, gender=g,
                              date_of_birth=date(random.randint(1960, 2008), random.randint(1, 12), random.randint(1, 28)),
                              joined_at=TODAY - timedelta(days=random.randint(10, 2000)), membership_status=random.choice(["active"] * 8 + ["inactive"])))

        titles = ["Pastor", "Rev.", "Evangelist", "Deacon", "Deaconess"]
        for i, b in enumerate(branches):
            for j in range(2 if b.is_hq else 1):
                first, last, g, email = person(900 + i * 10 + j)
                db.add(Minister(branch_id=b.id, title=random.choice(titles), full_name=f"{first} {last}", gender=g, phone="+2348031234567",
                                email=email, origin_country_code="NG", origin_region_code=random.choice(["NG-EB", "NG-EN", "NG-AN", "NG-IM"]),
                                education_level="Bachelor's degree", ministry_rank="Pastor", status="active", created_by=sup.id,
                                date_joined=TODAY - timedelta(days=800)))

        # Messages (sermons): published HQ messages, a branch message, a draft
        preachers = ["Pastor Samuel Okeke", "Rev. Grace Pastoral", "Pastor Tunde Bello"]
        for i in range(14):
            t = ["Focus Reloaded", "The Scent of Water", "Dominion in the Marketplace", "Walking in Grace", "When Dead Dreams Live Again",
                 "The Power of Covenant", "Faith That Speaks", "Restoration of Destiny", "The Prayer Altar", "Kingdom Finance",
                 "Raising Sons", "Light in Dark Places", "The Harvest Is Ripe", "A New Song"][i]
            b = hq if i < 12 else branches[1]
            db.add(Sermon(branch_id=b.id, title=t, slug=f"{t.lower().replace(' ', '-')}-{i}", preacher=preachers[i % 3],
                          description=f"A message on {t.lower()}.", caption=t, type="audio", status="draft" if i == 13 else "published",
                          published_at=NOW - timedelta(days=7 * i), series="Sunday Service" if i % 2 else "Midweek",
                          scripture="Job 14:7-9" if i % 3 == 0 else None, is_featured=i == 0, transcription_status="none", created_by=sup.id))

        # Events: past, this week, upcoming, a branch event
        for i, (title, days, major) in enumerate([("Sunday Celebration Service", 3, False), ("Midweek Bible Study", 1, False),
                                                  ("Holy Ghost Night", 12, True), ("Annual Convention 2026", 40, True),
                                                  ("Youth Outreach", -9, False), ("Accra Thanksgiving", 6, False)]):
            start = (NOW + timedelta(days=days)).replace(hour=9, minute=0, second=0)
            db.add(Event(title=title, description=f"Join us for {title}.", location="Church Auditorium", starts_at=start,
                         ends_at=start + timedelta(hours=3), is_published=True, is_major=major,
                         branch_id=branches[4].id if title.startswith("Accra") else hq.id, created_by=sup.id))

        # Giving: online + manual, several statuses and months
        for i in range(320):
            b = random.choice(branches)
            status = random.choice(["completed"] * 6 + ["pending", "failed"])
            paid = NOW - timedelta(days=random.randint(0, 180), hours=random.randint(0, 23))
            db.add(Giving(reference=f"SWM-AUD-{i:04d}", email=f"giver{i}@example.org", full_name=" ".join(person(i)[:2]),
                          amount=Decimal(random.choice([2000, 5000, 10000, 25000, 50000, 100000])), currency="NGN",
                          giving_type=random.choice(["Tithe", "Offering", "Building Project", "Missions"]), branch_id=b.id,
                          gateway_owner="hq", method=random.choice(["paystack", "paystack", "transfer", "cash"]),
                          payment_gateway="paystack", status=status, paid_at=paid if status == "completed" else None))
        db.add(Remittance(branch_id=branches[1].id, direction="to_hq", amount=Decimal("150000"), currency="NGN",
                          paid_on=TODAY - timedelta(days=20), reference="TRF-2210", recorded_by=sup.id))

        # Visitor submissions
        for i in range(9):
            first, last, _, email = person(500 + i)
            db.add(ContactInquiry(branch_id=random.choice(branches[:3]).id, name=f"{first} {last}", email=email, phone=None,
                                  subject=["Visiting this Sunday", "Counselling", "Wedding booking", "Volunteering"][i % 4],
                                  topic=None, message="I would like to know more about the church and service times.",
                                  status=["new", "new", "in_progress", "resolved"][i % 4], kind="visit" if i % 3 == 0 else "contact"))
            db.add(PrayerRequest(full_name=f"{first} {last}", email=email, prayer_request="Please pray for my family and my job search.",
                                 branch_id=random.choice(branches[:3]).id, status=["new", "praying", "answered"][i % 3]))
            db.add(NewsletterSubscriber(email=f"reader{i}@example.org", is_active=i != 4, token=f"tok{i:04d}audit", source="footer",
                                        subscribed_at=NOW - timedelta(days=i * 9)))

        for i, (t, cat) in enumerate([("Healed After 12 Years", "Testimony"), ("New Branch Opens in Accra", "News"), ("Youth Camp Highlights", "Events")]):
            db.add(Story(title=t, slug=t.lower().replace(" ", "-"), excerpt=t + ".", content=f"<p>{t}. Glory to God.</p>", category=cat,
                         author_name="Media Team", status="published", is_featured=i == 0, published_at=NOW - timedelta(days=i * 11), created_by=sup.id))

        db.add(Announcement(title="December convention planning", body="All branches should send delegate numbers by 30 November.",
                            audience="all", is_pinned=True, is_active=True, created_by=sup.id))

        # Department (unit) reports: submitted, reviewed, draft
        month = (TODAY.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        weeks = ["week1", "week2", "week3", "week4", "week5", "others", "total"]
        for i, h in enumerate(hods):
            att = {row: {w: str(random.randint(5, 40)) for w in weeks} for row in ("male", "female", "total")}
            db.add(UnitReport(branch_id=h.branch_id, department_id=h.department_id, hod_admin_id=h.id, report_month=month,
                              introduction="The unit served faithfully this month.", attendance=att,
                              collections={"dues": {w: "" for w in weeks}, "others": {"label": "", **{w: "" for w in weeks}}, "total": "12,500"},
                              observations="Good participation.", challenges="Transport for members.", recommendations="Provide a bus.",
                              status=["submitted", "draft", "submitted", "reviewed"][i], submitted_at=NOW - timedelta(days=3) if i != 1 else None))
        for back in range(6):                      # six months of branch reports from two branches
            y, mo = divmod(TODAY.year * 12 + TODAY.month - 2 - back, 12)
            m = f"{y}-{mo + 1:02d}"
            for b, author in ((branches[1], ba), (branches[4], None)):
                db.add(BranchReport(branch_id=b.id, author_admin_id=author.id if author else None, report_month=m,
                                    summary=f"A fruitful month in {b.city}.",
                                    metrics={"avg_attendance": random.randint(60, 160), "first_timers": random.randint(3, 20),
                                             "new_members": random.randint(1, 9), "converts": random.randint(0, 6), "baptised": random.randint(0, 3)},
                                    highlights="Youth outreach.", challenges="Rent increase.", requests_to_hq="Support for sound system.",
                                    status="acknowledged" if back else "submitted", submitted_at=NOW - timedelta(days=2 + 30 * back)))

        # Follow-up (HarvestTrack)
        w = HarvestWorker(full_name="Esther Follow-up", email="worker@example.org", password_hash=pw, branch_id=hq.id, role="worker", is_active=True)
        db.add(w)
        await db.flush()
        for i in range(6):
            first, last, _, email = person(700 + i)
            c = HarvestContact(worker_id=w.id, full_name=f"{first} {last}", email=email, status="active", source="outreach",
                               current_stage=["new", "contacted", "visited", "committed"][i % 4], next_follow_up_at=NOW + timedelta(days=i))
            db.add(c)
            await db.flush()
            db.add(HarvestFollowUp(contact_id=c.id, worker_id=w.id, follow_type="call", notes="Called, will attend Sunday."))

        # Website content the Settings checklist asks for
        for k, v in {"siteContactEmail": "office@example.org", "whatsappNumber": "+2347037736698",
                     "prayerResponseTime": "within 48 hours", "siteUrl": "http://localhost:8000"}.items():
            row = (await db.execute(select(SiteSetting).where(SiteSetting.key == k))).scalar_one_or_none()
            if row:
                row.value = v
            else:
                db.add(SiteSetting(key=k, value=v))
        await db.commit()
        print(f"Seeded {name}: {len(branches)} churches, {n} members, 14 messages, 320 gifts, {len(hods)} HODs.")
        print("All seeded logins use password Launch-Audit-2026 (super admin: admin@example.org).")


if __name__ == "__main__":
    asyncio.run(create_database())
    startup()
    asyncio.run(seed())
