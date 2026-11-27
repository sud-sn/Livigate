"""Claims workflow end to end: sync → queue → explanation → approval → reconciliation → dollars recovered."""

from __future__ import annotations

import uuid
from datetime import datetime

from levigate.demo.warehouse import record_upstream_payment


def _queue_items(billing, item_type: str) -> list[dict]:  # type: ignore[no-untyped-def]
    """Walk every page of one item type in the queue (server-side filter and paging)."""
    items, page = [], 1
    while True:
        q = billing.get("/api/queue", params={"type": item_type, "page": page, "limit": 50}).json()
        items += q["items"]
        if page * q["page_size"] >= q["filtered_total"]:
            return items
        page += 1


def _find_4471(billing) -> dict:  # type: ignore[no-untyped-def]
    return next(i for i in _queue_items(billing, "claim_rejected") if i["rx_number"] == "4471")


def test_queue_shows_refill_too_soon_with_rule_fix(login) -> None:  # type: ignore[no-untyped-def]
    billing = login("u-billing")
    q = billing.get("/api/queue").json()
    assert q["kind"] == "claims" and q["total"] > 0
    item = _find_4471(billing)
    assert item["reject_code"] == "79"
    assert item["headline"] == "Resubmit on 12 Dec"
    assert item["patient"]["masked"] is True and "." in item["patient"]["display"]
    flags = [i["days_overdue"] > 0 for i in q["items"]]
    assert flags == sorted(flags, reverse=True)  # every overdue item comes before every item not yet due
    amounts = [i["amount_at_risk"] for i in q["items"] if i["days_overdue"] > 0]
    assert amounts == sorted(amounts, reverse=True)  # overdue items: largest dollars first
    assert q["overdue"] > 0, "a backlog older than its SLA must surface as overdue"
    paged = billing.get("/api/queue", params={"type": "claim_unpaid", "limit": 10}).json()
    assert len(paged["items"]) == 10 and all(i["type"] == "claim_unpaid" for i in paged["items"])
    assert paged["total"] == q["total"]  # counts always describe the whole queue


def test_unknown_reject_code_goes_to_review_without_suggestion(login) -> None:  # type: ignore[no-untyped-def]
    billing = login("u-billing")
    review = _queue_items(billing, "reject_review")
    assert len(review) == 2 and all(i["reject_code"] == "AG" for i in review)
    detail = billing.get(f"/api/work-items/{review[0]['id']}").json()
    assert detail["fix"] is None and detail["rule"] is None
    assert billing.post(f"/api/work-items/{review[0]['id']}/explanation").status_code == 409
    no_note = billing.post(
        f"/api/work-items/{review[0]['id']}/approve", json={"idempotency_key": uuid.uuid4().hex, "confirm": True}
    )
    assert no_note.status_code == 422


def test_explanation_cites_rule_and_sends_no_patient_fields(login) -> None:  # type: ignore[no-untyped-def]
    billing = login("u-billing")
    item = _find_4471(billing)
    resp = billing.post(f"/api/work-items/{item['id']}/explanation")
    assert resp.status_code == 200, resp.text
    ex = resp.json()
    assert ex["label"] == "AI draft"
    assert "Rule 79 v3" in ex["text"] and "Dec 12" in ex["text"]
    assert not any("patient" in f or f in ("dob", "phone", "first_name") for f in ex["fields_sent"])
    assert ex["provider"] == "offline"


def test_approval_is_named_audited_idempotent_and_not_optimistic(login, app_db) -> None:  # type: ignore[no-untyped-def]
    billing = login("u-billing")
    item = _find_4471(billing)
    url = f"/api/work-items/{item['id']}/approve"
    assert billing.post(url, json={"idempotency_key": uuid.uuid4().hex}).status_code == 422  # no confirm
    key = uuid.uuid4().hex
    first = billing.post(url, json={"idempotency_key": key, "confirm": True, "ai_disposition": "accepted"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["message"] == "Approved · resubmission set for 12 Dec"
    assert body["audit_line"].startswith("Audit trail: approved by J. Patel, rule 79 v3,")
    # The response came back only after the approval and its audit event were committed.
    row = app_db.execute("SELECT action, actor_id FROM audit_events WHERE seq = ?", (body["audit_seq"],)).fetchone()
    assert tuple(row) == ("work_item.approve", "u-billing")
    again = billing.post(url, json={"idempotency_key": key, "confirm": True})
    assert again.status_code == 200 and again.json()["approval_id"] == body["approval_id"]
    assert billing.post(url, json={"idempotency_key": uuid.uuid4().hex, "confirm": True}).status_code == 409
    assert (
        app_db.execute(
            "SELECT disposition FROM ai_interactions WHERE task='claim_explanation' ORDER BY id DESC"
        ).fetchone()[0]
        == "accepted"
    )


def test_reconciliation_resolves_and_counts_recovered(login, env_paths, demo) -> None:  # type: ignore[no-untyped-def]
    admin = login("u-admin")
    before = admin.get("/api/dashboard").json()
    recovered_before = next(m for m in before["metrics"] if m["key"] == "dollars_recovered")["value"]
    # The payer pays the resubmitted claim upstream; the next warehouse load lands it.
    record_upstream_payment(env_paths["warehouse"], demo["layout"], "4471", 2, 68.0, datetime(2026, 11, 27, 9, 30))
    run = admin.post("/api/admin/sync").json()
    assert run["status"] == "ok" and all(s["status"] == "done" for s in run["steps"])
    assert run["stats"]["resolved"] >= 1
    billing = login("u-billing")
    assert not any(i["rx_number"] == "4471" for i in billing.get("/api/queue").json()["items"])
    after = admin.get("/api/dashboard").json()
    recovered_after = next(m for m in after["metrics"] if m["key"] == "dollars_recovered")["value"]
    assert round(recovered_after - recovered_before, 2) == 68.0


def test_claims_tracker_counts_match_source(login, canonical_sql) -> None:  # type: ignore[no-untyped-def]
    billing = login("u-billing")
    data = billing.get("/api/claims", params={"start": "2026-11-01", "end": "2026-11-27", "limit": 10}).json()
    source = canonical_sql(
        "SELECT COUNT(DISTINCT FILL_ID) FROM FACT_RXFILL WHERE FILL_DATE BETWEEN '2026-11-01' AND '2026-11-27' "
        "AND FILL_ID IN (SELECT FILL_ID FROM FACT_CLAIM)"
    )[0][0]
    assert data["total"] == source == sum(data["counts"].values())
    rejected = billing.get(
        "/api/claims", params={"start": "2026-11-01", "end": "2026-11-27", "status": "rejected"}
    ).json()
    assert rejected["total"] == data["counts"]["rejected"]
    assert all(r["status"] == "rejected" for r in rejected["rows"])


def test_followup_is_tracked(login) -> None:  # type: ignore[no-untyped-def]
    billing = login("u-billing")
    item = next(i for i in billing.get("/api/queue").json()["items"] if i["type"] == "claim_unpaid")
    r = billing.post(
        f"/api/work-items/{item['id']}/followups",
        json={"party": "plan", "outcome": "Payer says pending review", "next_followup_date": "2026-12-02"},
    )
    assert r.status_code == 200 and r.json()["attempts"] == 1
    detail = billing.get(f"/api/work-items/{item['id']}").json()
    assert any(e["kind"] == "followup" for e in detail["timeline"])
