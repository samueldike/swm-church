"""Print templates for daily devotionals (Books Pub → editor → Export → Designed PDF).

Each template is a Jinja file in ``app/views/print/`` laid out for a printed book: one
devotional day per page, mirrored left/right pages, page numbers, the church logo and a date
badge. WeasyPrint turns it into the PDF; the chooser's thumbnails are page 1 of the same PDF
rasterised with pypdfium2, so a thumbnail is never different from what is exported.

The content comes from the editor document (the user's edits win). A day block that lost a
part (e.g. its scripture line was deleted) falls back to the subtopic's own field.
"""
from __future__ import annotations

import base64
import copy
import html
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_APP = Path(__file__).resolve().parent.parent
_VIEWS = _APP / "views" / "print"
FONT_DIR = _APP / "static" / "fonts" / "print"
LOGO = _APP / "static" / "images" / "swm-logo.png"

TEMPLATES: dict[str, dict] = {
    "swm-lite": {
        "name": "SWM Lite",
        "file": "devotional-swm-lite.html",
        "description": "Classic print devotional: greyscale, date badge, shaded scripture, "
                       "drop cap, Action Plan and Prayer boxes, Bible-reading strip.",
    },
}
DEFAULT_TEMPLATE = "swm-lite"

TRIMS = {
    "a5": {"label": "A5 (148 × 210 mm)", "size": "148mm 210mm"},
    "digest": {"label": "Digest (5.5 × 8.5 in)", "size": "5.5in 8.5in"},
}

# Body sizes tried in turn until a day fits on its page (level 0 first).
FIT_LEVELS = 4

_WEEKDAYS = ["MON", "TUE", "WED", "THUR", "FRI", "SAT", "SUN"]
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ─── Bible reading plans (Genesis → Revelation in order) ──────────────

BOOKS: list[tuple[str, int]] = [
    ("Genesis", 50), ("Exodus", 40), ("Leviticus", 27), ("Numbers", 36), ("Deuteronomy", 34),
    ("Joshua", 24), ("Judges", 21), ("Ruth", 4), ("1 Samuel", 31), ("2 Samuel", 24),
    ("1 Kings", 22), ("2 Kings", 25), ("1 Chronicles", 29), ("2 Chronicles", 36), ("Ezra", 10),
    ("Nehemiah", 13), ("Esther", 10), ("Job", 42), ("Psalms", 150), ("Proverbs", 31),
    ("Ecclesiastes", 12), ("Song of Songs", 8), ("Isaiah", 66), ("Jeremiah", 52),
    ("Lamentations", 5), ("Ezekiel", 48), ("Daniel", 12), ("Hosea", 14), ("Joel", 3), ("Amos", 9),
    ("Obadiah", 1), ("Jonah", 4), ("Micah", 7), ("Nahum", 3), ("Habakkuk", 3), ("Zephaniah", 3),
    ("Haggai", 2), ("Zechariah", 14), ("Malachi", 4),
    ("Matthew", 28), ("Mark", 16), ("Luke", 24), ("John", 21), ("Acts", 28), ("Romans", 16),
    ("1 Corinthians", 16), ("2 Corinthians", 13), ("Galatians", 6), ("Ephesians", 6),
    ("Philippians", 4), ("Colossians", 4), ("1 Thessalonians", 5), ("2 Thessalonians", 3),
    ("1 Timothy", 6), ("2 Timothy", 4), ("Titus", 3), ("Philemon", 1), ("Hebrews", 13),
    ("James", 5), ("1 Peter", 5), ("2 Peter", 3), ("1 John", 5), ("2 John", 1), ("3 John", 1),
    ("Jude", 1), ("Revelation", 22),
]
_CHAPTERS: list[tuple[str, int]] = [(b, c) for b, n in BOOKS for c in range(1, n + 1)]
TOTAL_CHAPTERS = len(_CHAPTERS)  # 1189


