"""Books Pub service layer: periods, progress, AI drafting jobs and document assembly."""
from __future__ import annotations

import asyncio
import calendar
import html
import logging
import re
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core import ai
from app.core import publication_prompts as prompts
from app.core.publication_html import split_blocks, word_count
from app.database import async_session
from app.models.publication import Publication, PublicationSource, PublicationSubtopic

logger = logging.getLogger(__name__)

KINDS = {
    "devotionals": {"label": "Daily Devotional", "plural": "Daily Devotionals", "icon": "ri-sun-line",
                    "unit": "day", "main": "Main topic", "sub": "Day topic"},
    "books": {"label": "Book", "plural": "Books", "icon": "ri-book-2-line",
              "unit": "section", "main": "Chapter", "sub": "Section"},
}
DURATION_TYPES = [("year", "Full year"), ("half", "Half year"), ("quarter", "Quarter"), ("month", "Month"), ("custom", "Custom dates")]
MAX_DAYS = 400


# ─── Duration ────────────────────────────────────────────────────────

def compute_period(duration_type: str, year: int | None, period_index: int | None,
                   start: date | None, end: date | None) -> tuple[date, date, str]:
    """Resolve the form's duration choice to concrete dates and a label. Raises ValueError."""
    if duration_type == "custom":
        if not start or not end:
            raise ValueError("Choose a start and an end date.")
        if end < start:
            raise ValueError("The end date must be after the start date.")
        if (end - start).days + 1 > MAX_DAYS:
            raise ValueError(f"A devotional can cover at most {MAX_DAYS} days.")
        return start, end, f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"

    if not year or not (1900 <= year <= 2200):
        raise ValueError("Enter a valid year.")
    if duration_type == "year":
        return date(year, 1, 1), date(year, 12, 31), f"Year {year}"
    if duration_type == "half":
        if period_index not in (1, 2):
            raise ValueError("Choose the first or second half.")
        m1, m2 = (1, 6) if period_index == 1 else (7, 12)
        return date(year, m1, 1), date(year, m2, calendar.monthrange(year, m2)[1]), f"Half {period_index} {year}"
    if duration_type == "quarter":
        if period_index not in (1, 2, 3, 4):
            raise ValueError("Choose a quarter from 1 to 4.")
        m1 = (period_index - 1) * 3 + 1
        m2 = m1 + 2
        return date(year, m1, 1), date(year, m2, calendar.monthrange(year, m2)[1]), f"Quarter {period_index} {year}"
    if duration_type == "month":
        if period_index not in range(1, 13):
            raise ValueError("Choose a month.")
        return (date(year, period_index, 1), date(year, period_index, calendar.monthrange(year, period_index)[1]),
                f"{calendar.month_name[period_index]} {year}")
    raise ValueError("Choose a duration.")


def period_label(pub: Publication) -> str:
    if not pub.is_devotional or not pub.duration_type:
        return ""
    try:
        return compute_period(pub.duration_type, pub.year, pub.period_index, pub.start_date, pub.end_date)[2]
    except ValueError:
        return ""


# ─── Loading & progress ──────────────────────────────────────────────

def publication_query(pub_id: int | None = None, kind: str | None = None):
    q = select(Publication).options(
        selectinload(Publication.sources).selectinload(PublicationSource.sermon),
        selectinload(Publication.sources).selectinload(PublicationSource.subtopics),
    )
    if pub_id is not None:
        q = q.where(Publication.id == pub_id)
    if kind:
        q = q.where(Publication.kind == kind)
    return q


def numbered_subtopics(pub: Publication) -> list[tuple[int, PublicationSource, PublicationSubtopic]]:
    """Subtopics that have content, in publication order, with their day/section number."""
    out, n = [], 0
    for src in pub.sources:
        for sub in src.subtopics:
            if sub.has_content:
                n += 1
                out.append((n, src, sub))
    return out


