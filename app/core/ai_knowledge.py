from __future__ import annotations

import asyncio
import io
import ipaddress
import re
import socket
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import httpx

MAX_FILE = 5 * 1024 * 1024
MAX_TEXT = 100000


class KnowledgeError(ValueError):
    pass


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def clean_text(text):
    text = text.replace('\x00', '').strip()
    if not text:
        raise KnowledgeError('No readable text was found. For scanned PDFs, paste the text instead.')
    if len(text) > MAX_TEXT:
        raise KnowledgeError('Each entry can contain up to 100,000 characters. Please split this document.')
    return text


def extract_file(name: str, raw: bytes) -> str:
    if len(raw) > MAX_FILE:
        raise KnowledgeError('Files must be 5 MB or smaller.')
    ext = Path(name).suffix.lower()
    try:
        if ext == '.txt':
            text = raw.decode('utf-8-sig')
        elif ext == '.docx':
            from docx import Document
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 25 * 1024 * 1024:
                    raise KnowledgeError('The expanded document is too large.')
            doc = Document(io.BytesIO(raw))
            text = '\n'.join([p.text for p in doc.paragraphs] +
                             [' | '.join(c.text for c in row.cells) for table in doc.tables for row in table.rows])
        elif ext == '.pdf':
            from pypdf import PdfReader
            doc = PdfReader(io.BytesIO(raw))
            if doc.is_encrypted or len(doc.pages) > 150:
                raise KnowledgeError('Use an unencrypted PDF of 150 pages or fewer.')
            parts = []
            for page in doc.pages:
                parts.append(page.extract_text() or '')
                if sum(map(len, parts)) > MAX_TEXT:
                    raise KnowledgeError('Please split this PDF into smaller entries.')
            text = '\n'.join(parts)
        else:
            raise KnowledgeError('Upload a .txt, .pdf or .docx file.')
        return clean_text(text)
    except KnowledgeError:
        raise
    except Exception as exc:
        raise KnowledgeError('Could not read that file. Use a UTF-8 text file, text-based PDF or Word document.') from exc


async def fetch_link(raw_url: str) -> str:
    """Import a public HTTPS page; pin validated DNS to prevent internal requests."""
    try:
        url = httpx.URL(raw_url)
        async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
            for _ in range(5):
                if url.scheme != 'https' or not url.host or url.port not in (None, 443) or url.username or url.password:
                    raise KnowledgeError('Use a public HTTPS link without login details or custom ports.')
                addresses = await asyncio.to_thread(socket.getaddrinfo, url.host, 443, type=socket.SOCK_STREAM)
                ips = [item[4][0] for item in addresses]
                if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                    raise KnowledgeError('Only public website links can be imported.')
                async with client.stream('GET', url.copy_with(host=ips[0]),
                                         headers={'Host': url.host, 'User-Agent': 'ScentAI-Knowledge/1.0'},
                                         extensions={'sni_hostname': url.host}) as response:
                    if response.is_redirect:
                        location = response.headers.get('location')
                        if not location:
                            raise KnowledgeError('This link has an invalid redirect.')
                        url = url.join(location)
                        continue
                    response.raise_for_status()
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > MAX_FILE:
                            raise KnowledgeError('The linked page or file must be 5 MB or smaller.')
                    mime = response.headers.get('content-type', '').split(';')[0]
                    if mime == 'application/pdf':
                        return await asyncio.to_thread(extract_file, 'source.pdf', bytes(raw))
                    if mime == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
                        return await asyncio.to_thread(extract_file, 'source.docx', bytes(raw))
                    if mime not in ('text/html', 'application/xhtml+xml', 'text/plain'):
                        raise KnowledgeError('The link must lead to a web page, text, PDF or Word document.')
                    text = bytes(raw).decode(response.encoding or 'utf-8', errors='replace')
                    if mime != 'text/plain':
                        parser = PageText(); parser.feed(text)
                        text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
                    return clean_text(text)
            raise KnowledgeError('This link redirects too many times.')
    except KnowledgeError:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise KnowledgeError('Could not import this link. Try a direct public link or paste the text.') from exc


