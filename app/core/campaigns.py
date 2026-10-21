"""Building and rendering subscriber emails (new message, devotional day, or free text)."""
from __future__ import annotations

import re
from html import escape

from app.core import mailer
from app.core.publication_html import sanitize

_URL = re.compile(r"(https?://[^\s<>\"']+)")


def text_to_html(text: str) -> str:
    """Plain text the administrator typed -> safe email HTML: blank line = new paragraph, single newline = line break,
    bare web addresses become links. Everything else is escaped."""
    out = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        if not block.strip():
            continue
        safe = escape(block.strip()).replace("\n", "<br/>")
        safe = _URL.sub(lambda m: f'<a href="{m.group(1)}" style="color:#8F6A14;">{m.group(1)}</a>', safe)
        out.append(f'<p style="margin:0 0 16px;">{safe}</p>')
    return sanitize("".join(out))


async def render_campaign(snap: dict, token: str, base_url: str) -> tuple[str, str, dict]:
    ctx = await mailer.site_context(base_url)
    unsub = f"{base_url.rstrip('/')}/unsubscribe/{token}" if token else ""
    ctx.update(subject=snap["subject"], preheader=snap.get("preheader") or "", body_html=snap["body_html"], button=snap.get("button"), unsubscribe_url=unsub)
    html, text = mailer.render_email("campaign", **ctx)
    headers = {"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"} if unsub else {}
    return html, text, headers


def message_draft(sermon, base_url: str) -> dict:
    """A 'new message' email for a published audio message."""
    lines = []
    if sermon.caption:
        lines.append(sermon.caption.strip())
    if sermon.description and sermon.description.strip() != (sermon.caption or "").strip():
        lines.append(sermon.description.strip()[:900])
    meta = " · ".join(x for x in (sermon.preacher, sermon.scripture, sermon.series) if x)
    if meta:
        lines.insert(0, meta)
    return {"subject": f"New message: {sermon.title}", "preheader": (sermon.caption or sermon.title)[:140], "body_text": "\n\n".join(lines) or sermon.title,
            "button_label": "Listen now", "button_url": f"{base_url.rstrip('/')}/messages/{sermon.slug}", "kind": "message"}


def devotional_draft(sub, publication_title: str = "") -> dict:
    """A 'devotional' email from one day (subtopic) of a Books Pub devotional."""
    parts = []
    if sub.scripture:
        parts.append(sub.scripture.strip())
    if sub.body:
        parts.append(sub.body.strip())
    if sub.action_plan:
        parts.append("Take action\n" + sub.action_plan.strip())
    if sub.prayer_point:
        parts.append("Pray\n" + sub.prayer_point.strip())
    return {"subject": sub.title, "preheader": ((sub.body or "").strip().split("\n")[0])[:140], "body_text": "\n\n".join(parts),
            "button_label": None, "button_url": None, "kind": "devotional"}