def progress(pub: Publication) -> dict:
    subs = [s for src in pub.sources for s in src.subtopics]
    with_content = [s for s in subs if s.has_content]
    total_days = pub.total_days
    p = {
        "messages": len(pub.sources),
        "no_transcript": sum(1 for s in pub.sources if not (s.sermon and (s.sermon.transcript or "").strip())),
        "transcribing": sum(1 for s in pub.sources if s.sermon and s.sermon.transcription_status in ("queued", "processing")),
        "with_topics": sum(1 for s in pub.sources if s.subtopics),
        "subtopics": len(subs),
        "with_content": len(with_content),
        "without_content": len(subs) - len(with_content),
        "words": sum(s.word_count or 0 for s in with_content),
        "days_total": total_days,
        "days_filled": min(len(with_content), total_days) if total_days else len(with_content),
        "days_missing": max(total_days - len(with_content), 0) if total_days else 0,
        "overflow": max(len(with_content) - total_days, 0) if total_days else 0,
        "topics_busy": sum(1 for s in pub.sources if s.topics_status in ("queued", "processing")),
        "content_busy": sum(1 for s in subs if s.content_status in ("queued", "processing")),
        "topics_pending": sum(1 for s in pub.sources if not s.subtopics and s.topics_status != "processing" and s.topics_status != "queued"),
        "has_document": bool(pub.document_html),
    }
    p["busy"] = bool(p["topics_busy"] or p["content_busy"] or p["transcribing"])
    return p


def signature(pub: Publication) -> str:
    """Changes whenever anything the workspace displays about running jobs changes."""
    return "|".join(
        [f"s{s.id}:{s.topics_status}:{len(s.subtopics)}:{s.sermon.transcription_status if s.sermon else ''}" for s in pub.sources]
        + [f"t{t.id}:{t.content_status}:{t.word_count}" for s in pub.sources for t in s.subtopics]
    )


# ─── Job dispatch (Celery, with an in-process fallback for dev) ──────

_local_jobs: set[asyncio.Task] = set()


