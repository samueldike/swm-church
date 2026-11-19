"""Publishing shorts to social media through Zernio (https://zernio.com, API reference docs.zernio.com).

* The key is ``zernio_api_key`` (Admin → Settings → Social publishing, encrypted, write-only).
* Each church gets its own Zernio *profile*, found by ``externalId = swm-b<branch id>`` and created on first use, so
  a branch only ever sees and posts to the accounts it connected itself.
* Accounts are connected on Zernio's own OAuth pages (``GET /v1/connect/{platform}`` returns ``authUrl``); the
  app never holds a platform password or token.
* A post: the short's MP4 is PUT to a presigned URL (``POST /v1/media/presign``), then ``POST /v1/posts`` with the
  caption, the chosen accounts, per-platform options and ``publishNow`` or ``scheduledFor`` (UTC).

The ministry's own hashtags (``clipper_ministry_hashtags``, 1 or 2) are put on every caption (``with_ministry``).
Pure helpers are tested in tests/test_clipper.py; nothing here talks to Zernio without a key.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

BASE = "https://zernio.com/api/v1"
TIMEOUT = 60.0

# Platforms a short can go to, in the order the screens show them: (id, label, icon).
PLATFORMS = [
    ("youtube", "YouTube Shorts", "ri-youtube-line"),
    ("tiktok", "TikTok", "ri-tiktok-line"),
    ("instagram", "Instagram Reels", "ri-instagram-line"),
    ("facebook", "Facebook Reels", "ri-facebook-circle-line"),
    ("threads", "Threads", "ri-threads-line"),
    ("twitter", "X", "ri-twitter-x-line"),
    ("linkedin", "LinkedIn", "ri-linkedin-box-line"),
    ("pinterest", "Pinterest", "ri-pinterest-line"),
    ("bluesky", "Bluesky", "ri-bluesky-line"),
]
PLATFORM_IDS = {p[0] for p in PLATFORMS}
LABELS = {p[0]: p[1] for p in PLATFORMS}
ICONS = {p[0]: p[2] for p in PLATFORMS}
# The church's own WhatsApp goes through app/core/whatsapp.py, not Zernio (labels for the screens only).
LABELS["whatsapp"], ICONS["whatsapp"] = "WhatsApp", "ri-whatsapp-line"
TIKTOK_PRIVACY = ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"]
CAPTION_MAX = 2200        # Instagram/TikTok caption ceiling; X is shortened per platform below
X_MAX = 280


class SocialError(Exception):
    """Something the person can act on (shown as is)."""


# ─── ministry hashtags (pure) ─────────────────────────────────────────

def clean_tag(tag: str) -> str:
    return re.sub(r"[^\w]", "", str(tag or "").lstrip("#"))[:60]


def parse_ministry_tags(text: str) -> list[str]:
    """'#ScentOfWater, #SWM' -> ['ScentOfWater', 'SWM']: unique, in order."""
    out: list[str] = []
    for raw in re.split(r"[\s,;]+", str(text or "")):
        t = clean_tag(raw)
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out


def ministry_tags_problem(text: str) -> str | None:
    tags = parse_ministry_tags(text)
    if not tags:
        return "Enter at least one ministry hashtag."
    if len(tags) > 2:
        return "Use at most two ministry hashtags, so they stand out next to the topic tags."
    return None


def with_ministry(caption: str, ministry: list[str], *, max_tags: int = 7) -> str:
    """The caption with the ministry hashtags always present (first on the hashtag line), never twice, and at most
    ``max_tags`` hashtags in all (topic tags beyond that are dropped, the ministry's never are)."""
    text = str(caption or "").strip()
    if not ministry:
        return text
    lines = text.split("\n")
    tag_line_at = next((i for i in range(len(lines) - 1, -1, -1)
                        if lines[i].strip() and all(w.startswith("#") for w in lines[i].split())), None)
    body = [l for i, l in enumerate(lines) if i != tag_line_at]
    existing = lines[tag_line_at].split() if tag_line_at is not None else []
    body_text = "\n".join(body).strip()
    inline = {m.lower() for m in re.findall(r"#(\w+)", body_text)}
    own = [t for t in ministry if t.lower() not in inline]
    topic = [clean_tag(w) for w in existing]
    topic = [t for t in topic if t and t.lower() not in {m.lower() for m in ministry}]
    room = max(0, max_tags - len(ministry))
    line = " ".join("#" + t for t in own + topic[:room])
    return f"{body_text}\n{line}".strip() if line else body_text


def for_platform(caption: str, platform: str) -> str:
    """X has a 280-character ceiling: keep the hashtag line, shorten the words before it."""
    if platform != "twitter" or len(caption) <= X_MAX:
        return caption[:CAPTION_MAX]
    lines = caption.split("\n")
    tags = lines[-1] if lines and lines[-1].startswith("#") else ""
    body = "\n".join(lines[:-1] if tags else lines)
    room = X_MAX - len(tags) - 2
    body = (body[: max(0, room - 1)].rsplit(" ", 1)[0] + "…") if len(body) > room else body
    return f"{body}\n{tags}".strip() if tags else body[:X_MAX]


# ─── the Zernio API ───────────────────────────────────────────────────

async def setting(key: str) -> str:
    from app.core.settings_cache import get_setting
    from app.database import async_session

    try:
        async with async_session() as db:
            return (await get_setting(db, key, "")).strip()
    except Exception:
        return ""


async def api_key() -> str:
    return await setting("zernio_api_key")


async def ministry_tags() -> list[str]:
    return parse_ministry_tags(await setting("clipper_ministry_hashtags"))[:2]


def _raise(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    try:
        data = r.json()
        msg = data.get("error") or data.get("message") or ""
        code = data.get("code") or ""
    except ValueError:
        msg, code = r.text[:200], ""
    if r.status_code == 401:
        raise SocialError("Zernio refused the saved API key. The HQ Administrator can replace it in Settings → Social publishing.")
    if r.status_code == 402:
        raise SocialError(f"Zernio needs a paid plan or balance for this: {msg or code}".strip())
    if r.status_code == 429:
        raise SocialError("Zernio is rate-limiting this account. Try again in a minute.")
    raise SocialError(f"Zernio said: {msg or code or r.status_code}"[:400])


async def _call(method: str, path: str, *, key: str | None = None, **kw) -> dict:
    key = key or await api_key()
    if not key:
        raise SocialError("Social publishing is not set up yet. The HQ Administrator adds the Zernio API key in Settings → Social publishing.")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.request(method, BASE + path, headers={"Authorization": f"Bearer {key}"}, **kw)
    except httpx.HTTPError as exc:
        raise SocialError(f"Zernio could not be reached ({exc.__class__.__name__}). Try again shortly.") from exc
    _raise(r)
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {}


def external_id(branch_id: int) -> str:
    return f"swm-b{int(branch_id)}"


async def profile_id(branch_id: int, name: str) -> str:
    """The church's own Zernio profile (created on first use)."""
    ext = external_id(branch_id)
    data = await _call("GET", "/profiles", params={"externalId": ext})
    rows = data.get("profiles") or []
    if rows:
        return str(rows[0].get("_id"))
    made = await _call("POST", "/profiles", json={"name": (name or ext)[:100], "externalId": ext,
                                                   "description": "Scent of Water church: Video Clipper"})
    pid = (made.get("profile") or {}).get("_id")
    if not pid:
        raise SocialError("Zernio did not create a profile for this church.")
    return str(pid)


async def accounts(profile: str) -> list[dict]:
    data = await _call("GET", "/accounts", params={"profileId": profile})
    out = []
    for a in data.get("accounts") or []:
        if a.get("platform") not in PLATFORM_IDS:
            continue
        out.append({"id": str(a.get("_id")), "platform": a.get("platform"),
                    "name": a.get("displayName") or a.get("username") or a.get("platform"),
                    "username": a.get("username") or "", "url": a.get("profileUrl") or "",
                    "picture": a.get("profilePicture") or "", "active": bool(a.get("isActive", True)),
                    "reconnect": bool(a.get("needsReconnection"))})
    return out


async def connect_url(platform: str, profile: str, redirect: str) -> str:
    if platform not in PLATFORM_IDS:
        raise SocialError("That platform cannot be connected here.")
    data = await _call("GET", f"/connect/{platform}", params={"profileId": profile, "redirect_url": redirect})
    url = data.get("authUrl") or data.get("url")
    if not url:
        raise SocialError("Zernio did not return a sign-in link.")
    return str(url)


async def disconnect(account_id: str) -> None:
    await _call("DELETE", f"/accounts/{account_id}")


async def upload(path: Path, *, key: str | None = None) -> str:
    """PUT the file to Zernio's storage; returns its public URL for ``mediaItems``."""
    size = path.stat().st_size
    pre = await _call("POST", "/media/presign", key=key,
                      json={"filename": path.name, "contentType": "video/mp4", "size": size})
    put, public = pre.get("uploadUrl"), pre.get("publicUrl")
    if not put or not public:
        raise SocialError("Zernio did not hand over an upload link.")

    def chunks():
        with path.open("rb") as fh:
            while True:
                b = fh.read(1 << 20)
                if not b:
                    break
                yield b

    try:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.put(put, content=chunks(), headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    except httpx.HTTPError as exc:
        raise SocialError(f"Uploading the short to Zernio failed ({exc.__class__.__name__}).") from exc
    if r.status_code >= 400:
        raise SocialError(f"Uploading the short to Zernio failed ({r.status_code}).")
    return str(public)


def build_post(*, caption: str, title: str, media_url: str, targets: list[dict], when_utc: str | None,
               tz: str, tags: list[str]) -> dict:
    """The POST /v1/posts body (pure; tested). ``targets``: [{platform, accountId, options}]."""
    platforms = []
    for t in targets:
        p, opts = t["platform"], t.get("options") or {}
        entry: dict = {"platform": p, "accountId": t["accountId"]}
        text = for_platform(caption, p)
        if text != caption:
            entry["customContent"] = text
        data: dict = {}
        if p == "youtube":
            data = {"title": (opts.get("title") or title)[:100], "visibility": opts.get("visibility") or "public",
                    "madeForKids": bool(opts.get("madeForKids")), "containsSyntheticMedia": False}
        elif p == "tiktok":
            data = {"privacyLevel": opts.get("privacyLevel") or "PUBLIC_TO_EVERYONE",
                    "allowComment": opts.get("allowComment", True), "allowDuet": opts.get("allowDuet", True),
                    "allowStitch": opts.get("allowStitch", True), "contentPreviewConfirmed": True,
                    "expressConsentGiven": True, "commercialContentType": "none", "mediaType": "video"}
        elif p == "facebook":
            data = {"contentType": "reel", "title": title[:100]}
        elif p == "pinterest":
            data = {"title": title[:100]}
        if data:
            entry["platformSpecificData"] = data
        platforms.append(entry)
    body: dict = {"title": title[:200], "content": caption[:CAPTION_MAX], "platforms": platforms,
                  "mediaItems": [{"type": "video", "url": media_url}],
                  "tags": [t for t in tags if t][:15], "timezone": tz or "UTC", "metadata": {"source": "swm-clipper"}}
    if when_utc:
        body["scheduledFor"] = when_utc
    else:
        body["publishNow"] = True
    return body


async def create_post(body: dict, *, key: str | None = None) -> dict:
    return await _call("POST", "/posts", key=key, json=body)


async def get_post(post_id: str) -> dict:
    return await _call("GET", f"/posts/{post_id}")


def summarize(data: dict) -> tuple[str, list[dict]]:
    """(overall status, [{platform, status, url, error}]) from a Zernio post reply (pure; tested)."""
    post = data.get("post") or data
    rows = []
    for p in post.get("platforms") or []:
        rows.append({"platform": p.get("platform"), "status": str(p.get("status") or post.get("status") or ""),
                     "url": p.get("platformPostUrl") or "",
                     "error": str(p.get("errorMessage") or p.get("error") or "")[:300]})
    status = str(post.get("status") or "")
    if rows and all(r["status"] == "published" for r in rows):
        status = "published"
    elif rows and any(r["status"] == "failed" for r in rows):
        status = "partial" if any(r["status"] == "published" for r in rows) else "failed"
    return status or "scheduled", rows


# ─── the church's own WhatsApp (app/core/whatsapp.py: Status + groups/channels, sent by the scheduler) ───

WA_STATUS_MAX_SECONDS = 60      # WhatsApp Status plays at most a minute of video
WA_STATUS_CAPTION = 700         # WhatsApp shortens longer Status captions
WA_MESSAGE_CAPTION = 3000


def whatsapp_caption(title: str, caption: str, ministry: list[str], *, limit: int = WA_MESSAGE_CAPTION) -> str:
    """A caption written for WhatsApp: the title in WhatsApp bold, the words as plain text, no topic hashtags (they do
    nothing on WhatsApp), the ministry's own hashtags kept as a sign-off (pure; tested)."""
    lines = [ln for ln in str(caption or "").strip().split("\n")]
    if lines and lines[-1].strip() and all(w.startswith("#") for w in lines[-1].split()):
        lines = lines[:-1]
    body = "\n".join(lines).strip()
    body = re.sub(r"(?<!\w)#(\w+)", r"\1", body)                 # inline #tags read as words
    head = re.sub(r"[*_~`]", "", str(title or "")).strip()
    sign = " ".join("#" + t for t in ministry)
    parts = [f"*{head}*" if head else "", body, sign]
    text = "\n\n".join(p for p in parts if p)
    if len(text) > limit:
        room = limit - len(sign) - (2 if sign else 0) - 1
        core = "\n\n".join(p for p in parts[:2] if p)
        text = core[: max(0, room)].rsplit(" ", 1)[0] + "…" + (f"\n\n{sign}" if sign else "")
    return text


def overall(rows: list[dict]) -> str:
    """One status for a publish across Zernio platforms and WhatsApp (pure; tested)."""
    states = [r.get("status") for r in rows]
    if not states:
        return "scheduled"
    if all(s == "published" for s in states):
        return "published"
    if any(s == "failed" for s in states):
        return "partial" if any(s == "published" for s in states) else (
            "failed" if all(s == "failed" for s in states) else "partial")
    return "scheduled"
