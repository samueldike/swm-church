"""Global churches: places, money, time and lists come from standard codes, never free text."""
from datetime import datetime
from types import SimpleNamespace

from app.core import geo, ics, lists


def test_old_free_text_maps_to_codes_and_ambiguity_is_never_guessed():
    assert geo.match_country("Nigeria") == geo.match_country(" nigeria ") == "NG"
    assert geo.match_country("UK") == geo.match_country("United Kingdom") == "GB"
    assert geo.match_country("Narnia") is None and geo.match_country("") is None
    assert geo.match_region("NG", "FCT") == geo.match_region("NG", "Abuja") == "NG-FC"
    assert geo.match_region("NG", "Ebonyi State") == "NG-EB"
    assert geo.match_region("NG", "Nassarawa") is None          # a misspelling is flagged for review, not guessed
    assert geo.match_region("GH", "Lagos") is None              # a region only matches inside its own country


def test_official_region_lists():
    ng = dict(geo.regions("NG"))
    assert len(ng) == 37 and ng["NG-FC"] == "Federal Capital Territory (Abuja)" and "NG-EB" in ng
    assert len(geo.regions("GB")) > 100                         # the UK's four nations are too coarse: next level down
    assert geo.regions("XX") == []


def test_money_is_formatted_in_its_own_currency():
    assert geo.format_money(2500000.5, "NGN") == "₦2,500,000.50"
    assert geo.format_money(12000, "JPY") == "¥12,000"          # no minor unit
    assert geo.format_money("5,000", "NGN") == "₦5,000.00"
    assert geo.format_money("about 5k", "NGN") == "about 5k"    # free text from an old report is shown as written
    assert geo.minor_units("NGN") == 2 and geo.minor_units("JPY") == 0
    assert geo.currency_for("GH") == "GHS" and geo.currency_for("GB") == "GBP"


def test_phones_are_stored_internationally_and_never_lost():
    assert geo.normalize_phone("0803 123 4567", "NG") == "+2348031234567"
    assert geo.normalize_phone("+44 7911 123456", "NG") == "+447911123456"   # an explicit country code wins
    assert geo.normalize_phone("ext 22", "NG") == "ext 22"                   # not a number: kept as typed
    assert geo.whatsapp_digits("07911 123456", "GB") == "447911123456"
    assert geo.whatsapp_digits("not a phone", "NG") == ""


def test_lists_accept_only_their_own_items_in_their_own_spelling():
    purposes = lists.items({}, "giving_purposes")
    assert lists.canonical("  tithe ", purposes) == "Tithe"
    assert lists.canonical("Tithes", purposes) is None
    assert lists.items({"list_story_categories": '["News", "news", " Testimony "]'}, "story_categories") == ["News", "Testimony"]
    assert lists.gender("M") == lists.gender("Male ") == "male" and lists.gender("F") == "female" and lists.gender("x") is None
    assert lists.parse_textarea("A\n a \nB, C") == ["A", "B", "C"]


def test_calendar_times_are_converted_from_the_churchs_own_time_zone():
    london = SimpleNamespace(timezone="Europe/London")
    ev = SimpleNamespace(id=1, title="Service", description="", location="", branch=london,
                         starts_at=datetime(2026, 7, 5, 10, 0), ends_at=datetime(2026, 7, 5, 12, 0))
    text = ics.vevent(ev, "x.org")
    assert "DTSTART:20260705T090000Z" in text                   # 10:00 London summer time (BST, UTC+1)
    ev.starts_at, ev.ends_at = datetime(2026, 12, 6, 10, 0), datetime(2026, 12, 6, 12, 0)
    assert "DTSTART:20261206T100000Z" in ics.vevent(ev, "x.org")   # winter: GMT = UTC
    ev.branch = None
    assert "DTSTART:20261206T090000Z" in ics.vevent(ev, "x.org", default_tz="Africa/Lagos")
