"""The landing page's countdown and calendar read the church's own free-text service times."""
from datetime import date, datetime
from types import SimpleNamespace

from app.core import church_week as cw
from app.core.site_content import DEFAULT_SERVICES


def test_start_time_reads_the_published_formats():
    assert cw.start_time("7:00 — 9:30 am") == (7, 0)
    assert cw.start_time("9:30 am — 12:00 pm") == (9, 30)
    assert cw.start_time("4:40 — 6:00 pm") == (16, 40)
    assert cw.start_time("from 9:00 pm") == (21, 0)
    assert cw.start_time("12:00 pm") == (12, 0)
    assert cw.start_time("to be announced") is None


def test_last_friday_only_matches_the_last_one():
    assert cw.occurs_on("Last Friday", date(2026, 11, 27))
    assert not cw.occurs_on("Last Friday", date(2026, 11, 20))
    assert not cw.occurs_on("Last Friday", date(2026, 11, 28))
    assert cw.occurs_on("Sunday", date(2026, 11, 1))
    assert not cw.occurs_on("Someday", date(2026, 11, 1))


def test_next_gathering_skips_what_has_already_started():
    saturday_evening = datetime(2026, 10, 31, 19, 0)
    assert cw.next_gathering(DEFAULT_SERVICES, saturday_evening)["name"] == "First Service"
    sunday_between = datetime(2026, 11, 1, 8, 0)
    assert cw.next_gathering(DEFAULT_SERVICES, sunday_between)["name"] == "Second Service"
    assert cw.next_gathering([{"day": "Sunday", "name": "X", "time": "soon"}], saturday_evening) is None


def test_month_view_puts_events_and_services_on_their_days():
    carol = SimpleNamespace(id=7, title="Carol Service", starts_at=datetime(2026, 12, 11, 9, 0))
    view = cw.month_view(DEFAULT_SERVICES, [carol], date(2026, 12, 1), date(2026, 12, 6))
    cells = {c["date"]: c for week in view["weeks"] for c in week}
    assert view["label"] == "December 2026"
    assert all(week[0]["date"].weekday() == 6 for week in view["weeks"])
    assert cells[date(2026, 12, 11)]["items"][0] == {"title": "Carol Service", "time": "9:00 AM", "event_id": 7, "major": True}
    assert [i["title"] for i in cells[date(2026, 12, 6)]["items"]] == ["First Service", "Second Service"]
    assert cells[date(2026, 12, 6)]["today"]
    assert [i["title"] for i in cells[date(2026, 12, 25)]["items"]] == ["Monthly Vigil"]
