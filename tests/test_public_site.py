"""Pure-logic tests for the public-site helpers (no database, no network)."""
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core import analytics, ics, mailer
from app.core import site_content as sc
from app.core.campaigns import text_to_html


# ─── service times ────────────────────────────────────────────────────

@pytest.mark.parametrize("day,time,expected", [
    ("Sunday", "7:00 — 9:30 am", ("Sunday", "07:00", "09:30")),
    ("Sunday", "9:30 am — 12:00 pm", ("Sunday", "09:30", "12:00")),
    ("Monday", "4:40 — 6:00 pm", ("Monday", "16:40", "18:00")),
    ("Saturday", "5:00 — 6:00 pm", ("Saturday", "17:00", "18:00")),
    ("Wednesday", "11:00 — 1:00 pm", ("Wednesday", "11:00", "13:00")),      # start is the morning
    ("sunday", "8 — 10 am", ("Sunday", "08:00", "10:00")),
])
def test_parse_slot_reads_ranges(day, time, expected):
    got = sc.parse_slot(day, time)
    assert got and (got["dayOfWeek"], got["opens"], got["closes"]) == expected


@pytest.mark.parametrize("day,time", [
    ("Wednesday", "5:00 pm"),                  # no end: never guess one
    ("Last Friday", "from 9:00 pm"),           # monthly, open-ended
    ("Sunday", ""),
    ("Someday", "7:00 — 9:00 am"),
    ("Sunday", "9:00 — 7:00 am"),              # ends before it starts
])
def test_parse_slot_refuses_to_guess(day, time):
    assert sc.parse_slot(day, time) is None


def test_services_default_and_round_trip():
    assert [s["name"] for s in sc.get_services({})][:2] == ["First Service", "Second Service"]
    saved = sc.services_from_form(["Sunday", " ", "Friday"], ["Main", "ignored", "Vigil"], ["8:00 — 10:00 am", "x", "9 pm"], ["1", "0", "0"])
    out = sc.get_services({"serviceTimes": saved})
    assert [(s["day"], s["name"], s["lead"]) for s in out] == [("Sunday", "Main", True), ("Friday", "Vigil", False)]
    assert sc.get_services({"serviceTimes": "[]"}) == []                       # an administrator may clear the list
    assert len(sc.get_services({"serviceTimes": "not json"})) == len(sc.DEFAULT_SERVICES)


# ─── contact details ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,ok", [
    ("+234 (555) 123-4567", False), ("", False), ("123", False), ("+234 0000000000", False),
    ("+234 803 123 4567", True), ("08031234567", True),
])
def test_placeholder_phone_numbers_are_never_published(raw, ok):
    assert bool(sc.clean_phone(raw)) is ok


def test_whatsapp_and_url_helpers():
    assert sc.whatsapp_chat_url("0803 123 4567") == "https://wa.me/2348031234567"
    assert sc.whatsapp_chat_url("+234 803 123 4567") == "https://wa.me/2348031234567"
    assert sc.whatsapp_chat_url("0803 123 4567", "Hi there").endswith("?text=Hi%20there")
    assert sc.whatsapp_chat_url("") == "" and sc.whatsapp_chat_url("12") == ""
    assert sc.safe_https("https://t.me/swm") and sc.safe_https("javascript:alert(1)") == "" and sc.safe_https("ftp://x") == ""
    assert sc.channels({"socialTelegram": "https://t.me/a"})["telegramJoin"] == "https://t.me/a"


def test_jsonld_is_script_safe_and_has_hours():
    data = sc.church_jsonld(name="A </script> Church", url="https://x.org/", description="d", address="1 Road", phone="", email="",
                            services=[{"day": "Sunday", "time": "7:00 — 9:30 am"}, {"day": "Monday", "time": "6 pm"}], same_as=[], image="")
    assert data["@type"] == "Church" and len(data["openingHoursSpecification"]) == 1 and "telephone" not in data
    text = sc.jsonld_script(data)
    assert "</script>" not in text and "<\\/script>" in text


# ─── calendar files ───────────────────────────────────────────────────