def select_context(items: list[dict], question: str, budget: int = 180000) -> list[dict]:
    """Keep full sources when they fit; otherwise retrieve passages across all sources."""
    import json
    if len(json.dumps(items, ensure_ascii=False)) <= budget:
        return items
    terms = set(re.findall(r'\w{3,}', question.lower())) - {'the', 'and', 'what', 'how', 'does', 'about', 'that', 'with'}
    ranked = []
    for item in items:
        text = item.get('text', '')
        for start in range(0, len(text), 2500):
            chunk = text[max(0, start - 250):start + 2750]
            haystack = (item.get('title', '') + ' ' + chunk).lower()
            score = sum(1 + min(haystack.count(t), 5) for t in terms if t in haystack)
            ranked.append((score, {**item, 'text': chunk}))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    selected = []; size = 0
    for _, item in ranked:
        length = len(json.dumps(item, ensure_ascii=False))
        if size + length <= budget:
            selected.append(item); size += length
    return selected

async def ministry_context(db):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from sqlalchemy import select
    from app.models.ai_knowledge import AIKnowledge
    from app.models.event import Event
    from app.core.settings_cache import get_settings
    from app.core.platform import platform_timezone
    from app.core.site_content import get_services, get_expect, get_teaching, clean_phone
    from app.core.ics import event_tz, event_ends

    site = await get_settings(db)
    tz = await platform_timezone(db)
    now = datetime.now(ZoneInfo(tz))
    # Deliberate allowlist: credentials and internal settings never enter AI context.
    profile = {key: site.get(key, '') for key in (
        'siteName', 'siteTagline', 'siteContactEmail', 'siteHeadquartersAddress',
        'ourStory', 'pastorStory', 'whatsappChannelUrl', 'telegramJoinUrl', 'siteUrl')}
    profile['phone'] = clean_phone(site.get('siteSupportPhone'))
    profile['services'] = get_services(site)
    profile['visit'] = get_expect(site)
    profile['beliefs'] = get_teaching(site)
    entries = (await db.execute(select(AIKnowledge).where(AIKnowledge.is_active == True).order_by(AIKnowledge.id))).scalars().all()
    knowledge = [{'kind': 'ministry', 'id': k.id, 'title': k.title, 'text': k.content} for k in entries]
    events = (await db.execute(select(Event).where(Event.is_published == True).order_by(Event.starts_at))).scalars().all()
    calendar = []
    for ev in events:
        zone = ZoneInfo(event_tz(ev, tz))
        start = ev.starts_at.replace(tzinfo=zone)
        end = event_ends(ev).replace(tzinfo=zone)
        state = 'past' if end < now else ('upcoming' if start > now else 'ongoing')
        calendar.append({'id': ev.id, 'title': ev.title, 'description': ev.description,
                         'location': ev.location, 'starts_at': start.isoformat(),
                         'ends_at': end.isoformat(), 'timezone': str(zone), 'state': state})
    return {'current_time': now.isoformat(), 'timezone': tz, 'profile': profile,
            'knowledge': knowledge, 'events': calendar}


def public_source_items(ministry):
    import json
    items = list(ministry['knowledge'])
    profile = ministry['profile']
    # Settings are structured public facts, represented as plain text for faithful quoting.
    for index, (name, value) in enumerate(profile.items(), 1):
        if not value:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        items.append({'kind': 'profile', 'id': index, 'title': 'Ministry information · ' + name, 'text': text})
    for event in ministry['events']:
        text = (f"{event['title']}\nStatus: {event['state']}\n"
                f"Starts: {event['starts_at']}\nEnds: {event['ends_at']}\nTime zone: {event['timezone']}\n"
                f"Location: {event['location'] or 'Not specified'}\n{event['description'] or ''}")
        items.append({'kind': 'event', 'id': event['id'], 'title': event['title'], 'text': text})
    return items

