"""Negative tests for the API security controls (OWASP API Top 10) and HIPAA access rules."""

from __future__ import annotations

import json
import re
import uuid


def test_requires_session(app_client) -> None:  # type: ignore[no-untyped-def]
    app_client.cookies.clear()
    assert app_client.get("/api/queue").status_code == 401
    assert app_client.get("/api/compliance/controls").status_code == 401


def test_post_without_csrf_token_is_refused(login) -> None:  # type: ignore[no-untyped-def]
    billing = login("u-billing")
    item = billing.get("/api/queue").json()["items"][0]
    r = billing.post(
        f"/api/work-items/{item['id']}/approve", json={"idempotency_key": uuid.uuid4().hex, "confirm": True}, csrf=False
    )
    assert r.status_code == 403


def test_function_level_authorization(login) -> None:  # type: ignore[no-untyped-def]
    marketing = login("u-marketing")
    assert marketing.get("/api/claims").status_code == 403
    assert marketing.get("/api/admin/users").status_code == 403
    assert marketing.get("/api/compliance/controls").status_code == 403
    assert marketing.post("/api/patients/PT00042/reveal").status_code == 403
    auditor = login("u-auditor")
    assert auditor.get("/api/queue").status_code == 403
    assert auditor.get("/api/compliance/audit-log").status_code == 200
    outreach = login("u-outreach")
    assert (
        outreach.post(
            "/api/exports/claims", json={"confirm": True, "start": "2026-11-01", "end": "2026-11-27"}
        ).status_code
        == 403
    )


def test_object_level_authorization(login, app_db) -> None:  # type: ignore[no-untyped-def]
    """API1: billing cannot open a pharmacist-queue item, even by guessing its ID."""
    pharm_item = app_db.execute("SELECT id FROM work_items WHERE queue_role='pharmacist' AND status='open'").fetchone()
    assert pharm_item is not None
    billing = login("u-billing")
    assert billing.get(f"/api/work-items/{pharm_item['id']}").status_code == 404
    assert (
        billing.post(
            f"/api/work-items/{pharm_item['id']}/approve", json={"idempotency_key": uuid.uuid4().hex, "confirm": True}
        ).status_code
        == 404
    )
    assert billing.get("/api/work-items/999999").status_code == 404
    pharmacist = login("u-pharmacist")
    assert pharmacist.get(f"/api/work-items/{pharm_item['id']}").status_code == 200


def test_marketing_responses_carry_no_patient_fields(login) -> None:  # type: ignore[no-untyped-def]
    """C-05 / API3: marketing sees prescriber data and aggregate counts only."""
    marketing = login("u-marketing")
    listing = marketing.get("/api/prescribers", params={"segment": "declining"})
    assert listing.status_code == 200
    rows = listing.json()["rows"]
    assert any(r["id"] == "PR0007" for r in rows), "the declining story prescriber should be listed"
    profile = marketing.get("/api/prescribers/PR0007")
    assert profile.status_code == 200

    def keys(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k.lower()
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    # No field that could hold patient data, and no patient identifier anywhere in the values.
    forbidden = ("patient", "dob", "birth", "first_name", "last_name", "phone_patient")
    assert not [k for k in keys(profile.json()) if any(f in k for f in forbidden)]
    assert not re.search(r"PT\d{5}", json.dumps(profile.json()))
    draft = marketing.post("/api/prescribers/PR0007/draft", json={"kind": "visit_brief"}).json()
    assert draft["label"] == "AI draft" and not re.search(r"PT\d{5}", draft["text"])
    assert not any("patient" in f for f in draft["fields_sent"])


def test_phi_masked_by_default_and_reveal_is_audited(login, app_db) -> None:  # type: ignore[no-untyped-def]
    outreach = login("u-outreach")
    entries = outreach.get("/api/outreach/lists", params={"list_type": "refill_due"}).json()["entries"]
    assert entries and all(e["patient"]["masked"] for e in entries)
    assert all("first_name" not in e["patient"] for e in entries)
    pid = entries[0]["patient_id"]
    revealed = outreach.post(f"/api/patients/{pid}/reveal")
    assert revealed.status_code == 200 and revealed.json()["first_name"]
    row = app_db.execute("SELECT action, patient_id FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
    assert tuple(row) == ("phi.reveal", pid)


def test_offshore_user_sees_masked_phi_only(login) -> None:  # type: ignore[no-untyped-def]
    """C-11: a billing user outside the US is masked unless the client approves."""
    offshore = login("u-billing-in")
    assert offshore.me["phi_visible"] is False
    assert offshore.get("/api/queue").status_code == 200
    r = offshore.post("/api/patients/PT00042/reveal")
    assert r.status_code == 403 and "C-11" in r.json()["error"]


def test_removed_user_loses_access_immediately(login) -> None:  # type: ignore[no-untyped-def]
    victim = login("u-admin-2")
    assert victim.get("/api/auth/me").status_code == 200
    admin = login("u-admin")
    assert admin.patch("/api/admin/users/u-admin-2", json={"active": False}).status_code == 200
    assert victim.get("/api/auth/me").status_code == 401
    assert admin.patch("/api/admin/users/u-admin-2", json={"active": True}).status_code == 200


def test_role_change_applies_on_next_request(login) -> None:  # type: ignore[no-untyped-def]
    user = login("u-billing-in")
    assert user.get("/api/claims").status_code == 200
    admin = login("u-admin")
    admin.patch("/api/admin/users/u-billing-in", json={"role": "marketing"})
    assert user.get("/api/claims").status_code == 403
    admin.patch("/api/admin/users/u-billing-in", json={"role": "billing"})
    assert user.get("/api/claims").status_code == 200


def test_idle_timeout(login, app_db) -> None:  # type: ignore[no-untyped-def]
    user = login("u-auditor")
    app_db.execute("UPDATE user_sessions SET last_seen_at = '2026-01-01 00:00:00' WHERE user_id = 'u-auditor'")
    app_db.commit()
    r = user.get("/api/auth/me")
    assert r.status_code == 401 and "15 minutes" in r.json()["error"]


def test_errors_never_leak_stack_traces(login) -> None:  # type: ignore[no-untyped-def]
    billing = login("u-billing")
    r = billing.get("/api/claims", params={"start": "not-a-date"})
    assert r.status_code == 422 and "Traceback" not in r.text and r.json()["error"]


def test_security_headers(app_client) -> None:  # type: ignore[no-untyped-def]
    r = app_client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