def _span(first: int, last: int) -> str:
    """Chapters first..last (indexes into _CHAPTERS) as "Numbers 25-27; Deuteronomy 1"."""
    groups: list[list[tuple[str, int]]] = []
    for book, ch in _CHAPTERS[first:last + 1]:
        if groups and groups[-1][0][0] == book:
            groups[-1].append((book, ch))
        else:
            groups.append([(book, ch)])
    parts = []
    for g in groups:
        book, a, b = g[0][0], g[0][1], g[-1][1]
        parts.append(f"{book} {a}" if a == b else f"{book} {a}-{b}")
    return "; ".join(parts)


def plan_reading(index: int, days: int) -> str:
    """Reading for day ``index`` (0-based) of a plan that covers the Bible in ``days`` days."""
    index = max(0, min(index, days - 1))
    first = index * TOTAL_CHAPTERS // days
    last = (index + 1) * TOTAL_CHAPTERS // days - 1
    return _span(first, max(first, last))


def _year_len(y: int) -> int:
    return 366 if date(y, 12, 31).timetuple().tm_yday == 366 else 365


def one_year_reading(d: date) -> str:
    return plan_reading(d.timetuple().tm_yday - 1, _year_len(d.year))


def two_year_reading(d: date, year_of_plan: int) -> str:
    """``year_of_plan`` 1 reads Genesis to about Proverbs, 2 the rest."""
    idx = d.timetuple().tm_yday - 1
    if year_of_plan == 2:
        idx += 365
    return plan_reading(idx, 730 if _year_len(d.year) == 365 else 731)


# ─── Pulling each day out of the editor document ──────────────────────

_INLINE = {"strong", "b", "em", "i", "u", "br", "sub", "sup"}


