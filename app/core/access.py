"""Who may do what: roles, module permissions and the path -> permission policy.

Model (HQ + branches)
---------------------
* ``super_admin``  - HQ Administrator (General Overseer's office). Sees and manages every branch and
  all settings.
* ``hq_media`` / ``hq_finance`` / ``hq_pastoral`` - HQ staff. Each gets only its own modules and is
  scoped to the HQ branch's own records.
* ``branch_admin`` - runs one branch: its members, events, audio, inquiries, follow-up, department
  heads and reports, gifts and channel settings. Never sees another branch or HQ.
* ``hod``          - department head: monthly unit reports only.

Legacy role strings (``admin``, ``branch_super_admin``) are normalised to ``branch_admin`` by
``normalize_role`` and rewritten in the database at startup.
"""
from __future__ import annotations

import re

SUPER = "super_admin"
HQ_MEDIA = "hq_media"
HQ_FINANCE = "hq_finance"
HQ_PASTORAL = "hq_pastoral"
BRANCH_ADMIN = "branch_admin"
RESIDENT_PASTOR = "resident_pastor"
HOD = "hod"

HQ_STAFF_ROLES = (HQ_MEDIA, HQ_FINANCE, HQ_PASTORAL)
HQ_ROLES = (SUPER,) + HQ_STAFF_ROLES
ASSIGNABLE_ROLES = (SUPER, HQ_MEDIA, HQ_FINANCE, HQ_PASTORAL, BRANCH_ADMIN, RESIDENT_PASTOR)
# Roles an administrator may pick when creating an account by hand. The Resident Pastor is appointed from
# Ministers (HQ Administrator only) so the appointment, the minister record and the review rule stay in step.
FORM_ROLES = tuple(r for r in ASSIGNABLE_ROLES if r != RESIDENT_PASTOR)
# Roles that always belong to one specific branch (HQ counts as a branch here).
BRANCH_BOUND_ROLES = (BRANCH_ADMIN, RESIDENT_PASTOR)

ROLE_LABELS = {
    SUPER: "HQ Administrator",
    HQ_MEDIA: "HQ Media",
    HQ_FINANCE: "HQ Finance",
    HQ_PASTORAL: "HQ Pastoral Care",
    BRANCH_ADMIN: "Branch Admin",
    RESIDENT_PASTOR: "Resident Pastor",
    HOD: "Department HOD",
}
ROLE_DESCRIPTIONS = {
    SUPER: "Everything, across HQ and every branch, including settings, keys and roles.",
    HQ_MEDIA: "HQ audio messages, events, sliders, photos, flyers, stories, newsletter and Books Pub. No money, no members.",
    HQ_FINANCE: "Donations, gift entries and branch remittances. Nothing else.",
    HQ_PASTORAL: "Prayer requests, inquiries, members, HQ's own department reports, branch reports and follow-up for HQ.",
    BRANCH_ADMIN: "One branch only: its members, events, audio, inquiries, gifts, follow-up, department heads and channel settings.",
    RESIDENT_PASTOR: "Reviews the department reports of their church (HQ or one branch) and appends a comment before they reach the Super Admin's office; can view the church's ministers.",
    HOD: "Monthly unit reports for their own department.",
}

_LEGACY = {"admin": BRANCH_ADMIN, "branch_super_admin": BRANCH_ADMIN}


def normalize_role(role: str | None) -> str:
    return _LEGACY.get((role or "").strip(), (role or "").strip())


ALL = "*"
ROLE_PERMS: dict[str, set[str]] = {
    SUPER: {ALL},
    HQ_MEDIA: {"dashboard", "profile", "media", "audio", "events", "announcements.view", "analytics"},
    HQ_FINANCE: {"dashboard", "profile", "donations", "remittances", "announcements.view"},
    HQ_PASTORAL: {"dashboard", "profile", "prayer", "inquiries", "members", "reports", "branch_reports", "harvest", "departments.view", "ministers", "announcements.view"},
    BRANCH_ADMIN: {
        "dashboard", "profile", "members", "events", "audio", "inquiries", "prayer", "harvest", "hods",
        "reports", "branch_reports", "donations", "remittances", "branch_settings", "branch.view", "departments.view", "ministers", "announcements.view", "activity",
    },
    RESIDENT_PASTOR: {"dashboard", "profile", "reports", "ministers.view", "announcements.view"},
    HOD: {"profile", "hod_reports"},
}


