"""Controlled lists: every category a person picks rather than types.

Free text splits analysis ("Tithe", "tithes", "TITHE " become three rows). Each list here has
sensible defaults and can be edited by the HQ Administrator in Settings → Lists (stored in
``site_settings`` as a JSON array under ``list_<name>``). Forms render only these choices and the
server accepts only them: ``canonical()`` maps any submitted spelling to the list's own spelling,
or returns None so the route can refuse the value.
"""
from __future__ import annotations

import json

# name → (label shown in Settings, help text, defaults)
LISTS: dict[str, tuple[str, str, list[str]]] = {
    "giving_purposes": ("Giving purposes", "What a gift is for. \"Others\" is always offered, with a note.",
                        ["Tithe", "Offering", "Missions", "Building Fund", "Welfare"]),
    "minister_titles": ("Minister titles", "Shown before a minister's name.",
                        ["Pastor", "Rev.", "Evangelist", "Prophet", "Apostle", "Minister", "Deacon", "Deaconess",
                         "Elder", "Brother", "Sister"]),
    "ministry_ranks": ("Ministry ranks", "A minister's position in the ministry.",
                       ["Senior Pastor", "Resident Pastor", "Associate Pastor", "Assistant Pastor", "Minister",
                        "Minister-in-training", "Elder", "Deacon", "Deaconess"]),
    "education_levels": ("Education levels", "A minister's highest qualification (the field of study is typed separately).",
                         ["Primary", "Secondary", "Diploma / Certificate", "OND / NCE", "HND", "Bachelor's degree",
                          "Master's degree", "Doctorate", "Theological / Bible school", "Other"]),
    "story_categories": ("Story categories", "Groups stories on the website.",
                         ["Testimony", "News", "Outreach", "Event report", "Teaching"]),
    "inquiry_topics": ("Inquiry topics", "What a visitor is contacting the church about.",
                       ["General question", "Prayer request", "Counselling", "Membership", "Giving",
                        "Events & programmes", "Partnership", "Other"]),
    "followup_types": ("Follow-up types", "HarvestTrack: how a worker followed up a contact.",
                       ["Call", "Visit", "Text message", "WhatsApp", "Prayer", "Other"]),
}

GENDERS = [("male", "Male"), ("female", "Female")]
MARITAL_STATUSES = ["Single", "Married", "Widowed", "Divorced", "Separated"]
OTHERS = "Others"


def setting_key(name: str) -> str:
    return "giving_purposes" if name == "giving_purposes" else f"list_{name}"   # purposes keep their old key


def items(site: dict, name: str) -> list[str]:
    """The current list (saved in Settings, else the defaults), de-duplicated case-insensitively."""
    raw = site.get(setting_key(name)) or ""
    values: list[str]
    try:
        values = json.loads(raw) if raw.strip().startswith("[") else [v for v in raw.replace(",", "\n").splitlines()]
    except (ValueError, AttributeError):
        values = []
    values = [" ".join(str(v).split()) for v in values if str(v).strip()] or list(LISTS[name][2])
    out, seen = [], set()
    for v in values:
        if v.lower() not in seen and not (name == "giving_purposes" and v.lower() == OTHERS.lower()):
            seen.add(v.lower())
            out.append(v)
    return out


def parse_textarea(text: str) -> list[str]:
    out, seen = [], set()
    for line in (text or "").replace(",", "\n").splitlines():
        v = " ".join(line.split())
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v[:60])
    return out


def _key(text: str | None) -> str:
    return " ".join((text or "").lower().replace(".", "").split())


def canonical(value: str | None, choices: list[str]) -> str | None:
    """The list's own spelling of ``value`` (case, spaces and dots ignored), or None."""
    k = _key(value)
    if not k:
        return None
    for c in choices:
        if _key(c) == k:
            return c
    return None


def gender(value: str | None) -> str | None:
    """"M", "Male", "female " → "male"/"female"; anything else → None."""
    k = _key(value)
    return {"m": "male", "male": "male", "man": "male", "f": "female", "female": "female", "woman": "female"}.get(k)