class _DayParser(HTMLParser):
    """Collects the top-level h2/h3/p elements of one day block as (tag, classes, inline html)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, set[str], str]] = []
        self.cur: list | None = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if self.cur is None:
            if tag in ("h1", "h2", "h3", "h4", "p", "blockquote", "li"):
                cls = set((dict(attrs).get("class") or "").split())
                self.cur, self.depth = [tag, cls, []], 1
            return
        if tag == "br":
            self.cur[2].append("<br>")
            return
        self.depth += 1
        if tag in _INLINE:
            self.cur[2].append(f"<{'strong' if tag == 'b' else 'em' if tag == 'i' else tag}>")

    def handle_startendtag(self, tag, attrs):
        if self.cur is not None and tag == "br":
            self.cur[2].append("<br>")

    def handle_endtag(self, tag):
        if self.cur is None:
            return
        self.depth -= 1
        if self.depth == 0:
            text = "".join(self.cur[2]).strip()
            if re.sub(r"<[^>]+>", "", text).strip():
                self.items.append((self.cur[0], self.cur[1], text))
            self.cur = None
        elif tag in _INLINE and tag != "br":
            self.cur[2].append(f"</{'strong' if tag == 'b' else 'em' if tag == 'i' else tag}>")

    def handle_data(self, data):
        if self.cur is not None:
            self.cur[2].append(html.escape(data, quote=False))


def _plain(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _strip_label(fragment: str, *labels: str) -> str:
    """Remove a leading "Action Plan:" / "Prayer Point:" (with its <strong>) from a paragraph."""
    pat = "|".join(re.escape(x) for x in labels)
    out = re.sub(rf"^\s*(<strong>|<em>)*\s*({pat})\s*:?\s*(</strong>|</em>)*\s*:?\s*", "", fragment, flags=re.I)
    return out.strip()


def _esc_paragraphs(text: str | None) -> list[str]:
    return [html.escape(re.sub(r"\s*\n\s*", " ", p).strip(), quote=False)
            for p in re.split(r"\n\s*\n", text or "") if p.strip()]


@dataclass
class DayPage:
    number: int
    topic: str
    subtitle: str
    scripture: str = ""            # inline HTML
    paragraphs: list[str] = field(default_factory=list)  # inline HTML each
    action: str = ""
    prayer: str = ""
    day: date | None = None
    one_year: str = ""
    two_year: str = ""
    fit: int = 0

    @property
    def weekday(self) -> str:
        return _WEEKDAYS[self.day.weekday()] if self.day else ""

    @property
    def month(self) -> str:
        return _MONTHS[self.day.month - 1] if self.day else ""

    @property
    def lead(self) -> tuple[str, str]:
        """(drop-cap letter, rest of the first paragraph); no letter when it starts with markup or a quote."""
        first = self.paragraphs[0] if self.paragraphs else ""
        m = re.match(r"^([A-Za-z0-9])", first)
        return (m.group(1), first[1:]) if m else ("", first)

    @property
    def text_length(self) -> int:
        return sum(len(_plain(x)) for x in [self.scripture, self.action, self.prayer, *self.paragraphs])


def day_pages(pub, *, reading: bool = True, two_year_plan: int = 1) -> list[DayPage]:
    """The publication's days, in order, from its editor document (falling back to the subtopics)."""
    from app.core.publication_html import split_blocks
    from app.core.publications import numbered_subtopics

    blocks = {k: inner for k, inner in split_blocks(pub.document_html or "") if k}
    pages: list[DayPage] = []
    for n, src, sub in numbered_subtopics(pub):
        main = src.main_title or (src.sermon.title if src.sermon else "")
        page = DayPage(number=n, topic=main, subtitle=sub.title or "")
        inner = blocks.get(f"day:{sub.id}")
        if inner is not None:
            p = _DayParser()
            p.feed(inner)
            p.close()
            for tag, cls, frag in p.items:
                if tag in ("h1", "h2") or "pub-day-title" in cls:
                    page.topic = re.sub(r"^\s*Day\s+\d+\s*[:.\-–—]\s*", "", _plain(frag), flags=re.I) or page.topic
                elif tag in ("h3", "h4") or "pub-day-subtitle" in cls:
                    page.subtitle = _plain(frag)
                elif "pub-scripture" in cls:
                    page.scripture = re.sub(r"</?em>", "", frag)
                elif "pub-action" in cls or re.match(r"^\s*(<strong>)?\s*Action Plan", frag, re.I):
                    page.action = _strip_label(frag, "Action Plan")
                elif "pub-prayer" in cls or re.match(r"^\s*(<strong>)?\s*Prayer( Point)?", frag, re.I):
                    page.prayer = _strip_label(frag, "Prayer Point", "Prayer")
                else:
                    page.paragraphs.append(frag)
        else:
            page.scripture = html.escape(sub.scripture or "", quote=False)
            page.paragraphs = _esc_paragraphs(sub.body)
            page.action = html.escape(sub.action_plan or "", quote=False)
            page.prayer = html.escape(sub.prayer_point or "", quote=False)
        if pub.start_date:
            page.day = pub.start_date + timedelta(days=n - 1)
            if reading:
                page.one_year = one_year_reading(page.day)
                page.two_year = two_year_reading(page.day, two_year_plan)
        pages.append(page)
    return pages


SAMPLE_DAY = DayPage(
    number=1,
    topic="Blessing Consciousness",
    subtitle="The Call to the Blessing",
    scripture="“Not returning evil for evil or reviling for reviling, but on the contrary blessing, "
              "knowing that you were called to this, that you may inherit a blessing.” 1 Peter 3:9 (NKJV)",
    paragraphs=[
        "The world operates on a principle of reciprocity, but often in a negative sense. This “eye for an eye” "
        "mentality is deeply ingrained in human nature, and we see it play out in our daily lives. When we are "
        "insulted, betrayed or wronged, our immediate response is to retaliate.",
        "However, as followers of Christ, we are called to a higher standard, a way of living that is completely "
        "countercultural. The Scripture reminds us that we were <em>“called to this”</em>: a life where we "
        "actively choose to bless rather than curse.",
        "By choosing to bless, you are signalling to God and to yourself that your spiritual inheritance is more "
        "valuable than a momentary satisfaction of getting even.",
    ],
    action="When you feel the urge to retaliate, stop and say to yourself, “I am called to inherit a blessing.”",
    prayer="Lord, give me the grace to resist the urge to retaliate.",
    day=date(2026, 9, 24),
    one_year=one_year_reading(date(2026, 9, 24)),
    two_year=two_year_reading(date(2026, 9, 24), 1),
)