def _run_local(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _local_jobs.add(task)
    task.add_done_callback(_local_jobs.discard)


async def enqueue_topics(pub_id: int) -> None:
    from app.core.jobs import enqueue
    from app.tasks import generate_topics_task

    if not await enqueue(generate_topics_task, pub_id):
        logger.warning("Celery unavailable; running topic generation in-process")
        _run_local(run_topics(pub_id))


async def enqueue_contents(pub_id: int) -> None:
    from app.core.jobs import enqueue
    from app.tasks import generate_contents_task

    if not await enqueue(generate_contents_task, pub_id):
        logger.warning("Celery unavailable; running content generation in-process")
        _run_local(run_contents(pub_id))


# ─── AI: topics ──────────────────────────────────────────────────────

def _clean(text: object) -> str:
    return re.sub(r"\*\*|__", "", str(text or "")).strip()


async def _claim(db, model, row_id: int, field: str) -> bool:
    """queued -> processing, atomically, so parallel jobs never double-run a row."""
    res = await db.execute(
        update(model).where(model.id == row_id, getattr(model, field) == "queued").values({field: "processing"})
    )
    await db.commit()
    return res.rowcount == 1


async def run_topics(pub_id: int) -> None:
    async with async_session() as db:
        pub = (await db.execute(publication_query(pub_id))).scalar_one_or_none()
        if not pub:
            return
        kind = pub.kind
        queued = [s.id for s in pub.sources if s.topics_status == "queued"]

    for source_id in queued:
        await _topics_for_source(kind, source_id)


async def _topics_for_source(kind: str, source_id: int) -> None:
    async with async_session() as db:
        if not await _claim(db, PublicationSource, source_id, "topics_status"):
            return
        src = (await db.execute(
            select(PublicationSource).options(selectinload(PublicationSource.sermon)).where(PublicationSource.id == source_id)
        )).scalar_one()
        transcript = ((src.sermon.transcript if src.sermon else "") or "").strip()
        sermon_title = src.sermon.title if src.sermon else ""

    try:
        if not transcript:
            raise ai.AIError("This message has no transcript yet.")
        reply = await ai.complete(
            prompts.topics_system(kind),
            [f"Message title (for reference): {sermon_title}\n\nMESSAGE TRANSCRIPT:\n{transcript}", prompts.TOPICS_JSON_INSTRUCTION],
            max_tokens=6000,
        )
        data = ai.extract_json(reply)
        raw = data.get("subtopics")
        if not isinstance(raw, list) or not raw:
            raise ai.AIError("The model returned no subtopics.")
        topics = []
        for item in raw:
            if isinstance(item, str):
                item = {"title": item}
            title = _clean(item.get("title"))[:255]
            if not title:
                continue
            points = [_clean(p) for p in (item.get("points") or []) if _clean(p)]
            topics.append((title, points))
        if not topics:
            raise ai.AIError("The model returned no usable subtopics.")
        main_title = _clean(data.get("main_title"))[:255]
    except Exception as exc:
        logger.warning("Topic generation failed for source %s: %s", source_id, exc)
        await _set_source(source_id, topics_status="failed", topics_error=str(exc)[:500])
        return

    async with async_session() as db:
        src = (await db.execute(
            select(PublicationSource).options(selectinload(PublicationSource.subtopics)).where(PublicationSource.id == source_id)
        )).scalar_one_or_none()
        if not src:
            return
        for old in list(src.subtopics):
            await db.delete(old)
        await db.flush()
        for i, (title, points) in enumerate(topics, start=1):
            db.add(PublicationSubtopic(source_id=source_id, position=i, title=title, points=points, content_status="none"))
        if main_title:
            src.main_title = main_title
        src.topics_status = "done"
        src.topics_error = None
        await db.commit()


async def _set_source(source_id: int, **fields) -> None:
    async with async_session() as db:
        await db.execute(update(PublicationSource).where(PublicationSource.id == source_id).values(**fields))
        await db.commit()


# ─── AI: content ─────────────────────────────────────────────────────

async def run_contents(pub_id: int) -> None:
    async with async_session() as db:
        pub = (await db.execute(publication_query(pub_id))).scalar_one_or_none()
        if not pub:
            return
        kind = pub.kind
        ids = [s.id for src in pub.sources for s in src.subtopics if s.content_status == "queued"]

    sem = asyncio.Semaphore(max(1, settings.PUBLICATION_AI_CONCURRENCY))

    async def one(sub_id: int) -> None:
        async with sem:
            await _content_for_subtopic(kind, sub_id)

    await asyncio.gather(*(one(i) for i in ids))


def _subtopic_brief(main_title: str, sub: PublicationSubtopic) -> str:
    lines = [f"Main title of the message: {main_title}", "", "Subtopic to write on:", f'"{sub.title}"']
    lines += [f"- {p}" for p in (sub.points or [])]
    return "\n".join(lines)


def _parse_content(kind: str, reply: str) -> dict:
    data = ai.extract_json(reply)
    paragraphs = data.get("paragraphs")
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    paragraphs = [_clean(p) for p in (paragraphs or []) if _clean(p)]
    if not paragraphs:
        raise ai.AIError("The model returned no content.")
    out = {"scripture": _clean(data.get("scripture")), "body": "\n\n".join(paragraphs)}
    if kind == "devotionals":
        out["action_plan"] = _clean(data.get("action_plan"))
        out["prayer_point"] = _clean(data.get("prayer_point"))
    return out


async def _content_for_subtopic(kind: str, sub_id: int) -> None:
    async with async_session() as db:
        if not await _claim(db, PublicationSubtopic, sub_id, "content_status"):
            return
        sub = (await db.execute(
            select(PublicationSubtopic).options(
                selectinload(PublicationSubtopic.source).selectinload(PublicationSource.sermon)
            ).where(PublicationSubtopic.id == sub_id)
        )).scalar_one()
        src = sub.source
        transcript = ((src.sermon.transcript if src.sermon else "") or "").strip()
        brief = _subtopic_brief(src.main_title or (src.sermon.title if src.sermon else ""), sub)

    system = prompts.content_system(kind)
    instruction = prompts.content_json_instruction(kind)
    try:
        if not transcript:
            raise ai.AIError("This message has no transcript yet.")
        blocks = [f"MESSAGE TRANSCRIPT:\n{transcript}", brief + "\n\n" + instruction]
        max_tokens = 1800 if kind == "devotionals" else 3000
        reply = await ai.complete(system, blocks, max_tokens=max_tokens, cache_first_block=True)
        result = _parse_content(kind, reply)

        # The brief is ~200 words; one corrective pass if the draft ran long.
        wc = word_count(result["body"])
        if kind == "devotionals" and wc > 300:
            retry = await ai.complete(
                system,
                blocks + [f"Your previous draft was {wc} words, which is too long. Rewrite it at about 200 words, "
                          f"keeping the same message and the same JSON shape. Previous draft:\n{reply}"],
                max_tokens=max_tokens, cache_first_block=True,
            )
            try:
                shorter = _parse_content(kind, retry)
                if word_count(shorter["body"]) < wc:
                    result = shorter
            except ai.AIError:
                pass
    except Exception as exc:
        logger.warning("Content generation failed for subtopic %s: %s", sub_id, exc)
        async with async_session() as db:
            await db.execute(update(PublicationSubtopic).where(PublicationSubtopic.id == sub_id)
                             .values(content_status="failed", content_error=str(exc)[:500]))
            await db.commit()
        return

    async with async_session() as db:
        await db.execute(
            update(PublicationSubtopic).where(PublicationSubtopic.id == sub_id).values(
                scripture=result.get("scripture") or None,
                body=result["body"],
                action_plan=result.get("action_plan") or None,
                prayer_point=result.get("prayer_point") or None,
                word_count=word_count(result["body"]),
                content_status="done", content_error=None, edited=False,
                updated_at=datetime.utcnow(),
            )
        )
        await db.commit()


# ─── Document assembly ───────────────────────────────────────────────

def _e(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def _paragraphs(body: str) -> list[str]:
    return [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]


def _cover_html(pub: Publication) -> str:
    kind_label = KINDS[pub.kind]["label"]
    sub = " · ".join(x for x in (kind_label, period_label(pub)) if x)
    parts = [
        f'<h1 class="pub-title" style="text-align: center; font-size: 32pt; margin-top: 140pt">{_e(pub.title)}</h1>',
        f'<p class="pub-subtitle" style="text-align: center; font-size: 14pt">{_e(sub)}</p>',
    ]
    if pub.author_name:
        parts.append(f'<p style="text-align: center; margin-top: 24pt">{_e(pub.author_name)}</p>')
    return "".join(parts)


def _day_html(n: int, src: PublicationSource, sub: PublicationSubtopic) -> str:
    main = src.main_title or (src.sermon.title if src.sermon else "")
    parts = [
        '<hr class="pb">',
        f'<h2 class="pub-day-title" style="text-align: center">Day {n}: {_e(main)}</h2>',
        f'<h3 class="pub-day-subtitle" style="text-align: center">{_e(sub.title)}</h3>',
    ]
    if sub.scripture:
        parts.append(f'<p class="pub-scripture"><em>{_e(sub.scripture)}</em></p>')
    parts += [f"<p>{_e(p)}</p>" for p in _paragraphs(sub.body or "")]
    if sub.action_plan:
        parts.append(f'<p class="pub-action"><strong>Action Plan:</strong> {_e(sub.action_plan)}</p>')
    if sub.prayer_point:
        parts.append(f'<p class="pub-prayer"><strong>Prayer Point:</strong> {_e(sub.prayer_point)}</p>')
    return "".join(parts)


def _chapter_head_html(n: int, src: PublicationSource) -> str:
    main = src.main_title or (src.sermon.title if src.sermon else "")
    return f'<hr class="pb"><h1 class="pub-chapter-title" style="text-align: center">Chapter {n}: {_e(main)}</h1>'


def _section_html(sub: PublicationSubtopic) -> str:
    parts = [f'<h2 class="pub-section-title">{_e(sub.title)}</h2>']
    if sub.scripture:
        parts.append(f'<p class="pub-scripture"><em>{_e(sub.scripture)}</em></p>')
    parts += [f"<p>{_e(p)}</p>" for p in _paragraphs(sub.body or "")]
    return "".join(parts)


def _wrap(key: str, cls: str, inner: str) -> str:
    return f'<div data-block="{key}" class="{cls}">{inner}</div>'


def build_document(pub: Publication, existing: str | None = None, rebuild: bool = False) -> str:
    """Assemble the editor document from the ordered subtopics.

    With ``existing`` (Update), any day/chapter/section already in the document keeps the
    user's edits and is only renumbered; new material is generated fresh and material whose
    subtopic no longer exists is dropped. ``rebuild`` discards the document's edits.
    """
    keep: dict[str, str] = {}
    if existing and not rebuild:
        keep = {k: inner for k, inner in split_blocks(existing) if k}

    out = [_wrap("cover", "pub-cover", keep.get("cover") or _cover_html(pub))]

    if pub.is_devotional:
        for n, src, sub in numbered_subtopics(pub):
            key = f"day:{sub.id}"
            if key in keep:
                inner = re.sub(r"(<h2[^>]*>\s*)Day\s+\d+", lambda m: f"{m.group(1)}Day {n}", keep[key], count=1)
            else:
                inner = _day_html(n, src, sub)
            out.append(_wrap(key, "pub-day", inner))
    else:
        chapter = 0
        for src in pub.sources:
            subs = [s for s in src.subtopics if s.has_content]
            if not subs:
                continue
            chapter += 1
            key = f"chapter:{src.id}"
            if key in keep:
                head = re.sub(r"(<h1[^>]*>\s*)Chapter\s+\d+", lambda m: f"{m.group(1)}Chapter {chapter}", keep[key], count=1)
            else:
                head = _chapter_head_html(chapter, src)
            out.append(_wrap(key, "pub-chapter", head))
            for sub in subs:
                skey = f"section:{sub.id}"
                out.append(_wrap(skey, "pub-section", keep.get(skey) or _section_html(sub)))
    return "".join(out)
