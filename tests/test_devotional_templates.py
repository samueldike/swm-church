"""Print templates for devotionals (app/core/devotional_templates.py): pure logic + one real render."""
import copy
import importlib.util
from datetime import date
from types import SimpleNamespace

import pytest

from app.core import devotional_templates as dt
from app.core.publication_html import sanitize


def test_reading_plans_cover_the_bible_once_in_order():
    for days in (365, 366, 730):
        seen = []
        for i in range(days):
            for part in dt.plan_reading(i, days).split("; "):
                book, _, chs = part.rpartition(" ")
                a, _, b = chs.partition("-")
                seen += [(book, c) for c in range(int(a), int(b or a) + 1)]
        assert seen == dt._CHAPTERS, days
    assert dt.TOTAL_CHAPTERS == 1189
    assert dt.one_year_reading(date(2026, 1, 1)).startswith("Genesis 1")
    assert dt.one_year_reading(date(2026, 12, 31)).startswith("Revelation") and dt.one_year_reading(date(2026, 12, 31)).endswith("22")
    assert dt.two_year_reading(date(2027, 12, 31), 2).endswith("22")


def _pub(document_html, start=date(2026, 9, 24)):
    sub = SimpleNamespace(id=7, position=0, title="The Call to the Blessing", scripture="1 Peter 3:9 (NKJV)",
                          body="One.\n\nTwo.", action_plan="Bless.", prayer_point="Lord, help me.", has_content=True)
    src = SimpleNamespace(id=1, position=0, main_title="Blessing Consciousness", sermon=None, subtopics=[sub])
    return SimpleNamespace(sources=[src], document_html=document_html, start_date=start, is_devotional=True)


def test_days_come_from_the_edited_document(monkeypatch):
    from app.core import publications

    pub = _pub(sanitize(
        '<div data-block="day:7" class="pub-day"><hr class="pb"><h2 class="pub-day-title">Day 1: Blessing Edited</h2>'
        '<h3 class="pub-day-subtitle">New Subtitle</h3><p class="pub-scripture"><em>“Text” 1 Peter 3:9 (NKJV)</em></p>'
        '<p>The <strong>edited</strong> body.</p><p>Second &amp; last.</p>'
        '<p class="pub-action"><strong>Action Plan:</strong> Do it.</p>'
        '<p class="pub-prayer"><strong>Prayer Point:</strong> Amen.</p></div>'))
    monkeypatch.setattr(publications, "numbered_subtopics",
                        lambda p: [(1, p.sources[0], p.sources[0].subtopics[0])])
    [day] = dt.day_pages(pub)
    assert day.topic == "Blessing Edited"
    assert day.subtitle == "New Subtitle"
    assert day.scripture.startswith("“Text”") and "<em>" not in day.scripture
    assert day.paragraphs == ["The <strong>edited</strong> body.", "Second &amp; last."]
    assert day.action == "Do it." and day.prayer == "Amen."
    assert (day.weekday, day.day.day, day.month) == ("THUR", 24, "SEP")
    assert day.one_year and day.two_year
    assert day.lead == ("T", "he <strong>edited</strong> body.")


def test_days_fall_back_to_the_subtopic_without_a_block(monkeypatch):
    from app.core import publications

    pub = _pub("", start=None)
    monkeypatch.setattr(publications, "numbered_subtopics",
                        lambda p: [(3, p.sources[0], p.sources[0].subtopics[0])])
    [day] = dt.day_pages(pub)
    assert day.paragraphs == ["One.", "Two."] and day.action == "Bless."
    assert day.day is None and day.one_year == ""


def test_options_are_clamped():
    o = dt.Options.from_query({"template": "nope", "trim": "x", "first_page": "-5", "two_year_plan": "9", "reading": "0"})
    assert (o.template, o.trim, o.first_page, o.two_year_plan, o.reading) == ("swm-lite", "a5", 1, 2, False)


def test_html_escapes_text_and_mirrors_pages():
    day = copy.deepcopy(dt.SAMPLE_DAY)
    day.topic = "<script>x</script>"
    html = dt.render_html([day], dt.Options(first_page=270, title_page=False), title="T")
    assert "<script>x" not in html and "&lt;script&gt;" in html
    assert "counter-reset: page 270" in html
    assert "@page :left {" in html.replace(":left {\n  margin-left: 17mm", ":left {")  # even first page is a left page


@pytest.mark.skipif(not (importlib.util.find_spec("weasyprint") and importlib.util.find_spec("pypdfium2")),
                    reason="weasyprint/pypdfium2 not installed")
def test_real_pdf_one_page_per_day_and_thumbnail():
    long = copy.deepcopy(dt.SAMPLE_DAY)
    long.paragraphs = long.paragraphs * 3
    pages = [copy.deepcopy(dt.SAMPLE_DAY), long]
    pdf = dt.build_pdf(pages, dt.Options(first_page=269), title="Daily Devotional")
    assert pdf.startswith(b"%PDF")
    import pypdfium2 as pdfium
    assert len(pdfium.PdfDocument(pdf)) == 3  # title page + one page per day
    assert pages[1].fit > pages[0].fit
    png = dt.thumbnail_png([copy.deepcopy(dt.SAMPLE_DAY)], dt.Options())
    assert png.startswith(b"\x89PNG")
