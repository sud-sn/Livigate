"""Admin data-source screen: the mapping in use, its column contract, data checks and the comparison with the
client's BI. Runs on whichever layout the suite runs on (see conftest)."""

from __future__ import annotations

CHECK_FOR_MESS = {
    "reload_duplicates_removed": "duplicate_fill_rows",
    "soft_deleted_fills": "soft_deleted_fills",
    "orphan_claim_transactions": "orphan_claims",
    "secondary_payer_claims": "secondary_payer_claims",
    "split_remittances": "split_remittances",
}


def test_admin_sees_the_mapping_and_a_clean_contract(login, profile) -> None:  # type: ignore[no-untyped-def]
    data = login("u-admin").get("/api/admin/data-source").json()
    assert data["profile"]["name"] == profile
    assert len(data["relations"]) == 7 and all(r["ok"] for r in data["relations"]), data["relations"]
    mapped = [r for r in data["relations"] if r["mapped"]]
    if profile == "canonical":
        assert not mapped and not data["helpers"] and data["bi_views"] == []
    else:
        assert len(mapped) == 7 and all(r["sql"].lstrip().upper().startswith("SELECT") for r in mapped)
        assert {h["name"] for h in data["helpers"]} >= {"SRC_FILL", "SRC_REMIT"}
        assert data["bi_views"] == ["BI_CLAIM_STATUS", "BI_PRESCRIBER_MONTHLY"]
    assert data["last_sync"]["quality"]["fail"] == 0


def test_data_checks_run_on_demand_and_are_audited(login, demo, app_db, profile) -> None:  # type: ignore[no-untyped-def]
    admin = login("u-admin")
    r = admin.post("/api/admin/data-source/checks")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["fail"] == 0 and body["summary"]["error"] == 0
    by_key = {c["key"]: c for c in body["results"]}
    if profile != "canonical":
        for check, injected in CHECK_FOR_MESS.items():
            assert by_key[check]["count"] == demo["mess"][injected], check
    # Counts and explanations only: no patient identifiers in any result
    assert "PT0" not in r.text
    audit = app_db.execute("SELECT action FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()[0]
    assert audit == "admin.data_checks_run"


def test_bi_parity(login, profile) -> None:  # type: ignore[no-untyped-def]
    report = login("u-admin").get("/api/admin/data-source/parity").json()
    if profile == "canonical":
        assert report["available"] is False and report["reason"]
        return
    assert report["available"] is True
    sections = {s["key"]: s for s in report["sections"]}
    # Same money: the ported formula (or the BI view itself) agrees with the BI to the cent on every fill
    assert sections["expected"]["compared"] > 500 and sections["expected"]["mismatched"] == 0
    assert sections["days_to_payment"]["compared"] > 300 and sections["days_to_payment"]["mismatched"] == 0
    # Definitions differ on purpose, and every difference is explained
    status = sections["status"]
    assert status["mismatched"] > 0 and status["unexplained"] == 0, status["groups"]
    assert any("flat 30 days" in g["explanation"] for g in status["groups"])
    volume = sections["prescriber_volume"]
    assert volume["explained"] > 0 and volume["unexplained"] == 0, volume["groups"]
    assert report["unexplained"] == 0
    # Business keys only
    assert "PT0" not in str(report)


def test_parity_window_is_bounded(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("u-admin")
    assert admin.get("/api/admin/data-source/parity", params={"window_days": 2}).status_code == 422
    assert admin.get("/api/admin/data-source/parity", params={"window_days": 4000}).status_code == 422


def test_full_resync_on_request_changes_nothing(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("u-admin")
    run = admin.post("/api/admin/sync", params={"full": "true"}).json()
    assert run["status"] == "ok" and run["stats"]["full_scan"] is True
    assert run["stats"]["created"] == 0 and run["stats"]["updated"] == 0


def test_data_source_screens_are_admin_only(login) -> None:  # type: ignore[no-untyped-def]
    for user in ("u-billing", "u-billing-in", "u-pharmacist", "u-outreach", "u-marketing", "u-auditor"):
        s = login(user)
        assert s.get("/api/admin/data-source").status_code == 403, user
        assert s.get("/api/admin/data-source/parity").status_code == 403, user
        assert s.post("/api/admin/data-source/checks").status_code == 403, user