# ─── Rendering ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_VIEWS)), autoescape=select_autoescape(["html"]))


@lru_cache(maxsize=1)
def logo_data_uri() -> str:
    """The church logo in greyscale (the template is monochrome), small enough to embed."""
    if not LOGO.exists():
        return ""
    from PIL import Image

    with Image.open(LOGO) as im:
        im = im.convert("RGBA")
        im.thumbnail((520, 520))
        alpha = im.getchannel("A")
        grey = im.convert("L").convert("RGBA")
        grey.putalpha(alpha)
        buf = io.BytesIO()
        grey.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@dataclass
class Options:
    template: str = DEFAULT_TEMPLATE
    trim: str = "a5"
    first_page: int = 1
    title_page: bool = True
    reading: bool = True
    two_year_plan: int = 1

    @classmethod
    def from_query(cls, q) -> "Options":
        def num(name, default, lo, hi):
            try:
                return max(lo, min(hi, int(q.get(name, default))))
            except (TypeError, ValueError):
                return default
        tpl = q.get("template") or DEFAULT_TEMPLATE
        trim = q.get("trim") or "a5"
        return cls(
            template=tpl if tpl in TEMPLATES else DEFAULT_TEMPLATE,
            trim=trim if trim in TRIMS else "a5",
            first_page=num("first_page", 1, 1, 9999),
            title_page=q.get("title_page", "1") not in ("0", "", "off"),
            reading=q.get("reading", "1") not in ("0", "", "off"),
            two_year_plan=num("two_year_plan", 1, 1, 2),
        )


def render_html(pages: list[DayPage], opts: Options, *, title: str = "", subtitle: str = "",
                author: str = "", church: str = "", first_number: int | None = None,
                title_page: bool | None = None) -> str:
    """The template's HTML. ``first_number`` is the number of the document's first page (by default
    ``first_page``, or one less when a title page comes first); chunks of a long book pass their own."""
    title_page = opts.title_page if title_page is None else title_page
    if first_number is None:
        first_number = opts.first_page - (1 if title_page else 0)
    tpl = _env().get_template(TEMPLATES[opts.template]["file"])
    return tpl.render(
        pages=pages, opts=opts, size=TRIMS[opts.trim]["size"], counter_start=first_number,
        show_title_page=title_page,
        # WeasyPrint makes the document's first page a right-hand page; an even first number
        # belongs on the left, so swap the mirrored selectors.
        recto=":left" if first_number % 2 == 0 else ":right",
        verso=":right" if first_number % 2 == 0 else ":left",
        font_dir=FONT_DIR.as_uri(), logo=logo_data_uri(),
        title=title, subtitle=subtitle, author=author, church=church,
    )


def _render(html_text: str):
    from weasyprint import HTML

    return HTML(string=html_text, base_url=FONT_DIR.as_uri() + "/").render()


def _guess_fit(day: DayPage) -> int:
    n = day.text_length
    return 0 if n < 1450 else 1 if n < 1700 else 2 if n < 2000 else 3


