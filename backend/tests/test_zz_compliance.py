"""Compliance Center: live controls, audit pack, and tamper detection on the hash-chained audit log.

Named test_zz_* so it runs after the workflow tests have produced approvals, calls and exports to check.
"""

from __future__ import annotations

import io
import zipfile


def test_controls_are_computed_from_live_data(login) -> None:  # type: ignore[no-untyped-def]
    auditor = login("u-auditor")
    data = auditor.get("/api/compliance/controls").json()
    controls = {c["id"]: c for c in data["controls"]}
    assert list(controls) == [f"C-{i:02d}" for i in range(1, 15)]
    assert controls["C-06"]["status"] == "green" and "verified" in controls["C-06"]["evidence"]
    assert controls["C-08"]["status"] == "green"  # every approval in this session was named
    assert controls["C-09"]["status"] == "green"  # no call went out without consent
    assert controls["C-07"]["status"] == "green"
    assert controls["C-01"]["status"] == "amber"  # the telephony BAA renews within 60 days
    assert "VoiceBridge" in controls["C-01"]["evidence"]


def test_approval_and_consent_coverage(login) -> None:  # type: ignore[no-untyped-def]
    auditor = login("u-auditor")
    appr = auditor.get("/api/compliance/approvals").json()
    assert appr["pct"] == 100.0 and appr["exceptions"] == []
    consent = auditor.get("/api/compliance/consent").json()
    assert consent["contacts_without_consent"] == 0
    assert consent["opt_outs"] >= 1


def test_audit_pack_is_generated_and_logged(login, app_db) -> None:  # type: ignore[no-untyped-def]
    auditor = login("u-auditor")
    r = auditor.post("/api/compliance/audit-pack", json={"confirm": True, "start": "2026-09-01", "end": "2026-11-27"})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"audit-pack.pdf", "controls.csv", "approvals.csv", "audit_events.csv", "vendors.csv"} <= names
    assert z.read("audit-pack.pdf")[:4] == b"%PDF"
    last = app_db.execute("SELECT action FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()[0]
    assert last == "export.audit_pack"


def test_tampering_with_the_audit_log_is_detected(login, app_db) -> None:  # type: ignore[no-untyped-def]
    auditor = login("u-auditor")
    assert auditor.get("/api/compliance/audit-log/verify").json()["ok"] is True
    seq, detail = app_db.execute("SELECT seq, detail FROM audit_events ORDER BY seq LIMIT 1 OFFSET 1").fetchone()
    app_db.execute("UPDATE audit_events SET detail = ? WHERE seq = ?", ('{"edited": true}', seq))
    app_db.commit()
    try:
        result = auditor.get("/api/compliance/audit-log/verify").json()
        assert result["ok"] is False and result["first_bad_seq"] == seq
        c06 = next(c for c in auditor.get("/api/compliance/controls").json()["controls"] if c["id"] == "C-06")
        assert c06["status"] == "red"
    finally:
        app_db.execute("UPDATE audit_events SET detail = ? WHERE seq = ?", (detail, seq))
        app_db.commit()
    assert auditor.get("/api/compliance/audit-log/verify").json()["ok"] is True
