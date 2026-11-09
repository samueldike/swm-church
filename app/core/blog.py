"""Blog (admin "Blog", public "Stories"): post statuses, dates, categories, tags and safe HTML.

Modelled on WordPress posts. A post's ``status`` is one of STATUSES; "future" is WordPress's "Scheduled" and
becomes "published" by itself when its date arrives (``publish_due``, run by app/core/scheduler.py).
Trash keeps the post's previous status in ``trashed_status`` so Restore puts it back exactly.
``published_at`` is a timestamptz column: every value written here is timezone-aware UTC.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

from app.core import publication_html as ph

STATUSES = {"draft": "Draft", "pending": "Pending", "published": "Published", "future": "Scheduled",
            "private": "Private", "trash": "Trash"}
LIVE = "published"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ─── categories and tags (stored as JSON lists of names on the post) ───
def names(raw) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(raw) if raw and str(raw).strip().startswith("[") else str(raw or "").split(",")
        except ValueError:
            values = str(raw or "").split(",")
    out, seen = [], set()
    for v in values:
        v = " ".join(str(v).split())[:60]
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def dump(values: list[str]) -> str | None:
    values = names(values)
    return json.dumps(values, ensure_ascii=False) if values else None


def post_categories(post) -> list[str]:
    cats = names(getattr(post, "categories", None))
    if not cats and getattr(post, "category", None):
        cats = [post.category]
    return cats


def post_tags(post) -> list[str]:
    return names(getattr(post, "tags", None))


def set_categories(post, cats: list[str]) -> None:
    cats = names(cats)
    post.categories = dump(cats)
    post.category = cats[0][:100] if cats else None      # the public cards show the first one


# ─── status from the editor's choices, the WordPress way ───
def decide_status(action: str, chosen: str, when: datetime | None, now: datetime | None = None) -> str:
    """``action``: draft | publish | update | pending. ``chosen``: the sidebar status. ``when``: aware UTC or None."""
    now = now or now_utc()
    if action == "draft":
        return "draft"
    if action == "pending":
        return "pending"
    if chosen == "private":
        return "private"
    # Publish / Update / Schedule: a date in the future means Scheduled, otherwise it goes live now.
    return "future" if when and when > now else "published"


# ─── safe HTML for post content (the publication whitelist plus images and code) ───
_IMG_SRC_OK = re.compile(r"^(https://|/static/|/media/)[^\s\"'<>]*$", re.I)


class _BlogSanitizer(ph._Sanitizer):
    EXTRA = {"h5", "h6", "pre", "code", "figure", "figcaption"}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip:
            return
        if tag == "img":
            a = dict((k.lower(), v or "") for k, v in attrs)
            src = a.get("src", "").strip()
            if _IMG_SRC_OK.match(src):
                alt = html.escape(a.get("alt", "")[:300], quote=True)
                self.out.append(f'<img src="{html.escape(src, quote=True)}" alt="{alt}" loading="lazy">')
            return
        if tag in self.EXTRA:
            self.out.append(f"<{tag}>")
            self.stack.append(tag)
            return
        super().handle_starttag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "img":
            return self.handle_starttag(tag, attrs)
        super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.EXTRA and not self.skip and tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break
            return
        super().handle_endtag(tag)


def sanitize(raw: str | None) -> str:
    p = _BlogSanitizer()
    p.feed(raw or "")
    p.close()
    return p.result()


def words(raw: str | None) -> int:
    return len(re.sub(r"<[^>]+>", " ", raw or "").split())


async def publish_due() -> int:
    """Scheduled posts whose time has come go live (called by the scheduler)."""
    from sqlalchemy import update

    from app.database import async_session
    from app.models.story import Story

    async with async_session() as db:
        res = await db.execute(update(Story).where(Story.status == "future", Story.published_at <= now_utc()).values(status=LIVE))
        await db.commit()
        return res.rowcount or 0
