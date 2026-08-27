import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.database import async_session
from app.models.slider import Slider
from app.models.sermon import Sermon
from app.models.event import Event
from app.models.site_settings import SiteSetting
from app.models.story import Story


async def seed():
    async with async_session() as db:
        existing_sliders = (await db.execute(select(Slider))).scalars().all()
        if not existing_sliders:
            sliders = [
                Slider(title="Welcome to\nScent of Water Ministry", description="A community of grace, restoration and destiny.", cta_label="Join Us This Sunday", cta_url="/calendar", image_path="images/sliders/slide-1.jpg", sort_order=1, is_active=True),
                Slider(title="Raising a Generation\nfor the Harvest", description="Equipping believers through the Word of Faith and the demonstration of the Spirit.", cta_label="Explore Our Ministries", cta_url="/departments", image_path="images/sliders/slide-2.jpg", sort_order=2, is_active=True),
                Slider(title="Where Dead Dreams\nLive Again", description="At the scent of water, every cut-down tree will sprout again. — Job 14:7", cta_label="Listen to Messages", cta_url="/messages", image_path="images/sliders/slide-3.jpg", sort_order=3, is_active=True),
                Slider(title="Welcome Home", description="Experience the transforming power of God's word in a welcoming family.", cta_label="Plan Your Visit", cta_url="/contact", image_path="images/sliders/slide-0c34662b-1abe-4b50-98a0-afade3dfb6ce.jpg", sort_order=4, is_active=True),
            ]
            for s in sliders:
                db.add(s)
            await db.flush()
            print(f"Seeded {len(sliders)} sliders")
        else:
            print(f"Sliders already exist ({len(existing_sliders)}), skipping")

        existing_stories = (await db.execute(select(Story))).scalars().all()
        if not existing_stories:
            now = datetime.now(timezone.utc)
            stories = [
                Story(title="Finding Faith in the Midst of Uncertainty", slug="finding-faith-in-the-midst-of-uncertainty", excerpt="When I lost my job last year, I didn't know where to turn. This community surrounded me with prayer and practical support that changed my perspective entirely.", content="When I lost my job last year, I didn't know where to turn. The bills were piling up, my confidence was shaken, and I felt like I had failed my family.\n\nThen I found Scent of Water Ministry. From the first Sunday, the warmth of the community embraced me. Members prayed with me, counselled me, and even connected me to opportunities. Within months, God opened a new door — better than before.\n\nThis testimony is a reminder that God never abandons His children. At the scent of water, every dry season will end.", category="Testimony", author_name="Michael T.", status="published", is_featured=True, published_at=now - timedelta(days=5)),
                Story(title="A Weekend of Service: City Shelter Report", slug="weekend-of-service-city-shelter-report", excerpt="Our youth team spent the weekend serving meals and distributing clothes at the downtown shelter.", content="Our youth team spent the weekend serving meals and distributing clothes at the downtown shelter. We arrived early Saturday morning with food packs, clothing and most importantly, open hearts.\n\nThe smiles we saw were the greatest reward. One elderly man prayed with us and shared how a simple meal restored his hope for the day.\n\nOutreach is not just an event — it is who we are.", category="Outreach", author_name="Sarah J.", status="published", is_featured=True, published_at=now - timedelta(days=12)),
                Story(title="Building a Christ-Centered Marriage", slug="building-a-christ-centered-marriage", excerpt="Reflecting on 20 years of marriage and the biblical principles that have kept us grounded.", content="Twenty years ago, my wife and I said 'I do' with little idea of what lay ahead. Through storms and celebrations, three principles kept us grounded: Christ at the centre, forgiveness as a lifestyle, and prayer as a habit.\n\nAt Scent of Water, the Built to Last Marriage Prayer Network gave us tools and community. We share this to encourage every couple — your marriage can thrive.", category="Lifestyle", author_name="David R.", status="published", is_featured=True, published_at=now - timedelta(days=20)),
                Story(title="From Darkness to Light: My Encounter with Grace", slug="from-darkness-to-light-encounter-with-grace", excerpt="I walked into the church broken and left transformed by the power of God's love.", content="For years I battled addiction and shame. I thought I was too far gone for God.\n\nA friend invited me to a Sunday service. The message on grace broke me down — I realised God's love wasn't earned, it was given. That day I surrendered my life to Christ.\n\nToday I am free, restored, and serving in the Follow-Up Team, helping others find the same hope.", category="Testimony", author_name="Grace E.", status="published", is_featured=False, published_at=now - timedelta(days=30)),
            ]
            for st in stories:
                db.add(st)
            await db.flush()
            print(f"Seeded {len(stories)} stories")
        else:
            print(f"Stories already exist ({len(existing_stories)}), skipping")

        existing_sermons = (await db.execute(select(Sermon))).scalars().all()
        if not existing_sermons:
            now = datetime.now(timezone.utc)
            sermons = [
                Sermon(title="The Scent of Water: When Hope Returns", slug="scent-of-water-when-hope-returns", preacher="Pastor Samuel Daniel Inyeneabasi", caption="God's power to revive every dead situation at the scent of water.", description="An exposition of Job 14:7-9 on God's restorative power.", status="published", is_featured=True, published_at=now - timedelta(days=2)),
                Sermon(title="The Scent of Water", slug="the-scent-of-water", preacher="Pastor Samuel Daniel Inyeneabasi", caption="Why a cut-down tree still has hope.", description="Job 14:7-9 — how the Word and the Spirit revive what looked finished.", status="published", published_at=now - timedelta(days=9)),
                Sermon(title="Repositioned for God's Best", slug="repositioned-for-gods-best", preacher="Pastor Samuel Daniel Inyeneabasi", caption="Aligning with God's perfect will for your life.", description="A charge to step into God's very best through obedience.", status="published", published_at=now - timedelta(days=16)),
                Sermon(title="Grace Over Religion", slug="grace-over-religion", preacher="Pastor Samuel Daniel Inyeneabasi", caption="Embracing the transformative power of grace.", description="Why grace, not religion, is the anchor of our faith.", status="published", published_at=now - timedelta(days=23)),
                Sermon(title="Built to Last: Marriage God's Way", slug="built-to-last-marriage-gods-way", preacher="Pastor Samuel Daniel Inyeneabasi", caption="Biblical blueprints for a thriving marriage.", description="Principles from the Word for lasting marriages.", status="published", published_at=now - timedelta(days=35)),
                Sermon(title="Faith That Moves Mountains", slug="faith-that-moves-mountains", preacher="Pastor Samuel Daniel Inyeneabasi", caption="Activating audacious faith for impossible situations.", description="How to develop and release mountain-moving faith.", status="published", published_at=now - timedelta(days=45)),
            ]
            for s in sermons:
                db.add(s)
            await db.flush()
            print(f"Seeded {len(sermons)} sermons")
        else:
            print(f"Sermons already exist ({len(existing_sermons)}), skipping")

        email_setting = (await db.execute(select(SiteSetting).where(SiteSetting.key == "siteContactEmail"))).scalar_one_or_none()
        if not email_setting or not (email_setting.value or "").strip():
            if email_setting:
                email_setting.value = "info@scentofwater.org"
            else:
                db.add(SiteSetting(key="siteContactEmail", value="info@scentofwater.org"))
            await db.flush()
            print("Upserted siteContactEmail = info@scentofwater.org")
        else:
            print(f"siteContactEmail already set ({email_setting.value}), skipping")

        existing_events = (await db.execute(select(Event))).scalars().all()
        if not existing_events:
            base = datetime(2026, 1, 4, 9, 0)
            events = []
            for i in range(52):
                d = base + timedelta(weeks=i)
                events.append(Event(title="Sunday Celebration Service", description="Join us for worship, the Word and fellowship.", location="Main Sanctuary", starts_at=d, ends_at=d + timedelta(hours=2), is_published=True, is_major=(i % 8 == 0)))
                if d.weekday() == 6:
                    wed = d + timedelta(days=3)
                    wed = wed.replace(hour=18, minute=0)
                    events.append(Event(title="Midweek Bible Study & Prayer", description="Midweek refreshing through the Word and prayer.", location="Fellowship Hall", starts_at=wed, ends_at=wed + timedelta(hours=2), is_published=True, is_major=False))
            specials = [
                (datetime(2026, 1, 1, 22, 0), "Cross-Over Night Service", "Usher in the new year with praise and prophetic declarations.", True),
                (datetime(2026, 4, 5, 9, 0), "Easter Convention", "Celebrating the resurrection power of Christ.", True),
                (datetime(2026, 8, 15, 9, 0), "Anniversary Celebration", "Thanksgiving for God's faithfulness to the ministry.", True),
                (datetime(2026, 6, 1, 9, 0), "CASOM Session Begins", "Opening session for the Cave of Adullam School of Ministry.", False),
                (datetime(2026, 12, 24, 18, 0), "Christmas Carol & Outreach", "Carols, drama and community outreach.", True),
            ]
            for starts_at, title, desc, is_major in specials:
                events.append(Event(title=title, description=desc, location="Main Sanctuary", starts_at=starts_at, ends_at=starts_at + timedelta(hours=3), is_published=True, is_major=is_major))
            for ev in events:
                db.add(ev)
            await db.flush()
            print(f"Seeded {len(events)} events")
        else:
            print(f"Events already exist ({len(existing_events)}), skipping")

        await db.commit()
        print("Seeding complete")


if __name__ == "__main__":
    asyncio.run(seed())