def fit_day(day: DayPage, opts: Options) -> tuple[int, int]:
    """The largest type level at which ``day`` fits one page: (level, pages it takes at that level).

    Starts from a guess based on its length, then steps up (smaller type) while it overflows, or
    down (larger type) while the larger size still fits. At the smallest level a very long day may
    take two pages; that is reported, not hidden.
    """
    def pages_at(level: int) -> int:
        d = copy.copy(day)
        d.fit = level
        return len(_render(render_html([d], opts, title_page=False, first_number=1)).pages)

    level = _guess_fit(day)
    count = pages_at(level)
    if count == 1:
        while level > 0 and pages_at(level - 1) == 1:
            level -= 1
        return level, 1
    while count > 1 and level < FIT_LEVELS - 1:
        level += 1
        count = pages_at(level)
    return level, count


def _fit_many(args) -> list[tuple[int, int]]:
    days, opts = args
    return [fit_day(d, opts) for d in days]


def _render_chunk(args) -> bytes:
    days, opts, first_number, title_page, meta = args
    return _render(render_html(days, opts, first_number=first_number, title_page=title_page, **meta)).write_pdf()


def _chunks(items: list, n: int) -> list[list]:
    size = max(1, -(-len(items) // n))
    return [items[i:i + size] for i in range(0, len(items), size)]


def build_pdf(pages: list[DayPage], opts: Options, *, progress=None, workers: int | None = None, **meta) -> bytes:
    """The finished book. Each day first gets the largest type that fits its page (``fit_day``), then
    the pages are rendered; a long book is split over a few processes and the parts are merged.

    ``progress(done, total, stage)`` is called as days are measured and parts rendered.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    total = len(pages)
    if workers is None:
        workers = 0 if total <= 12 else max(1, min(3, (os.cpu_count() or 2) - 1))
    report = progress or (lambda *a: None)
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) if workers > 1 else None
    try:
        # 1. Measure: the type level of every day (in small batches so progress moves).
        batches = _chunks(pages, max(1, total // 4)) if pool else [[d] for d in pages]
        done = 0
        results = pool.map(_fit_many, [(b, opts) for b in batches]) if pool else map(_fit_many, [(b, opts) for b in batches])
        counts: list[int] = []
        for batch, res in zip(batches, results):
            for day, (level, count) in zip(batch, res):
                day.fit = level
                counts.append(count)
            done += len(batch)
            report(done, total, "measure")

        # 2. Render: page numbers are known now, so each part can start at the right number.
        parts = _chunks(pages, workers) if pool else [pages]
        jobs, idx = [], 0
        number = opts.first_page - (1 if opts.title_page else 0)
        for i, part in enumerate(parts):
            title_page = opts.title_page and i == 0
            jobs.append((part, opts, number, title_page, meta))
            number += sum(counts[idx:idx + len(part)]) + (1 if title_page else 0)
            idx += len(part)
        report(0, len(jobs), "render")
        pdfs = []
        for k, pdf in enumerate(pool.map(_render_chunk, jobs) if pool else map(_render_chunk, jobs)):
            pdfs.append(pdf)
            report(k + 1, len(jobs), "render")
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    return pdfs[0] if len(pdfs) == 1 else merge_pdfs(pdfs)


def merge_pdfs(pdfs: list[bytes]) -> bytes:
    import pypdfium2 as pdfium

    out = pdfium.PdfDocument.new()
    try:
        for data in pdfs:
            src = pdfium.PdfDocument(data)
            try:
                out.import_pages(src)
            finally:
                src.close()
        buf = io.BytesIO()
        out.save(buf)
        return buf.getvalue()
    finally:
        out.close()


def thumbnail_png(pages: list[DayPage], opts: Options, width: int = 360, **meta) -> bytes:
    """Page 1 of the real PDF as a PNG, for the template chooser."""
    import pypdfium2 as pdfium

    opts = Options(**{**opts.__dict__, "title_page": False})
    pdf = pdfium.PdfDocument(build_pdf(pages[:1], opts, workers=0, **meta))
    try:
        page = pdf[0]
        scale = width / page.get_width()
        image = page.render(scale=scale).to_pil()
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "PNG", optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()
