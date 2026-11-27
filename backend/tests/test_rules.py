"""Rules engine: deterministic results from catalog parameters."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from levigate.catalog import PLAYBOOK
from levigate.rules import (
    PayerRules,
    contact_eligibility,
    derive_status,
    outreach_priority,
    playbook_fix,
    refill_bucket,
    score_prescribers,
)

TODAY = date(2026, 11, 27)
PAYER = PayerRules(payment_window_days=14, underpay_pct=0.05, underpay_min_amount=10, refill_pct=0.8)


def claim(**kw):  # type: ignore[no-untyped-def]
    base = {
        "RESPONSE_STATUS": "P",
        "EXPECTED_AMOUNT": 100.0,
        "PAID_AMOUNT": 100.0,
        "FILL_DATE": "2026-11-01",
        "RX_NUMBER": "4471",
        "FILL_NUMBER": 2,
        "DAYS_SUPPLY": 30,
    }
    return {**base, **kw}


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (claim(RESPONSE_STATUS="R"), "rejected"),
        (claim(RESPONSE_STATUS="X"), "reversed"),
        (claim(), "paid"),
        (claim(PAID_AMOUNT=94.0), "paid"),  # $6 short: under the $10 minimum
        (claim(EXPECTED_AMOUNT=400.0, PAID_AMOUNT=385.0), "paid"),  # $15 short but only 3.75%
        (claim(PAID_AMOUNT=80.0), "underpaid"),
        (claim(RESPONSE_STATUS="C", FILL_DATE="2026-11-20"), "pending"),
        (claim(RESPONSE_STATUS="C", FILL_DATE="2026-11-12"), "unpaid_past_due"),  # 15 days > 14-day window
        (claim(RESPONSE_STATUS=None, FILL_DATE="2026-11-13"), "pending"),  # exactly 14 days: still inside
    ],
)
def test_derive_status(row, expected) -> None:  # type: ignore[no-untyped-def]
    assert derive_status(row, PAYER, TODAY) == expected


def test_refill_too_soon_computes_next_eligible_date_in_code() -> None:
    prior = [
        {"RX_NUMBER": "4471", "FILL_NUMBER": 0, "FILL_DATE": "2026-10-19", "DAYS_SUPPLY": 30, "STATUS": "picked_up"},
        {"RX_NUMBER": "4471", "FILL_NUMBER": 1, "FILL_DATE": "2026-11-18", "DAYS_SUPPLY": 30, "STATUS": "picked_up"},
        {"RX_NUMBER": "4471", "FILL_NUMBER": 2, "FILL_DATE": "2026-11-26", "DAYS_SUPPLY": 30, "STATUS": "on_hold"},
    ]
    fix = playbook_fix(PLAYBOOK["79"], claim(RESPONSE_STATUS="R", REJECT_CODE="79"), PAYER, TODAY, prior)
    # Last dispensed fill Nov 18 + ceil(30 * 0.8) = 24 days -> Dec 12.
    assert fix.computed["eligible_date"] == "2026-12-12"
    assert fix.due_date == date(2026, 12, 12)
    assert fix.summary == "Resubmit on 12 Dec"
    assert fix.computed["last_fill_number"] == 1  # the on-hold fill is not "dispensed"


def test_refill_too_soon_uses_payer_threshold() -> None:
    prior = [
        {"RX_NUMBER": "4471", "FILL_NUMBER": 1, "FILL_DATE": "2026-11-18", "DAYS_SUPPLY": 30, "STATUS": "picked_up"}
    ]
    fix = playbook_fix(PLAYBOOK["79"], claim(), PayerRules(refill_pct=0.75), TODAY, prior)
    assert fix.computed["eligible_date"] == "2026-12-11"  # ceil(22.5) = 23 days


def test_refill_too_soon_already_eligible_resubmits_today() -> None:
    prior = [
        {"RX_NUMBER": "4471", "FILL_NUMBER": 1, "FILL_DATE": "2026-10-01", "DAYS_SUPPLY": 30, "STATUS": "picked_up"}
    ]
    fix = playbook_fix(PLAYBOOK["79"], claim(), PAYER, TODAY, prior)
    assert fix.due_date == TODAY
    assert fix.summary.startswith("Resubmit today")


def test_refill_too_soon_without_history_does_not_guess() -> None:
    fix = playbook_fix(PLAYBOOK["79"], claim(), PAYER, TODAY, [])
    assert fix.action == "verify_fill_history"


def test_every_playbook_entry_has_a_handler() -> None:
    for code, entry in PLAYBOOK.items():
        fix = playbook_fix(entry, claim(RESPONSE_STATUS="R", REJECT_CODE=code), PAYER, TODAY, [])
        assert fix.summary and fix.steps, code


def _rx(fill_days_ago: int, days_supply: int = 30, refills: int = 3, expires: str | None = "2027-06-01"):  # type: ignore[no-untyped-def]
    return {
        "FILL_DATE": (TODAY - timedelta(days=fill_days_ago)).isoformat(),
        "DAYS_SUPPLY": days_supply,
        "REFILLS_REMAINING": refills,
        "RX_EXPIRES_DATE": expires,
    }


RULE = {"due_within_days": 7, "adherence_overdue_days": 8, "lapse_days": 90}


@pytest.mark.parametrize(
    ("candidate", "bucket"),
    [
        (_rx(27), "refill_due"),  # due in 3 days
        (_rx(36), "refill_due"),  # 6 days overdue: still the refill list
        (_rx(15), None),  # due in 15 days: not yet
        (_rx(40), "adherence_gap"),  # 10 days overdue
        (_rx(125), "lapsed"),  # 95 days past due: C-10 lapse
        (_rx(27, refills=0), None),  # no refills left
        (_rx(27, expires="2026-11-01"), None),  # Rx expired
    ],
)
def test_refill_bucket(candidate, bucket) -> None:  # type: ignore[no-untyped-def]
    assert refill_bucket(candidate, TODAY, RULE)[0] == bucket


def test_contact_eligibility_reasons() -> None:
    assert contact_eligibility(consent_granted=True, opted_out=False, lapsed=False).eligible
    no_consent = contact_eligibility(consent_granted=False, opted_out=False, lapsed=False)
    assert not no_consent.eligible and "No call consent on file (C-09)" in no_consent.reasons
    opted = contact_eligibility(consent_granted=False, opted_out=True, lapsed=False)
    assert opted.reasons == ["Patient opted out of calls"]
    lapsed = contact_eligibility(consent_granted=True, opted_out=False, lapsed=True)
    assert lapsed.reasons == ["Prescription lapsed over 90 days (C-10)"]


def test_outreach_priority_parts_sum_to_score() -> None:
    score, parts = outreach_priority(
        due=TODAY,
        revenue=100,
        last_contact=None,
        today=TODAY,
        weights={"urgency": 0.5, "revenue": 0.3, "contact_gap": 0.2},
        max_revenue=200,
    )
    assert score == pytest.approx(sum(parts.values()), abs=0.2)
    later, _ = outreach_priority(
        due=TODAY + timedelta(days=7),
        revenue=100,
        last_contact=None,
        today=TODAY,
        weights={"urgency": 0.5, "revenue": 0.3, "contact_gap": 0.2},
        max_revenue=200,
    )
    assert score > later  # sooner due ranks higher


def test_prescriber_segments() -> None:
    rows = [
        {"PRESCRIBER_ID": "A", "RX_90": 9, "RX_PRIOR_90": 60, "REV_90": 900, "FIRST_FILL": "2026-01-01"},
        {"PRESCRIBER_ID": "B", "RX_90": 40, "RX_PRIOR_90": 38, "REV_90": 5000, "FIRST_FILL": "2026-01-01"},
        {"PRESCRIBER_ID": "C", "RX_90": 5, "RX_PRIOR_90": 0, "REV_90": 400, "FIRST_FILL": "2026-10-01"},
        {"PRESCRIBER_ID": "D", "RX_90": 0, "RX_PRIOR_90": 12, "REV_90": 0, "FIRST_FILL": "2026-01-01"},
    ]
    body = {
        "weights": {"volume": 0.5, "revenue": 0.35, "trend": 0.15},
        "declining_drop": 0.3,
        "declining_min_prior": 6,
        "new_days": 90,
        "top_n": 1,
    }
    out = {p["PRESCRIBER_ID"]: p for p in score_prescribers(rows, body, TODAY)}
    assert out["A"]["IS_DECLINING"] and not out["B"]["IS_DECLINING"]
    assert out["B"]["IS_TOP"] and not out["A"]["IS_TOP"]
    assert out["C"]["IS_NEW"]
    assert out["D"]["IS_DORMANT"]


def test_patient_without_a_phone_is_not_callable() -> None:
    elig = contact_eligibility(consent_granted=True, opted_out=False, lapsed=False, has_phone=False)
    assert not elig.eligible and elig.reasons == ["No phone number on file — add one in the pharmacy system"]
