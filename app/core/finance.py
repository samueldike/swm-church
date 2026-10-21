"""Branch/HQ money position (pure arithmetic, so it can be tested without a database)."""
from __future__ import annotations


def branch_position(pct: float, held_by_hq: float, at_branch: float, to_hq: float, to_branch: float) -> dict:
    """What a branch and HQ owe each other.

    * ``total``       gifts recorded for the branch (paid online or recorded by hand)
    * ``held_by_hq``  gifts HQ's account received on the branch's behalf; ``at_branch`` gifts the branch took itself
    * ``entitlement`` HQ's share: ``pct`` % of the total
    * ``to_hq`` / ``to_branch``  remittances already made either way

    ``balance`` > 0: the branch still owes HQ that much. ``balance`` < 0: HQ owes the branch.
    HQ already physically holds ``held_by_hq`` (less what it has sent back) plus what the branch remitted.
    """
    total = round(held_by_hq + at_branch, 2)
    entitlement = round(total * pct / 100, 2)
    balance = round(entitlement - (held_by_hq - to_branch) - to_hq, 2)
    return {"total": total, "entitlement": entitlement, "balance": balance}