def has_perm(role: str | None, perm: str) -> bool:
    perms = ROLE_PERMS.get(normalize_role(role), set())
    if ALL in perms or perm in perms:
        return True
    # Managing a module implies being able to view it ("ministers" => "ministers.view").
    return perm.endswith(".view") and perm[: -len(".view")] in perms


def is_super(role: str | None) -> bool:
    return normalize_role(role) == SUPER


def is_hq_staff(role: str | None) -> bool:
    return normalize_role(role) in HQ_ROLES


def is_branch_admin(role: str | None) -> bool:
    return normalize_role(role) == BRANCH_ADMIN


def home_for(role: str | None) -> str:
    return "/admin/hod/reports" if normalize_role(role) == HOD else "/admin/dashboard"


# ─── path policy ─────────────────────────────────────────────────────

# Paths reachable without a signed-in admin (the route itself handles the redirect).
OPEN_PREFIXES = ("/admin/login", "/admin/logout", "/admin/invitation/", "/admin/hod/invitation/")

# (regex on the path AFTER /admin, permission, methods or None for all) - first match wins.
_RULES: list[tuple[re.Pattern, str, tuple[str, ...] | None]] = [
    (re.compile(r"^/(settings|security-logs|admins|telegram-archive|data-check)(/|$)"), "system", None),
    (re.compile(r"^/branches/\d+/settings(/|$)"), "system", None),
    (re.compile(r"^/branches/\d+$"), "branch.view", ("GET",)),
    (re.compile(r"^/branches(/|$)"), "system", None),
    (re.compile(r"^/departments(/|$)"), "departments.manage", ("POST",)),
    (re.compile(r"^/departments/create$"), "departments.manage", ("GET",)),
    (re.compile(r"^/departments/\d+/edit$"), "departments.manage", ("GET",)),
    (re.compile(r"^/departments(/|$)"), "departments.view", ("GET",)),
    (re.compile(r"^/(sliders|photos|program-flyers|stories|publications|subscribers|campaigns)(/|$)"), "media", None),
    (re.compile(r"^/analytics(/|$)"), "analytics", None),
    (re.compile(r"^/donations(/|$)"), "donations", None),
    (re.compile(r"^/remittances(/|$)"), "remittances", None),
    (re.compile(r"^/prayer-requests(/|$)"), "prayer", None),
    (re.compile(r"^/inquiries(/|$)"), "inquiries", None),
    (re.compile(r"^/members(/|$)"), "members", None),
    (re.compile(r"^/events(/|$)"), "events", None),
    (re.compile(r"^/(messages|transcriptions)(/|$)"), "audio", None),
    (re.compile(r"^/harvesttrack(/|$)"), "harvest", None),
    (re.compile(r"^/(hods|hod-invitations)(/|$)"), "hods", None),
    (re.compile(r"^/unit-reports(/|$)"), "reports", None),
    (re.compile(r"^/ministers/\d+/(make-hod|make-resident-pastor|remove-resident-pastor)$"), "ministers", ("POST",)),
    (re.compile(r"^/ministers(/create|/\d+/(edit|update|delete|photo))(/|$)"), "ministers", None),
    (re.compile(r"^/ministers(/|$)"), "ministers.view", ("GET",)),
    (re.compile(r"^/ministers(/|$)"), "ministers", None),
    (re.compile(r"^/announcements(/create|/\d+/(edit|update|delete))(/|$)"), "announcements", None),
    (re.compile(r"^/announcements(/|$)"), "announcements.view", ("GET",)),
    (re.compile(r"^/announcements(/|$)"), "announcements", None),
    (re.compile(r"^/activity(/|$)"), "activity", None),
    (re.compile(r"^/branch-reports(/|$)"), "branch_reports", None),
    (re.compile(r"^/branch-settings(/|$)"), "branch_settings", None),
    (re.compile(r"^/hod/"), "hod_reports", None),
    (re.compile(r"^/(dashboard|metrics)(/|$)"), "dashboard", None),
    (re.compile(r"^/profile(/|$)"), "profile", None),
]


def required_permission(method: str, path: str) -> str | None:
    """Permission needed for ``method path`` (path includes the /admin prefix), or None if unrestricted."""
    if not path.startswith("/admin"):
        return None
    sub = path[len("/admin"):] or "/"
    if sub in ("/", ""):
        return None
    for rx, perm, methods in _RULES:
        if rx.search(sub) and (methods is None or method.upper() in methods):
            return perm
    return None


def path_allowed(role: str | None, method: str, path: str) -> bool:
    if path.startswith(OPEN_PREFIXES):
        return True
    perm = required_permission(method, path)
    return perm is None or has_perm(role, perm)
