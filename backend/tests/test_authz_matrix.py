"""Every API route x every role: the permission table in ``levigate.policy`` is what the server actually enforces.

Routes are discovered from the app, not listed by hand, so a new route without a permission fails this test. Calls
use IDs that do not exist and empty bodies, so allowed calls stop at 404/422 and change nothing.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.routing import APIRoute

from levigate.models import Role
from levigate.policy import PERMISSIONS

PUBLIC = {
    "GET /api/health",
    "GET /api/auth/config",
    "GET /api/auth/demo-users",
    "POST /api/auth/demo-login",
    "GET /api/auth/session",
}
SIGNED_IN_ONLY = {"GET /api/auth/me", "POST /api/auth/logout"}  # any signed-in user, by design
NOT_CALLED = {"POST /api/auth/logout"}  # would end the shared test session; covered in test_security
USERS = {
    "u-billing": Role.BILLING,
    "u-billing-in": Role.BILLING,
    "u-pharmacist": Role.PHARMACIST,
    "u-outreach": Role.OUTREACH,
    "u-marketing": Role.MARKETING,
    "u-admin": Role.ADMIN,
    "u-auditor": Role.AUDITOR,
}
# Object-level policy on top of the role: offshore staff without PHI approval cannot reveal patient details.
OBJECT_DENIALS = {("u-billing-in", "POST /api/patients/{patient_id}/reveal")}
PATH_VALUES = {
    "item_id": "999999",
    "call_id": "999999",
    "alert_id": "999999",
    "entry_id": "999999",
    "user_id": "u-does-not-exist",
    "connection_id": "does-not-exist",
    "patient_id": "PT99999",
    "prescriber_id": "PR9999",
    "kind": "payer_config",
    "key": "PY99",
}


def _routes(app: Any) -> list[APIRoute]:
    def walk(routes: list[Any]) -> list[APIRoute]:
        out: list[APIRoute] = []
        for r in routes:
            if isinstance(r, APIRoute):
                out.append(r)
            elif hasattr(r, "original_router"):  # routers included with include_router
                out.extend(walk(r.original_router.routes))
        return out

    return walk(app.routes)


def _guards(dependant: Any, out: set[str]) -> set[str]:
    for d in dependant.dependencies:
        if getattr(d.call, "permission", None):
            out.add(d.call.permission)
        if getattr(d.call, "__name__", "") == "current_principal":
            out.add("<signed-in>")
        _guards(d, out)
    return out


@pytest.fixture(scope="module")
def matrix(app_client) -> list[tuple[str, str, str, set[str]]]:  # type: ignore[no-untyped-def]
    rows = []
    for route in _routes(app_client.app):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            label = f"{method} {route.path}"
            url = re.sub(r"\{(\w+)\}", lambda m: PATH_VALUES[m.group(1)], route.path)
            rows.append((label, method, url, _guards(route.dependant, set())))
    return rows


def test_every_route_is_public_signed_in_or_permissioned(matrix) -> None:  # type: ignore[no-untyped-def]
    assert len(matrix) >= 50
    for label, _, _, guards in matrix:
        if label in PUBLIC:
            assert not guards, f"{label} is meant to be public"
            continue
        assert "<signed-in>" in guards, f"{label} does not require a signed-in user"
        permissions = guards - {"<signed-in>"}
        if label not in SIGNED_IN_ONLY:
            assert len(permissions) == 1, f"{label} needs exactly one permission, has {permissions}"
            assert permissions <= set(PERMISSIONS), f"{label} uses an unknown permission {permissions}"


def _call(session_or_client: Any, method: str, url: str) -> int:
    if method == "GET":
        return session_or_client.get(url).status_code
    if method == "POST":
        return session_or_client.post(url, json={}).status_code
    return session_or_client.patch(url, json={}).status_code


def test_signed_out_requests_are_refused(app_client, matrix) -> None:  # type: ignore[no-untyped-def]
    app_client.cookies.clear()
    for label, method, url, _ in matrix:
        if label in PUBLIC or label in NOT_CALLED:
            continue
        status = getattr(app_client, method.lower())(url, **({} if method == "GET" else {"json": {}})).status_code
        assert status in (401, 403), f"{label} answered {status} without a session"


def test_role_matrix_matches_the_permission_table(login, matrix) -> None:  # type: ignore[no-untyped-def]
    sessions = {u: login(u) for u in USERS}
    seen: dict[str, set[bool]] = {}
    failures = []
    for label, method, url, guards in matrix:
        if label in PUBLIC or label in NOT_CALLED:
            continue
        permission = next(iter(guards - {"<signed-in>"}), None)
        for user, role in USERS.items():
            allowed = permission is None or role in PERMISSIONS[permission]
            if (user, label) in OBJECT_DENIALS:
                allowed = False
            status = _call(sessions[user], method, url)
            if permission:
                seen.setdefault(permission, set()).add(allowed)
            if allowed and status in (401, 403):
                failures.append(f"{user} ({role}) refused on {label}: {status}")
            if not allowed and status != 403:
                failures.append(f"{user} ({role}) not refused on {label}: {status}")
            if status >= 500:
                failures.append(f"{user} got a server error on {label}: {status}")
    assert not failures, "\n".join(failures)
    # The matrix has teeth: every permission not granted to every role was seen both granted and refused
    everyone = set(USERS.values())
    one_sided = {p: s for p, s in seen.items() if s != {True, False} and not everyone <= PERMISSIONS[p]}
    assert not one_sided, one_sided