def _event(**kw):
    base = dict(id=7, title="Youth, Night; Live", description="Line1\nLine2", location="Main hall", starts_at=datetime(2026, 12, 5, 17, 30), ends_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_ics_event_is_valid_and_escaped():
    text = ics.calendar("SWM", ics.vevent(_event(), "swm.org", "https://swm.org/events/7"))
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;TZID=Africa/Lagos:20261205T173000" in text and "DTEND;TZID=Africa/Lagos:20261205T193000" in text     # default 2 h
    assert "SUMMARY:Youth\\, Night\\; Live" in text and "DESCRIPTION:Line1\\nLine2" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert "\n" not in text.replace("\r\n", "")                                         # only CRLF line endings


def test_ics_long_lines_fold_at_75_octets():
    long = _event(description="é" * 200)
    for line in ics.vevent(long, "h", "").split("\r\n"):
        assert len(line.encode()) <= 75
    unfolded = ics.vevent(long, "h", "").replace("\r\n ", "")
    assert "é" * 200 in unfolded


def test_google_calendar_link_and_recurring_services():
    url = ics.google_calendar_url(_event())
    assert url.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE") and "20261205T173000%2F20261205T193000" in url and "ctz=Africa%2FLagos" in url
    text = ics.services_ics([{"day": "Sunday", "name": "First Service", "time": "7:00 — 9:30 am", "lead": True}, {"day": "Last Friday", "name": "Vigil", "time": "from 9 pm"}],
                            "SWM", "h", datetime(2026, 9, 24))
    assert text.count("BEGIN:VEVENT") == 1 and "RRULE:FREQ=WEEKLY;BYDAY=SU" in text and "DTSTART;TZID=Africa/Lagos:20260927T070000" in text


# ─── analytics rules ──────────────────────────────────────────────────

def test_analytics_counts_only_real_public_page_views():
    ok = dict(status=200, content_type="text/html; charset=utf-8", ua="Mozilla/5.0 (iPhone) Safari", headers={})
    assert analytics.countable("/about", **ok) and analytics.countable("/messages/a-slug", **ok)
    assert not analytics.countable("/admin/dashboard", **ok) and not analytics.countable("/static/x.css", **ok)
    assert not analytics.countable("/about", **{**ok, "status": 404}) and not analytics.countable("/feed.xml", **{**ok, "content_type": "application/xml"})
    assert not analytics.countable("/about", **{**ok, "ua": "Googlebot/2.1"}) and not analytics.countable("/about", **{**ok, "ua": ""})
    assert not analytics.countable("/about", **{**ok, "headers": {"dnt": "1"}}) and not analytics.countable("/about", **{**ok, "headers": {"sec-gpc": "1"}})
    assert not analytics.countable("/messages/5/stream", **ok)


def test_analytics_devices_referrers_and_visitor_hash():
    assert analytics.device_of("Mozilla/5.0 (iPhone; CPU iPhone OS)") == "mobile" and analytics.device_of("Mozilla/5.0 (Windows NT 10.0)") == "desktop"
    assert analytics.device_of("Mozilla/5.0 (iPad)") == "tablet"
    assert analytics.referrer_of("", "swm.org") == "direct" and analytics.referrer_of("https://swm.org/about", "swm.org") == ""
    assert analytics.referrer_of("https://l.facebook.com/x", "swm.org") == "Facebook" and analytics.referrer_of("https://www.google.com.ng/", "swm.org") == "Google"
    from datetime import date

    a = analytics.visitor_hash("1.2.3.4", "UA", date(2026, 1, 1))
    assert a == analytics.visitor_hash("1.2.3.4", "UA", date(2026, 1, 1)) and a != analytics.visitor_hash("1.2.3.4", "UA", date(2026, 1, 2))
    assert "1.2.3.4" not in a and len(a) == 20


# ─── email helpers ────────────────────────────────────────────────────

def test_email_validation_and_reserved_domains():
    assert mailer.valid_email("a@b.org") and not mailer.valid_email("a b@c.org") and not mailer.valid_email("nope") and not mailer.valid_email("a@b")
    assert mailer.is_reserved("x@example.com") and mailer.is_reserved("x@foo.test") and mailer.is_reserved("x@localhost") and not mailer.is_reserved("x@gmail.com")


def test_text_to_html_escapes_and_links():
    out = text_to_html("Hello <b>friend</b>\nsecond line\n\nVisit https://scentofwater.org/messages now")
    assert "<b>friend</b>" not in out and "&lt;b&gt;" in out and out.count("<p") == 2 and "<br" in out
    assert 'href="https://scentofwater.org/messages"' in out
    assert "href" not in text_to_html("javascript:alert(1)")


def test_notice_email_renders_and_has_a_text_version():
    from app.core.template import _env

    html = _env.get_template("emails/notice.html").render(siteName="SWM", siteTagline="", siteAddress="1 Road", sitePhone="", siteEmail="a@b.org", heading="Hi <there>", paragraphs=["Para <one>"],
                                                          details=[("When", "Sunday")], button=("Go", "https://x.org"), footnote="", unsubscribe_url="https://x.org/unsubscribe/t", preheader="p")
    assert "Hi &lt;there&gt;" in html and "Unsubscribe" in html and 'href="https://x.org"' in html
    text = mailer.html_to_text(html)
    assert "Para <one>" in text and "Sunday" in text and "<td" not in text and "https://x.org" in text


def test_legal_defaults_carry_the_essentials():
    from app.core.legal_defaults import privacy_html, terms_html

    p = privacy_html("Scent of Water Ministry", "Mile 50", "info@x.org", "")
    assert "Nigeria Data Protection Act" in p and "info@x.org" in p and "Paystack" in p and "never see or store them" in p and "unsubscribe" in p.lower()
    t = terms_html("Scent of Water Ministry", "Mile 50", "info@x.org", "")
    assert "Governing law" in t and "Federal Republic of Nigeria" in t and re.search(r"Giving", t)
    assert "<script" not in privacy_html("<script>x</script>", "", "", "")


def test_pagination_links_keep_the_filters():
    from starlette.requests import Request

    from app.routes.public import _paginate_url

    scope = {"type": "http", "method": "GET", "path": "/messages", "query_string": b"search=faith&series=Foundations&preacher=&page=2", "headers": []}
    url = _paginate_url(Request(scope), 3, "/messages")
    assert url.startswith("/messages?") and "search=faith" in url and "series=Foundations" in url and "page=3" in url and "page=2" not in url and "preacher" not in url


def test_public_site_end_to_end_script():
    """scripts/site_checks.py: pages, visit plans, branch directory, events + reminders, unsubscribe, prayer/contact/giving
    emails, message search, analytics, campaigns and the Website-content settings (SQLite, email provider mocked)."""
    import pathlib
    import subprocess
    import sys

    root = pathlib.Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, "scripts/site_checks.py"], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=590)
    failed = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAIL")]
    assert proc.returncode == 0 and not failed, "\n".join(failed) or proc.stderr[-2000:]
