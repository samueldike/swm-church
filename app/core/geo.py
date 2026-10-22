"""Countries, regions, currencies, time zones and phone numbers: one source of truth.

Every location and money field in the app is a CODE from a standard list, never free text, so
two people can never record the same place two ways:

* country  — ISO 3166-1 alpha-2 (``NG``), names from pycountry
* region   — ISO 3166-2 (``NG-EB`` Ebonyi, ``NG-FC`` Abuja FCT), the country's official list
* currency — ISO 4217 (``NGN``), defaulted from the country (Babel)
* timezone — IANA (``Africa/Lagos``), defaulted from the country (pytz)
* phone    — E.164 (``+2348031234567``), parsed with the church's country as the default

All lists are offline (no network). ``match_country`` / ``match_region`` map old free-text values
to codes for the migration, and return ``None`` rather than guess.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone as _tz
from functools import lru_cache
from zoneinfo import ZoneInfo

import phonenumbers
import pycountry
import pytz
from babel.numbers import format_decimal, get_currency_precision, get_currency_symbol, get_territory_currencies

DEFAULT_COUNTRY = "NG"
DEFAULT_TIMEZONE = "Africa/Lagos"
DEFAULT_CURRENCY = "NGN"

# Where a country's top level is just a handful of nations (e.g. the UK's four), branches pick
# from the level below (counties / council areas) instead.
_MIN_TOP_LEVEL = 8


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    text = re.sub(r"\b(state|region|province|county|federal capital territory|capital territory|territory|the)\b", " ", text)
    return " ".join(text.split())


# ─── Countries ─────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def countries() -> list[tuple[str, str]]:
    """``[(code, name)]`` sorted by name, with common names (e.g. "Bolivia", not the formal name)."""
    out = [(c.alpha_2, getattr(c, "common_name", None) or c.name) for c in pycountry.countries]
    return sorted(out, key=lambda x: _norm(x[1]))


def country_name(code: str | None) -> str:
    c = pycountry.countries.get(alpha_2=(code or "").upper()) if code else None
    return (getattr(c, "common_name", None) or c.name) if c else ""


def is_country(code: str | None) -> bool:
    return bool(code) and pycountry.countries.get(alpha_2=code.upper()) is not None


def match_country(text: str | None) -> str | None:
    """Old free text → ISO code, or None when it is not unambiguous."""
    if not text or not text.strip():
        return None
    t = text.strip()
    if len(t) == 2 and is_country(t):
        return t.upper()
    key = _norm(t)
    aliases = {"uk": "GB", "u k": "GB", "great britain": "GB", "britain": "GB", "usa": "US", "u s a": "US",
               "america": "US", "uae": "AE", "drc": "CD", "dr congo": "CD", "ivory coast": "CI"}
    if key in aliases:
        return aliases[key]
    for c in pycountry.countries:
        names = {c.name, getattr(c, "common_name", ""), getattr(c, "official_name", ""), c.alpha_3}
        if key in {_norm(n) for n in names if n}:
            return c.alpha_2
    return None


# ─── Regions (states, provinces, …) ────────────────────────────────

@lru_cache(maxsize=256)
def regions(country: str | None) -> list[tuple[str, str]]:
    """``[(code, name)]`` for the country's first useful administrative level, sorted by name."""
    if not is_country(country):
        return []
    subs = list(pycountry.subdivisions.get(country_code=country.upper()) or [])
    top = [s for s in subs if not s.parent_code]
    chosen = top if len(top) >= _MIN_TOP_LEVEL or len(top) == len(subs) else [s for s in subs if s.parent_code]
    return sorted(((s.code, _region_label(s)) for s in chosen), key=lambda x: _norm(x[1]))


def _region_label(s) -> str:
    if s.code == "NG-FC":
        return "Federal Capital Territory (Abuja)"
    return s.name


def region_name(code: str | None) -> str:
    s = pycountry.subdivisions.get(code=code.upper()) if code else None
    return _region_label(s) if s else ""


def is_region(country: str | None, code: str | None) -> bool:
    return bool(code) and any(c == code.upper() for c, _ in regions(country))


def match_region(country: str | None, text: str | None) -> str | None:
    """Old free text ("Ebonyi State", "FCT", "Lagos") → ISO 3166-2 code within the country."""
    if not text or not text.strip() or not is_country(country):
        return None
    t = text.strip()
    if is_region(country, t):
        return t.upper()
    key = _norm(t)
    aliases = {("NG", "fct"): "NG-FC", ("NG", "abuja"): "NG-FC", ("NG", "abuja fct"): "NG-FC"}
    if (country.upper(), key) in aliases:
        return aliases[(country.upper(), key)]
    hits = [c for c, n in regions(country) if _norm(n) == key or _norm(pycountry.subdivisions.get(code=c).name) == key]
    return hits[0] if len(hits) == 1 else None


# ─── Currencies ────────────────────────────────────────────────────

def currency_for(country: str | None) -> str:
    cur = get_territory_currencies((country or DEFAULT_COUNTRY).upper(), tender=True) if is_country(country) else []
    return cur[0] if cur else DEFAULT_CURRENCY


@lru_cache(maxsize=1)
def currencies() -> list[tuple[str, str]]:
    return sorted(((c.alpha_3, f"{c.alpha_3} — {c.name}") for c in pycountry.currencies), key=lambda x: x[0])


