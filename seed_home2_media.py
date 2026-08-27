"""Seed the admin-managed photo collections and extra audio messages.

Idempotent: re-running skips photos already registered and sermons whose slug
already exists. Safe to run against an existing database.

    python seed_home2_media.py
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.database import async_session, init_db

STATIC = Path(__file__).resolve().parent / "app" / "static"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# All thirteen outreach frames are watermarked "2023 Annual Community Medical
# Outreach", so captions state that rather than inventing scene descriptions.
# Admins can refine any of them from /admin/photos.
OUTREACH_CAPTIONS = {
    "outreach-01.jpg": "Unloading relief supplies — 2023 Annual Community Medical Outreach",
    "outreach-07.jpg": "Registering families for free care — 2023 Annual Community Medical Outreach",
}
OUTREACH_DEFAULT = "2023 Annual Community Medical Outreach"


def optimise(path: Path, max_side: int = 1600, quality: int = 84) -> None:
    """Downscale oversized source photos in place; skip if already small."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return
    try:
        im = Image.open(path)
        if max(im.size) <= max_side:
            return
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        im.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    except Exception as exc:  # pragma: no cover - best effort
        print(f"  ! could not optimise {path.name}: {exc}")


async def seed_photos(db) -> None:
    from app.models.photo import Photo

    for collection, folder, caption_for in (
        (Photo.GALLERY, "images/gallery", lambda _n: None),
        (Photo.OUTREACH, "images/outreach",
         lambda n: OUTREACH_CAPTIONS.get(n, OUTREACH_DEFAULT)),
    ):
        directory = STATIC / folder
        if not directory.is_dir():
            print(f"[{collection}] no folder at {directory}, skipped")
            continue

        existing = set(
            (await db.execute(
                select(Photo.image_path).where(Photo.collection == collection)
            )).scalars().all()
        )
        # Rows may have been stored with or without a leading slash.
        existing |= {p.lstrip("/") for p in existing}

        added = 0
        for order, f in enumerate(sorted(directory.iterdir())):
            if not f.is_file() or f.suffix.lower() not in EXTS:
                continue
            rel = f"{folder}/{f.name}"
            if rel in existing:
                continue
            optimise(f)
            db.add(Photo(
                collection=collection,
                image_path=rel,
                caption=caption_for(f.name),
                sort_order=order,
                is_active=True,
            ))
            added += 1

        total = len(existing) + added
        print(f"[{collection}] added {added} (collection now ~{total})")


async def seed_messages(db) -> None:
    """Top the published archive up to at least 7 so the homepage cap of 5 is
    demonstrably a cap and not just 'everything we had'."""
    from app.models.sermon import Sermon

    now = datetime.now(timezone.utc)
    extras = [
        ("Roots in Dry Ground", "Why the unseen season decides the visible harvest.",
         "Job 14:7-9 — what keeps a cut-down tree alive underground.", 3),
        ("The Grace That Repositions", "Grace is not a licence; it is a relocation.",
         "How the grace of God moves a man from where he settled to where he belongs.", 12),
        ("Knowledge and Inheritance", "You cannot enjoy what you do not know you own.",
         "Equipping believers to possess their inheritance in Christ.", 19),
        ("The Epaphras Pattern", "Labouring in prayer until it is formed.",
         "A study on intercession that does not quit early.", 26),
        ("Excellence as Worship", "Models of excellence in every sphere.",
         "Why the standard of the house is an act of worship, not vanity.", 34),
    ]

    added = 0
    for title, caption, description, days_ago in extras:
        slug = title.lower().replace("'", "").replace(",", "").replace(" ", "-")
        exists = (await db.execute(
            select(Sermon.id).where(Sermon.slug == slug)
        )).scalar_one_or_none()
        if exists:
            continue
        db.add(Sermon(
            title=title,
            slug=slug,
            caption=caption,
            description=description,
            preacher="Pastor Samuel Daniel Inyeneabasi",
            status="published",
            type="audio",
            published_at=now - timedelta(days=days_ago),
        ))
        added += 1

    published = (await db.execute(
        select(Sermon).where(Sermon.status == "published")
    )).scalars().all()
    print(f"[messages] added {added} (published total {len(published)})")


async def main() -> None:
    await init_db()
    async with async_session() as db:
        await seed_photos(db)
        await seed_messages(db)
        await db.commit()
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())
