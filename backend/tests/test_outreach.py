"""Patient outreach: consent (C-09), refill-reminder scope (C-10), outcomes (PO-03) and opt-outs (PO-05)."""

from __future__ import annotations


def _lists(outreach, list_type: str) -> dict:  # type: ignore[no-untyped-def]
    r = outreach.get("/api/outreach/lists", params={"list_type": list_type})
    assert r.status_code == 200, r.text
    return r.json()


def test_lists_apply_consent_and_lapse_rules_with_reasons(login, app_db) -> None:  # type: ignore[no-untyped-def]
    outreach = login("u-outreach")
    refill = _lists(outreach, "refill_due")
    granted = {
        r[0]
        for r in app_db.execute(
            "SELECT patient_id FROM consents c WHERE channel='call' AND id = (SELECT MAX(id) FROM consents "
            "WHERE patient_id = c.patient_id AND channel='call') AND status='granted'"
        )
    }
    assert refill["entries"], "expected patients due for refills"
    assert all(e["patient_id"] in granted for e in refill["entries"])
    assert any("No call consent on file (C-09)" in x["reasons"] for x in refill["exclusions"])
    gap = _lists(outreach, "adherence_gap")
    assert any("Prescription lapsed over 90 days (C-10)" in x["reasons"] for x in gap["exclusions"])
    assert all(e["days_to_due"] >= -90 for e in gap["entries"])
    assert "Ranked by due date" in refill["sort_explanation"]


def test_call_requires_consent_outcome_and_honours_opt_out(login) -> None:  # type: ignore[no-untyped-def]
    outreach = login("u-outreach")
    data = _lists(outreach, "refill_due")
    excluded = next(x for x in data["exclusions"] if "No call consent on file (C-09)" in x["reasons"])
    blocked = outreach.post(
        "/api/outreach/calls", json={"patient_id": excluded["patient_id"], "list_type": "refill_due"}
    )
    assert blocked.status_code == 403  # server re-checks consent at call time

    target = data["entries"][0]
    script = outreach.post(
        "/api/outreach/script",
        json={"patient_id": target["patient_id"], "list_type": "refill_due", "rx_number": target["rx_number"]},
    )
    assert script.status_code == 200 and script.json()["label"] == "AI draft"
    assert "opt" in script.json()["text"].lower() or "prefer not" in script.json()["text"].lower()

    call = outreach.post(
        "/api/outreach/calls",
        json={"patient_id": target["patient_id"], "list_type": "refill_due", "rx_number": target["rx_number"]},
    )
    assert call.status_code == 200, call.text
    second = outreach.post(
        "/api/outreach/calls", json={"patient_id": data["entries"][1]["patient_id"], "list_type": "refill_due"}
    )
    assert second.status_code == 409  # outcome required before the next call
    done = outreach.post(f"/api/outreach/calls/{call.json()['call_id']}/outcome", json={"outcome": "opted_out"})
    assert done.status_code == 200

    after = _lists(outreach, "refill_due")
    assert all(e["patient_id"] != target["patient_id"] for e in after["entries"])
    again = outreach.post("/api/outreach/calls", json={"patient_id": target["patient_id"], "list_type": "refill_due"})
    assert again.status_code == 403


def test_marketing_cannot_reach_outreach(login) -> None:  # type: ignore[no-untyped-def]
    marketing = login("u-marketing")
    assert marketing.get("/api/outreach/lists").status_code == 403
