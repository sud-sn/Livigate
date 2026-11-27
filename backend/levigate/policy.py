"""One shared policy layer (API1 / API5 / C-05).

Function-level checks (``PERMISSIONS``) decide which roles may call an endpoint at all; object-level checks
(``can_view_work_item`` and friends) decide whether *this* caller may see *that* record: same tenant, a role
that owns the record's queue, and — for PHI — a location the client has approved (C-11).
"""

from __future__ import annotations

from levigate.config import get_settings
from levigate.models import Role, User, WorkItem

R = Role

PERMISSIONS: dict[str, frozenset[Role]] = {
    "queue.view": frozenset({R.BILLING, R.OUTREACH, R.MARKETING, R.PHARMACIST, R.ADMIN}),
    "claims.view": frozenset({R.BILLING, R.PHARMACIST, R.ADMIN}),
    "claims.approve": frozenset({R.BILLING, R.PHARMACIST}),
    "claims.followup": frozenset({R.BILLING, R.PHARMACIST}),
    "claims.export": frozenset({R.BILLING, R.ADMIN}),
    "outreach.view": frozenset({R.OUTREACH, R.PHARMACIST}),
    "outreach.call": frozenset({R.OUTREACH, R.PHARMACIST}),
    "patients.phi": frozenset({R.BILLING, R.OUTREACH, R.PHARMACIST}),
    "prescribers.view": frozenset({R.MARKETING, R.BILLING, R.PHARMACIST, R.ADMIN}),
    "crm.write": frozenset({R.MARKETING}),
    "dashboard.view": frozenset({R.BILLING, R.OUTREACH, R.MARKETING, R.PHARMACIST, R.ADMIN, R.AUDITOR}),
    "compliance.view": frozenset({R.ADMIN, R.AUDITOR}),
    "compliance.manage": frozenset({R.ADMIN}),
    "audit.view": frozenset({R.ADMIN, R.AUDITOR}),
    "audit.export": frozenset({R.ADMIN, R.AUDITOR}),
    "admin.users": frozenset({R.ADMIN}),
    "admin.connections": frozenset({R.ADMIN}),
    "admin.catalog": frozenset({R.ADMIN}),
    "admin.sync": frozenset({R.ADMIN}),
    "catalog.view": frozenset({R.ADMIN, R.BILLING, R.PHARMACIST, R.AUDITOR}),
    "search": frozenset({R.BILLING, R.OUTREACH, R.MARKETING, R.PHARMACIST, R.ADMIN}),
}

# Which queues a role works. The pharmacist sees billing work too (clinical rejects are routed to them).
QUEUE_ACCESS: dict[Role, frozenset[str]] = {
    R.BILLING: frozenset({"billing"}),
    R.PHARMACIST: frozenset({"pharmacist", "billing"}),
    R.ADMIN: frozenset({"billing", "pharmacist"}),
}


def has_permission(user: User, permission: str) -> bool:
    allowed = PERMISSIONS.get(permission)
    if allowed is None:  # unknown permission names fail closed
        return False
    try:
        role = Role(user.role)
    except ValueError:  # service identities and unknown roles hold no user permissions
        return False
    return user.active and role in allowed


def permissions_for(user: User) -> list[str]:
    return sorted(p for p in PERMISSIONS if has_permission(user, p))


def can_view_work_item(user: User, item: WorkItem) -> bool:
    if item.tenant_id != user.tenant_id or not has_permission(user, "claims.view"):
        return False
    return item.queue_role in QUEUE_ACCESS.get(Role(user.role), frozenset())


def can_act_on_work_item(user: User, item: WorkItem) -> bool:
    return can_view_work_item(user, item) and has_permission(user, "claims.approve")


def phi_visible(user: User) -> bool:
    """Whether this user may ever see unmasked patient identifiers (role + offshore rule C-11)."""
    if not has_permission(user, "patients.phi"):
        return False
    if get_settings().offshore_phi_masking and user.location != "US" and not user.offshore_phi_approved:
        return False
    return True


def mask_name(first: str | None, last: str | None) -> str:
    return f"{(first or '?')[:1]}.{(last or '?')[:1]}.".upper()


def mask_phone(phone: str | None) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return f"•••• {digits[-4:]}" if len(digits) >= 4 else "••••"