def is_currency(code: str | None) -> bool:
    return bool(code) and pycountry.currencies.get(alpha_3=code.upper()) is not None


@lru_cache(maxsize=1)
def _currency_home() -> dict[str, str]:
    """Currency → a territory that uses it, so "NGN" displays as "₦" rather than "NGN"."""
    home: dict[str, str] = {}
    for c in pycountry.countries:
        for cur in get_territory_currencies(c.alpha_2, tender=True) or []:
            home.setdefault(cur, c.alpha_2)
    home.update({"USD": "US", "EUR": "DE", "GBP": "GB", "XAF": "CM", "XOF": "SN"})
    return home


@lru_cache(maxsize=512)
def currency_symbol(code: str | None) -> str:
    code = (code or DEFAULT_CURRENCY).upper()
    terr = _currency_home().get(code)
    for locale in ([f"en_{terr}"] if terr else []) + ["en"]:
        try:
            sym = get_currency_symbol(code, locale=locale)
            if sym:
                return sym
        except Exception:
            continue
    return code


def minor_units(code: str | None) -> int:
    """Decimal places of the currency: 2 for NGN/USD, 0 for JPY. Gateways take amount × 10^this."""
    try:
        return get_currency_precision((code or DEFAULT_CURRENCY).upper())
    except Exception:
        return 2


def format_money(amount, code: str | None, decimals: bool = True) -> str:
    code = (code or DEFAULT_CURRENCY).upper()
    places = minor_units(code) if decimals else 0
    pattern = "#,##0" + ("." + "0" * places if places else "")
    try:
        value = float(str(amount or 0).replace(",", ""))
    except ValueError:
        return str(amount)                     # free text typed into an old report: show it as written
    number = format_decimal(value, format=pattern, locale="en")
    sym = currency_symbol(code)
    return f"{sym}{number}" if len(sym) <= 3 else f"{number} {sym}"


# ─── Time zones ────────────────────────────────────────────────────

def timezones_for(country: str | None) -> list[str]:
    return list(pytz.country_timezones.get((country or "").upper(), [])) or [DEFAULT_TIMEZONE]


def timezone_for(country: str | None) -> str:
    return timezones_for(country)[0]


def is_timezone(name: str | None) -> bool:
    return bool(name) and name in pytz.all_timezones_set


def zone(name: str | None) -> ZoneInfo:
    return ZoneInfo(name if is_timezone(name) else DEFAULT_TIMEZONE)


def local_now(tz_name: str | None) -> datetime:
    """Naive wall-clock "now" in that time zone (how event times are stored)."""
    return datetime.now(_tz.utc).astimezone(zone(tz_name)).replace(tzinfo=None)


def to_utc(naive_local: datetime, tz_name: str | None) -> datetime:
    """Naive local wall-clock → naive UTC (DST-aware)."""
    return naive_local.replace(tzinfo=zone(tz_name)).astimezone(_tz.utc).replace(tzinfo=None)


def tz_abbrev(tz_name: str | None, when: datetime | None = None) -> str:
    return (when or datetime.now()).replace(tzinfo=zone(tz_name)).strftime("%Z")


# ─── Phone numbers ─────────────────────────────────────────────────

def normalize_phone(raw: str | None, default_country: str | None = DEFAULT_COUNTRY) -> str:
    """E.164 when the number is valid for its country; otherwise the trimmed input (never lost)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        n = phonenumbers.parse(raw, (default_country or DEFAULT_COUNTRY).upper())
        if phonenumbers.is_valid_number(n):
            return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return raw[:50]


def display_phone(stored: str | None) -> str:
    """International format for E.164 values ("+234 803 123 4567"); anything else as stored."""
    if stored and stored.startswith("+"):
        try:
            return phonenumbers.format_number(phonenumbers.parse(stored, None), phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except phonenumbers.NumberParseException:
            pass
    return stored or ""


def whatsapp_digits(raw: str | None, default_country: str | None = DEFAULT_COUNTRY) -> str:
    """Digits for wa.me links, or "" when the number is not a valid phone number."""
    e164 = normalize_phone(raw, default_country)
    return e164[1:] if e164.startswith("+") else ""


def phone_country(country: str | None) -> str:
    """"+234" — the calling code, for input placeholders."""
    try:
        return f"+{phonenumbers.country_code_for_region((country or DEFAULT_COUNTRY).upper())}"
    except Exception:
        return ""


# ─── Giving page helpers ───────────────────────────────────────────

_QUICK = {"NGN": [2000, 5000, 10000, 20000, 50000], "GHS": [50, 100, 200, 500, 1000], "KES": [500, 1000, 2000, 5000, 10000],
          "ZAR": [100, 200, 500, 1000, 2000], "XAF": [2000, 5000, 10000, 20000, 50000], "XOF": [2000, 5000, 10000, 20000, 50000],
          "UGX": [10000, 20000, 50000, 100000, 200000], "TZS": [5000, 10000, 20000, 50000, 100000], "INR": [500, 1000, 2000, 5000, 10000],
          "JPY": [1000, 3000, 5000, 10000, 30000]}


def quick_amounts(currency: str | None) -> list[int]:
    """Suggested gift amounts that make sense in this currency."""
    return _QUICK.get((currency or DEFAULT_CURRENCY).upper(), [10, 25, 50, 100, 250])


def minimum_gift(currency: str | None) -> int:
    return 100 if (currency or DEFAULT_CURRENCY).upper() == "NGN" else 1
